from django.contrib.auth.models import AbstractUser
from django.db import models
from django.db.models.signals import post_save
from django.dispatch import receiver
from django.utils import timezone


# Create your models here.
class CustomUser(AbstractUser):
    THEME_CHOICES = [
        ('light', 'Light'),
        ('dark', 'Dark'),
        ('system', 'System Default')
    ]

    email = models.EmailField(unique=True, db_index=True)

    profile_picture = models.ImageField(upload_to='profile_pics/%Y/%m/', default="profile_pics/default.jpg")
    bio = models.TextField(max_length=500, blank=True)
    location = models.CharField(max_length=30, blank=True)
    birth_date = models.DateField(null=True, blank=True)
    theme_preference = models.CharField(max_length=10, choices=THEME_CHOICES, default='system')
    last_active = models.DateTimeField(default=timezone.now)

    # Spotify OAuth Token Fields - Ensure these are correctly defined or added if missing
    spotify_access_token = models.TextField(null=True, blank=True)
    spotify_refresh_token = models.TextField(null=True, blank=True)
    spotify_token_expiry = models.DateTimeField(null=True, blank=True)
    spotify_scope = models.TextField(null=True, blank=True)

    # Fields for storing user's top items from Spotify
    spotify_top_artists = models.JSONField(null=True, blank=True, default=list) # Stores list of dicts: [{'id': '...', 'name': '...'}]
    spotify_top_tracks = models.JSONField(null=True, blank=True, default=list)  # Stores list of dicts: [{'id': '...', 'name': '...'}]

    class Meta:
        indexes = [
            models.Index(fields=['username', 'email']),
            models.Index(fields=['last_name', 'first_name']),
        ]

    def __str__(self):
        return self.username

    @property
    def full_name(self):
        return f"{self.first_name} {self.last_name}".strip() or self.username

    @property
    def is_premium(self):
        """Check if user has an active premium subscription"""
        return (
                hasattr(self, 'subscription') and
                self.subscription.is_active() and
                self.subscription.plan.price > 0
        )

    @property
    def following_count(self):
        # self.following_set is the related_name from Follow.follower
        return self.following_set.count()

    @property
    def followers_count(self):
        # self.followers_set is the related_name from Follow.following
        return self.followers_set.count()


class UserActivity(models.Model):
    user = models.ForeignKey(
        CustomUser,
        on_delete=models.CASCADE,
        related_name='activities'
    )
    activity_type = models.CharField(max_length=50, db_index=True)
    description = models.TextField()
    timestamp = models.DateTimeField(auto_now_add=True, db_index=True) # Individual index is good

    class Meta:
        indexes = [
            models.Index(fields=['user', 'timestamp']), # Composite index for user-specific activity ordering
        ]
        ordering = ['-timestamp'] # Default ordering

    def __str__(self):
        return f"{self.user.username} - {self.activity_type} - {self.timestamp}"


class Follow(models.Model):
    follower = models.ForeignKey(CustomUser, related_name='following_set', on_delete=models.CASCADE)
    following = models.ForeignKey(CustomUser, related_name='followers_set', on_delete=models.CASCADE)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        unique_together = ('follower', 'following')
        indexes = [
            models.Index(fields=['follower', 'following']),
            models.Index(fields=['following', 'follower']), # For quick lookup of followers
        ]

    def __str__(self):
        return f"{self.follower.username} follows {self.following.username}"
