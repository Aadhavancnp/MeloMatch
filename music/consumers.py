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
