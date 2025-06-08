from collections import Counter

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import render, redirect
from django.views.decorators.cache import cache_page

from core.forms import ContactForm
from core.models import FAQItem
from music.models import Playlist, Track
# Updated import for Spotify service client functions
from services.spotify_service.client import (
    get_spotify_client,
    get_user_playlists, # Assuming this function exists in the new client or was similar
    get_user_top_tracks, # Assuming this function exists
    get_user_recently_played, # Assuming this function exists
    calculate_listening_time, # Assuming this function exists
    get_favorite_genre # Assuming this function exists
)
# get_recommendations was moved and refactored.
from services.recommendation_service.recommender import get_hybrid_recommendations # Use the new hybrid recommender
from subscription.models import Subscription
from users.models import UserActivity, CustomUser # CustomUser for type hinting if needed, UserActivity for model access
from music.models import UserLikedTrack # For seed selection fallback
import logging # For logging in dashboard view

logger = logging.getLogger(__name__) # Define logger for this file if not already present at top


def home(request):
    return render(request, "core/home.html")


def about_us(request):
    return render(request, 'core/about_us.html')


def faq_list(request):
    faqs = FAQItem.objects.all()
    return render(request, 'core/faq_list.html', {'faqs': faqs})


def contact_us(request):
    if request.method == 'POST':
        form = ContactForm(request.POST)
        if form.is_valid():
            form.save()
            messages.success(request, 'Your message has been sent successfully!')
            return redirect('contact')
    else:
        form = ContactForm()
    return render(request, 'core/contact_us.html', {'form': form})


from concurrent.futures import ThreadPoolExecutor


@login_required(login_url="/users/login/")
# @cache_page(900)
def dashboard(request):
    user = request.user
    sp = get_spotify_client(request)

    # Execute independent API calls in parallel
    with ThreadPoolExecutor(max_workers=4) as executor:
        # Start all tasks
        playlists_future = executor.submit(get_user_playlists, sp, request)
        top_tracks_future = executor.submit(get_user_top_tracks, sp)
        recently_played_future = executor.submit(get_user_recently_played, sp)

        # Get results as they complete
        top_tracks = top_tracks_future.result()
        recently_played = recently_played_future.result()
        # Ensure playlists are fetched (result not directly used but cached)
        playlists_future.result()

    track_counter = Counter([track['id'] for track in recently_played])
    most_repeat_id = track_counter.most_common(1)[0][0]
    most_repeat = next(track for track in recently_played if track['id'] == most_repeat_id)

    # Use dict comprehension to remove duplicates while preserving the most repeated track
    unique_tracks = {}
    for track in recently_played:
        if track['id'] == most_repeat_id and track['id'] in unique_tracks:
            continue  # Skip duplicates of most_repeat
        unique_tracks[track['id']] = track

    recently_played = list(unique_tracks.values())

    # --- New Recommendation Logic ---
    recommended_tracks = []
    seed_track_for_hybrid_recs = None
    seed_track_spotify_id = None

    # 1. Try to get seed from UserLikedTrack (most recent like)
    try:
        latest_liked_entry = UserLikedTrack.objects.filter(user=user).order_by('-liked_at').select_related('track').first()
        if latest_liked_entry and latest_liked_entry.track:
            seed_track_for_hybrid_recs = latest_liked_entry.track
            seed_track_spotify_id = seed_track_for_hybrid_recs.spotify_id
            logger.info(f"Dashboard seed: UserLikedTrack '{seed_track_for_hybrid_recs.title}' (ID: {seed_track_spotify_id})")
    except Exception as e:
        # This can happen if UserLikedTrack table doesn't exist or other DB errors
        logger.error(f"Dashboard: Error fetching UserLikedTrack for seed: {e}")

    # 2. If no seed from likes, try from recently_played (if available and has items)
    if not seed_track_for_hybrid_recs and recently_played:
        # recently_played is a list of dicts, each dict should have an 'id' (spotify_id)
        first_recent_track_data = recently_played[0]
        seed_track_spotify_id = first_recent_track_data.get('id')
        if seed_track_spotify_id:
            try:
                # We need a Track object for get_hybrid_recommendations's content-based part
                seed_track_for_hybrid_recs = Track.objects.get(spotify_id=seed_track_spotify_id)
                logger.info(f"Dashboard seed: recently_played '{seed_track_for_hybrid_recs.title}' (ID: {seed_track_spotify_id})")
            except Track.DoesNotExist:
                logger.warning(f"Dashboard seed: Track with Spotify ID {seed_track_spotify_id} from recently_played not found in DB.")
                seed_track_spotify_id = None # Reset if track object not found
        else:
            logger.info("Dashboard seed: No 'id' found in first recently_played track data.")

    # 3. If a seed track (Spotify ID) is determined, call get_hybrid_recommendations
    if seed_track_spotify_id:
        try:
            # The get_hybrid_recommendations function itself handles fetching the Track object by spotify_id.
            # So we only need to pass the spotify_id.
            recommended_tracks = get_hybrid_recommendations(
                request.user,
                seed_track_id=seed_track_spotify_id,
                num_recommendations=12, # e.g., for a 4-column grid on dashboard
                context=f"dashboard_seed_{seed_track_spotify_id}"
            )
            logger.info(f"Dashboard: Got {len(recommended_tracks)} hybrid recommendations for seed {seed_track_spotify_id}")
        except Exception as e:
            logger.error(f"Dashboard: Error calling get_hybrid_recommendations: {e}", exc_info=True)
            recommended_tracks = [] # Ensure it's an empty list on error
    else:
        logger.info("Dashboard: No suitable seed track found. No recommendations will be generated.")
        recommended_tracks = []
    # --- End New Recommendation Logic ---


    # Simpler execution for other dashboard items, as recommended_tracks is now handled above.
    with ThreadPoolExecutor(max_workers=3) as executor: # Reduced workers as one task is done
        listening_time_future = executor.submit(calculate_listening_time, sp, recently_played) # recently_played is from earlier call
        favorite_genre_future = executor.submit(get_favorite_genre, sp, top_tracks) # top_tracks is from earlier call
        recent_activities_future = executor.submit(
            lambda: UserActivity.objects.filter(user=user).order_by('-timestamp')[:5]
        )
        subscription_future = executor.submit(
            lambda: Subscription.objects.filter(user=user).first()
        )
        user_playlists_future = executor.submit(
            lambda: Playlist.objects.filter(user=user).prefetch_related('tracks')
        )

        # Get results
        # recommended_tracks = recommended_tracks_future.result() # Commented out
        listening_time = listening_time_future.result()
        favorite_genre = favorite_genre_future.result()
        recent_activities = recent_activities_future.result()
        subscription = subscription_future.result()
        user_playlists = user_playlists_future.result()

    context = {
        'recommended_tracks': recommended_tracks,
        'recent_activities': recent_activities,
        'subscription': subscription,
        'user_playlists': user_playlists,
        'listening_time': listening_time,
        'favorite_genre': favorite_genre,
        'playlist_count': len(user_playlists),
    }
    return render(request, 'core/dashboard.html', context)
