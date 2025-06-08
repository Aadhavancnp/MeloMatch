from django.apps import AppConfig

class AnalyticsServiceConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'services.analytics_service'
    label = 'analytics_service' # Optional: if name is long or conflicts
    verbose_name = 'Analytics Service'
