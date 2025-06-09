from django.apps import AppConfig

class RecommendationServiceConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'services.recommendation_service'
    label = 'recommendation_service'
    verbose_name = 'Recommendation Service'
