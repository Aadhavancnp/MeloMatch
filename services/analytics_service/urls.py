from django.urls import path
from . import views

app_name = 'analytics_api'

urlpatterns = [
    path('listening-time-trend/', views.api_listening_time_trend, name='listening_time_trend'),
    path('mood-distribution/', views.api_mood_distribution, name='mood_distribution'),
    path('genre-distribution/', views.api_genre_distribution, name='genre_distribution'),
    path('discovery-insights/', views.api_music_discovery_insights, name='discovery_insights'),
]
