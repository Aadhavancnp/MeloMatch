from django.db import models
from django.conf import settings # For settings.AUTH_USER_MODEL
from django.utils import timezone # Though timestamp uses auto_now_add

class ChatMessage(models.Model):
    room_id = models.CharField(
        max_length=255,
        db_index=True,
        help_text="Identifier for the chat room (e.g., sync session ID, specific event ID)."
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE, # Or models.SET_NULL if messages should persist if user is deleted
        related_name='chat_messages',
        help_text="User who sent the message."
    )
    message_text = models.TextField(
        help_text="Content of the chat message."
    )
    timestamp = models.DateTimeField(
        auto_now_add=True,
        db_index=True,
        help_text="When the message was sent."
    )

    class Meta:
        ordering = ['timestamp'] # Default ordering for queries
        indexes = [
            models.Index(fields=['room_id', 'timestamp']), # For efficiently fetching messages for a room, ordered by time
        ]
        verbose_name = "Chat Message"
        verbose_name_plural = "Chat Messages"

    def __str__(self):
        # Truncate message_text for display in admin or logs if it's too long
        display_message = (self.message_text[:75] + '...') if len(self.message_text) > 75 else self.message_text
        return f"Room {self.room_id} - {self.user.username}: \"{display_message}\" @ {self.timestamp.strftime('%Y-%m-%d %H:%M')}"

    # Potential future methods:
    # def to_dict(self): # For serialization if not using DRF serializers
    #     return {
    #         'id': self.id,
    #         'room_id': self.room_id,
    #         'user_id': self.user.id,
    #         'username': self.user.username,
    #         # 'profile_picture_url': self.user.profile_picture.url if self.user.profile_picture else None, # Requires CustomUser.profile_picture
    #         'message_text': self.message_text,
    #         'timestamp': self.timestamp.isoformat(),
    #     }
