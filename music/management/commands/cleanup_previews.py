from django.core.management.base import BaseCommand
from django.conf import settings
import os
import time


class Command(BaseCommand):
    help = 'Cleanup old preview files from media/previews and temp_previews directories'

    def add_arguments(self, parser):
        parser.add_argument(
            '--days',
            type=int,
            default=7,
            help='Delete files older than N days (default: 7)',
        )
        parser.add_argument(
            '--temp-only',
            action='store_true',
            help='Only clean temp_previews directory (delete all temp files)',
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show what would be deleted without deleting',
        )

    def handle(self, *args, **options):
        days = options['days']
        dry_run = options['dry_run']
        temp_only = options['temp_only']
        
        total_deleted = 0
        total_freed = 0
        
        # Clean temp_previews first (always delete all)
        temp_dir = os.path.join(settings.MEDIA_ROOT, 'temp_previews')
        if os.path.exists(temp_dir):
            self.stdout.write(f'Cleaning temp_previews directory...')
            deleted, freed = self._clean_directory(temp_dir, cutoff_days=0, dry_run=dry_run)
            total_deleted += deleted
            total_freed += freed
        
        # Clean main previews if not temp_only
        if not temp_only:
            previews_dir = os.path.join(settings.MEDIA_ROOT, 'previews')
            if os.path.exists(previews_dir):
                self.stdout.write(f'\nCleaning previews older than {days} days...')
                deleted, freed = self._clean_directory(previews_dir, cutoff_days=days, dry_run=dry_run)
                total_deleted += deleted
                total_freed += freed
        
        total_freed_mb = total_freed / (1024 * 1024)
        self.stdout.write(self.style.SUCCESS(
            f'\n{total_deleted} files {"would be" if dry_run else ""} deleted'
        ))
        self.stdout.write(f'Total space freed: {total_freed_mb:.2f} MB')
        
        if dry_run:
            self.stdout.write(self.style.WARNING('\nDRY RUN - No files were actually deleted'))
    
    def _clean_directory(self, directory, cutoff_days, dry_run):
        """Clean files from a directory older than cutoff_days. If cutoff_days=0, delete all."""
        now = time.time()
        cutoff = now - (cutoff_days * 24 * 60 * 60) if cutoff_days > 0 else float('inf')
        
        deleted_count = 0
        total_size = 0

        for filename in os.listdir(directory):
            filepath = os.path.join(directory, filename)

            if not os.path.isfile(filepath):
                continue

            file_mtime = os.path.getmtime(filepath)
            file_size = os.path.getsize(filepath)

            # Delete if older than cutoff or if cutoff_days=0 (delete all)
            if cutoff_days == 0 or file_mtime < cutoff:
                age_days = int((now - file_mtime) / (24 * 60 * 60))
                size_mb = file_size / (1024 * 1024)

                self.stdout.write(
                    f'  {filename} - {age_days} days old, {size_mb:.2f} MB'
                )

                if not dry_run:
                    try:
                        os.remove(filepath)
                        deleted_count += 1
                        total_size += file_size
                    except Exception as e:
                        self.stdout.write(self.style.ERROR(
                            f'    Error deleting {filename}: {e}'
                        ))
                else:
                    deleted_count += 1
                    total_size += file_size

        return deleted_count, total_size
