"""
Comprehensive API tests for MeloMatch.

Tests all REST API endpoints with proper authentication and edge cases.
"""
from datetime import timedelta
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient, APITestCase

from music.models import Artist, Genre, Playlist, Track
from subscription.models import Subscription, SubscriptionPlan
from users.models import CustomUser, UserActivity


class APITestBase(APITestCase):
    """Base class for API tests with common setup."""

    @classmethod
    def setUpTestData(cls):
        """Set up test data once for all tests in the class."""
        # Create subscription plans
        cls.free_plan = SubscriptionPlan.objects.create(
            name='Free',
            price=Decimal('0.00'),
            duration_days=36500,
            description='Free tier',
            features='Basic features\nLimited playlists'
        )
        cls.premium_plan = SubscriptionPlan.objects.create(
            name='Premium',
            price=Decimal('9.99'),
            duration_days=30,
            description='Premium tier',
            features='All features\nUnlimited playlists\nHigh quality audio',
            stripe_price_id='price_premium_test'
        )

        # Create genres
        cls.genre_pop = Genre.objects.create(name='Pop')
        cls.genre_rock = Genre.objects.create(name='Rock')
        cls.genre_jazz = Genre.objects.create(name='Jazz')

        # Create artists
        cls.artist1 = Artist.objects.create(
            name='Test Artist 1', spotify_id='artist1_spotify')
        cls.artist1.genres.add(cls.genre_pop)
        cls.artist2 = Artist.objects.create(
            name='Test Artist 2', spotify_id='artist2_spotify')
        cls.artist2.genres.add(cls.genre_rock)

        # Create tracks
        cls.track1 = Track.objects.create(
            title='Track One',
            spotify_id='track1_spotify',
            album='Album One',
            duration=timedelta(minutes=3, seconds=30),
            popularity=85,
            release_date=timezone.now().date(),
            genres=cls.genre_pop,
            price=Decimal('0.99')
        )
        cls.track1.artists.add(cls.artist1)

        cls.track2 = Track.objects.create(
            title='Track Two',
            spotify_id='track2_spotify',
            album='Album Two',
            duration=timedelta(minutes=4, seconds=15),
            popularity=72,
            genres=cls.genre_rock,
            price=Decimal('1.29')
        )
        cls.track2.artists.add(cls.artist2)

        cls.track3 = Track.objects.create(
            title='Jazz Track',
            spotify_id='track3_spotify',
            album='Jazz Album',
            duration=timedelta(minutes=5),
            popularity=60,
            genres=cls.genre_jazz
        )

    def setUp(self):
        """Set up test client and users for each test."""
        # Create test users
        self.user = CustomUser.objects.create_user(
            username='testuser',
            email='test@example.com',
            password='testpass123'
        )
        self.user2 = CustomUser.objects.create_user(
            username='testuser2',
            email='test2@example.com',
            password='testpass123'
        )

        # Create subscription for user
        Subscription.objects.filter(
            user=self.user).delete()  # Remove auto-created
        self.subscription = Subscription.objects.create(
            user=self.user,
            plan=self.free_plan,
            end_date=timezone.now() + timedelta(days=365)
        )

        # Create API token
        self.token = Token.objects.create(user=self.user)

        # Setup API client
        self.client = APIClient()
        self.client.credentials(HTTP_AUTHORIZATION=f'Token {self.token.key}')

        # Create playlists
        self.playlist = Playlist.objects.create(
            user=self.user,
            name='My Playlist',
            spotify_id='playlist_local_123',
            description='Test playlist',
            is_public=True
        )
        self.playlist.tracks.add(self.track1)

        self.private_playlist = Playlist.objects.create(
            user=self.user,
            name='Private Playlist',
            spotify_id='playlist_private_123',
            is_public=False
        )


# =============================================================================
# TRACK API TESTS
# =============================================================================

class TrackAPITests(APITestBase):
    """Tests for Track API endpoints."""

    def test_list_tracks(self):
        """Test listing all tracks."""
        url = reverse('api:v1:track-list')
        response = self.client.get(url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn('results', response.data)
        self.assertEqual(len(response.data['results']), 3)

    def test_list_tracks_unauthenticated(self):
        """Test that unauthenticated requests are rejected."""
        self.client.credentials()  # Remove auth
        url = reverse('api:v1:track-list')
        response = self.client.get(url)

        # DRF returns 403 with SessionAuthentication, 401 with TokenAuthentication only
        self.assertIn(response.status_code, [
                      status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN])

    def test_retrieve_track(self):
        """Test retrieving a specific track."""
        url = reverse('api:v1:track-detail', kwargs={'pk': self.track1.pk})
        response = self.client.get(url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['title'], 'Track One')
        self.assertEqual(response.data['album'], 'Album One')
        self.assertIn('duration_seconds', response.data)
        # 3:30 = 210 seconds
        self.assertEqual(response.data['duration_seconds'], 210)

    def test_popular_tracks(self):
        """Test getting popular tracks."""
        url = reverse('api:v1:track-popular')
        response = self.client.get(url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        # Should be sorted by popularity descending
        self.assertEqual(response.data[0]['title'],
                         'Track One')  # popularity 85

    def test_recent_tracks(self):
        """Test getting recently added tracks."""
        url = reverse('api:v1:track-recent')
        response = self.client.get(url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(len(response.data) > 0)

    def test_tracks_by_genre(self):
        """Test filtering tracks by genre."""
        url = reverse('api:v1:track-by-genre')
        response = self.client.get(url, {'genre': 'Pop'})

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn('results', response.data)
        # Only Track One has Pop genre
        self.assertEqual(len(response.data['results']), 1)
        self.assertEqual(response.data['results'][0]['title'], 'Track One')

    def test_tracks_by_genre_missing_param(self):
        """Test that genre parameter is required."""
        url = reverse('api:v1:track-by-genre')
        response = self.client.get(url)

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)


# =============================================================================
# ARTIST API TESTS
# =============================================================================

class ArtistAPITests(APITestBase):
    """Tests for Artist API endpoints."""

    def test_list_artists(self):
        """Test listing all artists."""
        url = reverse('api:v1:artist-list')
        response = self.client.get(url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn('results', response.data)
        self.assertEqual(len(response.data['results']), 2)

    def test_retrieve_artist(self):
        """Test retrieving a specific artist."""
        url = reverse('api:v1:artist-detail', kwargs={'pk': self.artist1.pk})
        response = self.client.get(url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['name'], 'Test Artist 1')
        self.assertEqual(response.data['spotify_id'], 'artist1_spotify')
        self.assertIn('genres', response.data)

    def test_artist_tracks(self):
        """Test getting tracks by artist."""
        url = reverse('api:v1:artist-tracks', kwargs={'pk': self.artist1.pk})
        response = self.client.get(url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        # Artist 1 has Track One
        self.assertIn('results', response.data)


# =============================================================================
# GENRE API TESTS
# =============================================================================

class GenreAPITests(APITestBase):
    """Tests for Genre API endpoints."""

    def test_list_genres(self):
        """Test listing all genres."""
        url = reverse('api:v1:genre-list')
        response = self.client.get(url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data), 3)  # Pop, Rock, Jazz

        # Should have track counts
        genre_names = [g['name'] for g in response.data]
        self.assertIn('Pop', genre_names)
        self.assertIn('Rock', genre_names)


# =============================================================================
# PLAYLIST API TESTS
# =============================================================================

class PlaylistAPITests(APITestBase):
    """Tests for Playlist API endpoints."""

    def test_list_playlists(self):
        """Test listing user's playlists."""
        url = reverse('api:v1:playlist-list')
        response = self.client.get(url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn('results', response.data)
        # User has 2 playlists
        self.assertEqual(len(response.data['results']), 2)

    def test_retrieve_playlist(self):
        """Test retrieving a specific playlist."""
        url = reverse('api:v1:playlist-detail',
                      kwargs={'pk': self.playlist.pk})
        response = self.client.get(url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['name'], 'My Playlist')
        self.assertIn('tracks', response.data)
        self.assertEqual(len(response.data['tracks']), 1)

    def test_create_playlist(self):
        """Test creating a new playlist."""
        url = reverse('api:v1:playlist-list')
        data = {
            'name': 'New Playlist',
            'description': 'A new test playlist',
            'is_public': True
        }
        response = self.client.post(url, data)

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertTrue(Playlist.objects.filter(
            name='New Playlist', user=self.user).exists())

        # Check activity was logged
        self.assertTrue(
            UserActivity.objects.filter(
                user=self.user,
                activity_type='playlist_created'
            ).exists()
        )

    def test_update_playlist(self):
        """Test updating a playlist."""
        url = reverse('api:v1:playlist-detail',
                      kwargs={'pk': self.playlist.pk})
        data = {'name': 'Updated Playlist Name'}
        response = self.client.patch(url, data)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.playlist.refresh_from_db()
        self.assertEqual(self.playlist.name, 'Updated Playlist Name')

    def test_delete_playlist(self):
        """Test deleting a playlist."""
        url = reverse('api:v1:playlist-detail',
                      kwargs={'pk': self.playlist.pk})
        response = self.client.delete(url)

        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(Playlist.objects.filter(pk=self.playlist.pk).exists())

    def test_add_track_to_playlist(self):
        """Test adding a track to a playlist."""
        url = reverse('api:v1:playlist-add-track',
                      kwargs={'pk': self.playlist.pk})
        data = {'track_id': self.track2.pk}
        response = self.client.post(url, data)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(self.playlist.tracks.filter(
            pk=self.track2.pk).exists())

    def test_add_track_duplicate(self):
        """Test adding a duplicate track fails."""
        url = reverse('api:v1:playlist-add-track',
                      kwargs={'pk': self.playlist.pk})
        data = {'track_id': self.track1.pk}  # Already in playlist
        response = self.client.post(url, data)

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_remove_track_from_playlist(self):
        """Test removing a track from a playlist."""
        url = reverse('api:v1:playlist-remove-track',
                      kwargs={'pk': self.playlist.pk})
        data = {'track_id': self.track1.pk}
        response = self.client.post(url, data)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertFalse(self.playlist.tracks.filter(
            pk=self.track1.pk).exists())

    def test_my_playlists(self):
        """Test getting only user's own playlists."""
        url = reverse('api:v1:playlist-my-playlists')
        response = self.client.get(url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn('results', response.data)
        # All playlists should belong to current user
        for playlist in response.data['results']:
            self.assertEqual(playlist['user']['username'], self.user.username)

    def test_cannot_edit_other_user_playlist(self):
        """Test that users cannot edit playlists they don't own."""
        # Create playlist for user2
        other_playlist = Playlist.objects.create(
            user=self.user2,
            name='Other User Playlist',
            spotify_id='other_playlist_id',
            is_public=True
        )

        url = reverse('api:v1:playlist-detail',
                      kwargs={'pk': other_playlist.pk})
        data = {'name': 'Hacked Name'}
        response = self.client.patch(url, data)

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)


# =============================================================================
# USER API TESTS
# =============================================================================

class UserAPITests(APITestBase):
    """Tests for User API endpoints."""

    def test_get_current_user(self):
        """Test getting current user profile."""
        url = reverse('api:v1:current-user')
        response = self.client.get(url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['username'], 'testuser')
        self.assertEqual(response.data['email'], 'test@example.com')
        self.assertIn('is_premium', response.data)
        self.assertFalse(response.data['is_premium'])  # Free plan

    def test_update_current_user(self):
        """Test updating current user profile."""
        url = reverse('api:v1:current-user')
        data = {
            'first_name': 'John',
            'last_name': 'Doe',
            'bio': 'Music lover'
        }
        response = self.client.patch(url, data)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.user.refresh_from_db()
        self.assertEqual(self.user.first_name, 'John')
        self.assertEqual(self.user.bio, 'Music lover')

    def test_get_user_profile(self):
        """Test getting another user's profile."""
        url = reverse('api:v1:user-profile',
                      kwargs={'username': self.user2.username})
        response = self.client.get(url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['username'], 'testuser2')

    def test_get_user_activity(self):
        """Test getting user activity."""
        # Create some activity
        UserActivity.objects.create(
            user=self.user,
            activity_type='test',
            description='Test activity'
        )

        url = reverse('api:v1:user-activity')
        response = self.client.get(url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn('results', response.data)
        self.assertTrue(len(response.data['results']) > 0)

    def test_get_user_stats(self):
        """Test getting user statistics."""
        url = reverse('api:v1:user-stats')
        response = self.client.get(url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn('total_playlists', response.data)
        self.assertIn('total_tracks_saved', response.data)


# =============================================================================
# SUBSCRIPTION API TESTS
# =============================================================================

class SubscriptionAPITests(APITestBase):
    """Tests for Subscription API endpoints."""

    def test_list_subscription_plans(self):
        """Test listing subscription plans (public endpoint)."""
        self.client.credentials()  # Remove auth - should still work
        url = reverse('api:v1:subscription-plans')
        response = self.client.get(url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data), 2)  # Free and Premium

        plan_names = [p['name'] for p in response.data]
        self.assertIn('Free', plan_names)
        self.assertIn('Premium', plan_names)

    def test_get_current_subscription(self):
        """Test getting current user's subscription."""
        url = reverse('api:v1:current-subscription')
        response = self.client.get(url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['plan']['name'], 'Free')
        self.assertIn('is_active', response.data)
        self.assertIn('days_remaining', response.data)


# =============================================================================
# SEARCH API TESTS
# =============================================================================

class SearchAPITests(APITestBase):
    """Tests for Search API endpoints."""

    def test_global_search(self):
        """Test global search across all types."""
        url = reverse('api:v1:global-search')
        response = self.client.get(url, {'q': 'Track'})

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn('tracks', response.data)
        self.assertIn('artists', response.data)
        self.assertIn('playlists', response.data)
        self.assertIn('total_results', response.data)
        self.assertTrue(len(response.data['tracks']) > 0)

    def test_search_tracks_only(self):
        """Test searching only tracks."""
        url = reverse('api:v1:global-search')
        response = self.client.get(url, {'q': 'Jazz', 'type': 'track'})

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data['artists']), 0)
        self.assertTrue(len(response.data['tracks']) > 0)

    def test_search_missing_query(self):
        """Test search without query returns error."""
        url = reverse('api:v1:global-search')
        response = self.client.get(url)

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_search_artist(self):
        """Test searching for artists."""
        url = reverse('api:v1:global-search')
        response = self.client.get(url, {'q': 'Artist 1', 'type': 'artist'})

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(len(response.data['artists']) > 0)


# =============================================================================
# DASHBOARD API TESTS
# =============================================================================

class DashboardAPITests(APITestBase):
    """Tests for Dashboard API endpoint."""

    def test_get_dashboard(self):
        """Test getting dashboard data."""
        url = reverse('api:v1:dashboard')
        response = self.client.get(url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn('user', response.data)
        self.assertIn('subscription', response.data)
        self.assertIn('stats', response.data)
        self.assertIn('recent_tracks', response.data)

        # Verify user data
        self.assertEqual(response.data['user']['username'], 'testuser')


# =============================================================================
# AUTHENTICATION TESTS
# =============================================================================

class AuthenticationTests(APITestBase):
    """Tests for API authentication."""

    def test_token_authentication(self):
        """Test that token authentication works."""
        url = reverse('api:v1:current-user')
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_session_authentication(self):
        """Test that session authentication works."""
        client = APIClient()
        client.login(username='testuser', password='testpass123')

        url = reverse('api:v1:current-user')
        response = client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_invalid_token(self):
        """Test that invalid tokens are rejected."""
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION='Token invalidtoken123')

        url = reverse('api:v1:current-user')
        response = client.get(url)
        # DRF may return 401 or 403 depending on authentication backend
        self.assertIn(response.status_code, [
                      status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN])


# =============================================================================
# PAGINATION TESTS
# =============================================================================

class PaginationTests(APITestBase):
    """Tests for API pagination."""

    def test_pagination_structure(self):
        """Test that paginated responses have correct structure."""
        url = reverse('api:v1:track-list')
        response = self.client.get(url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn('count', response.data)
        self.assertIn('next', response.data)
        self.assertIn('previous', response.data)
        self.assertIn('results', response.data)

    def test_page_size_parameter(self):
        """Test that page_size parameter works."""
        url = reverse('api:v1:track-list')
        response = self.client.get(url, {'page_size': 1})

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data['results']), 1)


# =============================================================================
# ERROR HANDLING TESTS
# =============================================================================

class ErrorHandlingTests(APITestBase):
    """Tests for API error handling."""

    def test_not_found(self):
        """Test 404 response for non-existent resource."""
        url = reverse('api:v1:track-detail', kwargs={'pk': 99999})
        response = self.client.get(url)

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_validation_error(self):
        """Test validation error response."""
        url = reverse('api:v1:playlist-list')
        data = {'name': ''}  # Empty name should fail
        response = self.client.post(url, data)

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
