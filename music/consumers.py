import json
from channels.generic.websocket import AsyncJsonWebsocketConsumer
import logging

logger = logging.getLogger(__name__)

class RecommendationConsumer(AsyncJsonWebsocketConsumer):
    async def connect(self):
        self.user = self.scope.get("user") # AuthMiddlewareStack provides user in scope

        if self.user is None or self.user.is_anonymous:
            logger.warning("Anonymous user tried to connect to recommendations websocket. Closing.")
            await self.close()
        else:
            self.group_name = f"user_{self.user.id}_recommendations"
            logger.info(f"User {self.user.id} connecting to recommendations group: {self.group_name}")

            await self.channel_layer.group_add(
                self.group_name,
                self.channel_name
            )
            await self.accept()
            logger.info(f"User {self.user.id} connected successfully to {self.group_name}.")
            # Optionally send a welcome message
            await self.send_json({
                "type": "connection_established",
                "message": "Connected for real-time recommendation updates!"
            })

    async def disconnect(self, close_code):
        if hasattr(self, 'group_name') and self.user and not self.user.is_anonymous:
            logger.info(f"User {self.user.id} disconnecting from recommendations group: {self.group_name}")
            await self.channel_layer.group_discard(
                self.group_name,
                self.channel_name
            )
        else:
            logger.info("User (anonymous or no group) disconnected.")

    async def recommendation_notification(self, event):
        """
        Handler for messages sent from the server to this specific consumer group.
        This method is called when a message is sent to the group
        with type 'recommendation.notification'.
        """
        logger.info(f"Sending recommendation notification to group {self.group_name}: {event}")
        await self.send_json({
            "type": "new_recommendation", # This type will be handled by client-side JS
            "message": event.get("message", "You have new recommendations!"),
            "recommendations_summary": event.get("recommendations_summary", [])
        })

    # Example: If you wanted to receive messages from the client (not used in this PoC)
    # async def receive_json(self, content, **kwargs):
    #     logger.info(f"Received message from client {self.user.id}: {content}")
    #     await self.send_json({
    #         "type": "echo_message",
    #         "original_message": content.get("message", "")
    #     })


# For Collaborative Playlists
from channels.db import database_sync_to_async
from .models import Playlist, Track
from users.models import CustomUser, UserActivity # For permission checking and logging

class CollaborativePlaylistConsumer(AsyncJsonWebsocketConsumer):
    async def connect(self):
        self.user = self.scope.get("user")
        self.playlist_id = self.scope['url_route']['kwargs']['playlist_id']
        self.playlist_group_name = f'playlist_{self.playlist_id}'

        if self.user is None or self.user.is_anonymous:
            logger.warning(f"Anonymous user tried to connect to playlist {self.playlist_id}. Closing.")
            await self.close()
            return

        self.playlist = await self.get_playlist(self.playlist_id)

        if self.playlist is None:
            logger.warning(f"Playlist {self.playlist_id} not found. Closing connection for user {self.user.username}.")
            await self.close()
            return

        can_connect = await self.check_permissions(self.playlist, self.user, 'view')
        if not can_connect: # For collaboration, they must at least be able to view (owner or collaborator)
            logger.warning(f"User {self.user.username} does not have permission to connect to playlist {self.playlist_id}. Closing.")
            await self.close()
            return

        # Join room group
        await self.channel_layer.group_add(
            self.playlist_group_name,
            self.channel_name
        )
        await self.accept()
        logger.info(f"User {self.user.username} connected to playlist {self.playlist_id} group {self.playlist_group_name}.")
        # Send current track list to the newly connected user
        current_tracks = await self.get_playlist_tracks_serializable(self.playlist)
        await self.send_json({
            'type': 'playlist.initial_state',
            'tracks': current_tracks,
            'playlist_name': self.playlist.name,
            'playlist_id': str(self.playlist.id) # Assuming id is UUID or int
        })


    async def disconnect(self, close_code):
        if hasattr(self, 'playlist_group_name'):
            logger.info(f"User {self.user.username if self.user else 'Unknown'} disconnecting from playlist group {self.playlist_group_name}")
            await self.channel_layer.group_discard(
                self.playlist_group_name,
                self.channel_name
            )

    async def receive_json(self, content):
        action = content.get('action')
        track_id = content.get('track_id') # Spotify ID or DB ID, assuming DB ID for now

        if not self.user or self.user.is_anonymous:
            await self.send_error("User not authenticated.")
            return

        if not self.playlist: # Should be set during connect
            await self.send_error("Playlist not found or not connected.")
            return

        can_edit_playlist = await self.check_permissions(self.playlist, self.user, 'edit')
        if not can_edit_playlist:
            await self.send_error("You don't have permission to edit this playlist.")
            return

        track = None
        if track_id:
            track = await self.get_track(track_id)
            if not track:
                await self.send_error(f"Track with ID {track_id} not found.")
                return

        if action == 'add_track' and track:
            success = await self.add_track_to_playlist(self.playlist, track, self.user)
            if success:
                await self.log_user_activity(
                    user=self.user,
                    playlist=self.playlist,
                    track=track,
                    activity_type="playlist_add_track",
                    description=f"Added track '{track.title}' to playlist '{self.playlist.name}'"
                )
                await self.channel_layer.group_send(
                    self.playlist_group_name,
                    {
                        'type': 'playlist.update',
                        'action': 'track_added',
                        'track': await self.serialize_track(track),
                        'user_who_acted': self.user.username,
                        'playlist_id': str(self.playlist.id)
                    }
                )
        elif action == 'remove_track' and track:
            success = await self.remove_track_from_playlist(self.playlist, track, self.user)
            if success:
                await self.log_user_activity(
                    user=self.user,
                    playlist=self.playlist,
                    track=track,
                    activity_type="playlist_remove_track",
                    description=f"Removed track '{track.title}' from playlist '{self.playlist.name}'"
                )
                await self.channel_layer.group_send(
                    self.playlist_group_name,
                    {
                        'type': 'playlist.update',
                        'action': 'track_removed',
                        'track_id': track_id, # Send track_id for client to remove
                        'user_who_acted': self.user.username,
                        'playlist_id': str(self.playlist.id)
                    }
                )
        # Reordering action deferred as per plan
        # elif action == 'reorder_tracks':
        #     new_order = content.get('new_order_ids') # List of track DB IDs
        #     # ... complex logic for reordering ...
        #     await self.channel_layer.group_send(...)
        else:
            await self.send_error(f"Invalid action: {action} or missing track_id.")


    async def playlist_update(self, event):
        # This method is called when the channel layer sends a message to the group.
        # It then sends the message to the WebSocket client.
        # The 'type' in event (e.g. 'playlist.update') is the name of this handler method.
        # We re-encapsulate it into a generic JSON structure for the client.
        await self.send_json({
            'type': event['type'], # This will be 'playlist.update' as defined in group_send
            'action_type': event.get('action'), # e.g. 'track_added', 'track_removed'
            'data': event, # Pass the whole event data through
        })

    async def send_error(self, message):
        await self.send_json({
            'type': 'error',
            'message': message
        })

    @database_sync_to_async
    def get_playlist(self, playlist_id):
        try:
            # Assuming playlist_id is the primary key (e.g., UUID or int)
            return Playlist.objects.prefetch_related('owner', 'collaborators').get(id=playlist_id)
        except Playlist.DoesNotExist:
            return None
        except ValueError: # If playlist_id is not a valid UUID/int
             logger.error(f"Invalid playlist_id format: {playlist_id}")
             return None


    @database_sync_to_async
    def check_permissions(self, playlist, user, permission_type='view'):
        if permission_type == 'edit':
            return playlist.can_edit(user)
        return playlist.can_view(user) # Default to view

    @database_sync_to_async
    def get_track(self, track_id):
        try:
            # Assuming track_id is the primary key of the Track model
            return Track.objects.get(id=track_id)
        except Track.DoesNotExist:
            return None
        except ValueError: # If track_id is not a valid format for PK
            logger.error(f"Invalid track_id format: {track_id}")
            return None


    @database_sync_to_async
    def add_track_to_playlist(self, playlist, track, acting_user):
        # Basic M2M add. Idempotent by default.
        playlist.tracks.add(track)
        # Could add more logic here if an intermediary model was used for ordering, who added, etc.
        return True # Assuming success unless exception

    @database_sync_to_async
    def remove_track_from_playlist(self, playlist, track, acting_user):
        # Basic M2M remove. Idempotent by default.
        playlist.tracks.remove(track)
        return True # Assuming success unless exception

    @database_sync_to_async
    def log_user_activity(self, user, playlist, track, activity_type, description):
        UserActivity.objects.create(
            user=user,
            activity_type=activity_type,
            description=description,
            content_object=playlist # Generic foreign key to playlist
        )

    @database_sync_to_async
    def serialize_track(self, track):
        # Basic serialization. Expand as needed by client.
        if not track: return None
        return {
            'id': str(track.id), # Assuming track.id is UUID or int
            'title': track.title,
            'artists_names': track.artists_names, # Uses property from Track model
            'album': track.album,
            'image_url': track.image_url,
            'spotify_id': track.spotify_id,
        }

    @database_sync_to_async
    def get_playlist_tracks_serializable(self, playlist):
        tracks = playlist.tracks.all().prefetch_related('artists') # Optimize query
        return [
            {
                'id': str(track.id),
                'title': track.title,
                'artists_names': track.artists_names,
                'album': track.album,
                'image_url': track.image_url,
                'spotify_id': track.spotify_id,
            } for track in tracks
        ]
