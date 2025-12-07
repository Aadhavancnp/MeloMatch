"""
Management command to batch extract audio features for tracks with preview URLs.
"""
from django.core.management.base import BaseCommand
from music.models import Track
from music.tasks import extract_track_features_task


class Command(BaseCommand):
    help = 'Extract audio features for tracks that have preview URLs but no features'

    def add_arguments(self, parser):
        parser.add_argument(
            '--limit',
            type=int,
            default=50,
            help='Maximum number of tracks to process (default: 50)'
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show which tracks would be processed without actually processing them'
        )

    def handle(self, *args, **options):
        limit = options['limit']
        dry_run = options['dry_run']
        
        # Find tracks with preview URLs but no audio features
        tracks = Track.objects.filter(
            audio_features={}
        ).exclude(
            preview_url__isnull=True
        ).exclude(
            preview_url=''
        ).order_by('-id')[:limit]
        
        total = tracks.count()
        self.stdout.write(f'Found {total} tracks needing feature extraction')
        
        if dry_run:
            self.stdout.write(self.style.WARNING('DRY RUN - no changes will be made'))
            for track in tracks:
                self.stdout.write(f'  Would process: {track.title} (ID: {track.id})')
            return
        
        success = 0
        failed = 0
        
        for i, track in enumerate(tracks, 1):
            self.stdout.write(f'[{i}/{total}] Processing: {track.title}...')
            try:
                result = extract_track_features_task(track.id)
                if result:
                    self.stdout.write(self.style.SUCCESS(f'  ✓ Extracted features'))
                    success += 1
                else:
                    self.stdout.write(self.style.WARNING(f'  ✗ No features extracted'))
                    failed += 1
            except Exception as e:
                self.stdout.write(self.style.ERROR(f'  ✗ Error: {e}'))
                failed += 1
        
        self.stdout.write('')
        self.stdout.write(self.style.SUCCESS(f'Completed: {success} successful, {failed} failed'))
