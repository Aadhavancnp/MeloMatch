from django.core.management.base import BaseCommand
from services.recommendation_service.tasks import generate_batch_user_recommendations
from django.contrib.auth import get_user_model
from music.models import Track, UserLikedTrack # For creating test data

from subscription.models import SubscriptionPlan # Added import

class Command(BaseCommand):
    help = 'Runs the batch recommendation generation task and optionally creates test data.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--no-celery',
            action='store_true',
            help='Run the task directly without Celery .delay() for easier debugging.',
        )
        parser.add_argument(
            '--create-test-data',
            action='store_true',
            help='Create sample UserLikedTrack data before running.',
        )

    def handle(self, *args, **options):
        if options['create_test_data']:
            self.stdout.write(self.style.WARNING("Creating test UserLikedTrack data..."))
            self._create_test_data()

        self.stdout.write(self.style.SUCCESS("Attempting to run generate_batch_user_recommendations..."))

        if options['no_celery']:
            self.stdout.write("Running task directly (no Celery delay)...")
            result = generate_batch_user_recommendations()
            self.stdout.write(f"Task finished with result: {result}")
        else:
            self.stdout.write("Dispatching task via Celery (.delay())...")
            task_result = generate_batch_user_recommendations.delay()
            self.stdout.write(f"Task dispatched. Celery task ID: {task_result.id}")
            self.stdout.write("Note: You'll need a Celery worker running to process this task.")
            self.stdout.write("Check Celery logs for execution details and UserRecommendationCache for results.")

    def _create_test_data(self):
        User = get_user_model()

        # Ensure a "Free" SubscriptionPlan exists (required by user post_save signal)
        free_plan, created = SubscriptionPlan.objects.get_or_create(
            name='Free',
            defaults={'price': 0.00, 'duration_days': 99999} # Assuming some defaults
        )
        if created:
            self.stdout.write(self.style.SUCCESS("Created 'Free' subscription plan."))

        # Create a few users if they don't exist
        user1, _ = User.objects.get_or_create(username='collab_user1', defaults={'email': 'user1@example.com'})
        user2, _ = User.objects.get_or_create(username='collab_user2', defaults={'email': 'user2@example.com'})
        user3, _ = User.objects.get_or_create(username='collab_user3', defaults={'email': 'user3@example.com'})

        # Create a few tracks if they don't exist (ensure they have spotify_id)
        track_data = [
            {'spotify_id': 'spotid_A', 'title': 'Track A', 'album': 'Album X'},
            {'spotify_id': 'spotid_B', 'title': 'Track B', 'album': 'Album X'},
            {'spotify_id': 'spotid_C', 'title': 'Track C', 'album': 'Album Y'},
            {'spotify_id': 'spotid_D', 'title': 'Track D', 'album': 'Album Y'},
            {'spotify_id': 'spotid_E', 'title': 'Track E', 'album': 'Album Z'},
        ]

        tracks = {}
        for t_data in track_data:
            # Minimal genre for Track model
            genre, _ = Track._meta.get_field('genres').related_model.objects.get_or_create(name='Test Genre')
            track, _ = Track.objects.get_or_create(
                spotify_id=t_data['spotify_id'],
                defaults={'title': t_data['title'], 'album': t_data['album'], 'genres': genre}
            )
            tracks[t_data['spotify_id']] = track

        # Clear existing likes for these test users to ensure clean state for this test
        UserLikedTrack.objects.filter(user__in=[user1, user2, user3]).delete()

        # User 1 likes A, B, C
        UserLikedTrack.objects.create(user=user1, track=tracks['spotid_A'])
        UserLikedTrack.objects.create(user=user1, track=tracks['spotid_B'])
        UserLikedTrack.objects.create(user=user1, track=tracks['spotid_C'])

        # User 2 likes B, C, D
        UserLikedTrack.objects.create(user=user2, track=tracks['spotid_B'])
        UserLikedTrack.objects.create(user=user2, track=tracks['spotid_C'])
        UserLikedTrack.objects.create(user=user2, track=tracks['spotid_D'])

        # User 3 likes A, E (no co-occurrence with user2 directly for new items)
        UserLikedTrack.objects.create(user=user3, track=tracks['spotid_A'])
        UserLikedTrack.objects.create(user=user3, track=tracks['spotid_E'])

        self.stdout.write(self.style.SUCCESS("Test data created (or ensured to exist)."))
        self.stdout.write(f"  User1 likes: A, B, C (IDs: {tracks['spotid_A'].id}, {tracks['spotid_B'].id}, {tracks['spotid_C'].id})")
        self.stdout.write(f"  User2 likes: B, C, D (IDs: {tracks['spotid_B'].id}, {tracks['spotid_C'].id}, {tracks['spotid_D'].id})")
        self.stdout.write(f"  User3 likes: A, E (IDs: {tracks['spotid_A'].id}, {tracks['spotid_E'].id})")
        self.stdout.write("Expected co-occurrences:")
        self.stdout.write("  (A,B): 1; (A,C): 1; (B,C): 2; (B,D): 1; (C,D): 1")
        self.stdout.write("Recommendations for User1 might include D (from User2's co-likes with B,C).")
        self.stdout.write("Recommendations for User2 might include A (from User1's co-likes with B,C).")
        self.stdout.write("Recommendations for User3 might include B,C (from User1's co-likes with A).")
