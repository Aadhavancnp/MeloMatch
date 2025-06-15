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
