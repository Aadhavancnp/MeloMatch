from django.core.management.base import BaseCommand, CommandError
from services.spotify_service.client import get_spotify_client_credentials_client
import spotipy # For spotipy.exceptions.SpotifyException
import logging

# Configure logger for this command. You might want to use Django's logging settings.
logger = logging.getLogger(__name__)
# To see output in console from logger.info/error for management commands,
# ensure your Django logging settings are configured to handle this logger,
# or use self.stdout/self.stderr directly for simplicity in this command.

class Command(BaseCommand):
    help = 'Tests the Spotify audio_features endpoint using client credentials.'

    def handle(self, *args, **options):
        self.stdout.write("Attempting to get Spotify client using client credentials...")
        sp = get_spotify_client_credentials_client()

        if not sp:
            self.stderr.write(self.style.ERROR("Failed to initialize Spotify client with client credentials."))
            # You might want to raise CommandError here to indicate failure
            # raise CommandError("Failed to initialize Spotify client.")
            return

        self.stdout.write(self.style.SUCCESS("Spotify client (client credentials) initialized successfully."))

        # Test case 1: Valid Track IDs
        valid_track_ids = [
            "0c6xIDDpzE81m2q797ordA", # Toxicity by System Of A Down
            "6rqhFgbbKwnb9MLmUQDhG6", # Smells Like Teen Spirit by Nirvana
        ]
        self.stdout.write(f"\nTesting audio_features for VALID track IDs: {valid_track_ids}")
        try:
            features_response = sp.audio_features(tracks=valid_track_ids)
            self.stdout.write(self.style.SUCCESS("  Successfully called sp.audio_features(). Response:"))
            if features_response:
                for i, item in enumerate(features_response):
                    if item:
                        self.stdout.write(
                            f"    Track ID {valid_track_ids[i]} (Spotify: {item.get('id')}): "
                            f"Danceability: {item.get('danceability')}, Energy: {item.get('energy')}, "
                            f"Valence: {item.get('valence')}, Tempo: {item.get('tempo')}"
                        )
                    else:
                        self.stdout.write(self.style.WARNING(f"    Received None for track ID {valid_track_ids[i]}."))
            else:
                self.stdout.write(self.style.WARNING("  Response was empty or None for valid IDs."))

        except spotipy.exceptions.SpotifyException as e:
            self.stderr.write(self.style.ERROR(f"  Spotify API Error with valid IDs: {e}"))
            if hasattr(e, 'http_status'): self.stderr.write(f"  HTTP Status Code: {e.http_status}")
            if hasattr(e, 'code'): self.stderr.write(f"  Spotify Error Code: {e.code}")
        except Exception as e:
            self.stderr.write(self.style.ERROR(f"  An unexpected error occurred with valid IDs: {e}"))

        # Test case 2: Invalid Track ID
        invalid_track_ids = ["thisIsNotARealSpotifyTrackID"]
        self.stdout.write(f"\nTesting audio_features for INVALID track ID: {invalid_track_ids}")
        try:
            features_response = sp.audio_features(tracks=invalid_track_ids)
            self.stdout.write(self.style.SUCCESS("  Successfully called sp.audio_features(). Response:"))
            if features_response and len(features_response) == 1 and features_response[0] is None:
                self.stdout.write(self.style.SUCCESS("    Correctly received [None] for the invalid track ID."))
            elif not features_response: # Also acceptable if API returns empty list for all-invalid query
                 self.stdout.write(self.style.SUCCESS("    Correctly received an empty list for the invalid track ID."))
            else:
                self.stdout.write(self.style.WARNING(f"    Unexpected response for invalid ID: {features_response}"))

        except spotipy.exceptions.SpotifyException as e:
            self.stderr.write(self.style.ERROR(f"  Spotify API Error with invalid ID: {e}"))
            if hasattr(e, 'http_status'): self.stderr.write(f"  HTTP Status Code: {e.http_status}")
            if hasattr(e, 'code'): self.stderr.write(f"  Spotify Error Code: {e.code}")
        except Exception as e:
            self.stderr.write(self.style.ERROR(f"  An unexpected error occurred with invalid ID: {e}"))

        # Test case 3: Mixed Valid and Invalid Track IDs
        mixed_track_ids = [valid_track_ids[0], "anotherInvalidID", valid_track_ids[1]]
        self.stdout.write(f"\nTesting audio_features for MIXED track IDs: {mixed_track_ids}")
        try:
            features_response = sp.audio_features(tracks=mixed_track_ids)
            self.stdout.write(self.style.SUCCESS("  Successfully called sp.audio_features(). Response:"))
            if features_response and len(features_response) == len(mixed_track_ids):
                expected_behavior = True
                for i, item in enumerate(features_response):
                    is_valid_test_id = mixed_track_ids[i] in valid_track_ids
                    if item and is_valid_test_id:
                        self.stdout.write(
                            f"    Track ID {mixed_track_ids[i]} (Spotify: {item.get('id')}): "
                            f"Danceability: {item.get('danceability')}, Energy: {item.get('energy')}"
                        )
                    elif item is None and not is_valid_test_id:
                        self.stdout.write(f"    Received None for invalid track ID {mixed_track_ids[i]} (expected).")
                    else:
                        self.stdout.write(self.style.WARNING(
                            f"    Unexpected item for track ID {mixed_track_ids[i]}: {item}"
                        ))
                        expected_behavior = False
                if expected_behavior:
                    self.stdout.write(self.style.SUCCESS("    Mixed call behaved as expected (features for valid, None for invalid)."))
                else:
                    self.stdout.write(self.style.WARNING("    Mixed call did NOT entirely behave as expected."))
            else:
                self.stdout.write(self.style.WARNING(f"  Response for mixed IDs had unexpected structure or length: {features_response}"))

        except spotipy.exceptions.SpotifyException as e:
            self.stderr.write(self.style.ERROR(f"  Spotify API Error with mixed IDs: {e}"))
            if hasattr(e, 'http_status'): self.stderr.write(f"  HTTP Status Code: {e.http_status}")
            if hasattr(e, 'code'): self.stderr.write(f"  Spotify Error Code: {e.code}")
        except Exception as e:
            self.stderr.write(self.style.ERROR(f"  An unexpected error occurred with mixed IDs: {e}"))

        self.stdout.write("\nTest finished.")
