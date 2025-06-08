from django.apps import AppConfig

class RecommendationServiceConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'services.recommendation_service'
    label = 'recommendation_service' # Optional: if you want a shorter label for migrations etc.
