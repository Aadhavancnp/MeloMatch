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
        related_name='activities'
    )
    activity_type = models.CharField(max_length=50, db_index=True)
    description = models.TextField()
    timestamp = models.DateTimeField(auto_now_add=True, db_index=True)

    def __str__(self):
        return f"{self.user.username} - {self.activity_type} - {self.timestamp}"
