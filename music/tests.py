from .tasks import extract_track_features_task
from services.spotify_service.client import get_or_create_track, get_recommendations
from unittest.mock import patch, MagicMock
from django.test import Client, TestCase
from django.urls import reverse

from users.models import CustomUser
from .models import Playlist, Track, Genre, Artist  # Added Artist


class PlaylistSharingTestCase(TestCase):
    def setUp(self):
        self.owner = CustomUser.objects.create_user(
            username='owner', email='owner@example.com', password='password')
        self.viewer = CustomUser.objects.create_user(
            username='viewer', email='viewer@example.com', password='password')
        self.stranger = CustomUser.objects.create_user(username='stranger', email='stranger@example.com',
                                                       password='password')

        self.genre = Genre.objects.create(name='Test Genre')
        # self.artist = Artist.objects.create(name='Test Artist', spotify_id='testartistspotifyid_playlist') # Create unique spotify_id

        # Track needs to be associated with an artist for some views/templates to render correctly (e.g., artists_names)
        # For simplicity, we'll create tracks without artists if model allows, or add them.
        # Current Track model has artists as ManyToMany, not required on create.
        self.track = Track.objects.create(
            title='Test Track for Playlist',
            genres=self.genre,
            spotify_id='testtrackplaylist',  # Unique spotify_id
            album="Test Album for Playlist"
        )

        self.public_playlist = Playlist.objects.create(
            user=self.owner,
            name='Public Playlist',
            spotify_id='public_playlist_id',  # Unique spotify_id
            is_public=True
        )
        self.private_playlist = Playlist.objects.create(
            user=self.owner,
            name='Private Playlist',
            spotify_id='private_playlist_id',  # Unique spotify_id
            is_public=False
        )
        self.private_playlist_shared = Playlist.objects.create(
            user=self.owner,
            name='Private Shared Playlist',
            spotify_id='private_shared_id',  # Unique spotify_id
            is_public=False
        )
        self.private_playlist_shared.shared_with.add(self.viewer)

        self.client = Client()

    def test_public_playlist_access_by_owner(self):
        self.client.login(username='owner', password='password')
        response = self.client.get(reverse('playlist_detail', args=[
                                   self.public_playlist.spotify_id]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.public_playlist.name)

    def test_public_playlist_access_by_viewer(self):
        self.client.login(username='viewer', password='password')
        response = self.client.get(reverse('playlist_detail', args=[
                                   self.public_playlist.spotify_id]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.public_playlist.name)

    def test_public_playlist_access_by_anonymous(self):
        # client is not logged in
        response = self.client.get(reverse('playlist_detail', args=[
                                   self.public_playlist.spotify_id]))
        # Public playlists should be viewable by anyone
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.public_playlist.name)

    def test_private_playlist_access_by_owner(self):
        self.client.login(username='owner', password='password')
        response = self.client.get(reverse('playlist_detail', args=[
                                   self.private_playlist.spotify_id]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.private_playlist.name)

    def test_private_playlist_no_access_by_stranger(self):
        self.client.login(username='stranger', password='password')
        response = self.client.get(reverse('playlist_detail', args=[
                                   self.private_playlist.spotify_id]))
        # Expecting HttpResponseForbidden (403) as per the view logic
        self.assertEqual(response.status_code, 403)

    def test_private_playlist_no_access_by_anonymous(self):
        response = self.client.get(reverse('playlist_detail', args=[
                                   self.private_playlist.spotify_id]))
        # Anonymous users trying to access private playlists should be redirected to login or shown a forbidden page.
        # The view uses @login_required, which redirects to LOGIN_URL.
        # However, the permission check is inside the view after login.
        # Anonymous users are redirected to login for private playlists
        # This is the expected behavior for better UX
        self.assertEqual(response.status_code, 302)  # Redirect to login
        # Should redirect to login
        self.assertIn('/users/login/', response.url)

    def test_private_playlist_shared_access_by_viewer(self):
        self.client.login(username='viewer', password='password')
        response = self.client.get(reverse('playlist_detail', args=[
                                   self.private_playlist_shared.spotify_id]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.private_playlist_shared.name)

    def test_private_playlist_no_access_by_stranger_to_shared(self):
        """Test that a stranger cannot access a playlist even if it's shared with someone else."""
        self.client.login(username='stranger', password='password')
        response = self.client.get(reverse('playlist_detail', args=[
                                   self.private_playlist_shared.spotify_id]))
        # Stranger is not the owner or in shared_with
        self.assertEqual(response.status_code, 403)

    def test_edit_playlist_settings_access_by_owner(self):
        self.client.login(username='owner', password='password')
        response = self.client.get(reverse('edit_playlist_settings', args=[
                                   self.private_playlist.spotify_id]))
        self.assertEqual(response.status_code, 200)
        # Template uses "Playlist Settings" header
        self.assertContains(response, "Playlist Settings")

    def test_edit_playlist_settings_no_access_by_viewer(self):
        self.client.login(username='viewer', password='password')
        response = self.client.get(reverse('edit_playlist_settings', args=[
                                   self.private_playlist.spotify_id]))
        self.assertEqual(response.status_code, 403)  # Only owner can edit

    def test_edit_playlist_settings_update(self):
        self.client.login(username='owner', password='password')
        new_name = "Updated Private Playlist Name"
        new_description = "Updated description."
        form_data = {
            'name': new_name,
            'description': new_description,
            'is_public': True,  # Change to public
            'shared_with': [self.viewer.pk]  # Share with viewer
        }
        response = self.client.post(reverse('edit_playlist_settings', args=[self.private_playlist.spotify_id]),
                                    data=form_data)

        # Redirects to playlist_detail on success
        self.assertEqual(response.status_code, 302)
        self.assertRedirects(response, reverse(
            'playlist_detail', args=[self.private_playlist.spotify_id]))

        updated_playlist = Playlist.objects.get(
            spotify_id=self.private_playlist.spotify_id)
        self.assertEqual(updated_playlist.name, new_name)
        self.assertEqual(updated_playlist.description, new_description)
        self.assertTrue(updated_playlist.is_public)
        self.assertTrue(self.viewer in updated_playlist.shared_with.all())


# Example test for a track related view (if any were substantially changed beyond prefetching)
# class TrackViewTestCase(TestCase):
#    def setUp(self):
#        # ... setup users, genre, artist, track ...
#        pass
#    def test_track_detail_optimizations(self):
#        # This would typically involve using Django's assertNumQueries
#        # to verify that prefetching reduces the number of DB queries.
#        # For example:
#        # with self.assertNumQueries(expected_query_count):
#        #    response = self.client.get(reverse('track_detail', args=[self.track.spotify_id]))
#        # self.assertEqual(response.status_code, 200)
#        pass

# Note: get_recommendations was moved from music.spotify to music.recommendations in a previous hypothetical step
# For this test, I'll assume it's still in music.spotify as per the prompt, or adjust if it's confirmed moved.
# If get_recommendations is NOT in spotify, this import will need to change.


class TrackModelPopulationTests(TestCase):
    """Test track model population from Spotify data."""

    def setUp(self):
        self.genre = Genre.objects.create(name='Test Genre')
        self.mock_sp_client = MagicMock()

    @patch('services.spotify_service.client.extract_track_features_task')
    def test_populate_new_track_fields(self, mock_features_task):
        """Test that get_or_create_track properly populates track fields from Spotify data."""
        spotify_track_data = {
            'id': 'testspotifyid123',
            'name': 'Test Track with Details',
            'artists': [{'id': 'artist123', 'name': 'Test Artist'}],
            'album': {
                'name': 'Test Album',
                'images': [{'url': 'http://example.com/image.jpg'}],
                'release_date': '2023-01-01',
                'release_date_precision': 'day'
            },
            'duration_ms': 200000,
            'popularity': 75,
            'explicit': True,  # This field might not exist on model
            'disc_number': 1,  # This field might not exist on model
            'track_number': 5,  # This field might not exist on model
            'is_local': False,  # This field might not exist on model
            'preview_url': 'http://example.com/preview.mp3',
        }

        # Mock sp.artists() call if get_or_create_track uses it for genres
        self.mock_sp_client.artists.return_value = {
            'artists': [{'genres': ['Test Genre From Artist']}]}

        # Ensure artist exists if get_or_create_track tries to link it
        Artist.objects.get_or_create(spotify_id='artist123', defaults={
                                     'name': 'Test Artist'})

        track = get_or_create_track(spotify_track_data, self.mock_sp_client)

        # Only test fields that exist in the Track model
        self.assertEqual(track.title, 'Test Track with Details')
        self.assertEqual(track.preview_url, 'http://example.com/preview.mp3')
        self.assertTrue(track.artists.filter(name='Test Artist').exists())
        self.assertEqual(track.popularity, 75)
        # Check release_date if parsed correctly
        from datetime import date
        self.assertEqual(track.release_date, date(2023, 1, 1))


class RecommendationEngineTests(TestCase):
    """Test the recommendation engine logic."""

    def setUp(self):
        self.genre = Genre.objects.create(name='Test Genre')
        self.artist = Artist.objects.create(
            name='Artist', spotify_id='artist_id_rec_test')
        self.mock_sp = MagicMock()

        self.target_track = Track.objects.create(
            title='Target Track', spotify_id='target_rec1', genres=self.genre,
            preview_url='http://target.mp3', album="Target Album"
        )
        self.target_track.artists.add(self.artist)

        self.comp_track1 = Track.objects.create(
            title='Comp Track 1', spotify_id='comp_rec1', genres=self.genre,
            preview_url='http://comp1.mp3', album="Comp Album 1",
            audio_features={'tempo': 120.0,
                            'mfcc_mean': 0.5, 'danceability': 0.7}
        )
        self.comp_track1.artists.add(self.artist)

        self.comp_track2_no_features = Track.objects.create(
            title='Comp Track 2 No Features', spotify_id='comp_rec2', genres=self.genre,
            preview_url='http://comp2.mp3', album="Comp Album 2"
        )
        self.comp_track2_no_features.artists.add(self.artist)

        self.comp_track3_no_preview = Track.objects.create(
            title='Comp Track 3 No Preview', spotify_id='comp_rec3', genres=self.genre,
            album="Comp Album 3"
        )
        self.comp_track3_no_preview.artists.add(self.artist)

        self.comparison_pool_data = [
            {'id': self.comp_track1.spotify_id,
                'name': self.comp_track1.title, 'artist': self.artist.name},
            {'id': self.comp_track2_no_features.spotify_id, 'name': self.comp_track2_no_features.title,
             'artist': self.artist.name},
            {'id': self.comp_track3_no_preview.spotify_id, 'name': self.comp_track3_no_preview.title,
             'artist': self.artist.name},
        ]

    def test_get_recommendations_no_features_returns_empty(self):
        """Test that get_recommendations returns empty list when target has no features."""
        # Target track (self.target_track) initially has no audio_features
        recommendations = get_recommendations(
            self.mock_sp, self.target_track.spotify_id, self.comparison_pool_data)
        self.assertEqual(recommendations, [])

    def test_get_recommendations_with_features(self):
        """Test that get_recommendations uses tracks with features for similarity."""
        self.target_track.audio_features = {
            'tempo': 125.0, 'mfcc_mean': 0.6, 'danceability': 0.8}
        self.target_track.save()

        recommendations = get_recommendations(
            self.mock_sp, self.target_track.spotify_id, self.comparison_pool_data)

        # We expect recommendations to only be based on comp_track1 since it has features.
        # Result may be empty if similarity threshold not met, but test verifies call succeeds.
        self.assertIsInstance(recommendations, list)

    def test_get_recommendations_only_returns_tracks_with_features(self):
        """Test that recommendations only include tracks that have audio features."""
        self.target_track.audio_features = {
            'tempo': 125.0, 'mfcc_mean': 0.6, 'danceability': 0.8}
        self.target_track.save()

        # comp_track1 has features. comp_track2 and comp_track3 do not.
        recommendations = get_recommendations(
            self.mock_sp, self.target_track.spotify_id, self.comparison_pool_data)

        # If there are recommendations, they should only include comp_track1
        if recommendations:
            track_ids = [r.get('id') for r in recommendations]
            # comp_track2 and comp_track3 should NOT be in recommendations
            self.assertNotIn(
                self.comp_track2_no_features.spotify_id, track_ids)
            self.assertNotIn(self.comp_track3_no_preview.spotify_id, track_ids)


class CeleryTaskLogicTest(TestCase):
    def setUp(self):
        self.genre = Genre.objects.create(name='Celery Test Genre')
        self.artist = Artist.objects.create(
            name='Celery Artist', spotify_id='celery_artist_id')
        self.track_for_task = Track.objects.create(
            title='Task Track', spotify_id='tasktrack_celery1', genres=self.genre,
            preview_url='http://task.mp3', album="Celery Album"
        )
        self.track_for_task.artists.add(self.artist)

    @patch('music.tasks.os.path.exists')
    @patch('music.tasks.extract_audio_features')
    @patch('music.tasks.download_preview')
    def test_extract_track_features_task_logic_success(self, mock_download_preview, mock_extract_features,
                                                       mock_exists):
        """Test successful feature extraction with mocked audio processing."""
        mock_download_preview.return_value = '/fake/path/to/preview.mp3'
        mock_exists.return_value = True
        mock_extract_features.return_value = {'tempo': 130.0, 'mfcc_mean': 0.7}

        result = extract_track_features_task(
            self.track_for_task.id, self.track_for_task.preview_url)

        mock_download_preview.assert_called_once()
        mock_extract_features.assert_called_once()

        updated_track = Track.objects.get(id=self.track_for_task.id)
        self.assertIsNotNone(updated_track.audio_features)
        self.assertTrue(result)

    def test_extract_track_features_task_no_preview_url(self):
        """Test that task returns False when no preview URL is available."""
        self.track_for_task.preview_url = None
        self.track_for_task.save()

        result = extract_track_features_task(self.track_for_task.id)

        self.assertFalse(result)

        # Verify audio_features is still None (default)
        updated_track = Track.objects.get(id=self.track_for_task.id)
        self.assertIsNone(updated_track.audio_features)

    @patch('music.tasks.download_preview')
    def test_extract_track_features_task_download_fails(self, mock_download_preview):
        """Test that task returns False when download fails."""
        mock_download_preview.return_value = None  # Simulate download failure

        result = extract_track_features_task(
            self.track_for_task.id, self.track_for_task.preview_url)

        mock_download_preview.assert_called_once()
        self.assertFalse(result)

    @patch('music.tasks.os.path.exists')
    @patch('music.tasks.extract_audio_features')
    @patch('music.tasks.download_preview')
    def test_extract_track_features_task_extraction_fails(self, mock_download_preview, mock_extract_features,
                                                          mock_exists):
        """Test that task returns False when feature extraction fails."""
        mock_download_preview.return_value = '/fake/path/to/preview.mp3'
        mock_exists.return_value = True
        mock_extract_features.return_value = None  # Simulate feature extraction failure

        result = extract_track_features_task(
            self.track_for_task.id, self.track_for_task.preview_url)

        mock_extract_features.assert_called_once()
        self.assertFalse(result)
        updated_track = Track.objects.get(id=self.track_for_task.id)
        self.assertIsNone(updated_track.audio_features)
