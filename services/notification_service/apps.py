from django.apps import AppConfig

class NotificationServiceConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'services.notification_service'
    label = 'notification_service' # Optional: if name is long or conflicts
    verbose_name = 'Notification Service'
