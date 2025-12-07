"""
API URL Configuration for MeloMatch.

Provides versioned API routes with OpenAPI documentation.
"""
from django.urls import include, path
from drf_spectacular.views import (
    SpectacularAPIView,
    SpectacularRedocView,
    SpectacularSwaggerView,
)
from rest_framework.routers import DefaultRouter

from .views import (
    ArtistViewSet,
    CurrentSubscriptionView,
    CurrentUserView,
    DashboardView,
    GenreListView,
    GlobalSearchView,
    PlaylistViewSet,
    SubscriptionPlanListView,
    TrackViewSet,
    UserActivityView,
    UserProfileView,
    UserStatsView,
)

# Create router for ViewSets
router = DefaultRouter()
router.register(r'tracks', TrackViewSet, basename='track')
router.register(r'artists', ArtistViewSet, basename='artist')
router.register(r'playlists', PlaylistViewSet, basename='playlist')

# API v1 URL patterns
v1_patterns = [
    # ViewSet routes
    path('', include(router.urls)),

    # Genre list
    path('genres/', GenreListView.as_view(), name='genre-list'),

    # User endpoints
    path('users/me/', CurrentUserView.as_view(), name='current-user'),
    path('users/me/activity/', UserActivityView.as_view(), name='user-activity'),
    path('users/me/stats/', UserStatsView.as_view(), name='user-stats'),
    path('users/<str:username>/', UserProfileView.as_view(), name='user-profile'),

    # Subscription endpoints
    path('subscriptions/plans/', SubscriptionPlanListView.as_view(),
         name='subscription-plans'),
    path('subscriptions/current/', CurrentSubscriptionView.as_view(),
         name='current-subscription'),

    # Search
    path('search/', GlobalSearchView.as_view(), name='global-search'),

    # Dashboard
    path('dashboard/', DashboardView.as_view(), name='dashboard'),
]

# Main API URL patterns
urlpatterns = [
    # API v1
    path('v1/', include((v1_patterns, 'v1'), namespace='v1')),

    # OpenAPI Schema
    path('schema/', SpectacularAPIView.as_view(), name='schema'),

    # Swagger UI
    path('docs/', SpectacularSwaggerView.as_view(url_name='api:schema'),
         name='swagger-ui'),

    # ReDoc
    path('redoc/', SpectacularRedocView.as_view(url_name='api:schema'), name='redoc'),
]
