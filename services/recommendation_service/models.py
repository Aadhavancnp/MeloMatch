from django.db import models
from django.conf import settings # To import CustomUser indirectly
from music.models import Track # Assuming Track model is in music app
from django.utils import timezone

class UserRecommendationCache(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='recommendation_caches')
    context = models.CharField(max_length=200, db_index=True)  # Increased max_length for potentially complex contexts like "similar_to_track_spotify:xxxx"

    # Storing as JSONField list of track Spotify IDs
    recommended_track_ids = models.JSONField(default=list)

    generated_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField(null=True, blank=True, db_index=True) # Index for querying expired caches

    class Meta:
        unique_together = ('user', 'context') # A user should have one set of active recommendations for a given context
        ordering = ['-generated_at']
        indexes = [
            models.Index(fields=['user', 'context', 'expires_at']), # For faster lookup of valid cache
        ]

    def __str__(self):
        return f"Cache for {self.user.username} - Context: {self.context} (Generated: {self.generated_at.strftime('%Y-%m-%d %H:%M')})"

    def is_expired(self):
        if self.expires_at:
            return timezone.now() >= self.expires_at
        return False # If no expiry is set, consider it non-expiring or handle differently based on logic

    def get_tracks(self):
        """Helper to fetch actual Track objects from stored IDs, maintaining order."""
        if not self.recommended_track_ids:
            return []

        # Fetch tracks and create a dictionary for ordering
        tracks_qs = Track.objects.filter(spotify_id__in=self.recommended_track_ids).prefetch_related('artists', 'genres')
        tracks_map = {track.spotify_id: track for track in tracks_qs}

        # Return tracks in the original stored order
        ordered_tracks = [tracks_map[spotify_id] for spotify_id in self.recommended_track_ids if spotify_id in tracks_map]
        return ordered_tracks
