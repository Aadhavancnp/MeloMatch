from django.test import TestCase, Client
from django.urls import reverse
from unittest.mock import patch
from users.models import CustomUser # Assuming CustomUser is your user model

class DashboardViewTests(TestCase):
    def setUp(self):
        self.user = CustomUser.objects.create_user(username='testuser', email='test@example.com', password='password')
        self.client = Client()
        self.client.login(username='testuser', password='password')

    @patch('core.views.get_user_recently_played')
    @patch('core.views.get_user_top_tracks')
    @patch('core.views.get_user_playlists')
    @patch('core.views.get_recommendations') # Also mock get_recommendations
    @patch('core.views.calculate_listening_time')
    @patch('core.views.get_favorite_genre')
    def test_dashboard_view_all_data_sources_ok(self, mock_fav_genre, mock_listen_time, mock_get_recs, mock_playlists, mock_top_tracks, mock_recently_played):
        # Setup mock return values
        mock_playlists.return_value = [{'name': 'Playlist 1', 'id': 'pl1'}]
        mock_top_tracks.return_value = [{'name': 'Top Track 1', 'id': 'tt1', 'artist': 'Artist A'}]
        # Ensure recently_played returns a list with at least one item that has 'id' for Counter and 'name' for context
        mock_recently_played.return_value = [{'name': 'Recent Track 1', 'id': 'rt1', 'artist': 'Artist B'}]
        mock_get_recs.return_value = [{'id': 'rectrack1', 'title': 'Recommended Track 1'}]
        # Ensure Track objects exist for recommendations if the view tries to fetch them by ID (it does)
        # This might require creating Genre and Artist objects if not already available globally or in setUp
        genre, _ = Genre.objects.get_or_create(name='Test Genre')
        Track.objects.create(spotify_id='rectrack1', title='Recommended Track 1', genres=genre)

        mock_listen_time.return_value = 10.5
        mock_fav_genre.return_value = "Electronic"

        response = self.client.get(reverse('dashboard'))
        self.assertEqual(response.status_code, 200)

        # Check for some data that should be in the context and rendered
        # Note: The dashboard template renders 'name' from these dicts.
        self.assertContains(response, 'Top Track 1')
        self.assertContains(response, 'Recent Track 1')
        self.assertContains(response, 'Recommended Track 1') # Check for recommendation
        self.assertContains(response, "10.5 hours") # Check listening time
        self.assertContains(response, "Electronic") # Check favorite genre

        # Check if the Spotify API utility functions were called with the request object
        mock_playlists.assert_called_once_with(response.wsgi_request)
        mock_top_tracks.assert_called_once_with(response.wsgi_request)
        mock_recently_played.assert_called_once_with(response.wsgi_request)


    @patch('core.views.get_user_recently_played')
    @patch('core.views.get_user_top_tracks')
    @patch('core.views.get_user_playlists')
    @patch('core.views.get_recommendations')
    @patch('core.views.calculate_listening_time')
    @patch('core.views.get_favorite_genre')
    def test_dashboard_view_one_source_fails(self, mock_fav_genre, mock_listen_time, mock_get_recs, mock_playlists, mock_top_tracks, mock_recently_played):
        mock_playlists.return_value = [{'name': 'Playlist 1', 'id': 'pl1'}]
        mock_top_tracks.return_value = [{'name': 'Top Track 1', 'id': 'tt1', 'artist': 'Artist A'}]

        # Simulate failure by returning empty list (as per error handling in the functions)
        mock_recently_played.return_value = []
        # OR simulate by raising an exception to test the view's try-except for future.result()
        # mock_recently_played.side_effect = Exception("Simulated API error")

        # Adjust dependent mocks if recently_played is empty
        mock_get_recs.return_value = [{'id': 'rectrack1', 'title': 'Recommended Track 1'}] # Still provide some recs
        Track.objects.get_or_create(spotify_id='rectrack1', title='Recommended Track 1', defaults={'genres_id': Genre.objects.get_or_create(name='Fallback Genre')[0].id})


        mock_listen_time.return_value = 0
        mock_fav_genre.return_value = "Rock" # Still provide other data

        response = self.client.get(reverse('dashboard'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Top Track 1')
        # If recently_played is empty, its specific data might not be rendered or a default message shown
        # This depends on template logic. For now, check context.
        self.assertEqual(len(response.context.get('recently_played', [])), 0)
        # Check that other parts of the page still render
        self.assertContains(response, "Rock") # Favorite genre should still be there
        # If recommendations can still be generated (e.g., from top_tracks if recently_played is empty), check for them.
        # The current dashboard logic might not generate recs if recently_played is empty and most_repeat cannot be found.
        # Let's assume for this test, recommendations are not generated or are empty if recently_played is empty.
        # If you have fallback logic in dashboard to use top_tracks for recs, this check would change.
        # self.assertNotContains(response, 'Recommended Track 1') # Or check for empty list in context

    @patch('core.views.get_user_recently_played')
    @patch('core.views.get_user_top_tracks')
    @patch('core.views.get_user_playlists')
    @patch('core.views.get_recommendations')
    @patch('core.views.calculate_listening_time')
    @patch('core.views.get_favorite_genre')
    def test_dashboard_view_all_sources_fail_gracefully(self, mock_fav_genre, mock_listen_time, mock_get_recs, mock_playlists, mock_top_tracks, mock_recently_played):
        # Simulate all data sources returning empty lists (or raising exceptions)
        mock_playlists.return_value = []
        mock_top_tracks.return_value = []
        mock_recently_played.return_value = []
        mock_get_recs.return_value = []
        mock_listen_time.return_value = 0
        mock_fav_genre.return_value = None # Or "Unknown"

        response = self.client.get(reverse('dashboard'))
        self.assertEqual(response.status_code, 200) # View should still load

        # Check context variables are empty or have default values
        self.assertEqual(len(response.context.get('top_tracks', [])), 0)
        self.assertEqual(len(response.context.get('recently_played', [])), 0)
        self.assertEqual(len(response.context.get('recommended_tracks', [])), 0)
        self.assertEqual(response.context.get('listening_time'), 0)
        self.assertIsNone(response.context.get('favorite_genre')) # Or "Unknown"
        self.assertContains(response, "Dashboard") # Basic check that the page rendered somewhat

# Need to import Genre and Track for the mock setup in test_dashboard_view_all_data_sources_ok
from music.models import Genre, Track
