from django.core.management.base import BaseCommand
from music.models import Track
from services.jiosaavn_service.api_client import search_songs


class Command(BaseCommand):
    help = 'Fix wrong preview URLs for tracks by re-running improved search logic'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show what would be fixed without making changes',
        )
        parser.add_argument(
            '--track-id',
            type=str,
            help='Fix a specific track by Spotify ID',
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        specific_track_id = options.get('track_id')

        if specific_track_id:
            tracks = Track.objects.filter(spotify_id=specific_track_id)
        else:
            # Find tracks with JioSaavn URLs
            tracks = Track.objects.filter(preview_url__contains='saavncdn.com')

        self.stdout.write(f'Found {tracks.count()} tracks with JioSaavn URLs')

        fixed_count = 0
        skipped_count = 0
        cleared_count = 0

        for track in tracks:
            # Construct improved search query
            artist_name = track.artists.first().name if track.artists.exists() else ''
            release_year = str(
                track.release_date.year) if track.release_date else ''

            query = f"{track.title} {artist_name} {release_year}".lower().strip()
            query = " ".join(query.split())

            self.stdout.write(
                f'\nProcessing: {track.title} ({track.spotify_id})')
            self.stdout.write(f'  Current URL: {track.preview_url}')
            self.stdout.write(f'  Search query: {query}')

            # Get duration for validation
            target_duration_ms = int(
                track.duration.total_seconds() * 1000) if track.duration else 0

            try:
                # Search JioSaavn with improved query
                saavn_results = search_songs(query, limit=5)

                new_preview_url = None
                for result in saavn_results:
                    saavn_duration = result.get('duration')
                    saavn_title = result.get('title', '').lower()
                    track_title = track.title.lower()

                    # Check if title roughly matches (at least 50% of words match)
                    track_words = set(track_title.split())
                    saavn_words = set(saavn_title.split())
                    if track_words and saavn_words:
                        common_words = track_words & saavn_words
                        similarity = len(common_words) / \
                            max(len(track_words), len(saavn_words))
                    else:
                        similarity = 0

                    if saavn_duration and target_duration_ms > 0:
                        saavn_duration_ms = int(saavn_duration) * 1000
                        # Check if duration matches (within 10s tolerance) AND title is similar
                        duration_match = abs(
                            saavn_duration_ms - target_duration_ms) < 10000
                        title_match = similarity > 0.3  # At least 30% word overlap

                        if duration_match and title_match:
                            if result.get('preview_url'):
                                new_preview_url = result['preview_url']
                                self.stdout.write(self.style.SUCCESS(
                                    f'  ✓ Found matching URL: {new_preview_url} (duration: {saavn_duration}s, similarity: {similarity:.2f})'
                                ))
                                break

                if new_preview_url and new_preview_url != track.preview_url:
                    if not dry_run:
                        track.preview_url = new_preview_url
                        track.save(update_fields=['preview_url'])
                    self.stdout.write(self.style.SUCCESS(
                        f'  → Fixed preview URL'))
                    fixed_count += 1
                elif not new_preview_url:
                    # No match found - clear the URL
                    if not dry_run:
                        track.preview_url = None
                        track.save(update_fields=['preview_url'])
                    self.stdout.write(self.style.WARNING(
                        f'  ⚠ No duration match - cleared URL'))
                    cleared_count += 1
                else:
                    self.stdout.write(f'  → Already correct, skipping')
                    skipped_count += 1

            except Exception as e:
                self.stdout.write(self.style.ERROR(f'  ✗ Error: {e}'))
                skipped_count += 1

        self.stdout.write(self.style.SUCCESS(f'\n\nSummary:'))
        self.stdout.write(f'  Fixed: {fixed_count}')
        self.stdout.write(f'  Cleared (no match): {cleared_count}')
        self.stdout.write(f'  Skipped: {skipped_count}')

        if dry_run:
            self.stdout.write(self.style.WARNING(
                '\nDRY RUN - No changes made'))
