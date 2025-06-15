import asyncio
import base64

from django.core.files.uploadedfile import SimpleUploadedFile
from googletrans import Translator


async def context_specific_translation(text):
    async with Translator() as translator:
        if (await translator.detect(text)) == 'en':
            return text
        return (await translator.translate(text, dest='en')).text


def translate_text(text):
    return asyncio.run(context_specific_translation(text))


def convert_image_to_base64(image) -> str:
    """Converts image to base64 string"""

    return base64.b64encode(image.read()).decode()


def convert_str_to_image(image_data: str):
    """Converts base 64 string to django image"""

    decoded_data = base64.b64decode(image_data.encode())

    file = SimpleUploadedFile.from_dict(
        {
            "filename": "logo",
            "content": decoded_data,
            "content-type": "'image/jpeg'",
        }
    )

    return file

from .models import Track # Ensure this is placed correctly, typically at the top of the file with other imports
from django.db.models import Q # Ensure this is also at the top

def get_tracks_by_same_artists(track_pk, limit=5):
    try:
        # Using select_related for artists assuming a direct ForeignKey from Track to a primary Artist,
        # but Track.artists is ManyToMany. So prefetch_related is correct.
        current_track = Track.objects.prefetch_related('artists').get(pk=track_pk)
        if not current_track.artists.exists():
            return []

        artist_ids = [artist.id for artist in current_track.artists.all()]

        recommended_tracks = Track.objects.filter(artists__id__in=artist_ids) \
            .exclude(pk=track_pk) \
            .distinct() \
            .order_by('-popularity', '-release_date')[:limit]
        return list(recommended_tracks)
    except Track.DoesNotExist:
        return []
    except Exception as e: # Generic catch for other potential errors
        print(f"Error in get_tracks_by_same_artists for track_pk {track_pk}: {e}")
        return []

def get_tracks_from_same_album(track_pk, limit=5):
    try:
        current_track = Track.objects.prefetch_related('artists').get(pk=track_pk)

        if not current_track.album:
            return []

        primary_artist_id = current_track.artists.first().id if current_track.artists.exists() else None

        query = Q(album__iexact=current_track.album)
        # Optional: If matching by primary artist, consider if album names are unique enough
        # or if artist context is truly needed. For many compilation albums, artist might differ.
        # For simplicity, if an artist is present, we can try to narrow it down.
        if primary_artist_id:
             query &= Q(artists__id=primary_artist_id)

        recommended_tracks = Track.objects.filter(query) \
            .exclude(pk=track_pk) \
            .distinct() \
            .order_by('track_number', 'title')[:limit] # Order by track number, then title
        return list(recommended_tracks)
    except Track.DoesNotExist:
        return []
    except Exception as e: # Generic catch
        print(f"Error in get_tracks_from_same_album for track_pk {track_pk}: {e}")
        return []
