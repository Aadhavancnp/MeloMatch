from django.contrib import admin
from .models import UserRecommendationCache

@admin.register(UserRecommendationCache)
class UserRecommendationCacheAdmin(admin.ModelAdmin):
    list_display = ('user', 'context', 'generated_at', 'expires_at', 'track_count')
    list_filter = ('user', 'context', 'generated_at', 'expires_at')
    search_fields = ('user__username', 'context')

    def track_count(self, obj):
        return len(obj.recommended_track_ids)
    track_count.short_description = 'Cached Tracks'
