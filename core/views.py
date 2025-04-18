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

    # Get recommendations
    recommendation_ids = get_recommendations(recently_played[0]['id'], top_tracks + recently_played)
    if not recommendation_ids:
        recommendation_ids = get_recommendations(most_repeat['id'], top_tracks + recently_played)

    recommendation_ids = list({track['id']: track for track in recommendation_ids}.values())

    # Execute remaining independent operations in parallel
    with ThreadPoolExecutor(max_workers=4) as executor:
        # Start all tasks
        track_ids = [track['id'] for track in recommendation_ids]
        recommended_tracks_future = executor.submit(
            lambda: list(Track.objects.filter(spotify_id__in=track_ids)
                         .select_related('genres')
                         .prefetch_related('artists'))
        )
        listening_time_future = executor.submit(calculate_listening_time, sp, recently_played)
        favorite_genre_future = executor.submit(get_favorite_genre, sp, top_tracks)
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
