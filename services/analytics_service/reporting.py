from collections import Counter
from datetime import timedelta

from django.utils import timezone
from django.core.cache import cache
from django.db.models import Count, Q, DateField
from django.db.models.functions import TruncDate

# Assuming models are in these locations. Adjust if necessary.
from users.models import CustomUser
from music.models import Track, UserLikedTrack, Genre, Playlist # UserActivity might be used later
# UserActivity is not used in this iteration as detailed play logging isn't available.

# Define cache timeouts
CACHE_TIMEOUT_LONG = 60 * 60 * 24  # 24 hours
CACHE_TIMEOUT_MEDIUM = 60 * 60 * 6 # 6 hours
CACHE_TIMEOUT_SHORT = 60 * 30    # 30 minutes

# Mood thresholds (can be tuned)
VALENCE_THRESHOLD_HIGH = 0.6
VALENCE_THRESHOLD_LOW = 0.4
ENERGY_THRESHOLD_HIGH = 0.6
ENERGY_THRESHOLD_LOW = 0.4

logger = None # Placeholder, add import logging and logger = logging.getLogger(__name__) if needed.
# For now, keeping it simple without explicit logging in these functions unless an error occurs.
import logging
logger = logging.getLogger(__name__)


def get_user_listening_time_trend(user: CustomUser, days: int = 30) -> list:
    """
    Calculates the trend of 'listening time' for a user, proxied by the number of liked songs per day.
    Args:
        user: The CustomUser instance.
        days: The number of past days to include in the trend.
    Returns:
        A list of dictionaries, e.g., [{'date': 'YYYY-MM-DD', 'count': X}, ...].
    """
    cache_key = f"analytics_listening_trend_{user.id}_{days}d"
    cached_data = cache.get(cache_key)
    if cached_data:
        logger.info(f"Returning cached listening trend for user {user.id}")
        return cached_data

    logger.info(f"Calculating listening trend for user {user.id} for last {days} days.")
    end_date = timezone.now().date()
    start_date = end_date - timedelta(days=days - 1) # -1 because we include the end_date

    # Aggregate liked tracks by date
    liked_tracks_trend = (
        UserLikedTrack.objects.filter(user=user, liked_at__date__gte=start_date, liked_at__date__lte=end_date)
        .annotate(date=TruncDate('liked_at'))
        .values('date')
        .annotate(count=Count('id'))
        .order_by('date')
    )

    # Convert to list of dicts and ensure all days in the range are present (even if count is 0)
    trend_data_map = {item['date'].strftime('%Y-%m-%d'): item['count'] for item in liked_tracks_trend}

    result_list = []
    for i in range(days):
        current_date = start_date + timedelta(days=i)
        date_str = current_date.strftime('%Y-%m-%d')
        result_list.append({
            'date': date_str,
            'count': trend_data_map.get(date_str, 0)
        })

    cache.set(cache_key, result_list, CACHE_TIMEOUT_MEDIUM)
    logger.info(f"Cached listening trend for user {user.id} for {days} days.")
    return result_list


def get_user_mood_distribution(user: CustomUser) -> list:
    """
    Calculates the distribution of moods for tracks liked by the user.
    Moods are derived from valence and energy audio features.
    Args:
        user: The CustomUser instance.
    Returns:
        A list of dictionaries, e.g., [{'mood': 'Happy/Energetic', 'count': X}, ...].
    """
    cache_key = f"analytics_mood_dist_{user.id}"
    cached_data = cache.get(cache_key)
    if cached_data:
        logger.info(f"Returning cached mood distribution for user {user.id}")
        return cached_data

    logger.info(f"Calculating mood distribution for user {user.id}")
    liked_tracks_with_features = UserLikedTrack.objects.filter(user=user)\
                                .select_related('track')\
                                .exclude(track__audio_features__isnull=True)\
                                .exclude(track__audio_features={}) # Exclude empty JSON objects

    mood_counts = Counter()

    for liked_track in liked_tracks_with_features:
        features = liked_track.track.audio_features
        if not isinstance(features, dict): # Ensure features is a dict
            logger.debug(f"Track {liked_track.track.id} has non-dict audio_features: {features}. Skipping for mood calc.")
            continue

        valence = features.get('valence')
        energy = features.get('energy')

        if valence is None or energy is None:
            logger.debug(f"Track {liked_track.track.id} missing valence/energy. Features: {features}. Skipping for mood calc.")
            continue

        # Ensure valence and energy are numbers (they might be stored as strings if not careful with JSON input)
        try:
            valence = float(valence)
            energy = float(energy)
        except (ValueError, TypeError):
            logger.warning(f"Could not convert valence/energy to float for track {liked_track.track.id}. V: {valence}, E: {energy}. Skipping.")
            continue


        mood = "Undefined"
        if valence >= VALENCE_THRESHOLD_HIGH and energy >= ENERGY_THRESHOLD_HIGH:
            mood = "Happy/Energetic"
        elif valence >= VALENCE_THRESHOLD_HIGH and energy < ENERGY_THRESHOLD_LOW: # Using LOW for clear separation
            mood = "Happy/Calm"
        elif valence < VALENCE_THRESHOLD_LOW and energy < ENERGY_THRESHOLD_LOW:
            mood = "Sad/Calm"
        elif valence < VALENCE_THRESHOLD_LOW and energy >= ENERGY_THRESHOLD_HIGH:
            mood = "Sad/Energetic" # (e.g., Angry/Tense)
        elif VALENCE_THRESHOLD_LOW <= valence < VALENCE_THRESHOLD_HIGH and energy >= ENERGY_THRESHOLD_HIGH:
            mood = "Neutral/Energetic"
        elif VALENCE_THRESHOLD_LOW <= valence < VALENCE_THRESHOLD_HIGH and energy < ENERGY_THRESHOLD_LOW:
            mood = "Neutral/Calm"
        elif valence >= VALENCE_THRESHOLD_HIGH and ENERGY_THRESHOLD_LOW <= energy < ENERGY_THRESHOLD_HIGH:
            mood = "Happy/Neutral Energy"
        elif valence < VALENCE_THRESHOLD_LOW and ENERGY_THRESHOLD_LOW <= energy < ENERGY_THRESHOLD_HIGH:
            mood = "Sad/Neutral Energy"
        else: # Mid-range for both
            mood = "Neutral"

        mood_counts[mood] += 1

    result_list = [{'mood': mood, 'count': count} for mood, count in mood_counts.items() if count > 0]

    if not result_list and liked_tracks_with_features.exists():
        logger.warning(f"No moods could be determined for user {user.id} despite having liked tracks with features. This might indicate issues with feature values or thresholds.")
        # Potentially add a default "Not Enough Data" category if all tracks had missing/invalid features
        result_list.append({'mood': 'Not Enough Data', 'count': liked_tracks_with_features.count()})
    elif not liked_tracks_with_features.exists():
         logger.info(f"User {user.id} has no liked tracks with audio features to analyze for mood distribution.")
         result_list.append({'mood': 'No Data', 'count': 0})


    cache.set(cache_key, result_list, CACHE_TIMEOUT_MEDIUM)
    logger.info(f"Cached mood distribution for user {user.id}")
    return result_list


def get_user_genre_distribution(user: CustomUser, top_n: int = 10) -> list:
    """
    Calculates the distribution of top N genres for tracks liked by the user.
    Args:
        user: The CustomUser instance.
        top_n: The number of top genres to return.
    Returns:
        A list of dictionaries, e.g., [{'genre': 'Electronic', 'count': X}, ...].
    """
    cache_key = f"analytics_genre_dist_{user.id}_{top_n}n"
    cached_data = cache.get(cache_key)
    if cached_data:
        logger.info(f"Returning cached genre distribution for user {user.id}")
        return cached_data

    logger.info(f"Calculating genre distribution for user {user.id}")

    # UserLikedTrack -> Track -> Genre (Track.genres is FK to Genre)
    liked_genres = UserLikedTrack.objects.filter(user=user)\
                                .select_related('track__genres')\
                                .values('track__genres__name')\
                                .annotate(count=Count('track__genres__name'))\
                                .filter(track__genres__name__isnull=False)\
                                .order_by('-count')[:top_n]
                                # Directly get name and count

    result_list = [
        {'genre': item['track__genres__name'], 'count': item['count']}
        for item in liked_genres if item['track__genres__name'] # Ensure genre name is not None
    ]

    if not result_list and UserLikedTrack.objects.filter(user=user).exists():
        logger.info(f"User {user.id} has liked tracks, but no genre data could be aggregated (possibly missing genre links).")
        result_list.append({'genre': 'No Genre Data', 'count': 0}) # Or count of tracks with missing genres
    elif not UserLikedTrack.objects.filter(user=user).exists():
        logger.info(f"User {user.id} has no liked tracks to analyze for genre distribution.")
        result_list.append({'genre': 'No Liked Tracks', 'count': 0})


    cache.set(cache_key, result_list, CACHE_TIMEOUT_MEDIUM)
    logger.info(f"Cached genre distribution for user {user.id}")
    return result_list


def get_music_discovery_insights(user: CustomUser, days: int = 30) -> dict:
    """
    Provides insights into music discovery, such as new genres encountered recently.
    Args:
        user: The CustomUser instance.
        days: The number of past days to consider "recent".
    Returns:
        A dictionary containing discovery insights, e.g., {'newly_discovered_genres': ['Synthwave', ...], 'new_genre_count': X}.
    """
    cache_key = f"analytics_discovery_insights_{user.id}_{days}d"
    cached_data = cache.get(cache_key)
    if cached_data:
        logger.info(f"Returning cached music discovery insights for user {user.id}")
        return cached_data

    logger.info(f"Calculating music discovery insights for user {user.id} for period {days} days.")

    recent_period_start_date = timezone.now().date() - timedelta(days=days)

    # Genres from recently liked tracks
    recent_liked_genres_qs = UserLikedTrack.objects.filter(
        user=user,
        liked_at__date__gte=recent_period_start_date
    ).select_related('track__genres').filter(track__genres__name__isnull=False)

    recent_genres = set(item.track.genres.name for item in recent_liked_genres_qs if item.track.genres)

    # Genres from tracks liked before the recent period
    older_liked_genres_qs = UserLikedTrack.objects.filter(
        user=user,
        liked_at__date__lt=recent_period_start_date
    ).select_related('track__genres').filter(track__genres__name__isnull=False)

    older_genres = set(item.track.genres.name for item in older_liked_genres_qs if item.track.genres)

    newly_discovered_genres = list(recent_genres - older_genres)

    # Handle case where user might be new and all liked genres are recent
    # In this scenario, if older_genres is empty and recent_genres is not, all recent_genres are "new".
    # The set difference already handles this correctly. If older_genres is empty, recent_genres - older_genres = recent_genres.

    insights = {
        'newly_discovered_genres': newly_discovered_genres,
        'new_genre_count': len(newly_discovered_genres),
        'period_days': days
    }

    if not UserLikedTrack.objects.filter(user=user).exists():
        logger.info(f"User {user.id} has no liked tracks for discovery insights.")
        insights['message'] = "No liked track data available."
        insights['newly_discovered_genres'] = []
        insights['new_genre_count'] = 0


    cache.set(cache_key, insights, CACHE_TIMEOUT_MEDIUM)
    logger.info(f"Cached music discovery insights for user {user.id}")
    return insights
