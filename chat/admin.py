from django.contrib import admin
from .models import ChatMessage

@admin.register(ChatMessage)
class ChatMessageAdmin(admin.ModelAdmin):
    list_display = ('room_id', 'user', 'message_summary', 'timestamp')
    list_filter = ('room_id', 'user', 'timestamp')
    search_fields = ('room_id', 'user__username', 'message_text')
    readonly_fields = ('timestamp',) # auto_now_add is effectively read-only once created

    def message_summary(self, obj):
        return (obj.message_text[:50] + '...') if len(obj.message_text) > 50 else obj.message_text
    message_summary.short_description = 'Message (Summary)'
