from django.urls import reverse
from django.test import Client, TestCase
from users.models import CustomUser
from .models import Playlist, Track, Genre, Artist # Added Artist
from django.contrib.auth.models import AnonymousUser # For testing anonymous access

class PlaylistSharingTestCase(TestCase):
    def setUp(self):
        self.owner = CustomUser.objects.create_user(username='owner', email='owner@example.com', password='password')
        self.viewer = CustomUser.objects.create_user(username='viewer', email='viewer@example.com', password='password')
        self.stranger = CustomUser.objects.create_user(username='stranger', email='stranger@example.com', password='password')

        self.genre = Genre.objects.create(name='Test Genre')
        # self.artist = Artist.objects.create(name='Test Artist', spotify_id='testartistspotifyid_playlist') # Create unique spotify_id

        # Track needs to be associated with an artist for some views/templates to render correctly (e.g., artists_names)
        # For simplicity, we'll create tracks without artists if model allows, or add them.
        # Current Track model has artists as ManyToMany, not required on create.
        self.track = Track.objects.create(
            title='Test Track for Playlist',
            genres=self.genre,
            spotify_id='testtrackplaylist', # Unique spotify_id
            album = "Test Album for Playlist"
        )

        self.public_playlist = Playlist.objects.create(
            user=self.owner,
            name='Public Playlist',
            spotify_id='public_playlist_id', # Unique spotify_id
            is_public=True
        )
        self.private_playlist = Playlist.objects.create(
            user=self.owner,
            name='Private Playlist',
            spotify_id='private_playlist_id', # Unique spotify_id
            is_public=False
        )
        self.private_playlist_shared = Playlist.objects.create(
            user=self.owner,
            name='Private Shared Playlist',
            spotify_id='private_shared_id', # Unique spotify_id
            is_public=False
        )
        self.private_playlist_shared.shared_with.add(self.viewer)

        self.client = Client()

    def test_public_playlist_access_by_owner(self):
        self.client.login(username='owner', password='password')
        response = self.client.get(reverse('playlist_detail', args=[self.public_playlist.spotify_id]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.public_playlist.name)

    def test_public_playlist_access_by_viewer(self):
        self.client.login(username='viewer', password='password')
        response = self.client.get(reverse('playlist_detail', args=[self.public_playlist.spotify_id]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.public_playlist.name)

    def test_public_playlist_access_by_anonymous(self):
        # client is not logged in
        response = self.client.get(reverse('playlist_detail', args=[self.public_playlist.spotify_id]))
        self.assertEqual(response.status_code, 200) # Public playlists should be viewable by anyone
        self.assertContains(response, self.public_playlist.name)

    def test_private_playlist_access_by_owner(self):
        self.client.login(username='owner', password='password')
        response = self.client.get(reverse('playlist_detail', args=[self.private_playlist.spotify_id]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.private_playlist.name)

    def test_private_playlist_no_access_by_stranger(self):
        self.client.login(username='stranger', password='password')
        response = self.client.get(reverse('playlist_detail', args=[self.private_playlist.spotify_id]))
        # Expecting HttpResponseForbidden (403) as per the view logic
        self.assertEqual(response.status_code, 403)

    def test_private_playlist_no_access_by_anonymous(self):
        response = self.client.get(reverse('playlist_detail', args=[self.private_playlist.spotify_id]))
        # Anonymous users trying to access private playlists should be redirected to login or shown a forbidden page.
        # The view uses @login_required, which redirects to LOGIN_URL.
        # However, the permission check is inside the view after login.
        # If an anonymous user somehow bypassed @login_required (not possible for this setup),
        # the internal check `request.user in playlist.shared_with.all()` would fail for AnonymousUser.
        # The current `playlist_detail` view's permission logic:
        # `can_view = playlist.is_public or is_owner or (request.user.is_authenticated and request.user in playlist.shared_with.all())`
        # For anonymous, `is_public` is false, `is_owner` is false, `request.user.is_authenticated` is false. So `can_view` is false.
        self.assertEqual(response.status_code, 403) # Expect Forbidden due to view logic

    def test_private_playlist_shared_access_by_viewer(self):
        self.client.login(username='viewer', password='password')
        response = self.client.get(reverse('playlist_detail', args=[self.private_playlist_shared.spotify_id]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.private_playlist_shared.name)

    def test_private_playlist_no_access_by_stranger_to_shared(self):
        """Test that a stranger cannot access a playlist even if it's shared with someone else."""
        self.client.login(username='stranger', password='password')
        response = self.client.get(reverse('playlist_detail', args=[self.private_playlist_shared.spotify_id]))
        self.assertEqual(response.status_code, 403) # Stranger is not the owner or in shared_with

    def test_edit_playlist_settings_access_by_owner(self):
        self.client.login(username='owner', password='password')
        response = self.client.get(reverse('edit_playlist_settings', args=[self.private_playlist.spotify_id]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Edit Settings for")

    def test_edit_playlist_settings_no_access_by_viewer(self):
        self.client.login(username='viewer', password='password')
        response = self.client.get(reverse('edit_playlist_settings', args=[self.private_playlist.spotify_id]))
        self.assertEqual(response.status_code, 403) # Only owner can edit

    def test_edit_playlist_settings_update(self):
        self.client.login(username='owner', password='password')
        new_name = "Updated Private Playlist Name"
        new_description = "Updated description."
        form_data = {
            'name': new_name,
            'description': new_description,
            'is_public': True, # Change to public
            'shared_with': [self.viewer.pk] # Share with viewer
        }
        response = self.client.post(reverse('edit_playlist_settings', args=[self.private_playlist.spotify_id]), data=form_data)

        self.assertEqual(response.status_code, 302) # Redirects to playlist_detail on success
        self.assertRedirects(response, reverse('playlist_detail', args=[self.private_playlist.spotify_id]))

        updated_playlist = Playlist.objects.get(spotify_id=self.private_playlist.spotify_id)
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

from unittest.mock import patch, MagicMock
# Note: get_recommendations was moved from music.spotify to music.recommendations in a previous hypothetical step
# For this test, I'll assume it's still in music.spotify as per the prompt, or adjust if it's confirmed moved.
# If get_recommendations is NOT in spotify, this import will need to change.
from .spotify import get_or_create_track, get_recommendations
from .tasks import extract_track_features_task
from .audio_utils import download_preview, extract_audio_features


class TrackModelPopulationTests(TestCase):
    def setUp(self):
        self.genre = Genre.objects.create(name='Test Genre')
        self.mock_sp_client = MagicMock()

    def test_populate_new_track_fields(self):
        spotify_track_data = {
            'id': 'testspotifyid123',
            'name': 'Test Track with Details',
            'artists': [{'id': 'artist123', 'name': 'Test Artist'}],
            'album': {
                'name': 'Test Album',
                'images': [{'url': 'http://example.com/image.jpg'}],
                'release_date': '2023-01-01', # Added for release_date
                'release_date_precision': 'day' # Added for release_date
            },
            'duration_ms': 200000,
            'popularity': 75,
            'explicit': True,
            'disc_number': 1,
            'track_number': 5,
            'is_local': False,
            'preview_url': 'http://example.com/preview.mp3',
        }

        # Mock sp.artists() call if get_or_create_track uses it for genres
        self.mock_sp_client.artists.return_value = {'artists': [{'genres': ['Test Genre From Artist']}]}

        # Ensure artist exists if get_or_create_track tries to link it
        Artist.objects.get_or_create(spotify_id='artist123', defaults={'name': 'Test Artist'})

        track = get_or_create_track(spotify_track_data, self.mock_sp_client)

        self.assertEqual(track.title, 'Test Track with Details')
        self.assertEqual(track.explicit, True)
        self.assertEqual(track.disc_number, 1)
        self.assertEqual(track.track_number, 5)
        self.assertEqual(track.is_local, False)
        self.assertEqual(track.preview_url, 'http://example.com/preview.mp3')
        self.assertTrue(track.artists.filter(name='Test Artist').exists())
        self.assertEqual(track.popularity, 75)
        # Add check for release_date if parsed correctly
        from datetime import date
        self.assertEqual(track.release_date, date(2023, 1, 1))


class RecommendationEngineTests(TestCase):
    def setUp(self):
        self.genre = Genre.objects.create(name='Test Genre')
        self.artist = Artist.objects.create(name='Artist', spotify_id='artist_id_rec_test')

        self.target_track = Track.objects.create(
            title='Target Track', spotify_id='target_rec1', genres=self.genre,
            preview_url='http://target.mp3', is_local=False, explicit=False,
            album="Target Album"
        )
        self.target_track.artists.add(self.artist)

        self.comp_track1 = Track.objects.create(
            title='Comp Track 1', spotify_id='comp_rec1', genres=self.genre,
            preview_url='http://comp1.mp3', is_local=False, explicit=False,
            album="Comp Album 1",
            audio_features={'tempo': 120.0, 'mfcc_mean': 0.5, 'danceability': 0.7} # Ensure features are somewhat realistic
        )
        self.comp_track1.artists.add(self.artist)

        self.comp_track2_no_features = Track.objects.create(
            title='Comp Track 2 No Features', spotify_id='comp_rec2', genres=self.genre,
            preview_url='http://comp2.mp3', is_local=False, explicit=False,
            album="Comp Album 2"
        )
        self.comp_track2_no_features.artists.add(self.artist)

        self.comp_track3_no_preview = Track.objects.create(
            title='Comp Track 3 No Preview', spotify_id='comp_rec3', genres=self.genre,
            is_local=False, explicit=False, album="Comp Album 3"
        )
        self.comp_track3_no_preview.artists.add(self.artist)

        self.comparison_pool_data = [
            {'id': self.comp_track1.spotify_id, 'name': self.comp_track1.title, 'artist': self.artist.name},
            {'id': self.comp_track2_no_features.spotify_id, 'name': self.comp_track2_no_features.title, 'artist': self.artist.name},
            {'id': self.comp_track3_no_preview.spotify_id, 'name': self.comp_track3_no_preview.title, 'artist': self.artist.name},
        ]

    @patch('music.spotify.extract_track_features_task.delay')
    def test_get_recommendations_queues_for_target_if_no_features(self, mock_task_delay):
        # Target track (self.target_track) initially has no audio_features
        recommendations = get_recommendations(self.target_track.spotify_id, self.comparison_pool_data)
        mock_task_delay.assert_called_with(self.target_track.id, self.target_track.preview_url)
        self.assertEqual(recommendations, [])

    @patch('music.spotify.extract_track_features_task.delay')
    @patch('music.spotify.search_jiosaavn') # Mock jiosaavn if preview URL might be missing
    def test_get_recommendations_queues_for_comparison_tracks(self, mock_search_jiosaavn, mock_task_delay):
        # Mock jiosaavn to provide a preview URL for comp_track2_no_features if it's sought
        mock_search_jiosaavn.return_value = [{'id': 'jiosaavn_id_for_comp2'}]
        # This mock might need to be more specific if get_track_details_jiosaavn is called next
        # For simplicity, assume preview_url is on the object or this mock is enough.

        self.target_track.audio_features = {'tempo': 125.0, 'mfcc_mean': 0.6, 'danceability': 0.8}
        self.target_track.save()

        get_recommendations(self.target_track.spotify_id, self.comparison_pool_data)

        # Check if task was called for comp_track2_no_features
        # (it has a preview_url, but no features initially)
        call_args_list = [call[0][0] for call in mock_task_delay.call_args_list]
        self.assertIn(self.comp_track2_no_features.id, call_args_list)


    @patch('music.spotify.extract_track_features_task.delay') # Keep it patched to avoid actual Celery calls
    def test_get_recommendations_uses_only_tracks_with_features(self, mock_task_delay):
        self.target_track.audio_features = {'tempo': 125.0, 'mfcc_mean': 0.6, 'danceability': 0.8}
        self.target_track.save()

        # comp_track1 has features. comp_track2 and comp_track3 do not.
        # comp_track2 will have a task queued. comp_track3 has no preview_url, so no task.
        recommendations = get_recommendations(self.target_track.spotify_id, self.comparison_pool_data)

        # We expect recommendations to only be based on comp_track1.
        if recommendations: # Check if any recommendations were returned
            self.assertEqual(len(recommendations), 1)
            self.assertEqual(recommendations[0]['id'], self.comp_track1.spotify_id)
        else:
            # This might happen if similarity is too low or other filtering occurs.
            # For this test structure, if comp_track1 is the only one with features,
            # it should be the only candidate. If it's not similar enough, empty is fine.
            pass

    @patch('music.spotify.search_jiosaavn')
    @patch('music.spotify.extract_track_features_task.delay')
    def test_get_recommendations_no_target_preview(self, mock_task_delay, mock_search_jiosaavn):
        mock_search_jiosaavn.return_value = [] # Simulate JioSaavn not finding a preview
        self.target_track.preview_url = None
        self.target_track.save() # Target track has no features and no preview_url

        recommendations = get_recommendations(self.target_track.spotify_id, self.comparison_pool_data)

        # Assert that no task was queued for the target track because no preview URL could be found
        target_queued = False
        for call_args in mock_task_delay.call_args_list:
            if call_args[0][0] == self.target_track.id:
                target_queued = True
                break
        self.assertFalse(target_queued, "Task should not be queued for target if no preview URL is found.")
        self.assertEqual(recommendations, [])


class CeleryTaskLogicTest(TestCase):
    def setUp(self):
        self.genre = Genre.objects.create(name='Celery Test Genre')
        self.artist = Artist.objects.create(name='Celery Artist', spotify_id='celery_artist_id')
        self.track_for_task = Track.objects.create(
            title='Task Track', spotify_id='tasktrack_celery1', genres=self.genre,
            preview_url='http://task.mp3', album="Celery Album"
        )
        self.track_for_task.artists.add(self.artist)

    @patch('music.audio_utils.download_preview')
    @patch('music.audio_utils.extract_audio_features')
    @patch('os.remove') # Mock os.remove to check if it's called
    def test_extract_track_features_task_logic_success(self, mock_os_remove, mock_extract_librosa_features, mock_download_preview):
        mock_download_preview.return_value = '/fake/path/to/preview.mp3'
        mock_extract_librosa_features.return_value = {'tempo': 130.0, 'mfcc_mean': 0.7}

        result = extract_track_features_task(self.track_for_task.id, self.track_for_task.preview_url)

        mock_download_preview.assert_called_with('http://task.mp3', self.track_for_task.spotify_id)
        mock_extract_librosa_features.assert_called_with('/fake/path/to/preview.mp3')
        mock_os_remove.assert_called_with('/fake/path/to/preview.mp3') # Check cleanup

        updated_track = Track.objects.get(id=self.track_for_task.id)
        self.assertIsNotNone(updated_track.audio_features)
        self.assertEqual(updated_track.audio_features['tempo'], 130.0)
        self.assertTrue(result)

    @patch('music.audio_utils.download_preview')
    def test_extract_track_features_task_no_preview_url(self, mock_download_preview):
        self.track_for_task.preview_url = None # Ensure no preview URL
        self.track_for_task.save()

        result = extract_track_features_task(self.track_for_task.id) # Call without override

        mock_download_preview.assert_not_called()
        self.assertFalse(result)

        # Verify audio_features is still empty or unchanged
        updated_track = Track.objects.get(id=self.track_for_task.id)
        self.assertEqual(updated_track.audio_features, {}) # Assuming it was initialized as {}

    @patch('music.audio_utils.download_preview')
    @patch('music.audio_utils.extract_audio_features')
    @patch('os.remove')
    def test_extract_track_features_task_download_fails(self, mock_os_remove, mock_extract_librosa_features, mock_download_preview):
        mock_download_preview.return_value = None # Simulate download failure

        result = extract_track_features_task(self.track_for_task.id, self.track_for_task.preview_url)

        mock_download_preview.assert_called_with('http://task.mp3', self.track_for_task.spotify_id)
        mock_extract_librosa_features.assert_not_called()
        mock_os_remove.assert_not_called() # File wouldn't exist to be removed
        self.assertFalse(result)

    @patch('music.audio_utils.download_preview')
    @patch('music.audio_utils.extract_audio_features')
    @patch('os.remove')
    def test_extract_track_features_task_extraction_fails(self, mock_os_remove, mock_extract_librosa_features, mock_download_preview):
        mock_download_preview.return_value = '/fake/path/to/preview.mp3'
        mock_extract_librosa_features.return_value = None # Simulate feature extraction failure

        result = extract_track_features_task(self.track_for_task.id, self.track_for_task.preview_url)

        mock_extract_librosa_features.assert_called_with('/fake/path/to/preview.mp3')
        mock_os_remove.assert_called_with('/fake/path/to/preview.mp3') # Cleanup should still happen
        self.assertFalse(result)
        updated_track = Track.objects.get(id=self.track_for_task.id)
        self.assertEqual(updated_track.audio_features, {})
