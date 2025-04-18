from django.core.management.base import BaseCommand
from music.models import Artist, Genre
from music.spotify import get_spotify_client
from django.contrib.auth import get_user_model

class Command(BaseCommand):
    help = 'Updates genres for all artists using Spotify API'

    def handle(self, *args, **options):
        # Get a superuser to use their credentials
        User = get_user_model()
        admin_user = User.objects.filter(is_superuser=True).first()
        
        if not admin_user:
            self.stdout.write(self.style.ERROR('No superuser found'))
            return
            
        sp = get_spotify_client(admin_user)
        
        # Process artists in batches of 50 (Spotify API limit)
        artists = Artist.objects.filter(spotify_id__isnull=False)
        total = artists.count()
        processed = 0
        
        while processed < total:
            batch = artists[processed:processed + 50]
            artist_ids = [artist.spotify_id for artist in batch]
            
            try:
                spotify_artists = sp.artists(artist_ids)['artists']
                
                for i, artist in enumerate(batch):
                    # Create and associate genres
                    artist_genres = []
                    for genre_name in spotify_artists[i]['genres']:
                        genre, _ = Genre.objects.get_or_create(name=genre_name)
                        artist_genres.append(genre)
                    artist.genres.set(artist_genres)
                    artist.save()
                    
                processed += len(batch)
                self.stdout.write(f'Processed {processed}/{total} artists')
                    
            except Exception as e:
                self.stdout.write(self.style.ERROR(f'Error processing batch: {str(e)}'))
                continue
                
        self.stdout.write(self.style.SUCCESS('Successfully updated artist genres'))