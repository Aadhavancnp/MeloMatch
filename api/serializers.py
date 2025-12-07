"""
API Serializers for MeloMatch.

Provides serialization/deserialization for all models exposed via the REST API.
"""
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from music.models import Artist, Genre, Playlist, Track
from subscription.models import Subscription, SubscriptionPlan
from users.models import CustomUser, UserActivity


# =============================================================================
# USER SERIALIZERS
# =============================================================================

class UserSummarySerializer(serializers.ModelSerializer):
    """Minimal user info for nested relationships."""

    class Meta:
        model = CustomUser
        fields = ['id', 'username', 'profile_picture']
        read_only_fields = fields


class UserProfileSerializer(serializers.ModelSerializer):
    """Full user profile with computed fields."""

    full_name = serializers.ReadOnlyField()
    is_premium = serializers.ReadOnlyField()
    followers_count = serializers.SerializerMethodField()
    following_count = serializers.SerializerMethodField()

    class Meta:
        model = CustomUser
        fields = [
            'id', 'username', 'email', 'first_name', 'last_name', 'full_name',
            'profile_picture', 'bio', 'location', 'birth_date',
            'theme_preference', 'last_active', 'is_premium',
            'followers_count', 'following_count', 'date_joined',
        ]
        read_only_fields = ['id', 'email',
                            'date_joined', 'last_active', 'is_premium']

    @extend_schema_field(serializers.IntegerField())
    def get_followers_count(self, obj) -> int:
        return obj.followers.count()

    @extend_schema_field(serializers.IntegerField())
    def get_following_count(self, obj) -> int:
        return obj.following.count()


class UserUpdateSerializer(serializers.ModelSerializer):
    """Serializer for updating user profile."""

    class Meta:
        model = CustomUser
        fields = [
            'first_name', 'last_name', 'bio', 'location',
            'birth_date', 'theme_preference', 'profile_picture',
        ]

    def validate_profile_picture(self, value):
        if value:
            # 5MB limit
            if value.size > 5 * 1024 * 1024:
                raise serializers.ValidationError(
                    "Profile picture must be less than 5MB.")

            # Validate file type
            allowed_types = ['image/jpeg',
                             'image/png', 'image/gif', 'image/webp']
            if hasattr(value, 'content_type') and value.content_type not in allowed_types:
                raise serializers.ValidationError(
                    "Only JPEG, PNG, GIF, and WebP images are allowed.")

        return value


class UserActivitySerializer(serializers.ModelSerializer):
    """User activity log entries."""

    class Meta:
        model = UserActivity
        fields = ['id', 'activity_type', 'description', 'timestamp']
        read_only_fields = fields


# =============================================================================
# MUSIC SERIALIZERS
# =============================================================================

class GenreSerializer(serializers.ModelSerializer):
    """Genre with track count."""

    track_count = serializers.SerializerMethodField()

    class Meta:
        model = Genre
        fields = ['id', 'name', 'track_count']
        read_only_fields = fields

    @extend_schema_field(serializers.IntegerField())
    def get_track_count(self, obj) -> int:
        return obj.track_set.count()


class ArtistSummarySerializer(serializers.ModelSerializer):
    """Minimal artist info for nested relationships."""

    class Meta:
        model = Artist
        fields = ['id', 'name', 'spotify_id']
        read_only_fields = fields


class ArtistSerializer(serializers.ModelSerializer):
    """Full artist details with genres."""

    genres = GenreSerializer(many=True, read_only=True)
    track_count = serializers.SerializerMethodField()

    class Meta:
        model = Artist
        fields = ['id', 'name', 'spotify_id', 'genres', 'track_count']
        read_only_fields = fields

    @extend_schema_field(serializers.IntegerField())
    def get_track_count(self, obj) -> int:
        return obj.tracks.count()


class TrackSummarySerializer(serializers.ModelSerializer):
    """Minimal track info for listings."""

    artists = ArtistSummarySerializer(many=True, read_only=True)
    duration_seconds = serializers.SerializerMethodField()

    class Meta:
        model = Track
        fields = [
            'id', 'title', 'spotify_id', 'album', 'duration_seconds',
            'image_url', 'artists',
        ]
        read_only_fields = fields

    @extend_schema_field(serializers.IntegerField(allow_null=True))
    def get_duration_seconds(self, obj) -> int | None:
        if obj.duration:
            return int(obj.duration.total_seconds())
        return None


class TrackSerializer(serializers.ModelSerializer):
    """Full track details with audio features."""

    artists = ArtistSummarySerializer(many=True, read_only=True)
    genres = GenreSerializer(read_only=True)
    duration_seconds = serializers.SerializerMethodField()
    duration_formatted = serializers.SerializerMethodField()

    class Meta:
        model = Track
        fields = [
            'id', 'title', 'spotify_id', 'album', 'duration', 'duration_seconds',
            'duration_formatted', 'preview_url', 'image_url', 'popularity',
            'release_date', 'artists', 'genres', 'audio_features', 'price',
            'created_at',
        ]
        read_only_fields = fields

    @extend_schema_field(serializers.IntegerField(allow_null=True))
    def get_duration_seconds(self, obj) -> int | None:
        if obj.duration:
            return int(obj.duration.total_seconds())
        return None

    @extend_schema_field(serializers.CharField(allow_null=True))
    def get_duration_formatted(self, obj) -> str | None:
        if obj.duration:
            total_seconds = int(obj.duration.total_seconds())
            minutes, seconds = divmod(total_seconds, 60)
            return f"{minutes}:{seconds:02d}"
        return None


class PlaylistSummarySerializer(serializers.ModelSerializer):
    """Minimal playlist info for listings."""

    user = UserSummarySerializer(read_only=True)
    track_count = serializers.ReadOnlyField()

    class Meta:
        model = Playlist
        fields = [
            'id', 'name', 'spotify_id', 'image_url', 'is_public',
            'user', 'track_count', 'created_at',
        ]
        read_only_fields = fields


class PlaylistSerializer(serializers.ModelSerializer):
    """Full playlist details with tracks."""

    user = UserSummarySerializer(read_only=True)
    tracks = TrackSummarySerializer(many=True, read_only=True)
    track_count = serializers.ReadOnlyField()
    shared_with = UserSummarySerializer(many=True, read_only=True)
    total_duration = serializers.SerializerMethodField()

    class Meta:
        model = Playlist
        fields = [
            'id', 'name', 'spotify_id', 'description', 'image_url',
            'is_public', 'user', 'tracks', 'track_count', 'shared_with',
            'total_duration', 'created_at', 'updated_at',
        ]
        read_only_fields = ['id', 'spotify_id',
                            'user', 'created_at', 'updated_at']

    @extend_schema_field(serializers.IntegerField())
    def get_total_duration(self, obj) -> int:
        """Calculate total duration of all tracks in seconds."""
        total = 0
        for track in obj.tracks.all():
            if track.duration:
                total += int(track.duration.total_seconds())
        return total


class PlaylistCreateSerializer(serializers.ModelSerializer):
    """Serializer for creating/updating playlists."""

    class Meta:
        model = Playlist
        fields = ['name', 'description', 'is_public', 'image_url']

    def validate_name(self, value):
        if len(value.strip()) < 1:
            raise serializers.ValidationError("Playlist name cannot be empty.")
        if len(value) > 200:
            raise serializers.ValidationError(
                "Playlist name must be less than 200 characters.")
        return value.strip()


class PlaylistTrackActionSerializer(serializers.Serializer):
    """Serializer for adding/removing tracks from playlists."""

    track_id = serializers.IntegerField(required=False)
    spotify_id = serializers.CharField(max_length=100, required=False)

    def validate(self, data):
        if not data.get('track_id') and not data.get('spotify_id'):
            raise serializers.ValidationError(
                "Either 'track_id' or 'spotify_id' must be provided."
            )
        return data


# =============================================================================
# SUBSCRIPTION SERIALIZERS
# =============================================================================

class SubscriptionPlanSerializer(serializers.ModelSerializer):
    """Subscription plan details."""

    features_list = serializers.SerializerMethodField()
    price_formatted = serializers.SerializerMethodField()

    class Meta:
        model = SubscriptionPlan
        fields = [
            'id', 'name', 'price', 'price_formatted', 'duration_days',
            'description', 'features', 'features_list',
        ]
        read_only_fields = fields

    @extend_schema_field(serializers.ListField(child=serializers.CharField()))
    def get_features_list(self, obj) -> list[str]:
        """Parse features string into a list."""
        if obj.features:
            return [f.strip() for f in obj.features.split('\n') if f.strip()]
        return []

    @extend_schema_field(serializers.CharField())
    def get_price_formatted(self, obj) -> str:
        return f"${obj.price:.2f}"


class SubscriptionSerializer(serializers.ModelSerializer):
    """User subscription details."""

    plan = SubscriptionPlanSerializer(read_only=True)
    is_active = serializers.SerializerMethodField()
    days_remaining = serializers.SerializerMethodField()

    class Meta:
        model = Subscription
        fields = [
            'id', 'plan', 'start_date', 'end_date', 'status',
            'is_active', 'days_remaining',
        ]
        read_only_fields = fields

    @extend_schema_field(serializers.BooleanField())
    def get_is_active(self, obj) -> bool:
        return obj.is_active()

    @extend_schema_field(serializers.IntegerField())
    def get_days_remaining(self, obj) -> int:
        return obj.days_remaining()


# =============================================================================
# SEARCH SERIALIZERS
# =============================================================================

class SearchResultSerializer(serializers.Serializer):
    """Unified search results across tracks, artists, and playlists."""

    tracks = TrackSummarySerializer(many=True)
    artists = ArtistSerializer(many=True)
    playlists = PlaylistSummarySerializer(many=True)
    total_results = serializers.IntegerField()
    query = serializers.CharField()


class SpotifySearchSerializer(serializers.Serializer):
    """Serializer for external Spotify search results."""

    id = serializers.CharField()
    name = serializers.CharField()
    artists = serializers.ListField(child=serializers.DictField())
    album = serializers.DictField()
    preview_url = serializers.URLField(allow_null=True)
    external_url = serializers.URLField()
    popularity = serializers.IntegerField()
    duration_ms = serializers.IntegerField()


# =============================================================================
# STATISTICS SERIALIZERS
# =============================================================================

class UserStatsSerializer(serializers.Serializer):
    """User listening statistics."""

    total_playlists = serializers.IntegerField()
    total_tracks_saved = serializers.IntegerField()
    total_listening_time_minutes = serializers.IntegerField()
    favorite_genre = serializers.CharField(allow_null=True)
    top_artists = ArtistSummarySerializer(many=True)
    recent_activity = UserActivitySerializer(many=True)


class DashboardSerializer(serializers.Serializer):
    """Dashboard data aggregation."""

    user = UserProfileSerializer()
    subscription = SubscriptionSerializer()
    stats = UserStatsSerializer()
    recent_tracks = TrackSummarySerializer(many=True)
    recommended_tracks = TrackSummarySerializer(many=True)
