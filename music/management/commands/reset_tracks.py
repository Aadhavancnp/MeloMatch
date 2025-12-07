import os
import shutil
from django.core.management.base import BaseCommand
from django.conf import settings
from music.models import Track, Playlist


class Command(BaseCommand):
    help = 'Resets all track data and cleans up media files.'

    def handle(self, *args, **options):
        self.stdout.write('Starting track reset...')

        # 1. Delete all Track objects
        # This will cascade delete ManyToMany relationships in Playlists
        track_count = Track.objects.count()
        Track.objects.all().delete()
        self.stdout.write(self.style.SUCCESS(
            f'Deleted {track_count} tracks from the database.'))

        # 2. Clean up media/previews directory
        previews_dir = os.path.join(settings.MEDIA_ROOT, 'previews')
        if os.path.exists(previews_dir):
            # Remove all files in the directory
            for filename in os.listdir(previews_dir):
                file_path = os.path.join(previews_dir, filename)
                try:
                    if os.path.isfile(file_path) or os.path.islink(file_path):
                        os.unlink(file_path)
                    elif os.path.isdir(file_path):
                        shutil.rmtree(file_path)
                except Exception as e:
                    self.stdout.write(self.style.ERROR(
                        f'Failed to delete {file_path}. Reason: {e}'))
            self.stdout.write(self.style.SUCCESS(
                f'Cleaned up {previews_dir}.'))
        else:
            self.stdout.write(self.style.WARNING(
                f'Previews directory {previews_dir} does not exist.'))

        # 3. Optional: Clear playlist tracks explicitly if needed (though cascade should handle it)
        # Just to be safe and ensure "playlist all put to same playlist" issue is cleared
        for playlist in Playlist.objects.all():
            playlist.tracks.clear()
        self.stdout.write(self.style.SUCCESS(
            'Cleared tracks from all playlists.'))

        self.stdout.write(self.style.SUCCESS('Track reset complete.'))
