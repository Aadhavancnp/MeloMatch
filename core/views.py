from collections import Counter

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import render, redirect
from django.views.decorators.cache import cache_page

from core.forms import ContactForm
from core.models import FAQItem
from music.models import Playlist, Track
from music.spotify import get_recommendations, get_spotify_client, get_user_playlists, get_user_top_tracks, \
    get_user_recently_played, calculate_listening_time, get_favorite_genre
from subscription.models import Subscription
from users.models import UserActivity


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
        # Start all tasks - pass request instead of sp
        playlists_future = executor.submit(get_user_playlists, request)
        top_tracks_future = executor.submit(get_user_top_tracks, request)
        recently_played_future = executor.submit(get_user_recently_played, request)

        # Get results as they complete, with error handling
        try:
            top_tracks = top_tracks_future.result()
        except Exception as e:
            print(f"Error getting top_tracks from future: {type(e).__name__} - {e}")
            top_tracks = [] # Default to empty list on error

        try:
            recently_played = recently_played_future.result()
        except Exception as e:
            print(f"Error getting recently_played from future: {type(e).__name__} - {e}")
            recently_played = [] # Default to empty list on error

        try:
            # Ensure playlists are fetched (result not directly used but cached)
            playlists_results = playlists_future.result()
            if playlists_results is None: # Functions now return [] on error, so this might be if playlists_results == []
                 print("Playlists future returned None or empty, check for errors in get_user_playlists")
        except Exception as e:
            print(f"Error getting playlists from future: {type(e).__name__} - {e}")
            # playlists_results will not be assigned if an exception occurs before this.
            # No need to default it here as it's not directly used in context below this block.

    # Ensure recently_played and top_tracks are not empty before proceeding with logic that depends on them
    if not recently_played:
        most_repeat_id = None
        most_repeat = None
        # Handle cases where recently_played might be empty to avoid errors
        # For example, provide default values or skip logic that depends on it
        print("Warning: recently_played is empty. Some dashboard features might be limited.")
    else:
        track_counter = Counter([track['id'] for track in recently_played])
        if track_counter: # Ensure track_counter is not empty
            most_repeat_id = track_counter.most_common(1)[0][0]
            most_repeat = next((track for track in recently_played if track['id'] == most_repeat_id), None)
        else:
            most_repeat_id = None
            most_repeat = None
            print("Warning: track_counter for recently_played is empty.")
    # Use dict comprehension to remove duplicates while preserving the most repeated track
    if recently_played: # Ensure recently_played is not empty
        unique_tracks = {}
        for track_item in recently_played: # Renamed track to track_item to avoid conflict
            if most_repeat_id and track_item['id'] == most_repeat_id and track_item['id'] in unique_tracks:
                continue  # Skip duplicates of most_repeat
            unique_tracks[track_item['id']] = track_item
        recently_played_unique_list = list(unique_tracks.values())
    else:
        recently_played_unique_list = []

    # Get recommendations
    recommendation_ids = [] # Default to empty list
    if recently_played_unique_list: # Check if list is not empty
        recommendation_ids = get_recommendations(recently_played_unique_list[0]['id'], top_tracks + recently_played_unique_list)
        if not recommendation_ids and most_repeat: # Ensure most_repeat is not None
            recommendation_ids = get_recommendations(most_repeat['id'], top_tracks + recently_played_unique_list)
    elif top_tracks: # Fallback: if no recently played, try with top_tracks if available
        # This part needs a decision: what should be the seed if recently_played is empty?
        # For now, let's assume we need a seed from recently_played or most_repeat.
        # If both are empty/None, recommendation_ids will remain [].
        # Alternatively, could use a top track as seed:
        # recommendation_ids = get_recommendations(top_tracks[0]['id'], top_tracks) # Example
        pass


    # Ensure recommendation_ids is a list of dicts with 'id' before list comprehension
    if recommendation_ids and isinstance(recommendation_ids, list) and all(isinstance(rec, dict) and 'id' in rec for rec in recommendation_ids):
        recommendation_ids_processed = list({rec['id']: rec for rec in recommendation_ids}.values())
    else:
        recommendation_ids_processed = []

    # Execute remaining independent operations in parallel
    # Note: sp is defined in the outer scope of dashboard view.
    # For functions called by executor that need sp, and are not refactored to take request,
    # they will use this sp. This could be problematic if sp is not thread-safe.
    # calculate_listening_time and get_favorite_genre still expect 'sp'.
    # Ideally, they should also be refactored like get_user_top_tracks etc.
    # For this subtask, per plan, only get_user_playlists, get_user_top_tracks, get_user_recently_played were refactored.

    with ThreadPoolExecutor(max_workers=4) as executor:
        # Start all tasks
        # Corrected: recommendation_ids_processed is the list of dicts
        track_spotify_ids_for_query = [rec['id'] for rec in recommendation_ids_processed]
        recommended_tracks_future = executor.submit(
            lambda: list(Track.objects.filter(spotify_id__in=track_spotify_ids_for_query)
                         .select_related('genres')
                         .prefetch_related('artists'))
        )
        # These still use 'sp' from the view's main scope.
        listening_time_future = executor.submit(calculate_listening_time, sp, recently_played_unique_list)
        favorite_genre_future = executor.submit(get_favorite_genre, sp, top_tracks)
        recent_activities_future = executor.submit(
            lambda: UserActivity.objects.filter(user=user).order_by('-timestamp')[:5] # This is DB bound, less of an issue with sp
        )
        subscription_future = executor.submit(
            lambda: Subscription.objects.filter(user=user).first()
        )
        user_playlists_future = executor.submit(
            lambda: Playlist.objects.filter(user=user).prefetch_related('tracks')
        )

        # Get results
        recommended_tracks = recommended_tracks_future.result()
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
