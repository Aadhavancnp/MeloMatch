"""
Management command to seed the database with initial data.
"""
from django.core.management.base import BaseCommand
from django.utils import timezone
from datetime import timedelta

from core.models import FAQItem
from subscription.models import SubscriptionPlan, Subscription
from users.models import CustomUser


class Command(BaseCommand):
    help = 'Seed the database with initial data (FAQs, Subscription Plans, Test User)'

    def add_arguments(self, parser):
        parser.add_argument(
            '--clear',
            action='store_true',
            help='Clear existing data before seeding',
        )

    def handle(self, *args, **options):
        if options['clear']:
            self.stdout.write('Clearing existing data...')
            FAQItem.objects.all().delete()
            # Don't delete subscription plans if they're in use
            self.stdout.write(self.style.WARNING(
                'Cleared FAQs. Subscription plans preserved (may be in use).'))

        self.seed_subscription_plans()
        self.seed_faqs()
        self.seed_test_user()
        self.seed_superuser()

        self.stdout.write(self.style.SUCCESS('Database seeded successfully!'))

    def seed_subscription_plans(self):
        """Create subscription plans."""
        self.stdout.write('Seeding subscription plans...')

        plans = [
            {
                'name': 'Free',
                'price': 0.00,
                'duration_days': 36500,  # ~100 years (effectively forever)
                'description': 'Get started with MeloMatch for free. Perfect for casual listeners.',
                'features': '''Ad-supported streaming
Basic audio quality (128kbps)
Limited skips per hour
Access to curated playlists
Basic search functionality
Web player only''',
                'stripe_price_id': None,
            },
            {
                'name': 'Premium',
                'price': 9.99,
                'duration_days': 30,
                'description': 'Unlock the full MeloMatch experience with Premium.',
                'features': '''Ad-free listening
High quality audio (320kbps)
Unlimited skips
Offline downloads
Exclusive premium playlists
Priority customer support
All devices supported
Lyrics display
Audio equalizer''',
                'stripe_price_id': 'price_premium_monthly',
            },
            {
                'name': 'Premium Annual',
                'price': 99.99,
                'duration_days': 365,
                'description': 'Save 17% with our annual Premium subscription.',
                'features': '''Everything in Premium
2 months free (vs monthly)
Early access to new features
Exclusive annual member perks
Priority feature requests''',
                'stripe_price_id': 'price_premium_annual',
            },
            {
                'name': 'Family',
                'price': 14.99,
                'duration_days': 30,
                'description': 'Premium for up to 6 family members living at the same address.',
                'features': '''All Premium features
Up to 6 accounts
Individual recommendations per member
Family Mix playlist
Parental controls
Shared family playlists''',
                'stripe_price_id': 'price_family_monthly',
            },
            {
                'name': 'Student',
                'price': 4.99,
                'duration_days': 30,
                'description': 'Premium at a discount for eligible students.',
                'features': '''All Premium features
50% off regular Premium price
Valid student verification required
Renews at student rate for up to 4 years''',
                'stripe_price_id': 'price_student_monthly',
            },
        ]

        for plan_data in plans:
            plan, created = SubscriptionPlan.objects.update_or_create(
                name=plan_data['name'],
                defaults=plan_data
            )
            status = 'Created' if created else 'Updated'
            self.stdout.write(f'  {status}: {plan.name} - ${plan.price}')

    def seed_faqs(self):
        """Create FAQ items."""
        self.stdout.write('Seeding FAQs...')

        faqs = [
            # Account & Getting Started
            {
                'question': 'How do I create a MeloMatch account?',
                'answer': 'Creating an account is easy! Click the "Sign Up" button on our homepage, enter your email address, create a password, and verify your email. You can also sign up using your Google or Apple account for faster registration.'
            },
            {
                'question': 'Is MeloMatch free to use?',
                'answer': 'Yes! MeloMatch offers a free tier that includes ad-supported streaming, basic audio quality, and access to our curated playlists. For an enhanced experience without ads and with premium features, check out our Premium subscription options.'
            },
            {
                'question': 'How do I reset my password?',
                'answer': 'Click "Forgot Password" on the login page, enter your email address, and we\'ll send you a password reset link. The link expires after 24 hours for security. If you don\'t see the email, check your spam folder.'
            },

            # Subscription & Billing
            {
                'question': 'What\'s included in Premium?',
                'answer': 'Premium includes ad-free listening, high-quality audio (320kbps), unlimited skips, offline downloads, exclusive playlists, lyrics display, and priority customer support. You can also use MeloMatch on all your devices.'
            },
            {
                'question': 'How do I upgrade to Premium?',
                'answer': 'Go to Settings > Subscription, choose your preferred plan, and complete the payment. Your Premium benefits activate immediately. We accept all major credit cards and PayPal.'
            },
            {
                'question': 'Can I cancel my subscription anytime?',
                'answer': 'Absolutely! You can cancel your subscription at any time from Settings > Subscription > Cancel. You\'ll continue to have Premium access until the end of your current billing period, and you won\'t be charged again.'
            },
            {
                'question': 'Do you offer refunds?',
                'answer': 'We offer a full refund within 7 days of your first Premium subscription if you\'re not satisfied. After that, we don\'t offer refunds, but you can cancel anytime to prevent future charges.'
            },
            {
                'question': 'What is the Family plan?',
                'answer': 'The Family plan allows up to 6 family members living at the same address to each have their own Premium account for $14.99/month. Each member gets their own recommendations and playlists.'
            },

            # Music & Features
            {
                'question': 'How do I create a playlist?',
                'answer': 'Click the "+" button in your Library, select "Create Playlist," give it a name, and start adding songs! You can search for tracks, browse albums, or add songs directly from the player. You can also make playlists collaborative to let friends add songs.'
            },
            {
                'question': 'Can I download music for offline listening?',
                'answer': 'Yes, with Premium! Tap the download button on any song, album, or playlist. Downloaded music is available offline for up to 30 days. Connect to the internet periodically to keep your downloads active.'
            },
            {
                'question': 'How does music recommendation work?',
                'answer': 'Our AI analyzes your listening history, liked songs, and playlist preferences to suggest new music. The more you listen and interact with MeloMatch, the better our recommendations become. Check out your personalized Discover Weekly playlist!'
            },
            {
                'question': 'Can I see lyrics while listening?',
                'answer': 'Yes! Premium users can view synchronized lyrics that scroll along with the song. Tap the lyrics button on the now-playing screen. We have lyrics for millions of songs and are constantly adding more.'
            },

            # Technical & Troubleshooting
            {
                'question': 'What audio quality does MeloMatch offer?',
                'answer': 'Free users get 128kbps audio quality. Premium users can enjoy high-quality 320kbps streaming, and can also choose lower quality to save data. Go to Settings > Audio Quality to adjust your preferences.'
            },
            {
                'question': 'Why is my music buffering?',
                'answer': 'Buffering usually occurs due to slow internet connection. Try switching to a lower audio quality in Settings, moving closer to your WiFi router, or downloading songs for offline listening with Premium.'
            },
            {
                'question': 'How do I connect to speakers or other devices?',
                'answer': 'MeloMatch supports Bluetooth, AirPlay, Chromecast, and many smart speakers. Look for the device icon in the player and select your preferred output device. Make sure your device is on the same network.'
            },
            {
                'question': 'The app is crashing. What should I do?',
                'answer': 'First, try closing and reopening the app. If that doesn\'t work, clear the app cache in Settings > Storage, or reinstall the app. Make sure you\'re running the latest version. If issues persist, contact our support team.'
            },

            # Privacy & Security
            {
                'question': 'How do you protect my data?',
                'answer': 'We use industry-standard encryption for all data transmission and storage. We never sell your personal data to third parties. You can review and download your data anytime in Settings > Privacy.'
            },
            {
                'question': 'Can I make my listening activity private?',
                'answer': 'Yes! Go to Settings > Privacy > Listening Activity and enable Private Session. Your friends won\'t see what you\'re listening to during private sessions. You can also hide specific playlists from your profile.'
            },
            {
                'question': 'How do I delete my account?',
                'answer': 'Go to Settings > Account > Delete Account. This action is permanent and will delete all your playlists, saved music, and listening history. Any active subscription will be cancelled, and you\'ll receive a prorated refund.'
            },
        ]

        for faq_data in faqs:
            faq, created = FAQItem.objects.update_or_create(
                question=faq_data['question'],
                defaults={'answer': faq_data['answer']}
            )
            status = 'Created' if created else 'Updated'
            self.stdout.write(f'  {status}: {faq.question[:50]}...')

    def seed_test_user(self):
        """Create a test user for development."""
        self.stdout.write('Seeding test user...')

        # Create test user
        user, created = CustomUser.objects.get_or_create(
            username='testuser',
            defaults={
                'email': 'test@melomatch.com',
                'first_name': 'Test',
                'last_name': 'User',
                'bio': 'A test account for development purposes.',
            }
        )

        if created:
            user.set_password('testpass123')
            user.save()
            self.stdout.write(f'  Created test user: testuser / testpass123')
        else:
            self.stdout.write(f'  Test user already exists: testuser')

        # Ensure user has a subscription (signal should handle this, but just in case)
        if not hasattr(user, 'subscription') or user.subscription is None:
            try:
                free_plan = SubscriptionPlan.objects.get(name='Free')
                Subscription.objects.create(
                    user=user,
                    plan=free_plan,
                    end_date=timezone.now() + timedelta(days=free_plan.duration_days),
                    status='active'
                )
                self.stdout.write('  Created Free subscription for test user')
            except SubscriptionPlan.DoesNotExist:
                self.stdout.write(self.style.WARNING(
                    '  Free plan not found, skipping subscription'))

    def seed_superuser(self):
        """Create a superuser for admin access."""
        self.stdout.write('Seeding superuser...')

        # Create superuser
        admin, created = CustomUser.objects.get_or_create(
            username='admin',
            defaults={
                'email': 'admin@melomatch.com',
                'first_name': 'Admin',
                'last_name': 'User',
                'is_staff': True,
                'is_superuser': True,
            }
        )

        if created:
            admin.set_password('admin123')
            admin.save()
            self.stdout.write(f'  Created superuser: admin / admin123')
        else:
            # Ensure existing user has admin privileges
            admin.is_staff = True
            admin.is_superuser = True
            admin.save()
            self.stdout.write(f'  Superuser already exists: admin')

        # Ensure admin has a subscription
        if not hasattr(admin, 'subscription') or admin.subscription is None:
            try:
                free_plan = SubscriptionPlan.objects.get(name='Free')
                Subscription.objects.create(
                    user=admin,
                    plan=free_plan,
                    end_date=timezone.now() + timedelta(days=free_plan.duration_days),
                    status='active'
                )
                self.stdout.write('  Created Free subscription for admin')
            except SubscriptionPlan.DoesNotExist:
                self.stdout.write(self.style.WARNING(
                    '  Free plan not found, skipping subscription'))
