from django.core.management.base import BaseCommand
from subscription.models import SubscriptionPlan


class Command(BaseCommand):
    help = 'Populates the database with initial subscription plans'

    def handle(self, *args, **kwargs):
        self.stdout.write('Creating subscription plans...')

        plans = [
            {
                'name': 'Free',
                'price': 0.00,
                'duration_days': 30,
                'description': 'Basic features for casual listeners',
                'features': 'Limited song selection,Ad-supported,Standard audio quality'
            },
            {
                'name': 'Pro',
                'price': 9.99,
                'duration_days': 30,
                'description': 'Enhanced features for music enthusiasts',
                'features': 'Unlimited song selection,Ad-free listening,High-quality audio,Offline mode'
            },
            {
                'name': 'Premium',
                'price': 14.99,
                'duration_days': 30,
                'description': 'Ultimate experience for audiophiles',
                'features': 'Everything in Pro,Ultra-high-quality audio,Exclusive content,Early access to new features'
            }
        ]

        for plan in plans:
            SubscriptionPlan.objects.create(**plan)
            self.stdout.write(self.style.SUCCESS(f'Successfully created {plan["name"]} plan'))

        self.stdout.write(self.style.SUCCESS('Subscription plans created successfully'))
