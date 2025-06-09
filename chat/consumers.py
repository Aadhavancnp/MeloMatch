import json
import logging
from channels.generic.websocket import AsyncJsonWebsocketConsumer
from channels.db import database_sync_to_async
from django.contrib.auth import get_user_model
from .models import ChatMessage
from django.utils import timezone # For timestamp formatting, though model auto-generates

logger = logging.getLogger(__name__)
User = get_user_model()

class ChatConsumer(AsyncJsonWebsocketConsumer):
    async def connect(self):
        self.user = self.scope.get("user")
        if not self.user or not self.user.is_authenticated: # More explicit check
            logger.warning("ChatConsumer: Anonymous user tried to connect. Closing.")
            await self.close()
            return

        # Get room_id from the URL route. Assumes URL pattern is like /ws/chat/{room_id}/
        # The 'url_route' key should exist if Django Channels routing is correctly set up.
        url_route_kwargs = self.scope.get('url_route', {}).get('kwargs', {})
        self.room_id = url_route_kwargs.get('room_id')

        if not self.room_id:
            logger.warning("ChatConsumer: room_id not found in URL. Closing connection.")
            await self.close()
            return

        self.room_group_name = f'chat_{self.room_id}'

        # Join room group
        await self.channel_layer.group_add(
            self.room_group_name,
            self.channel_name
        )
        await self.accept()
        logger.info(f"User {self.user.username} (ID: {self.user.id}) connected to chat room '{self.room_id}' (Group: {self.room_group_name}).")
        # Optional: Send a connection confirmation or load recent messages
        # await self.load_recent_messages()

    async def disconnect(self, close_code):
        if hasattr(self, 'room_group_name') and self.room_group_name: # Check if attribute exists
            await self.channel_layer.group_discard(
                self.room_group_name,
                self.channel_name
            )
        logger.info(f"User {self.user.username if self.user and self.user.is_authenticated else 'Anonymous/Unset'} disconnected from chat room {getattr(self, 'room_id', 'unknown')}.")

    async def receive_json(self, content):
        message_text = content.get('message')

        if not self.user or not self.user.is_authenticated: # Should be caught at connect
            await self.send_json({'error': 'User not authenticated.'}); return
        if not hasattr(self, 'room_id') or not self.room_id: # Should be set at connect
             await self.send_json({'error': 'Chat room not identified.'}); return

        if not message_text or not isinstance(message_text, str) or not message_text.strip():
            await self.send_json({'error': 'Message cannot be empty or invalid.'}); return

        # Limit message length
        if len(message_text.strip()) > 1024: # Example limit
            await self.send_json({'error': 'Message too long.'}); return

        chat_message_obj = await self.save_message(message_text.strip())

        if not chat_message_obj:
            await self.send_json({'error': 'Failed to save message to database.'}); return

        # Prepare data for broadcast
        profile_picture_url = None
        if hasattr(self.user, 'profile_picture') and self.user.profile_picture:
            try:
                profile_picture_url = self.user.profile_picture.url
            except ValueError: # Handle if image file is missing
                profile_picture_url = "/static/media/profile_pics/default.jpg" # Fallback
        else:
            profile_picture_url = "/static/media/profile_pics/default.jpg" # Fallback

        message_data_for_broadcast = {
            'id': chat_message_obj.id, # Include message ID
            'user_id': self.user.id,
            'username': self.user.username,
            'profile_picture_url': profile_picture_url,
            'message_text': chat_message_obj.message_text, # Already stripped
            'timestamp': chat_message_obj.timestamp.isoformat(), # ISO 8601 format
            'room_id': self.room_id # Include room_id in broadcast
        }

        # Send message to room group
        await self.channel_layer.group_send(
            self.room_group_name,
            {
                'type': 'chat.message', # Invokes chat_message handler on consumers in the group
                'message_data': message_data_for_broadcast
            }
        )
        logger.debug(f"Message from {self.user.username} broadcasted to group {self.room_group_name}.")

    async def chat_message(self, event): # Handler for messages from the group (i.e., from other users)
        """
        Sends the actual message to the WebSocket client.
        This method is called by `group_send` when a message of type 'chat.message' is sent.
        """
        await self.send_json({
            'type': 'new_chat_message', # This is the type client-side JS will look for
            'data': event['message_data']
        })
        logger.debug(f"Sent new_chat_message to client for user {self.user.id if self.user else 'Unknown'}")


    @database_sync_to_async
    def save_message(self, message_text: str):
        """
        Saves a chat message to the database.
        Called from receive_json.
        """
        try:
            # self.user should be an authenticated User instance from AuthMiddlewareStack
            if not self.user or not self.user.is_authenticated:
                 logger.error(f"save_message: Attempted to save message for unauthenticated user in room {self.room_id}.")
                 return None

            return ChatMessage.objects.create(
                room_id=self.room_id,
                user=self.user,
                message_text=message_text
            )
        except Exception as e:
            logger.error(f"Error saving chat message for room {self.room_id}, user {self.user.id if self.user else 'Unknown'}: {e}", exc_info=True)
            return None

    # Optional: Method to load recent messages on connect
    # @database_sync_to_async
    # def get_recent_messages(self, limit=20):
    #     messages = ChatMessage.objects.filter(room_id=self.room_id).order_by('-timestamp')[:limit]
    #     # Serialize messages (older first for display)
    #     return reversed([{
    #         'id': msg.id, 'user_id': msg.user.id, 'username': msg.user.username,
    #         'profile_picture_url': getattr(msg.user, 'profile_picture_url', None) or "/static/media/profile_pics/default.jpg",
    #         'message_text': msg.message_text, 'timestamp': msg.timestamp.isoformat()
    #     } for msg in messages])

    # async def load_recent_messages(self):
    #     recent_messages = await self.get_recent_messages()
    #     await self.send_json({
    #         'type': 'recent_messages',
    #         'data': list(recent_messages)
    #     })
    #     logger.info(f"Sent {len(list(recent_messages))} recent messages to user {self.user.username} for room {self.room_id}")
