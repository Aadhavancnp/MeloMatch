import json
import logging
from channels.generic.websocket import AsyncJsonWebsocketConsumer
from channels.db import database_sync_to_async # If any DB access is needed, though not directly in this consumer

logger = logging.getLogger(__name__)

class FriendActivityConsumer(AsyncJsonWebsocketConsumer):
    async def connect(self):
        self.user = self.scope.get("user")

        if self.user is None or not self.user.is_authenticated:
            logger.warning("Anonymous user tried to connect to friend activity websocket. Closing.")
            await self.close()
            return

        self.friend_activity_group_name = f"friend_activity_feed__{self.user.id}"
        logger.info(f"User {self.user.username} (ID: {self.user.id}) connecting to friend activity group: {self.friend_activity_group_name}")

        await self.channel_layer.group_add(
            self.friend_activity_group_name,
            self.channel_name
        )
        await self.accept()
        logger.info(f"User {self.user.username} connected successfully to {self.friend_activity_group_name}.")
        # Optionally send a welcome/confirmation message
        await self.send_json({
            "type": "connection_established",
            "message": "Connected for real-time friend activity updates!"
        })

    async def disconnect(self, close_code):
        if hasattr(self, 'friend_activity_group_name') and self.user and self.user.is_authenticated:
            logger.info(f"User {self.user.username} disconnecting from friend activity group: {self.friend_activity_group_name}")
            await self.channel_layer.group_discard(
                self.friend_activity_group_name,
                self.channel_name
            )
        else:
            logger.info(f"User (anonymous or no group) disconnected from friend activity.")

    async def friend_listening_update(self, event):
        """
        Handler for messages sent from the Celery task to this consumer's group.
        This method is called when a message is sent to the group with type 'friend.listening.update'.
        """
        logger.info(f"FriendActivityConsumer (User ID: {self.user.id}): Received friend_listening_update event: {event}")

        # Extract data from the event, which was prepared by the Celery task
        broadcasting_user_id = event.get('broadcasting_user_id')
        broadcasting_user_username = event.get('broadcasting_user_username')
        broadcasting_user_profile_pic_url = event.get('broadcasting_user_profile_pic_url')
        status_data = event.get('status_data') # This is the track_data dict

        # Ensure essential data is present
        if not all([broadcasting_user_id, broadcasting_user_username, status_data]):
            logger.warning(f"FriendActivityConsumer: Missing essential data in event: {event}")
            return

        # Send the structured data to the connected WebSocket client
        await self.send_json({
            'type': 'friend_activity_update', # This type will be handled by client-side JS
            'broadcasting_user': {
                'id': broadcasting_user_id,
                'username': broadcasting_user_username,
                'profile_picture_url': broadcasting_user_profile_pic_url
            },
            'listening_status': status_data # Contains track details, playback status etc.
        })
        logger.info(f"FriendActivityConsumer (User ID: {self.user.id}): Sent friend activity update to WebSocket client.")

    # Placeholder for receiving messages from client, if ever needed for this consumer
    # async def receive_json(self, content, **kwargs):
    #     logger.info(f"FriendActivityConsumer received message from client {self.user.username}: {content}")
    #     # Example: Echo back or process client message
    #     await self.send_json({
    #         "type": "echo_message",
    #         "original_message": content.get("message", "")
    #     })
