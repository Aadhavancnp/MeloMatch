"""
API Views for MeloMatch.

Provides RESTful endpoints for tracks, playlists, users, and subscriptions.
"""
import logging
import uuid
from typing import Any

from asgiref.sync import sync_to_async
from django.db.models import Q
from django.shortcuts import get_object_or_404
from drf_spectacular.utils import (
    OpenApiParameter,
    OpenApiResponse,
    extend_schema,
    extend_schema_view,
)
from rest_framework import generics, permissions, status, viewsets
from rest_framework.decorators import action
from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response
from rest_framework.views import APIView

from music.models import Artist, Genre, Playlist, Track
from subscription.models import Subscription, SubscriptionPlan
from users.models import CustomUser, UserActivity

from .serializers import (
    ArtistSerializer,
    ArtistSummarySerializer,
    DashboardSerializer,
    GenreSerializer,
    PlaylistCreateSerializer,
    PlaylistSerializer,
    PlaylistSummarySerializer,
    PlaylistTrackActionSerializer,
    SearchResultSerializer,
    SubscriptionPlanSerializer,
    SubscriptionSerializer,
    TrackSerializer,
    TrackSummarySerializer,
    UserActivitySerializer,
    UserProfileSerializer,
    UserStatsSerializer,
    UserUpdateSerializer,
)

logger = logging.getLogger(__name__)


# =============================================================================
# CUSTOM PAGINATION
# =============================================================================

class StandardResultsPagination(PageNumberPagination):
    """Standard pagination for list endpoints."""
    page_size = 20
    page_size_query_param = 'page_size'
    max_page_size = 100


class LargeResultsPagination(PageNumberPagination):
    """Larger pagination for search results."""
    page_size = 50
    page_size_query_param = 'page_size'
    max_page_size = 200


# =============================================================================
# CUSTOM PERMISSIONS
# =============================================================================

class IsOwnerOrReadOnly(permissions.BasePermission):
    """Allow owners to edit, others can only read public resources."""

    def has_object_permission(self, request, view, obj):
        # Read permissions allowed for any request
        if request.method in permissions.SAFE_METHODS:
            # For playlists, check if public or owned by user
            if hasattr(obj, 'is_public'):
                return obj.is_public or obj.user == request.user
            return True

        # Write permissions only for owner
        if hasattr(obj, 'user'):
            return obj.user == request.user
        return False


class IsPremiumUser(permissions.BasePermission):
    """Require premium subscription for certain endpoints."""

    message = "This feature requires a premium subscription."

    def has_permission(self, request, view):
        return request.user.is_authenticated and request.user.is_premium


# =============================================================================
# TRACK VIEWS
# =============================================================================

@extend_schema_view(
    list=extend_schema(
        summary="List all tracks",
        description="Retrieve a paginated list of all tracks in the catalog.",
        tags=['Tracks'],
    ),
    retrieve=extend_schema(
        summary="Get track details",
        description="Retrieve detailed information about a specific track.",
        tags=['Tracks'],
    ),
)
class TrackViewSet(viewsets.ReadOnlyModelViewSet):
    """
    ViewSet for browsing and retrieving tracks.

    Provides list and detail endpoints for the track catalog.
    """
    queryset = Track.objects.select_related(
        'genres').prefetch_related('artists').all()
    pagination_class = StandardResultsPagination
    permission_classes = [permissions.IsAuthenticated]

    def get_serializer_class(self):
        if self.action == 'list':
            return TrackSummarySerializer
        return TrackSerializer

    @extend_schema(
        summary="Get popular tracks",
        description="Retrieve the most popular tracks based on Spotify popularity.",
        tags=['Tracks'],
        responses={200: TrackSummarySerializer(many=True)},
    )
    @action(detail=False, methods=['get'])
    def popular(self, request):
        """Get popular tracks sorted by popularity."""
        tracks = self.queryset.order_by('-popularity')[:50]
        serializer = TrackSummarySerializer(tracks, many=True)
        return Response(serializer.data)

    @extend_schema(
        summary="Get recent tracks",
        description="Retrieve recently added tracks.",
        tags=['Tracks'],
        responses={200: TrackSummarySerializer(many=True)},
    )
    @action(detail=False, methods=['get'])
    def recent(self, request):
        """Get recently added tracks."""
        tracks = self.queryset.order_by('-created_at')[:50]
        serializer = TrackSummarySerializer(tracks, many=True)
        return Response(serializer.data)

    @extend_schema(
        summary="Get tracks by genre",
        description="Retrieve tracks filtered by genre.",
        tags=['Tracks'],
        parameters=[
            OpenApiParameter(
                name='genre',
                type=str,
                location=OpenApiParameter.QUERY,
                description="Genre name to filter by",
                required=True,
            ),
        ],
        responses={200: TrackSummarySerializer(many=True)},
    )
    @action(detail=False, methods=['get'])
    def by_genre(self, request):
        """Get tracks by genre."""
        genre_name = request.query_params.get('genre')
        if not genre_name:
            return Response(
                {'error': 'Genre parameter is required'},
                status=status.HTTP_400_BAD_REQUEST
            )

        tracks = self.queryset.filter(genres__name__icontains=genre_name)
        page = self.paginate_queryset(tracks)

        if page is not None:
            serializer = TrackSummarySerializer(page, many=True)
            return self.get_paginated_response(serializer.data)

        serializer = TrackSummarySerializer(tracks, many=True)
        return Response(serializer.data)


# =============================================================================
# ARTIST VIEWS
# =============================================================================

@extend_schema_view(
    list=extend_schema(
        summary="List all artists",
        description="Retrieve a paginated list of all artists.",
        tags=['Tracks'],
    ),
    retrieve=extend_schema(
        summary="Get artist details",
        description="Retrieve detailed information about a specific artist.",
        tags=['Tracks'],
    ),
)
class ArtistViewSet(viewsets.ReadOnlyModelViewSet):
    """
    ViewSet for browsing and retrieving artists.
    """
    queryset = Artist.objects.prefetch_related('genres').all()
    serializer_class = ArtistSerializer
    pagination_class = StandardResultsPagination
    permission_classes = [permissions.IsAuthenticated]

    @extend_schema(
        summary="Get artist tracks",
        description="Retrieve all tracks by a specific artist.",
        tags=['Tracks'],
        responses={200: TrackSummarySerializer(many=True)},
    )
    @action(detail=True, methods=['get'])
    def tracks(self, request, pk=None):
        """Get tracks by this artist."""
        artist = self.get_object()
        tracks = artist.tracks.select_related(
            'genres').prefetch_related('artists').all()

        page = self.paginate_queryset(tracks)
        if page is not None:
            serializer = TrackSummarySerializer(page, many=True)
            return self.get_paginated_response(serializer.data)

        serializer = TrackSummarySerializer(tracks, many=True)
        return Response(serializer.data)


# =============================================================================
# GENRE VIEWS
# =============================================================================

@extend_schema(tags=['Tracks'])
class GenreListView(generics.ListAPIView):
    """List all available genres with track counts."""
    queryset = Genre.objects.all()
    serializer_class = GenreSerializer
    permission_classes = [permissions.IsAuthenticated]
    pagination_class = None  # Return all genres without pagination


# =============================================================================
# PLAYLIST VIEWS
# =============================================================================

@extend_schema_view(
    list=extend_schema(
        summary="List user playlists",
        description="Retrieve all playlists owned by or shared with the authenticated user.",
        tags=['Playlists'],
    ),
    retrieve=extend_schema(
        summary="Get playlist details",
        description="Retrieve detailed information about a playlist including all tracks.",
        tags=['Playlists'],
    ),
    create=extend_schema(
        summary="Create playlist",
        description="Create a new playlist for the authenticated user.",
        tags=['Playlists'],
    ),
    update=extend_schema(
        summary="Update playlist",
        description="Update playlist name, description, or visibility.",
        tags=['Playlists'],
    ),
    partial_update=extend_schema(
        summary="Partially update playlist",
        description="Partially update playlist fields.",
        tags=['Playlists'],
    ),
    destroy=extend_schema(
        summary="Delete playlist",
        description="Delete a playlist owned by the authenticated user.",
        tags=['Playlists'],
    ),
)
class PlaylistViewSet(viewsets.ModelViewSet):
    """
    ViewSet for managing user playlists.

    Provides CRUD operations for playlists and track management.
    """
    pagination_class = StandardResultsPagination
    permission_classes = [permissions.IsAuthenticated, IsOwnerOrReadOnly]

    def get_queryset(self):
        """Return playlists owned by or shared with the user."""
        user = self.request.user
        return Playlist.objects.filter(
            Q(user=user) | Q(shared_with=user) | Q(is_public=True)
        ).select_related('user').prefetch_related('tracks', 'shared_with').distinct()

    def get_serializer_class(self):
        if self.action in ['create', 'update', 'partial_update']:
            return PlaylistCreateSerializer
        if self.action == 'list':
            return PlaylistSummarySerializer
        return PlaylistSerializer

    def perform_create(self, serializer):
        """Create playlist with generated spotify_id."""
        playlist = serializer.save(
            user=self.request.user,
            spotify_id=f"local_{uuid.uuid4().hex[:22]}"
        )

        # Log activity
        UserActivity.objects.create(
            user=self.request.user,
            activity_type='playlist_created',
            description=f"Created playlist: {playlist.name}"
        )

        logger.info(
            f"User {self.request.user.username} created playlist: {playlist.name}")

    def perform_destroy(self, instance):
        """Log playlist deletion."""
        playlist_name = instance.name
        instance.delete()

        UserActivity.objects.create(
            user=self.request.user,
            activity_type='playlist_deleted',
            description=f"Deleted playlist: {playlist_name}"
        )

        logger.info(
            f"User {self.request.user.username} deleted playlist: {playlist_name}")

    @extend_schema(
        summary="Add track to playlist",
        description="Add a track to the playlist by track ID or Spotify ID.",
        tags=['Playlists'],
        request=PlaylistTrackActionSerializer,
        responses={
            200: OpenApiResponse(description="Track added successfully"),
            400: OpenApiResponse(description="Invalid request"),
            404: OpenApiResponse(description="Track not found"),
        },
    )
    @action(detail=True, methods=['post'])
    def add_track(self, request, pk=None):
        """Add a track to the playlist."""
        playlist = self.get_object()
        serializer = PlaylistTrackActionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        # Find the track
        track_id = serializer.validated_data.get('track_id')
        spotify_id = serializer.validated_data.get('spotify_id')

        try:
            if track_id:
                track = Track.objects.get(id=track_id)
            else:
                track = Track.objects.get(spotify_id=spotify_id)
        except Track.DoesNotExist:
            return Response(
                {'error': 'Track not found'},
                status=status.HTTP_404_NOT_FOUND
            )

        # Check if track already in playlist
        if playlist.tracks.filter(id=track.id).exists():
            return Response(
                {'error': 'Track already in playlist'},
                status=status.HTTP_400_BAD_REQUEST
            )

        playlist.tracks.add(track)

        UserActivity.objects.create(
            user=request.user,
            activity_type='track_added',
            description=f"Added '{track.title}' to playlist '{playlist.name}'"
        )

        return Response({'message': f"Track '{track.title}' added to playlist"})

    @extend_schema(
        summary="Remove track from playlist",
        description="Remove a track from the playlist.",
        tags=['Playlists'],
        request=PlaylistTrackActionSerializer,
        responses={
            200: OpenApiResponse(description="Track removed successfully"),
            404: OpenApiResponse(description="Track not found in playlist"),
        },
    )
    @action(detail=True, methods=['post'])
    def remove_track(self, request, pk=None):
        """Remove a track from the playlist."""
        playlist = self.get_object()
        serializer = PlaylistTrackActionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        track_id = serializer.validated_data.get('track_id')
        spotify_id = serializer.validated_data.get('spotify_id')

        try:
            if track_id:
                track = playlist.tracks.get(id=track_id)
            else:
                track = playlist.tracks.get(spotify_id=spotify_id)
        except Track.DoesNotExist:
            return Response(
                {'error': 'Track not found in playlist'},
                status=status.HTTP_404_NOT_FOUND
            )

        playlist.tracks.remove(track)

        UserActivity.objects.create(
            user=request.user,
            activity_type='track_removed',
            description=f"Removed '{track.title}' from playlist '{playlist.name}'"
        )

        return Response({'message': f"Track '{track.title}' removed from playlist"})

    @extend_schema(
        summary="Get user's own playlists",
        description="Get only playlists created by the authenticated user.",
        tags=['Playlists'],
        responses={200: PlaylistSummarySerializer(many=True)},
    )
    @action(detail=False, methods=['get'])
    def my_playlists(self, request):
        """Get only the user's own playlists."""
        playlists = Playlist.objects.filter(
            user=request.user).select_related('user')

        page = self.paginate_queryset(playlists)
        if page is not None:
            serializer = PlaylistSummarySerializer(page, many=True)
            return self.get_paginated_response(serializer.data)

        serializer = PlaylistSummarySerializer(playlists, many=True)
        return Response(serializer.data)


# =============================================================================
# USER VIEWS
# =============================================================================

@extend_schema(tags=['Users'])
class CurrentUserView(APIView):
    """Get or update the current authenticated user's profile."""
    permission_classes = [permissions.IsAuthenticated]

    @extend_schema(
        summary="Get current user profile",
        description="Retrieve the authenticated user's profile information.",
        responses={200: UserProfileSerializer},
    )
    def get(self, request):
        """Get current user profile."""
        serializer = UserProfileSerializer(request.user)
        return Response(serializer.data)

    @extend_schema(
        summary="Update current user profile",
        description="Update the authenticated user's profile information.",
        request=UserUpdateSerializer,
        responses={200: UserProfileSerializer},
    )
    def patch(self, request):
        """Update current user profile."""
        serializer = UserUpdateSerializer(
            request.user, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()

        # Return full profile
        return Response(UserProfileSerializer(request.user).data)


@extend_schema(tags=['Users'])
class UserProfileView(generics.RetrieveAPIView):
    """View another user's public profile."""
    queryset = CustomUser.objects.all()
    serializer_class = UserProfileSerializer
    permission_classes = [permissions.IsAuthenticated]
    lookup_field = 'username'


@extend_schema(tags=['Users'])
class UserActivityView(generics.ListAPIView):
    """Get the current user's activity history."""
    serializer_class = UserActivitySerializer
    permission_classes = [permissions.IsAuthenticated]
    pagination_class = StandardResultsPagination

    def get_queryset(self):
        return UserActivity.objects.filter(user=self.request.user).order_by('-timestamp')


@extend_schema(tags=['Users'])
class UserStatsView(APIView):
    """Get user listening statistics."""
    permission_classes = [permissions.IsAuthenticated]

    @extend_schema(
        summary="Get user statistics",
        description="Retrieve listening statistics for the authenticated user.",
        responses={200: UserStatsSerializer},
    )
    def get(self, request):
        """Calculate and return user statistics."""
        user = request.user

        # Calculate stats
        playlists = Playlist.objects.filter(user=user)
        total_playlists = playlists.count()

        # Get unique tracks across all playlists
        track_ids = set()
        for playlist in playlists.prefetch_related('tracks'):
            track_ids.update(playlist.tracks.values_list('id', flat=True))

        # Recent activity
        recent_activity = UserActivity.objects.filter(
            user=user).order_by('-timestamp')[:10]

        # Top artists from user's playlists
        from django.db.models import Count
        top_artists = Artist.objects.filter(
            tracks__playlists__user=user
        ).annotate(
            track_count=Count('tracks')
        ).order_by('-track_count')[:5]

        # Favorite genre
        favorite_genre = Genre.objects.filter(
            track__playlists__user=user
        ).annotate(
            count=Count('track')
        ).order_by('-count').first()

        stats_data = {
            'total_playlists': total_playlists,
            'total_tracks_saved': len(track_ids),
            'total_listening_time_minutes': 0,  # Would need actual listening data
            'favorite_genre': favorite_genre.name if favorite_genre else None,
            'top_artists': ArtistSummarySerializer(top_artists, many=True).data,
            'recent_activity': UserActivitySerializer(recent_activity, many=True).data,
        }

        return Response(stats_data)


# =============================================================================
# SUBSCRIPTION VIEWS
# =============================================================================

@extend_schema(tags=['Subscriptions'])
class SubscriptionPlanListView(generics.ListAPIView):
    """List all available subscription plans."""
    queryset = SubscriptionPlan.objects.all()
    serializer_class = SubscriptionPlanSerializer
    permission_classes = [permissions.AllowAny]  # Allow anyone to view plans
    pagination_class = None


@extend_schema(tags=['Subscriptions'])
class CurrentSubscriptionView(APIView):
    """Get the current user's subscription."""
    permission_classes = [permissions.IsAuthenticated]

    @extend_schema(
        summary="Get current subscription",
        description="Retrieve the authenticated user's subscription details.",
        responses={200: SubscriptionSerializer},
    )
    def get(self, request):
        """Get current user's subscription."""
        try:
            subscription = Subscription.objects.select_related(
                'plan').get(user=request.user)
            serializer = SubscriptionSerializer(subscription)
            return Response(serializer.data)
        except Subscription.DoesNotExist:
            return Response(
                {'error': 'No subscription found'},
                status=status.HTTP_404_NOT_FOUND
            )


# =============================================================================
# SEARCH VIEWS
# =============================================================================

@extend_schema(tags=['Search'])
class GlobalSearchView(APIView):
    """
    Global search across tracks, artists, and playlists.
    """
    permission_classes = [permissions.IsAuthenticated]

    @extend_schema(
        summary="Global search",
        description="Search across tracks, artists, and playlists.",
        parameters=[
            OpenApiParameter(
                name='q',
                type=str,
                location=OpenApiParameter.QUERY,
                description="Search query",
                required=True,
            ),
            OpenApiParameter(
                name='type',
                type=str,
                location=OpenApiParameter.QUERY,
                description="Filter by type: track, artist, playlist, or all (default)",
                required=False,
            ),
            OpenApiParameter(
                name='limit',
                type=int,
                location=OpenApiParameter.QUERY,
                description="Maximum results per type (default: 10)",
                required=False,
            ),
        ],
        responses={200: SearchResultSerializer},
    )
    def get(self, request):
        """Perform global search."""
        query = request.query_params.get('q', '').strip()
        search_type = request.query_params.get('type', 'all').lower()
        limit = min(int(request.query_params.get('limit', 10)), 50)

        if not query:
            return Response(
                {'error': 'Search query is required'},
                status=status.HTTP_400_BAD_REQUEST
            )

        results = {
            'tracks': [],
            'artists': [],
            'playlists': [],
            'total_results': 0,
            'query': query,
        }

        if search_type in ['all', 'track']:
            tracks = Track.objects.filter(
                Q(title__icontains=query) |
                Q(album__icontains=query) |
                Q(artists__name__icontains=query)
            ).select_related('genres').prefetch_related('artists').distinct()[:limit]

            results['tracks'] = TrackSummarySerializer(tracks, many=True).data
            results['total_results'] += len(results['tracks'])

        if search_type in ['all', 'artist']:
            artists = Artist.objects.filter(
                name__icontains=query
            ).prefetch_related('genres')[:limit]

            results['artists'] = ArtistSerializer(artists, many=True).data
            results['total_results'] += len(results['artists'])

        if search_type in ['all', 'playlist']:
            playlists = Playlist.objects.filter(
                Q(name__icontains=query) |
                Q(description__icontains=query),
                Q(is_public=True) | Q(user=request.user)
            ).select_related('user').distinct()[:limit]

            results['playlists'] = PlaylistSummarySerializer(
                playlists, many=True).data
            results['total_results'] += len(results['playlists'])

        return Response(results)


# =============================================================================
# DASHBOARD VIEW
# =============================================================================

@extend_schema(tags=['Users'])
class DashboardView(APIView):
    """
    Aggregated dashboard data for the authenticated user.
    """
    permission_classes = [permissions.IsAuthenticated]

    @extend_schema(
        summary="Get dashboard data",
        description="Retrieve aggregated dashboard data including user profile, subscription, stats, and recommendations.",
        responses={200: DashboardSerializer},
    )
    def get(self, request):
        """Get dashboard data."""
        user = request.user

        # User profile
        user_data = UserProfileSerializer(user).data

        # Subscription
        subscription_data = None
        try:
            subscription = Subscription.objects.select_related(
                'plan').get(user=user)
            subscription_data = SubscriptionSerializer(subscription).data
        except Subscription.DoesNotExist:
            pass

        # Recent tracks from user's playlists
        recent_tracks = Track.objects.filter(
            playlists__user=user
        ).select_related('genres').prefetch_related('artists').order_by('-created_at')[:10]

        # Stats (simplified)
        stats_data = {
            'total_playlists': Playlist.objects.filter(user=user).count(),
            'total_tracks_saved': Track.objects.filter(playlists__user=user).distinct().count(),
            'total_listening_time_minutes': 0,
            'favorite_genre': None,
            'top_artists': [],
            'recent_activity': UserActivitySerializer(
                UserActivity.objects.filter(
                    user=user).order_by('-timestamp')[:5],
                many=True
            ).data,
        }

        return Response({
            'user': user_data,
            'subscription': subscription_data,
            'stats': stats_data,
            'recent_tracks': TrackSummarySerializer(recent_tracks, many=True).data,
            'recommended_tracks': [],  # Would integrate with recommendation service
        })
