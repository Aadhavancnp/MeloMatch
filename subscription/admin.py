from datetime import timedelta

from django.contrib import admin

from .models import SubscriptionPlan, Subscription


@admin.register(SubscriptionPlan)
class SubscriptionPlanAdmin(admin.ModelAdmin):
    list_display = ('name', 'price', 'duration_days', 'stripe_price_id')
    search_fields = ('name', 'description')
    list_filter = ('duration_days',)


@admin.register(Subscription)
class SubscriptionAdmin(admin.ModelAdmin):
    list_display = ('user', 'plan', 'status', 'start_date', 'end_date', 'is_active_display', 'days_remaining')
    list_filter = ('status', 'plan')
    search_fields = ('user__username', 'user__email', 'stripe_subscription_id', 'stripe_customer_id')
    date_hierarchy = 'start_date'
    actions = ['cancel_subscriptions', 'extend_subscriptions']

    def is_active_display(self, obj):
        return obj.is_active()

    is_active_display.boolean = True
    is_active_display.short_description = 'Active'

    def cancel_subscriptions(self, request, queryset):
        queryset.update(status='cancelled')
        self.message_user(request, f"{queryset.count()} subscriptions were cancelled.")

    cancel_subscriptions.short_description = "Cancel selected subscriptions"

    def extend_subscriptions(self, request, queryset):
        for subscription in queryset:
            subscription.end_date = subscription.end_date + timedelta(days=30)
            subscription.status = 'active'
            subscription.save()
        self.message_user(request, f"{queryset.count()} subscriptions were extended by 30 days.")

    extend_subscriptions.short_description = "Extend subscriptions by 30 days"
