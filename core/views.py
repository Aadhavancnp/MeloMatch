"""
Core views for MeloMatch.

Includes both sync views for simple pages and async views for 
API-heavy operations like the dashboard.
"""
import asyncio
import logging
from collections import Counter

from asgiref.sync import sync_to_async
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import render, redirect

from core.forms import ContactForm
from core.models import FAQItem
from ecommerce.models import Cart
from music.models import Playlist, Track
from services.async_utils import gather_with_exceptions
from services.spotify_service.client import (
    get_spotify_client,
    async_get_spotify_client,
    async_get_user_top_tracks,
    async_get_user_recently_played,
    async_get_user_playlists,
    async_calculate_listening_time,
    async_get_favorite_genre,
    async_get_or_create_track,
)
from subscription.models import Subscription
from users.models import UserActivity

logger = logging.getLogger(__name__)


def home(request):
    """Home page view."""
    return render(request, "core/home.html")


def about_us(request):
    """About us page view."""
    return render(request, 'core/about_us.html')


def faq_list(request):
    """FAQ listing page view."""
    faqs = FAQItem.objects.all()
    return render(request, 'core/faq_list.html', {'faqs': faqs})


def contact_us(request):
    """Contact form view."""
    if request.method == 'POST':
        form = ContactForm(request.POST)
        if form.is_valid():
            form.save()
            messages.success(
                request, 'Your message has been sent successfully!')
            return redirect('contact')
    else:
        form = ContactForm()
    return render(request, 'core/contact_us.html', {'form': form})


@login_required(login_url="/users/login/")
async def dashboard(request):
    """
    Async dashboard view.

    Makes parallel API calls to Spotify for better performance.
    All ORM operations are wrapped with sync_to_async for SQLite compatibility.
    """
    # Get user asynchronously to avoid sync context error
    @sync_to_async
    def get_user():
        return request.user
    
    user = await get_user()

    # Get Spotify client asynchronously
    sp = await async_get_spotify_client(request)

    # Execute independent API calls in parallel
    results = await gather_with_exceptions(
        async_get_user_top_tracks(request),
        async_get_user_recently_played(request),
        async_get_user_playlists(request),
        return_exceptions=True
    )

    # Process results with error handling
    top_tracks = results[0] if not isinstance(results[0], Exception) else []
    recently_played = results[1] if not isinstance(
        results[1], Exception) else []

    if isinstance(results[0], Exception):
        logger.error(f"Error getting top_tracks: {results[0]}")
    if isinstance(results[1], Exception):
        logger.error(f"Error getting recently_played: {results[1]}")
    if isinstance(results[2], Exception):
        logger.error(f"Error syncing playlists: {results[2]}")

    # Ensure lists are valid
    top_tracks = top_tracks or []
    recently_played = recently_played or []

    # Process recently played tracks
    most_repeat_id = None
    most_repeat = None

    if recently_played:
        track_counter = Counter([track['id'] for track in recently_played])
        if track_counter:
            most_repeat_id = track_counter.most_common(1)[0][0]
            most_repeat = next(
                (track for track in recently_played if track['id']
                 == most_repeat_id), None
            )
    else:
        logger.warning(
            "Recently played is empty. Some dashboard features might be limited.")

    # Remove duplicates while preserving the most repeated track
    recently_played_unique_list = []
    if recently_played:
        unique_tracks = {}
        for track_item in recently_played:
            if most_repeat_id and track_item['id'] == most_repeat_id and track_item['id'] in unique_tracks:
                continue
            unique_tracks[track_item['id']] = track_item
        recently_played_unique_list = list(unique_tracks.values())

    # Trigger feature extraction for tracks without audio features
    # This helps improve recommendations over time
    @sync_to_async
    def trigger_feature_extraction():
        from music.tasks import extract_track_features_task
        from django.db.models import Q

        # Collect all track IDs we care about
        all_track_ids = set()
        for track in top_tracks[:10]:  # Top 10 tracks
            all_track_ids.add(track['id'])
        for track in recently_played_unique_list[:10]:  # Recent 10 tracks
            all_track_ids.add(track['id'])

        # Find tracks in database without features
        tracks_needing_features = Track.objects.filter(
            spotify_id__in=all_track_ids
        ).filter(
            Q(audio_features__isnull=True) | Q(audio_features={})
        ).exclude(
            preview_url__isnull=True
        ).exclude(
            preview_url=''
        )

        # Trigger extraction for up to 5 tracks at a time
        count = 0
        for track in tracks_needing_features[:5]:
            extract_track_features_task.delay(track.id)
            count += 1

        if count > 0:
            logger.info(f"Triggered feature extraction for {count} tracks")

    # Trigger extraction in background (don't wait for it)
    try:
        await trigger_feature_extraction()
    except Exception as e:
        logger.warning(f"Failed to trigger feature extraction: {e}")

    # Build recommendations from user's listening data
    # Use top tracks and recently played (we already have this data from Spotify)
    # Shuffle and deduplicate to create a "recommended for you" list
    recommendation_track_dicts = []
    seen_ids = set()
    
    # Mix top tracks and recently played for variety
    all_candidate_tracks = []
    
    # Add top tracks (high priority - user likes these)
    for track in top_tracks[:15]:
        if track.get('id') and track['id'] not in seen_ids:
            all_candidate_tracks.append(track)
            seen_ids.add(track['id'])
    
    # Add recently played (shows current interest)  
    for track in recently_played_unique_list[:15]:
        if track.get('id') and track['id'] not in seen_ids:
            all_candidate_tracks.append(track)
            seen_ids.add(track['id'])
    
    # Take first 10 as recommendations
    recommendation_track_dicts = all_candidate_tracks[:10]
    
    # Get track IDs for database query
    track_spotify_ids_for_query = [t['id'] for t in recommendation_track_dicts]

    @sync_to_async
    def get_existing_track_ids():
        """Get IDs of tracks that already exist in the database."""
        return set(
            Track.objects.filter(spotify_id__in=track_spotify_ids_for_query)
            .values_list('spotify_id', flat=True)
        )

    existing_ids = await get_existing_track_ids()
    
    # Create missing tracks in PARALLEL (not sequential)
    missing_track_dicts = [t for t in recommendation_track_dicts if t['id'] not in existing_ids]
    
    if missing_track_dicts and sp:
        # Create all missing tracks concurrently
        create_tasks = [
            async_get_or_create_track(track_dict, sp)
            for track_dict in missing_track_dicts
        ]
        await gather_with_exceptions(*create_tasks, return_exceptions=True)

    # Async ORM queries using sync_to_async
    @sync_to_async
    def get_recommended_tracks():
        return list(
            Track.objects.filter(spotify_id__in=track_spotify_ids_for_query)
            .select_related('genres')
            .prefetch_related('artists')
        )

    @sync_to_async
    def get_recent_activities():
        return list(UserActivity.objects.filter(user=user).order_by('-timestamp')[:5])

    @sync_to_async
    def get_subscription():
        return Subscription.objects.filter(user=user).select_related('plan').first()

    @sync_to_async
    def get_user_playlists_db():
        return list(Playlist.objects.filter(user=user).prefetch_related('tracks'))

    @sync_to_async
    def get_cart_count():
        try:
            cart = Cart.objects.get(user=user)
            return cart.items.count()
        except Cart.DoesNotExist:
            return 0

    # Parallel execution of ALL database queries and API calls
    db_results = await gather_with_exceptions(
        get_recommended_tracks(),
        get_recent_activities(),
        get_subscription(),
        get_user_playlists_db(),
        get_cart_count(),
        async_calculate_listening_time(sp, recently_played_unique_list),
        async_get_favorite_genre(sp, top_tracks),
        return_exceptions=True
    )

    # Extract results with error handling
    recommended_tracks = db_results[0] if not isinstance(
        db_results[0], Exception) else []
    recent_activities = db_results[1] if not isinstance(
        db_results[1], Exception) else []
    subscription = db_results[2] if not isinstance(
        db_results[2], Exception) else None
    user_playlists = db_results[3] if not isinstance(
        db_results[3], Exception) else []
    cart_count = db_results[4] if not isinstance(
        db_results[4], Exception) else 0
    listening_time = db_results[5] if not isinstance(
        db_results[5], Exception) else 0.0
    favorite_genre = db_results[6] if not isinstance(
        db_results[6], Exception) else None

    context = {
        'recommended_tracks': recommended_tracks,
        'recent_activities': recent_activities,
        'subscription': subscription,
        'user_playlists': user_playlists,
        'listening_time': listening_time,
        'favorite_genre': favorite_genre,
        'playlist_count': len(user_playlists),
        'cart_count': cart_count,
    }

    return render(request, 'core/dashboard.html', context)
