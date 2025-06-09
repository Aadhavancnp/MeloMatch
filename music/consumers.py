import json
import logging
from channels.generic.websocket import AsyncJsonWebsocketConsumer
from channels.db import database_sync_to_async # If any DB access is needed

logger = logging.getLogger(__name__)

class RecommendationConsumer(AsyncJsonWebsocketConsumer):
    async def connect(self):
        self.user = self.scope.get("user") # AuthMiddlewareStack provides user in scope

        if self.user is None or not self.user.is_authenticated: # Check is_authenticated for safety
            logger.warning("Anonymous user tried to connect to recommendations websocket. Closing.")
            await self.close()
            return

        self.group_name = f"user_{self.user.id}_recommendations"
        logger.info(f"User {self.user.id} ({self.user.username}) connecting to recommendations group: {self.group_name}")

        await self.channel_layer.group_add(
            self.group_name,
            self.channel_name
        )
        await self.accept()
        logger.info(f"User {self.user.id} connected successfully to recommendations group {self.group_name}.")
        # Optionally send a welcome message or initial state if needed
        await self.send_json({
            "type": "connection_established",
            "message": "Connected for real-time recommendation updates!"
        })

    async def disconnect(self, close_code):
        if hasattr(self, 'group_name') and self.user and self.user.is_authenticated:
            logger.info(f"User {self.user.id} disconnecting from recommendations group: {self.group_name}")
            await self.channel_layer.group_discard(
                self.group_name,
                self.channel_name
            )
        else:
            logger.info("User (anonymous or no group attribute) disconnected from recommendations.")

    async def live_recommendation_update(self, event):
        """
        Handler for live recommendation updates sent from the Celery task.
        Event type should be 'live.recommendation.update'.
        """
        logger.info(f"RecommendationConsumer (User ID: {self.user.id}): Received live_recommendation_update event: {event.get('type')}")

        recommendations_data = event.get("recommendations", [])

        await self.send_json({
            "type": "live_recommendations", # Client-side will look for this specific type
            "data": { # Nesting data under a 'data' key is a good practice
                "message": event.get("message", "Fresh recommendations based on your current listening!"),
                "recommendations": recommendations_data
            }
        })
        logger.info(f"RecommendationConsumer (User ID: {self.user.id}): Sent live_recommendations to WebSocket client.")

    async def general_recommendation_notification(self, event): # Renamed from send_recommendation_notification
        """
        Handler for general recommendation notifications (e.g., new batch recs available).
        Event type should be 'general.recommendation.notification'.
        """
        logger.info(f"RecommendationConsumer (User ID: {self.user.id}): Received general_recommendation_notification event: {event.get('type')}")
        await self.send_json({
            "type": "general_recommendations", # Client-side will look for this type
            "data": {
                 "message": event.get("message", "You have new recommendations!"),
                 "details": event.get("details", {}) # Could be a summary, link, etc.
            }
        })
        logger.info(f"RecommendationConsumer (User ID: {self.user.id}): Sent general_recommendations to WebSocket client.")

    # Fallback for any other types if needed, or just log
    async def unknown_event_type(self, event):
        logger.warning(f"RecommendationConsumer (User ID: {self.user.id}): Received event with unknown type: {event.get('type')}")
        # Optionally send an error or ignore
        pass

# If CollaborativePlaylistConsumer was also in this file, it should be preserved.
# Based on previous step (Phase 2, Step 18), it was. Adding it back.
from .models import Playlist, Track
from users.models import UserActivity # For permission checking and logging (CustomUser is via self.user)

class CollaborativePlaylistConsumer(AsyncJsonWebsocketConsumer):
    async def connect(self):
        self.user = self.scope.get("user")
        self.playlist_id = self.scope['url_route']['kwargs']['playlist_id']
        self.playlist_group_name = f'playlist_{self.playlist_id}'

        if self.user is None or not self.user.is_authenticated:
            await self.close(); return
        self.playlist = await self.get_playlist_for_collab(self.playlist_id)
        if self.playlist is None or not await self.check_collab_permissions(self.playlist, self.user, 'view'):
            await self.close(); return
        await self.channel_layer.group_add(self.playlist_group_name, self.channel_name)
        await self.accept()
        current_tracks = await self.get_playlist_tracks_serializable_for_collab(self.playlist)
        await self.send_json({'type': 'playlist.initial_state', 'tracks': current_tracks, 'playlist_name': self.playlist.name, 'playlist_id': str(self.playlist.id)})

    async def disconnect(self, close_code):
        if hasattr(self, 'playlist_group_name'):
            await self.channel_layer.group_discard(self.playlist_group_name, self.channel_name)

    async def receive_json(self, content):
        action = content.get('action'); track_db_id = content.get('track_id')
        if not self.user or not self.user.is_authenticated or not self.playlist or not await self.check_collab_permissions(self.playlist, self.user, 'edit'):
            await self.send_json({'type': 'error', 'message': 'Not authorized or playlist not found.'}); return
        track = await self.get_track_for_collab(track_db_id) if track_db_id else None
        if (action in ['add_track', 'remove_track']) and not track:
            await self.send_json({'type': 'error', 'message': f"Track ID {track_db_id} not found."}); return

        broadcast_payload = None
        if action == 'add_track' and track:
            await self.add_track_to_playlist_collab(self.playlist, track)
            await self.log_user_activity_collab(self.user, self.playlist, track, "playlist_add_track", f"Added '{track.title}' to '{self.playlist.name}'")
            broadcast_payload = {'type': 'playlist.update', 'action': 'track_added', 'track': await self.serialize_track_for_collab(track), 'user_who_acted': self.user.username, 'playlist_id': str(self.playlist.id)}
        elif action == 'remove_track' and track:
            await self.remove_track_from_playlist_collab(self.playlist, track)
            await self.log_user_activity_collab(self.user, self.playlist, track, "playlist_remove_track", f"Removed '{track.title}' from '{self.playlist.name}'")
            broadcast_payload = {'type': 'playlist.update', 'action': 'track_removed', 'track_id': track_db_id, 'user_who_acted': self.user.username, 'playlist_id': str(self.playlist.id)}
        else: await self.send_json({'type': 'error', 'message': f"Invalid action: {action}"})
        if broadcast_payload: await self.channel_layer.group_send(self.playlist_group_name, broadcast_payload)

    async def playlist_update(self, event): # Renamed from playlist.update to avoid dot in name for method
        await self.send_json({'type': 'playlist_operation_broadcast', 'action_type': event.get('action'), 'data': event})

    @database_sync_to_async
    def get_playlist_for_collab(self, playlist_id): return Playlist.objects.prefetch_related('owner', 'collaborators').filter(id=playlist_id).first()
    @database_sync_to_async
    def check_collab_permissions(self, playlist, user, perm_type='view'): return playlist.can_view(user) if perm_type == 'view' else playlist.can_edit(user)
    @database_sync_to_async
    def get_track_for_collab(self, track_id): return Track.objects.filter(id=track_id).first()
    @database_sync_to_async
    def add_track_to_playlist_collab(self, playlist, track): playlist.tracks.add(track)
    @database_sync_to_async
    def remove_track_from_playlist_collab(self, playlist, track): playlist.tracks.remove(track)
    @database_sync_to_async
    def log_user_activity_collab(self, u, p, t, at, d): UserActivity.objects.create(user=u, activity_type=at, description=d, content_object=p)
    @database_sync_to_async
    def serialize_track_for_collab(self, track): return {'id': str(track.id), 'title': track.title, 'artists_names': track.artists_names, 'album': track.album, 'image_url': track.image_url, 'spotify_id': track.spotify_id} if track else None
    @database_sync_to_async
    def get_playlist_tracks_serializable_for_collab(self, playlist): return [ {'id': str(t.id), 'title': t.title, 'artists_names': t.artists_names, 'album': t.album, 'image_url': t.image_url, 'spotify_id': t.spotify_id} for t in playlist.tracks.all().prefetch_related('artists')]

# Note: The 'type' in group_send should map to method names in the consumer.
# So, if Celery sends type 'live.recommendation.update', the consumer needs 'live_recommendation_update' method.
# If Celery sends type 'general.recommendation.notification', consumer needs 'general_recommendation_notification'.
# The CollaborativePlaylistConsumer uses 'playlist.update', so its handler should be 'playlist_update'.
# It's good practice to use underscores in method names if dots are in type strings, e.g. type 'foo.bar' -> method foo_bar.
# Corrected CollaborativePlaylistConsumer's playlist_update handler name.
