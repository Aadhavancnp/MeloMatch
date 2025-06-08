from collections import Counter

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import render, redirect
from django.views.decorators.cache import cache_page

from core.forms import ContactForm
from core.models import FAQItem
from music.models import Playlist, Track
# Updated import for Spotify service
from services.spotify_service.client import get_spotify_client, get_user_playlists, get_user_top_tracks, \
    get_user_recently_played, calculate_listening_time, get_favorite_genre
    # get_recommendations removed from here
from services.recommendation_service.recommender import get_hybrid_recommendations # Added
from subscription.models import Subscription
from users.models import UserActivity
from channels.layers import get_channel_layer
from asgiref.sync import async_to_sync
import logging # For logging in trigger function

logger = logging.getLogger(__name__)

def trigger_recommendation_notification(user, message, recommendations_summary=None):
    try:
        channel_layer = get_channel_layer()
        group_name = f"user_{user.id}_recommendations"

        logger.info(f"Triggering recommendation notification for group {group_name}")
        async_to_sync(channel_layer.group_send)(
            group_name,
            {
                "type": "recommendation.notification", # This will call recommendation_notification method in consumer
                "message": message,
                "recommendations_summary": recommendations_summary or []
            }
        )
        logger.info(f"Successfully sent notification to group {group_name}")
    except Exception as e:
        logger.error(f"Error triggering recommendation notification for user {user.id}: {str(e)}")


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

    # Get recommendations using the new hybrid recommender
    # The hybrid recommender needs a seed_track_id.
    # We can use the most recently played track or the most repeated track as a seed.
    seed_track_for_hybrid_recs = None
    if recently_played:
        seed_track_for_hybrid_recs = recently_played[0]['id'] # Use most recent
    elif top_tracks: # Fallback to a top track if no recently played
        seed_track_for_hybrid_recs = top_tracks[0]['id']

    recommended_tracks = [] # Initialize
    if seed_track_for_hybrid_recs:
        # get_hybrid_recommendations returns a list of Track objects
        recommendation_context = f"dashboard_general_seed_{seed_track_for_hybrid_recs}"
        recommended_tracks = get_hybrid_recommendations(
            request.user,
            seed_track_id=seed_track_for_hybrid_recs,
            num_recommendations=10,
            context=recommendation_context
        )

    # If recommended_tracks are already Track model instances, no need for further DB query for them.
    # The ThreadPoolExecutor part for recommended_tracks_future can be removed or adapted if
    # get_hybrid_recommendations is already efficient enough or if it's called directly.
    # For now, let's assume get_hybrid_recommendations returns fully formed Track objects.

    # Execute remaining independent operations in parallel
    with ThreadPoolExecutor(max_workers=4) as executor:
        # Start all tasks
        # track_ids = [track.spotify_id for track in recommended_tracks] # If recommended_tracks are Track objects
        # recommended_tracks_future = executor.submit( # This might be redundant if recommended_tracks are already fetched
        #     lambda: list(Track.objects.filter(spotify_id__in=track_ids)
        #                  .select_related('genres')
        #                  .prefetch_related('artists'))
        # )
        listening_time_future = executor.submit(calculate_listening_time, sp, recently_played, user_id_for_cache=request.user.id) # Pass user_id for cache key
        favorite_genre_future = executor.submit(get_favorite_genre, sp, top_tracks, user_id_for_cache=request.user.id) # Pass user_id for cache key
        recent_activities_future = executor.submit(
            lambda: UserActivity.objects.filter(user=user).order_by('-timestamp')[:5]
        )
        subscription_future = executor.submit(
            lambda: Subscription.objects.filter(user=user).first()
        )
        user_playlists_future = executor.submit(
            # Ensure prefetching is effective for template needs
            lambda: Playlist.objects.filter(user=user).prefetch_related('tracks__artists', 'tracks__genres')
        )

        # Get results
        # recommended_tracks are already fetched above if seed_track_for_hybrid_recs was valid
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

    # PoC: Trigger a notification when the dashboard is loaded for an authenticated user
    if request.user.is_authenticated:
        # Create a dummy summary for the notification
        dummy_recs_summary = []
        if recommended_tracks: # Use actual recommended tracks if available
            for track in recommended_tracks[:2]: # Send summary of first 2
                 dummy_recs_summary.append({'title': track.title, 'artist': track.artists.first().name if track.artists.exists() else 'Unknown Artist'})
        else: # Fallback dummy data if no recommendations yet
            dummy_recs_summary = [{'title': 'Awesome New Song'}, {'title': 'Another Great Hit'}]

        trigger_recommendation_notification(
            request.user,
            "Fresh recommendations just for you!",
            recommendations_summary=dummy_recs_summary
        )

    return render(request, 'core/dashboard.html', context)


from django.urls import reverse

@login_required(login_url="/users/login/")
def analytics_dashboard(request):
    """
    View to render the analytics dashboard page.
    JavaScript on the client-side will fetch data from API endpoints.
    """
    context = {
        'listening_trend_api_url': reverse('analytics_api:listening_time_trend'),
        'mood_dist_api_url': reverse('analytics_api:mood_distribution'),
        'genre_dist_api_url': reverse('analytics_api:genre_distribution'),
        'discovery_insights_api_url': reverse('analytics_api:discovery_insights'),
    }
    return render(request, 'core/analytics_dashboard.html', context)
