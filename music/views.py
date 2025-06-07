from datetime import datetime

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse, HttpResponseForbidden
from django.shortcuts import render, redirect, get_object_or_404
from django.urls import reverse
from django.views.decorators.cache import cache_page

from users.models import UserActivity
from .models import Playlist, Track
from .spotify import get_recommendations, get_spotify_client, \
    search_jiosaavn, get_track_details_jiosaavn, get_user_top_tracks, get_user_recently_played, create_playlist_spotify, \
    search_tracks, get_or_create_playlist, get_playlist_tracks, extract_audio_features, download_preview, \
    add_tracks_to_playlist_spotify, delete_playlist_spotify, remove_tracks_from_playlist_spotify, \
    get_artist_details, get_artist_albums, get_artist_top_tracks # Moved import here
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


@login_required
def search(request):
    query = request.GET.get('q', '')
    sort = request.GET.get('sort', '')
    # Add a way to add to cart from search results, if desired.
    # For now, cart actions are separate.

    all_results = [] # Will hold combined results

    if query:
        # Spotify Search
        sp = get_spotify_client(request)
        spotify_db_tracks = search_tracks(sp, query) # Returns Track model instances

        for track in spotify_db_tracks:
            all_results.append({
                'id': track.spotify_id, # For linking to track_detail if it's a Spotify track
                'title': track.title,
                'artists': track.artists_names, # Property on Track model
                'album': track.album,
                'image_url': track.image_url,
                'preview_url': track.preview_url,
                'source': 'Spotify',
                'is_db_track': True, # Flag to identify it's a full Track model instance
                'popularity': track.popularity, # For potential sorting, though mixed-source sort is complex
                'release_date': track.release_date.strftime('%Y-%m-%d') if track.release_date else None,
            })

        # Apple Music Search
        from .applemusic import search_apple_music
        # Default storefront 'us', types include 'songs' and 'artists'
        apple_music_search_output = search_apple_music(query, types=['songs'])
        applemusic_songs = apple_music_search_output.get('songs', [])

        for song_data in applemusic_songs:
            all_results.append({
                'id': song_data.get('id'), # Apple Music ID
                'title': song_data.get('title'),
                'artists': [song_data.get('artist')] if song_data.get('artist') else [],
                'album': song_data.get('album'),
                'image_url': song_data.get('artwork_url'),
                'preview_url': song_data.get('preview_url'),
                'source': 'Apple Music',
                'is_db_track': False,
                'popularity': None, # Not directly available from AM search in this format
                'release_date': None, # Not directly available from AM search in this format
            })

        # Placeholder for JioSaavn search results integration (if enabled)
        # jiosaavn_tracks = search_jiosaavn(query) # Assuming it returns a list of dicts
        # for track_data in jiosaavn_tracks:
        #     all_results.append({
        #         'id': track_data.get('id'),
        #         'title': track_data.get('title'),
        #         'artists': [track_data.get('artist')] if track_data.get('artist') else [],
        #         'album': track_data.get('album'),
        #         'image_url': track_data.get('image_url'),
        #         'preview_url': track_data.get('preview_url'),
        #         'source': 'JioSaavn',
        #         'is_db_track': False
        #     })

        # Sorting: The existing sort logic applies to Track model instances (Spotify results).
        # For a combined list, sorting needs a common basis or a more complex strategy.
        # For now, we can sort Spotify results first, then append others, or apply a generic sort.
        # Let's remove the old sort for now to simplify, as it won't work on the mixed list.
        # A simple client-side sort or a basic server-side sort by title could be an option.
        # all_results.sort(key=lambda x: (x.get('title') or '').lower())


        UserActivity.objects.create(
            user=request.user,
            activity_type='search',
            description=f"Searched for: {query} (Sources: Spotify, Apple Music)"
        )

    context = {
        'query': query,
        'tracks': all_results, # Use the combined list
        'sort': sort # Keep for UI, though backend effect is limited now
    }

    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        # AJAX response needs to handle the new structure of 'all_results'
        # Ensure artists is a list of strings for consistency if some sources return string and others list
        ajax_tracks = []
        for track_item in all_results:
            artists_display = track_item.get('artists')
            if isinstance(artists_display, list):
                artists_display = ", ".join(artists_display)

            ajax_tracks.append({
                'id': track_item.get('id'),
                'title': track_item.get('title'),
                'artists': artists_display,
                'album': track_item.get('album'),
                'release_date': track_item.get('release_date'),
                'popularity': track_item.get('popularity'),
                'image_url': track_item.get('image_url'),
                'preview_url': track_item.get('preview_url'),
                'source': track_item.get('source'),
                'is_db_track': track_item.get('is_db_track', False)
            })
        return JsonResponse({'query': query, 'tracks': ajax_tracks})

    return render(request, 'music/search.html', context)


def callback(request):
    code = request.GET.get('code')
    sp = get_spotify_client(request)
    token_info = sp.auth_manager.get_access_token(code)
    request.session['token_info'] = token_info
    return redirect('dashboard')


@login_required
@cache_page(3600)
def track_detail(request, track_id):
    track = Track.objects.get(spotify_id=track_id)
    sp = get_spotify_client(request)
    top_tracks = get_user_top_tracks(sp)
    recently_played = get_user_recently_played(sp)
    recommendation_ids = get_recommendations(track_id, top_tracks + recently_played, limit=5)
    recommendation_ids = list({track['id']: track for track in recommendation_ids}.values())
    recommendations = [Track.objects.get(spotify_id=track['id']) for track in recommendation_ids]
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
