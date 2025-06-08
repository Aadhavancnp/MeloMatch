from django.db import models
from django.conf import settings # To import CustomUser indirectly
# from music.models import Track # Not strictly needed for this model, but often related
from django.utils import timezone

class UserRecommendationCache(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='recommendation_caches'
    )
    context = models.CharField(
        max_length=255, # Increased from 200 to 255 for more flexibility
        db_index=True,
        help_text="Context for the recommendations (e.g., 'item_item_collab_batch', 'mood_playlist_happy')"
    )
    # Storing as JSONField list of track Spotify IDs
    recommended_track_ids = models.JSONField(
        default=list,
        help_text="List of Spotify Track IDs for the recommendations."
    )
    generated_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField(
        null=True,
        blank=True,
        db_index=True,
        help_text="When these cached recommendations should expire."
    )

    class Meta:
        unique_together = ('user', 'context')
        ordering = ['-generated_at']
        indexes = [
            models.Index(fields=['user', 'context', 'expires_at']),
        ]
        verbose_name = "User Recommendation Cache"
        verbose_name_plural = "User Recommendation Caches"

    def __str__(self):
        return f"Cache for {self.user.username} - Context: {self.context} (Expires: {self.expires_at})"

    def is_expired(self):
        if self.expires_at:
            return timezone.now() >= self.expires_at
        return False # If no expiry is set, treat as non-expiring (or handle based on app logic)

    # If we need to fetch actual Track objects (e.g., in a view/template):
    # def get_tracks(self):
    #     """Helper to fetch actual Track objects from stored Spotify IDs, maintaining order."""
    #     if not self.recommended_track_ids:
    #         return []
    #
    #     # This import needs to be here or globally if Track model is confirmed available
    #     from music.models import Track
    #
    #     tracks_qs = Track.objects.filter(spotify_id__in=self.recommended_track_ids)
    #     tracks_map = {track.spotify_id: track for track in tracks_qs}
    #
    #     ordered_tracks = [tracks_map[spotify_id] for spotify_id in self.recommended_track_ids if spotify_id in tracks_map]
    #     return ordered_tracks
