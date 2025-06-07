from datetime import datetime

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse, HttpResponseForbidden
from django.shortcuts import render, redirect, get_object_or_404
from django.urls import reverse
from django.views.decorators.cache import cache_page

from users.models import UserActivity
from .models import Playlist, Track
# Updated import for Spotify service
from services.spotify_service.client import get_spotify_client, \
    search_jiosaavn, get_track_details_jiosaavn, get_user_top_tracks, get_user_recently_played, create_playlist_spotify, \
    search_tracks, get_or_create_playlist, get_playlist_tracks, extract_audio_features, download_preview, \
# get_recommendations was moved to recommendation_service, other spotify functions remain
    add_tracks_to_playlist_spotify, delete_playlist_spotify, remove_tracks_from_playlist_spotify, \
    get_artist_details, get_artist_albums, get_artist_top_tracks
# Import for new hybrid recommender
from services.recommendation_service.recommender import get_hybrid_recommendations
from .utils import convert_image_to_base64
from .models import Cart, CartItem, Order, OrderItem
from django.db import transaction


@login_required
@transaction.atomic
def place_order(request):
    cart = Cart.objects.filter(user=request.user).first()
    if not cart or not cart.items.exists():
        messages.error(request, "Your cart is empty. Please add items before placing an order.")
        return redirect('view_cart')

    # Create the order
    order = Order.objects.create(user=request.user, total_price=0) # Initialize total_price

    total_order_price = 0
    order_items_for_template = []

    for cart_item in cart.items.all():
        track_price = 1.29  # Placeholder price
        order_item = OrderItem.objects.create(
            order=order,
            track=cart_item.track,
            quantity=cart_item.quantity,
            price=track_price
        )
        total_order_price += order_item.quantity * order_item.price
        order_items_for_template.append(order_item)

    order.total_price = round(total_order_price, 2)
    order.save()

    # Clear the cart
    cart.items.all().delete() # Deletes all CartItems associated with the cart
    # Optionally, delete the cart itself if it's always empty after an order
    # cart.delete()

    messages.success(request, "Your order has been placed successfully!")
    UserActivity.objects.create(
        user=request.user,
        activity_type='place_order',
        description=f"Placed order with ID: {order.id}, Total: ${order.total_price}"
    )
    # Instead of rendering order_confirmation, redirect to Stripe checkout
    # The order_confirmation logic will now be part of the payment_success view or order history
    return redirect('create_checkout_session', order_id=order.id)


@login_required
def order_history(request):
    orders = Order.objects.filter(user=request.user)\
                          .prefetch_related('items__track__artists')\
                          .order_by('-created_at')
    # Note: 'items__track__album__artist' might be too deep or specific.
    # The template 'order_history.html' shows: item.track.title
    # Let's assume 'items__track' is sufficient for now unless album/artist details are shown.
    # If item.track.album.name or item.track.album.artist.name were used, then deeper prefetch is needed.
    # The current template only shows item.track.title, so 'items__track' is good.
    # Adding 'items__track__artists' because `track.artists_names` might be used or is a common access pattern.
    context = {
        'orders': orders
    }
    return render(request, 'music/order_history.html', context)


@login_required
def view_cart(request):
    cart, created = Cart.objects.get_or_create(user=request.user)
    cart_items = cart.items.select_related('track__genres')\
                           .prefetch_related('track__artists').all()
    # The template 'cart.html' uses: item.track.title, item.track.album, item.track.price (default)
    # 'track__genres' for primary_genre if used. 'track__album' for album name.
    # 'track__artists' for artists_names.
    # For item.track.album, select_related('track__album') would be better.
    # Let's refine:
    cart_items = cart.items.select_related('track', 'track__genres')\
                           .prefetch_related('track__artists').all()


    total_price = sum(item.quantity * 1.29 for item in cart_items) # Assuming 1.29 per track

    context = {
        'cart_items': cart_items,
        'total_price': round(total_price, 2)
    }
    return render(request, 'music/cart.html', context)


@login_required
def add_to_cart(request, track_id):
    track = get_object_or_404(Track, spotify_id=track_id)
    cart, created = Cart.objects.get_or_create(user=request.user)
    cart_item, created = CartItem.objects.get_or_create(cart=cart, track=track)

    if not created:
        cart_item.quantity += 1
        cart_item.save()
        messages.success(request, f"'{track.title}' quantity updated in your cart.")
    else:
        messages.success(request, f"'{track.title}' added to your cart.")

    UserActivity.objects.create(
        user=request.user,
        activity_type='add_to_cart',
        description=f"Added track {track.title} to cart."
    )
    return redirect('view_cart')


@login_required
def remove_from_cart(request, item_id):
    cart_item = get_object_or_404(CartItem, id=item_id, cart__user=request.user)
    track_title = cart_item.track.title
    cart_item.delete()
    messages.success(request, f"'{track_title}' removed from your cart.")
    UserActivity.objects.create(
        user=request.user,
        activity_type='remove_from_cart',
        description=f"Removed track {track_title} from cart."
    )
    return redirect('view_cart')


# from ratelimit.decorators import ratelimit # Import removed
# @ratelimit(key='user_or_ip', rate='20/m', block=True) # Decorator removed
@login_required
def search(request):
    query = request.GET.get('q', '')
    sort = request.GET.get('sort', '')
    # Add a way to add to cart from search results, if desired.
    # For now, cart actions are separate.

    all_results = [] # Will hold combined results

    from services.jiosaavn_service.api_client import search_songs as search_jiosaavn_songs # Updated Import JioSaavn search

    all_results = []
    if query:
        # Spotify Search
        sp = get_spotify_client(request)
        spotify_db_tracks = search_tracks(sp, query) # Returns Track model instances

        for track in spotify_db_tracks:
            # Standardize Spotify Track model instance to common dict structure
            all_results.append({
                'id': track.spotify_id,
                'title': track.title,
                'artists': track.artists_names, # This is already a list of strings from model property
                'album': track.album,
                'image_url': track.image_url,
                'preview_url': track.preview_url,
                'source': 'Spotify',
                'is_db_track': True, # Flag to identify it's a full Track model instance from our DB
                'popularity': track.popularity, # Spotify specific
                'release_date': track.release_date.strftime('%Y-%m-%d') if track.release_date else None,
                'duration': int(track.duration.total_seconds()) if track.duration else None,
                'language': None # Spotify results via this app don't store language directly on Track model
            })

        # JioSaavn Search
        jiosaavn_api_songs = search_jiosaavn_songs(query) # Returns list of standardized dicts
        all_results.extend(jiosaavn_api_songs)

        # Note on sorting: The previous 'sort' parameter applied to Spotify Track model fields.
        # With mixed sources, a unified sorting key is needed if server-side sort is desired.
        # For example, sort by title (case-insensitive):
        # all_results.sort(key=lambda x: (x.get('title') or '').lower())
        # Or, if sorting by popularity or release_date, ensure all sources provide comparable data,
        # or handle missing data gracefully (e.g., items with None for sort key go to end).
        # For now, we'll rely on default order from APIs + Spotify results potentially being first.
        # The 'sort' parameter from UI is still available if client-side sorting is implemented,
        # or if a more advanced server-side sort strategy is added later.

        UserActivity.objects.create(
            user=request.user,
            activity_type='search',
            description=f"Searched for: {query} (Sources: Spotify, JioSaavn)"
        )

    context = {
        'query': query,
        'tracks': all_results, # Pass the combined list
        'sort': sort # Keep sort for UI, though backend effect may change
    }

    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        # AJAX response needs to handle the new structure of 'all_results'
        ajax_tracks_response = []
        for item in all_results:
            # Ensure artists is a string for AJAX if it was a list
            artists_display = item.get('artists')
            if isinstance(artists_display, list):
                artists_display = ", ".join(artists_display)

            ajax_tracks_response.append({
                'id': item.get('id'),
                'title': item.get('title'),
                'artists': artists_display, # Now a string
                'album': item.get('album'),
                'release_date': item.get('release_date'),
                'popularity': item.get('popularity'), # Will be None for JioSaavn
                'image_url': item.get('image_url'),
                'preview_url': item.get('preview_url'),
                'source': item.get('source'),
                'is_db_track': item.get('is_db_track', False), # Important for UI logic
                'duration': item.get('duration'),
                'language': item.get('language')
            })
        return JsonResponse({'query': query, 'tracks': ajax_tracks_response})

    return render(request, 'music/search.html', context)


from django.utils import timezone # Add this import
from datetime import timedelta # Add this import

def callback(request):
    code = request.GET.get('code')
    # Use the existing get_spotify_client which initializes SpotifyOAuth via DjangoSessionCacheHandler
    # This will handle the token fetching using the code.
    sp_oauth = get_spotify_client(request).auth_manager

    try:
        token_info = sp_oauth.get_access_token(code, check_cache=False) # Ensure fresh token fetch
    except Exception as e:
        logger.error(f"Error obtaining access token from Spotify: {str(e)}")
        messages.error(request, "Error connecting to Spotify. Please try again.")
        return redirect('home') # Or some other error page or retry page

    if not token_info:
        logger.error("Failed to get token_info from Spotify.")
        messages.error(request, "Could not authenticate with Spotify. No token information received.")
        return redirect('home')

    # Store token info in session (legacy, can be kept for other parts of app if needed, or removed)
    request.session['token_info'] = token_info

    # Store token info on the CustomUser model (Conceptual - fields not yet migrated)
    if request.user.is_authenticated:
        try:
            user = request.user
            user.spotify_access_token = token_info.get('access_token')
            user.spotify_refresh_token = token_info.get('refresh_token') # Store if provided
            expires_at_timestamp = token_info.get('expires_at')
            if expires_at_timestamp:
                # Convert Unix timestamp to datetime object, make it timezone-aware
                user.spotify_token_expiry = timezone.make_aware(datetime.fromtimestamp(expires_at_timestamp))
            else: # Fallback if 'expires_at' not present, use 'expires_in'
                expires_in_seconds = token_info.get('expires_in')
                if expires_in_seconds:
                    user.spotify_token_expiry = timezone.now() + timedelta(seconds=expires_in_seconds)

            user.spotify_scope = token_info.get('scope')

            user.save(update_fields=['spotify_access_token', 'spotify_refresh_token', 'spotify_token_expiry', 'spotify_scope'])
            logger.info(f"Successfully saved Spotify token info for user {user.username}")
            messages.success(request, "Successfully connected your Spotify account!")
        except Exception as e:
            logger.error(f"Error saving Spotify token info for user {request.user.username}: {str(e)}")
            messages.error(request, "There was an issue saving your Spotify connection details.")

    return redirect('dashboard')


@login_required
@cache_page(3600)
def track_detail(request, track_id):
    track = Track.objects.get(spotify_id=track_id)
    sp = get_spotify_client(request)
    # For track_detail, we want recommendations related to the current track_id
    # The get_hybrid_recommendations expects a user and seed_track_id
    # We need to ensure that `top_tracks + recently_played` is suitable for `stored_tracks_data` if that's how hybrid works,
    # or adapt the call. For now, let's assume this view should call the hybrid recommender.
    # The hybrid recommender itself will call content-based and item-item, which might use `stored_tracks_data`
    # or fetch their own candidates.
    # The `get_recommendations` from spotify_service was simpler.
    # Let's call the new hybrid recommender.
    # It needs `user` and `seed_track_id`.
    # The `top_tracks + recently_played` might not be directly used by `get_hybrid_recommendations` in the same way.
    # It will fetch its own candidates or use item-item data.

    # Assuming `get_hybrid_recommendations` returns a list of Track objects
    recommendations = get_hybrid_recommendations(request.user, seed_track_id=track.spotify_id, num_recommendations=5)
    # recommendation_ids = list({rec.spotify_id: rec for rec in recommendations}.values()) # Not needed if recs are Track objects
    # recommendations = [Track.objects.get(spotify_id=rec.spotify_id) for rec in recommendations] # Not needed if recs are Track objects

    # The old `get_recommendations` returned a list of dicts with 'id'.
    # The new `get_hybrid_recommendations` is planned to return Track objects.
    # If `recommendations` is already a list of Track objects, no further processing is needed here.
    artists = [
        {'name': artist.name.strip(), 'url': reverse('artist_detail', args=[artist.name.strip()])}
        for artist in track.artists.all()]

    artists_str = "".join([artist.name for artist in track.artists.all()])
    query = f"{track.title} {artists_str} {track.album}".strip()
    search_current_track = search_jiosaavn(query)
    if search_current_track:
        track_details = get_track_details_jiosaavn(search_current_track[0]['id'])
        audio_features = extract_audio_features(download_preview(track_details['preview_url'], track.spotify_id))
        track.audio_features = audio_features
        track.preview_url = track_details['preview_url']
        track.save()

    # Log user activity
    UserActivity.objects.create(
        user=request.user,
        activity_type='view_track',
        description=f"Viewed track: {track.title} by {', '.join([artist.name for artist in track.artists.all()])}"
    )
    context = {
        'track': track,
        'recommendations': recommendations,
        'artists': artists
    }

    return render(request, 'music/track_detail.html', context)



@login_required
@cache_page(3600) # This caches the whole page output
def artist_detail(request, artist_name):
    sp = get_spotify_client(request)
    # The search for artist_id itself isn't cached by our new functions yet.
    # This sp.search call could be wrapped if it's a bottleneck.
    # For now, assuming it's acceptable or less frequent than fetching details.
    search_results = sp.search(artist_name, type='artist', limit=1)
    if not search_results['artists']['items']:
        messages.error(request, f"Artist '{artist_name}' not found.")
        return redirect('home') # Or some other appropriate page

    artist_id = search_results['artists']['items'][0]['id']

    # Use cached functions
    artist = get_artist_details(sp, artist_id) # sp is passed for auth context if cache key needs it
    top_tracks_data = get_artist_top_tracks(sp, artist_id) # Assuming get_artist_top_tracks is from spotify.py and cached
    albums_data = get_artist_albums(sp, artist_id, album_type='album', limit=5)

    context = {
        'artist': artist,
        'top_tracks': top_tracks_data,
        'albums': albums_data,
    }

    UserActivity.objects.create(
        user=request.user,
        activity_type='view_artist',
        description=f"Viewed artist: {artist['name']}"
    )
    return render(request, 'music/artist_detail.html', context)


@login_required
# @cache_page(3600)
def playlist_detail(request, playlist_id):
    sp = get_spotify_client(request)

    playlist_obj = get_or_create_playlist(playlist_id, request, sp) # Renamed to avoid conflict
    if playlist_obj:
        # The tracks_data is now handled in get_playlist_tracks and get_or_create_playlist
        spotify_tracks_data = get_playlist_tracks(sp, playlist_id) # This is list of Track model instances from cached function
        playlist_obj.tracks.set(spotify_tracks_data) # M2M update
        playlist_obj.save()

    if not playlist_obj:
        return redirect('dashboard')

    # Re-fetch with prefetch for template rendering from DB
    final_playlist_obj = Playlist.objects.prefetch_related(
        'tracks__artists',
        'tracks__genres',
        # 'tracks__album__artist' # Add if album/artist details of tracks are shown in playlist_detail.html
        # The template music/playlist_detail.html iterates playlist.tracks.all and shows:
        # track.title, track.artists_names, track.album, track.duration, track.image_url
        # So, 'tracks__album' could be useful if album name is directly from track.album.name
        # 'tracks__album__artist' is not directly used.
        # Let's add 'tracks__album' for album name.
        'tracks__album'
    ).get(spotify_id=playlist_id, user=request.user)


    context = {
        'playlist': final_playlist_obj
    }

    UserActivity.objects.create(
        user=request.user,
        activity_type='view_playlist',
        description=f"Viewed playlist: {playlist.name}"
    )

    return render(request, 'music/playlist_detail.html', context)


@login_required
def create_playlist(request):
    if request.method == 'POST':
        name = request.POST.get('name')
        description = request.POST.get('description', '')
        cover_image = request.FILES.get('cover_image')
        if name:
            sp = get_spotify_client(request)
            playlist = create_playlist_spotify(sp, name, description)

            if cover_image:
                base64_img = convert_image_to_base64(cover_image)
                sp.playlist_upload_cover_image(playlist['id'], base64_img)
                playlist = sp.playlist(playlist['id'])

            Playlist.objects.create(
                user=request.user,
                description=playlist['description'],
                name=playlist['name'],
                spotify_id=playlist['id'],
                image_url=playlist['images'][0]['url'] if playlist['images'] else None
            )

            UserActivity.objects.create(
                user=request.user,
                activity_type='create_playlist',
                description=f"Created playlist: {playlist['name']}"
            )

            return redirect('playlist_detail', playlist_id=playlist['id'])
    return render(request, 'music/create_playlist.html')


@login_required
def add_to_playlist(request):
    if request.method == 'POST':
        track_id = request.POST.get('track_id')
        playlist_id = request.POST.get('playlist_id')
        if track_id and playlist_id:
            sp = get_spotify_client(request)
            add_tracks_to_playlist_spotify(sp, playlist_id, [track_id])
            playlist = Playlist.objects.get(spotify_id=playlist_id, user=request.user)
            song = Track.objects.get(spotify_id=track_id)
            playlist.tracks.add(song)
            playlist.save()
            UserActivity.objects.create(
                user=request.user,
                activity_type='add_to_playlist',
                description=f"Added song {song.title} to playlist {playlist.name}"
            )

            if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                return JsonResponse({'status': 'success'})

            return redirect('playlist_detail', playlist_id=playlist_id)
    return JsonResponse({'status': 'error'}, status=400)


@login_required
def delete_playlist(request, playlist_id):
    playlist = get_object_or_404(Playlist, spotify_id=playlist_id)
    if request.user != playlist.user:
        return HttpResponseForbidden("You don't have permission to delete this playlist.")

    if request.method == 'DELETE':
        sp = get_spotify_client(request)
        delete_playlist_spotify(sp, playlist_id=playlist_id)
        playlist.delete()
        messages.success(request, 'Playlist deleted successfully.')
        return redirect('dashboard')

    return HttpResponseForbidden("Invalid request method.")


# --- Test View for Mood Playlist Generation ---
from services.recommendation_service.playlist_generator import generate_mood_playlist, MOOD_ACTIVITY_PROFILES
from django.http import HttpResponse # Already imported but good to note for this view
import logging # Already imported but good to note for this view

# logger is already defined at the top of this file if it's the same logger instance.
# If not, initialize: logger = logging.getLogger(__name__)

@login_required
def test_generate_mood_playlist_view(request, mood_key):
    if not request.user.is_authenticated: # Redundant due to @login_required but safe
        return HttpResponseForbidden("You must be logged in to test playlist generation.")

    if mood_key.lower() not in MOOD_ACTIVITY_PROFILES:
        available_keys = ", ".join(MOOD_ACTIVITY_PROFILES.keys())
        return HttpResponse(f"Error: Mood key '{mood_key}' not recognized. Available keys: {available_keys}", status=400)

    logger.info(f"Test view: Generating '{mood_key}' playlist for user {request.user.username}")

    # Note: get_spotify_client_for_user is conceptual if CustomUser token fields are not migrated/populated.
    # This test view might not fully work if that client isn't functional for the user.
    # The playlist_generator function itself has a fallback if sp client is None and returns [].
    generated_tracks = generate_mood_playlist(request.user, mood_key, num_tracks=5) # Generate 5 for test

    if not generated_tracks:
        message = f"Could not generate any tracks for mood: {mood_key}. This might be due to issues obtaining a functional Spotify client for the user (e.g., missing tokens), or no recommendations being found by Spotify for the given criteria and seeds."
        logger.warning(message)
        return HttpResponse(message)

    response_html = f"<h1>Generated Playlist for Mood: {mood_key.capitalize()}</h1>"
    response_html += f"<p>Attempted to generate for user: {request.user.username}</p>"
    if not generated_tracks:
        response_html += "<p>No tracks generated. Check logs for more details.</p>"
    else:
        response_html += "<ul>"
        for track in generated_tracks:
            # Assuming track is a Track model instance
            artists_names = ", ".join(a.name for a in track.artists.all())
            audio_source = track.audio_features.get('source', 'Unknown') if track.audio_features else 'N/A'
            response_html += f"<li>{track.title} - {artists_names} (Audio Source: {audio_source})</li>"
        response_html += "</ul>"

    return HttpResponse(response_html)
# --- End Test View ---


# --- View for Mood/Activity Playlist Generation and Display ---
from services.recommendation_service.playlist_generator import generate_mood_playlist, MOOD_ACTIVITY_PROFILES
from django.template.loader import render_to_string # For potential HTML messages

@login_required
def generate_and_display_mood_playlist(request):
    available_moods = list(MOOD_ACTIVITY_PROFILES.keys()) # For GET request if needed for a selection form

    if request.method == 'POST':
        mood_or_activity_key = request.POST.get('mood_or_activity_key')
        if not mood_or_activity_key or mood_or_activity_key.lower() not in MOOD_ACTIVITY_PROFILES:
            messages.error(request, "Invalid mood or activity selected.")
            return redirect(request.META.get('HTTP_REFERER', 'dashboard')) # Or to a page with selection

        logger.info(f"Generating '{mood_or_activity_key}' playlist for user {request.user.username}")

        # The generate_mood_playlist function expects a user object.
        generated_tracks = generate_mood_playlist(request.user, mood_or_activity_key, num_tracks=20)

        if generated_tracks is None: # Indicates an issue with Spotify client usually
             messages.error(request, f"Could not connect to Spotify to generate your playlist. Please ensure your account is linked or try again later.")
             return redirect('dashboard') # Or a more specific error page

        if not generated_tracks:
            messages.warning(request, f"Could not generate any tracks for the mood: {mood_or_activity_key}. Try a different mood or check back later.")
            # Optionally, still render the display page with a message
            return render(request, 'music/mood_playlist_display.html', {
                'selected_mood': mood_or_activity_key,
                'generated_tracks': [],
                'user_has_spotify_scopes': False # Placeholder, see below
            })

        # Check if user has necessary Spotify scopes for saving playlists
        # This is conceptual as user.spotify_scope might not be populated or accurate yet.
        user_scopes = getattr(request.user, 'spotify_scope', "")
        can_save_to_spotify = 'playlist-modify-public' in user_scopes or 'playlist-modify-private' in user_scopes

        # For now, let's save the generated playlist to the session to pass to the display page.
        # A more robust solution might save it temporarily in DB or cache if it's large.
        final_tracks_for_template = []
        if generated_tracks:
            track_ids_for_prefetch = [t.id for t in generated_tracks if t.id is not None]
            # Querying with prefetch. Note: This assumes generated_tracks are saved instances with primary keys.
            # The order might be lost with filter().id__in. If order is critical, alternative needed.
            # For now, let's assume default ordering from Track model or prefetch is sufficient.
            # A more robust way to maintain order:
            prefetched_tracks_dict = {
                t.id: t for t in Track.objects.filter(id__in=track_ids_for_prefetch).prefetch_related('artists', 'genres')
            }
            final_tracks_for_template = [prefetched_tracks_dict.get(tid) for tid in track_ids_for_prefetch if prefetched_tracks_dict.get(tid)]

        request.session['generated_mood_playlist'] = {
            'mood': mood_or_activity_key,
            'track_ids': [track.spotify_id for track in final_tracks_for_template] # Store Spotify IDs
        }

        context = {
            'selected_mood': mood_or_activity_key,
            'generated_tracks': final_tracks_for_template, # Pass pre-fetched list
            'user_has_spotify_scopes': can_save_to_spotify,
        }
        return render(request, 'music/mood_playlist_display.html', context)

    # For GET request, if we wanted a page with a form to select mood:
    # context = {'available_moods': available_moods}
    # return render(request, 'music/mood_selection_page.html', context)
    # For now, dashboard buttons POST directly, so GET to this URL might not be used.
    messages.info(request, "Select a mood or activity from your dashboard to generate a playlist.")
    return redirect('dashboard')
# --- End Mood Playlist View ---


@login_required
def delete_track(request, playlist_id, track_id):
    playlist = get_object_or_404(Playlist, spotify_id=playlist_id)
    track = get_object_or_404(Track, spotify_id=track_id)

    if request.user != playlist.user:
        return HttpResponseForbidden("You don't have permission to modify this playlist.")

    if request.method == 'DELETE':
        sp = get_spotify_client(request)
        remove_tracks_from_playlist_spotify(sp, playlist_id, [track_id])
        playlist.tracks.remove(track)
        messages.success(request, 'Track removed from playlist successfully.')
        return redirect('playlist_detail', playlist_id=playlist_id)

    return HttpResponseForbidden("Invalid request method.")
