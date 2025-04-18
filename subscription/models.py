from datetime import timedelta
from django.db import models
from django.utils import timezone
from django.dispatch import receiver
from django.db.models.signals import post_save
from users.models import CustomUser


class SubscriptionPlan(models.Model):
    name = models.CharField(max_length=100, db_index=True)
    price = models.DecimalField(max_digits=6, decimal_places=2)
    duration_days = models.IntegerField(help_text="Duration in days")
    description = models.TextField()
    features = models.TextField()

    def __str__(self):
        return self.name


class Subscription(models.Model):
    STATUS_CHOICES = (
        ('active', 'Active'),
        ('expired', 'Expired'),
        ('cancelled', 'Cancelled'),
    )
    user = models.OneToOneField(CustomUser, on_delete=models.CASCADE, related_name='subscription')
    plan = models.ForeignKey(SubscriptionPlan, on_delete=models.PROTECT, related_name='subscriptions')
    start_date = models.DateTimeField(auto_now_add=True)
    end_date = models.DateTimeField(db_index=True)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='active', db_index=True)

    class Meta:
        indexes = [
            models.Index(fields=['status', 'end_date']),
        ]

    def __str__(self):
        return f"{self.user.username} - {self.plan.name}"

    def is_active(self):
        return self.status == 'active' and self.end_date > timezone.now()

    def days_remaining(self):
        if not self.is_active():
            return 0
        delta = self.end_date - timezone.now()
        return max(0, delta.days)


@receiver(post_save, sender=CustomUser)
def create_user_subscription(sender, instance, created, **kwargs):
    # Check if user has no subscription
    if not created:
        return

    free_plan = SubscriptionPlan.objects.get(name='Free')

    if not hasattr(instance, 'subscription'):
        Subscription.objects.create(
            user=instance,
            plan=free_plan,
            end_date=timezone.now() + timedelta(days=free_plan.duration_days)
        )
