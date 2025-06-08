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

    # Spotify Integration Fields (re-adding after reset)
    spotify_access_token = models.TextField(blank=True, null=True)
    spotify_refresh_token = models.TextField(blank=True, null=True)
    spotify_token_expiry = models.DateTimeField(blank=True, null=True)
    spotify_scope = models.TextField(blank=True, null=True)

    # Fields for storing user's top items from Spotify
    spotify_top_artists = models.JSONField(null=True, blank=True, default=dict) # Stores list of artist dicts
    spotify_top_tracks = models.JSONField(null=True, blank=True, default=dict) # Stores list of track dicts


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


class UserActivity(models.Model):
    user = models.ForeignKey(
        CustomUser,
        on_delete=models.CASCADE,
        related_name='activities' # Consider changing to 'user_activities' if 'activities' is too generic
    )
    activity_type = models.CharField(max_length=100, db_index=True) # Increased max_length
    description = models.TextField(blank=True) # Made blank=True
    timestamp = models.DateTimeField(auto_now_add=True, db_index=True)
    # Consider adding GenericForeignKey if activities relate to various models (Track, Playlist, Artist)
    # from django.contrib.contenttypes.fields import GenericForeignKey
    # from django.contrib.contenttypes.models import ContentType
    # content_type = models.ForeignKey(ContentType, on_delete=models.CASCADE, null=True, blank=True)
    # object_id = models.PositiveIntegerField(null=True, blank=True)
    # content_object = GenericForeignKey('content_type', 'object_id')

    class Meta: # Added Meta for UserActivity
        ordering = ['-timestamp']
        indexes = [
            models.Index(fields=['user', 'timestamp']), # Composite index
            models.Index(fields=['activity_type']),
        ]
        verbose_name = "User Activity"
        verbose_name_plural = "User Activities"

    def __str__(self):
        return f"{self.user.username} - {self.activity_type} at {self.timestamp.strftime('%Y-%m-%d %H:%M')}"

# Follow model (re-adding after reset, assuming it was part of users app)
class Follow(models.Model):
    follower = models.ForeignKey(CustomUser, related_name='following_set', on_delete=models.CASCADE)
    following = models.ForeignKey(CustomUser, related_name='followers_set', on_delete=models.CASCADE)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        unique_together = ('follower', 'following')
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['follower', 'following']),
        ]
        verbose_name = "Follow Relationship"
        verbose_name_plural = "Follow Relationships"

    def __str__(self):
        return f"{self.follower.username} follows {self.following.username}"

# Re-add post-save signal for subscriptions if it was in this file or ensure it's in subscription/models.py
# For this subtask, focusing on CustomUser fields.
# The signal handler for creating a default subscription for a new user is typically in subscription/models.py
# and connected via AppConfig.ready().
# If it was in users/models.py, it would look something like:
# from subscription.models import SubscriptionPlan, Subscription # Adjust import if needed

# @receiver(post_save, sender=CustomUser)
# def create_user_subscription_on_user_create(sender, instance, created, **kwargs):
#     if created:
#         try:
#             free_plan = SubscriptionPlan.objects.get(name='Free') # Or some default plan ID/slug
#             Subscription.objects.create(user=instance, plan=free_plan, start_date=timezone.now())
#             logger.info(f"Default subscription created for new user: {instance.username}")
#         except SubscriptionPlan.DoesNotExist:
#             logger.error("Default 'Free' plan not found for new user subscription.")
#         except Exception as e:
#             logger.error(f"Error creating default subscription for {instance.username}: {e}")
