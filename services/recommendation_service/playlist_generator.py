import random
from django.conf import settings
from services.spotify_service.client import (
    get_spotify_client_for_user,
    get_or_create_track,
    get_user_top_tracks, # Assuming this returns data usable for seeds
    get_favorite_genre # Assuming this can give us seed genres
)
from music.models import Track, Playlist # Playlist might be used later to save the generated list
import logging

logger = logging.getLogger(__name__)

# Define Mood/Activity to Audio Feature Profiles
# Values for target_* features are typically 0.0 to 1.0.
# Tempo can be target_tempo (BPM), min_tempo, max_tempo.
# Popularity is 0-100.
MOOD_ACTIVITY_PROFILES = {
    "happy": {
        "min_valence": 0.6, "target_valence": 0.8, "min_energy": 0.6, "target_energy": 0.8,
        "min_danceability": 0.5, "target_danceability": 0.7, "target_mode": 1, # Major key
        "seed_genres": ["pop", "dance", "happy"],
    },
    "sad": {
        "max_valence": 0.3, "target_valence": 0.2, "max_energy": 0.4,
        "target_mode": 0, # Minor key
        "seed_genres": ["sad", "acoustic", "blues"],
    },
    "energetic": {
        "min_energy": 0.7, "target_energy": 0.9, "min_danceability": 0.6, "target_danceability": 0.8,
        "min_tempo": 120, "target_tempo": 140,
        "seed_genres": ["electronic", "dance", "pop", "rock"],
    },
    "chill": {
        "max_energy": 0.4, "target_energy": 0.3, "max_tempo": 100, "target_tempo": 80,
        "min_acousticness": 0.5, "target_acousticness": 0.8,
        "seed_genres": ["chill", "ambient", "acoustic", "lo-fi"],
    },
    "workout": {
        "min_energy": 0.7, "target_energy": 0.85, "min_danceability": 0.6, "target_danceability": 0.75,
        "min_tempo": 125, "target_tempo": 145,
        "seed_genres": ["electronic", "dance", "hip-hop", "pop", "workout"],
    },
    "focus": {
        "min_instrumentalness": 0.6, "target_instrumentalness": 0.8, "max_speechiness": 0.3,
        "max_energy": 0.5, "target_energy": 0.3, "max_valence": 0.5,
        "seed_genres": ["ambient", "classical", "focus", "instrumental"],
    },
    "party": {
        "min_energy": 0.7, "target_energy": 0.8, "min_danceability": 0.7, "target_danceability": 0.85,
        "min_popularity": 50, # Popular tracks often good for parties
        "seed_genres": ["party", "dance", "pop", "funk", "hip-hop"],
    },
    "romance": {
        "target_valence": 0.6, "max_energy": 0.6, "target_acousticness": 0.5,
        "seed_genres": ["r-n-b", "soul", "romance", "pop"],
    },
    "sleep": {
        "max_energy": 0.2, "target_instrumentalness": 0.7, "min_acousticness":0.7,
        "max_tempo": 80,
        "seed_genres": ["sleep", "ambient", "classical", "instrumental"],
    }
}

def generate_mood_playlist(user, mood_or_activity_key, num_tracks=20):
    """
    Generates a playlist based on the user's selected mood or activity.
    """
    profile = MOOD_ACTIVITY_PROFILES.get(mood_or_activity_key.lower())
    if not profile:
        logger.warning(f"No profile found for mood/activity: {mood_or_activity_key}")
        return []

    sp = get_spotify_client_for_user(user.id) # Assumes user.id is passed
    if not sp:
        logger.warning(f"Could not get Spotify client for user {user.username} to generate mood playlist.")
        return []

    seed_artists_ids = []
    seed_genres_names = []
    seed_tracks_ids = []

    # 1. Get user's top tracks to extract artist and track seeds
    try:
        user_top_tracks = get_user_top_tracks(sp) # This is cached
        if user_top_tracks:
            # Use top 2 tracks as seed_tracks
            seed_tracks_ids = [t['id'] for t in user_top_tracks[:2] if t and t.get('id')]

            # Extract artist IDs from top tracks for seed_artists
            # Need to fetch full artist objects if get_user_top_tracks only returns names
            # For simplicity, if get_user_top_tracks gives artist IDs directly, use them.
            # Assuming get_user_top_tracks gives dicts like {'id': 'track_id', 'artist_id': 'artist_id_if_available'}
            # The current get_user_top_tracks returns {'artist': 'Artist Name'}. We need artist ID.
            # This part might require an extra call or modification to get_user_top_tracks.
            # For now, let's assume we can get one artist ID from the first top track's artist.
            if user_top_tracks[0] and user_top_tracks[0].get('artist'):
                try:
                    # Search for the artist to get their ID
                    artist_search_result = sp.search(q=f"artist:{user_top_tracks[0]['artist']}", type="artist", limit=1)
                    if artist_search_result and artist_search_result['artists']['items']:
                        seed_artists_ids.append(artist_search_result['artists']['items'][0]['id'])
                except Exception as e:
                    logger.error(f"Error searching for artist ID for seed: {user_top_tracks[0]['artist']}, error: {e}")

    except Exception as e:
        logger.error(f"Error fetching user's top tracks for seeds: {str(e)}")

    # 2. Get user's favorite genre as a seed genre
    try:
        favorite_genre = get_favorite_genre(sp, user_top_tracks if 'user_top_tracks' in locals() else []) # Pass top_tracks if available
        if favorite_genre:
            seed_genres_names.append(favorite_genre.lower()) # Ensure lowercase for consistency
    except Exception as e:
        logger.error(f"Error fetching user's favorite genre for seeds: {str(e)}")

    # 3. Supplement with default seed genres from profile if needed, respecting 5-seed limit
    current_seed_count = len(seed_artists_ids) + len(seed_genres_names) + len(seed_tracks_ids)
    if current_seed_count < 5 and profile.get("seed_genres"):
        for genre in profile["seed_genres"]:
            if genre.lower() not in seed_genres_names: # Avoid duplicates
                seed_genres_names.append(genre.lower())
                current_seed_count += 1
                if current_seed_count >= 5:
                    break

    # Ensure lists are not empty, if so, provide broad default seeds from profile
    if not seed_artists_ids and not seed_genres_names and not seed_tracks_ids:
        seed_genres_names = profile.get("seed_genres", [])[:2] # Use up to 2 default genres if no seeds at all

    # Trim seeds to Spotify's limit (max 5 total for artists, genres, tracks)
    # Prioritize track seeds, then artist, then genre if trimming is needed.
    # This is a simple trim, more sophisticated logic could be used.
    while len(seed_artists_ids) + len(seed_genres_names) + len(seed_tracks_ids) > 5:
        if len(seed_genres_names) > 2: seed_genres_names.pop()
        elif len(seed_artists_ids) > 1: seed_artists_ids.pop()
        elif len(seed_tracks_ids) > 2: seed_tracks_ids.pop()
        else: # Failsafe, just trim from genres if still too many
             if seed_genres_names: seed_genres_names.pop()
             elif seed_artists_ids: seed_artists_ids.pop()
             elif seed_tracks_ids: seed_tracks_ids.pop()


    # Prepare target audio features for the API call
    target_features_for_api = {}
    for key, value in profile.items():
        if key.startswith(("target_", "min_", "max_")) and key != "seed_genres":
            # Spotify API expects BPM for tempo, not normalized 0-1
            # Our MOOD_ACTIVITY_PROFILES stores BPM directly for min_tempo, target_tempo, max_tempo
            target_features_for_api[key] = value

    logger.info(f"Generating recommendations for {mood_or_activity_key} with seeds: artists={seed_artists_ids}, genres={seed_genres_names}, tracks={seed_tracks_ids} and features: {target_features_for_api}")

    try:
        recommendations = sp.recommendations(
            seed_artists=seed_artists_ids if seed_artists_ids else None,
            seed_genres=seed_genres_names if seed_genres_names else None,
            seed_tracks=seed_tracks_ids if seed_tracks_ids else None,
            limit=num_tracks,
            **target_features_for_api
        )
    except Exception as e:
        logger.error(f"Error getting recommendations from Spotify: {str(e)}")
        return []

    # Process recommended tracks
    generated_tracks = []
    if recommendations and recommendations.get('tracks'):
        for track_data in recommendations['tracks']:
            if track_data and track_data.get('id'): # Ensure track_data and its id are not None
                # `get_or_create_track` expects the full Spotify track object.
                # The recommendation endpoint returns a simplified track object.
                # We might need to fetch the full track object if `get_or_create_track` needs it,
                # or adapt `get_or_create_track` if it can handle this simplified structure,
                # or fetch full track details here.
                # For now, let's assume `get_or_create_track` can handle it or we fetch full details.

                # Option 1: Fetch full track details (safer, but more API calls)
                try:
                    full_track_data = sp.track(track_data['id']) # API call
                    if full_track_data:
                        track_instance = get_or_create_track(full_track_data, sp)
                        if track_instance:
                            generated_tracks.append(track_instance)
                except Exception as e:
                    logger.error(f"Error fetching full track details for recommended track {track_data['id']}: {e}")

                # Option 2: Adapt get_or_create_track or ensure it handles simplified objects (current approach of get_or_create_track)
                # track_instance = get_or_create_track(track_data, sp)
                # if track_instance:
                #    generated_tracks.append(track_instance)

    logger.info(f"Generated {len(generated_tracks)} tracks for mood/activity: {mood_or_activity_key}")
    return generated_tracks
