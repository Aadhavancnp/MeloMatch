"""
Comprehensive tests for subscription app.

Tests subscription plans, user subscriptions, and Stripe integration.
"""
from datetime import timedelta
from decimal import Decimal
from unittest.mock import MagicMock, patch

from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from users.models import CustomUser

from .models import Subscription, SubscriptionPlan


class SubscriptionPlanModelTests(TestCase):
    """Tests for SubscriptionPlan model."""

    def test_create_subscription_plan(self):
        """Test creating a subscription plan."""
        plan = SubscriptionPlan.objects.create(
            name='Test Plan',
            price=Decimal('9.99'),
            duration_days=30,
            description='Test description',
            features='Feature 1\nFeature 2'
        )

        self.assertEqual(plan.name, 'Test Plan')
        self.assertEqual(plan.price, Decimal('9.99'))
        self.assertEqual(plan.duration_days, 30)
        self.assertEqual(str(plan), 'Test Plan')

    def test_plan_with_stripe_price_id(self):
        """Test plan with Stripe price ID."""
        plan = SubscriptionPlan.objects.create(
            name='Premium',
            price=Decimal('19.99'),
            duration_days=30,
            description='Premium plan',
            features='All features',
            stripe_price_id='price_123abc'
        )

        self.assertEqual(plan.stripe_price_id, 'price_123abc')


class SubscriptionModelTests(TestCase):
    """Tests for Subscription model."""

    @classmethod
    def setUpTestData(cls):
        """Set up test data."""
        cls.free_plan = SubscriptionPlan.objects.create(
            name='Free',
            price=Decimal('0.00'),
            duration_days=36500,
            description='Free tier',
            features='Basic features'
        )
        cls.premium_plan = SubscriptionPlan.objects.create(
            name='Premium',
            price=Decimal('9.99'),
            duration_days=30,
            description='Premium tier',
            features='All features'
        )

    def setUp(self):
        """Set up test user."""
        self.user = CustomUser.objects.create_user(
            username='testuser',
            email='test@example.com',
            password='testpass123'
        )
        # Delete auto-created subscription
        Subscription.objects.filter(user=self.user).delete()

    def test_create_subscription(self):
        """Test creating a subscription."""
        end_date = timezone.now() + timedelta(days=30)
        subscription = Subscription.objects.create(
            user=self.user,
            plan=self.premium_plan,
            end_date=end_date
        )

        self.assertEqual(subscription.user, self.user)
        self.assertEqual(subscription.plan, self.premium_plan)
        self.assertEqual(subscription.status, 'active')

    def test_is_active_method(self):
        """Test is_active method."""
        # Active subscription
        active_sub = Subscription.objects.create(
            user=self.user,
            plan=self.premium_plan,
            end_date=timezone.now() + timedelta(days=30),
            status='active'
        )
        self.assertTrue(active_sub.is_active())

        # Expired subscription
        active_sub.end_date = timezone.now() - timedelta(days=1)
        active_sub.save()
        self.assertFalse(active_sub.is_active())

        # Cancelled subscription
        active_sub.end_date = timezone.now() + timedelta(days=30)
        active_sub.status = 'cancelled'
        active_sub.save()
        self.assertFalse(active_sub.is_active())

    def test_days_remaining_method(self):
        """Test days_remaining method."""
        end_date = timezone.now() + timedelta(days=15)
        subscription = Subscription.objects.create(
            user=self.user,
            plan=self.premium_plan,
            end_date=end_date
        )

        # Should be approximately 15 (could be 14 depending on timing)
        self.assertIn(subscription.days_remaining(), [14, 15])

    def test_days_remaining_expired(self):
        """Test days_remaining for expired subscription."""
        subscription = Subscription.objects.create(
            user=self.user,
            plan=self.premium_plan,
            end_date=timezone.now() - timedelta(days=5),
            status='expired'
        )

        self.assertEqual(subscription.days_remaining(), 0)

    def test_subscription_string(self):
        """Test subscription string representation."""
        subscription = Subscription.objects.create(
            user=self.user,
            plan=self.premium_plan,
            end_date=timezone.now() + timedelta(days=30)
        )

        self.assertEqual(str(subscription),
                         f"{self.user.username} - {self.premium_plan.name}")


class AutoCreateSubscriptionTests(TestCase):
    """Tests for automatic subscription creation on user creation."""

    @classmethod
    def setUpTestData(cls):
        """Create free plan for auto-assignment."""
        cls.free_plan = SubscriptionPlan.objects.create(
            name='Free',
            price=Decimal('0.00'),
            duration_days=36500,
            description='Free tier',
            features='Basic features'
        )

    def test_subscription_auto_created_on_user_creation(self):
        """Test that subscription is automatically created when user is created."""
        user = CustomUser.objects.create_user(
            username='newuser',
            email='new@example.com',
            password='testpass123'
        )

        # Should have a subscription
        self.assertTrue(hasattr(user, 'subscription'))
        self.assertEqual(user.subscription.plan.name, 'Free')
        self.assertTrue(user.subscription.is_active())


class UserIsPremiumTests(TestCase):
    """Tests for user is_premium property."""

    @classmethod
    def setUpTestData(cls):
        """Set up plans."""
        cls.free_plan = SubscriptionPlan.objects.create(
            name='Free',
            price=Decimal('0.00'),
            duration_days=36500,
            description='Free tier',
            features='Basic features'
        )
        cls.premium_plan = SubscriptionPlan.objects.create(
            name='Premium',
            price=Decimal('9.99'),
            duration_days=30,
            description='Premium tier',
            features='All features'
        )

    def test_free_user_not_premium(self):
        """Test that free user is not premium."""
        user = CustomUser.objects.create_user(
            username='freeuser',
            email='free@example.com',
            password='testpass123'
        )

        self.assertFalse(user.is_premium)

    def test_premium_user_is_premium(self):
        """Test that premium user is premium."""
        user = CustomUser.objects.create_user(
            username='premiumuser',
            email='premium@example.com',
            password='testpass123'
        )

        # Update to premium plan
        user.subscription.plan = self.premium_plan
        user.subscription.save()

        self.assertTrue(user.is_premium)

    def test_expired_premium_not_premium(self):
        """Test that expired premium user is not premium."""
        user = CustomUser.objects.create_user(
            username='expireduser',
            email='expired@example.com',
            password='testpass123'
        )

        # Set to premium but expired
        user.subscription.plan = self.premium_plan
        user.subscription.end_date = timezone.now() - timedelta(days=1)
        user.subscription.save()

        self.assertFalse(user.is_premium)


class SubscriptionViewTests(TestCase):
    """Tests for subscription views."""

    @classmethod
    def setUpTestData(cls):
        """Set up plans."""
        cls.free_plan = SubscriptionPlan.objects.create(
            name='Free',
            price=Decimal('0.00'),
            duration_days=36500,
            description='Free tier',
            features='Basic features'
        )
        cls.premium_plan = SubscriptionPlan.objects.create(
            name='Premium',
            price=Decimal('9.99'),
            duration_days=30,
            description='Premium tier',
            features='All features',
            stripe_price_id='price_test_premium'
        )

    def setUp(self):
        """Set up user and client."""
        self.user = CustomUser.objects.create_user(
            username='testuser',
            email='test@example.com',
            password='testpass123'
        )
        self.client = Client()
        self.client.login(username='testuser', password='testpass123')

    def test_subscription_list_view(self):
        """Test subscription plans list view."""
        response = self.client.get(reverse('subscription_plans'))

        self.assertEqual(response.status_code, 200)
        self.assertIn('plans', response.context)

    def test_subscription_list_shows_plans(self):
        """Test that subscription list shows available plans."""
        response = self.client.get(reverse('subscription_plans'))

        self.assertContains(response, 'Free')
        self.assertContains(response, 'Premium')

    @patch('stripe.checkout.Session.create')
    def test_subscribe_creates_checkout_session(self, mock_checkout):
        """Test that subscribe action creates Stripe checkout session."""
        mock_checkout.return_value = MagicMock(
            url='https://checkout.stripe.com/test')

        response = self.client.post(
            reverse('subscribe', kwargs={'plan_id': self.premium_plan.id})
        )

        # Should redirect to Stripe checkout
        self.assertEqual(response.status_code, 302)


class StripeWebhookTests(TestCase):
    """Tests for Stripe webhook handling."""

    @classmethod
    def setUpTestData(cls):
        """Set up plans."""
        cls.free_plan = SubscriptionPlan.objects.create(
            name='Free',
            price=Decimal('0.00'),
            duration_days=36500,
            description='Free tier',
            features='Basic features'
        )
        cls.premium_plan = SubscriptionPlan.objects.create(
            name='Premium',
            price=Decimal('9.99'),
            duration_days=30,
            description='Premium tier',
            features='All features',
            stripe_price_id='price_test_premium'
        )

    def setUp(self):
        """Set up user."""
        self.user = CustomUser.objects.create_user(
            username='testuser',
            email='test@example.com',
            password='testpass123',
            stripe_customer_id='cus_test123'
        )

    @patch('stripe.Webhook.construct_event')
    def test_webhook_invalid_payload(self, mock_construct):
        """Test handling webhook with invalid payload."""
        mock_construct.side_effect = ValueError("Invalid payload")

        response = self.client.post(
            reverse('stripe_webhook'),
            content_type='application/json',
            data='{}',
            HTTP_STRIPE_SIGNATURE='test_sig'
        )

        self.assertEqual(response.status_code, 400)


class SubscriptionCancellationTests(TestCase):
    """Tests for subscription cancellation."""

    @classmethod
    def setUpTestData(cls):
        """Set up plans."""
        cls.free_plan = SubscriptionPlan.objects.create(
            name='Free',
            price=Decimal('0.00'),
            duration_days=36500,
            description='Free tier',
            features='Basic features'
        )
        cls.premium_plan = SubscriptionPlan.objects.create(
            name='Premium',
            price=Decimal('9.99'),
            duration_days=30,
            description='Premium tier',
            features='All features',
            stripe_price_id='price_test_premium'
        )

    def setUp(self):
        """Set up user with premium subscription."""
        self.user = CustomUser.objects.create_user(
            username='testuser',
            email='test@example.com',
            password='testpass123'
        )

        # Delete auto-created subscription if exists
        Subscription.objects.filter(user=self.user).delete()

        # Create premium subscription
        self.subscription = Subscription.objects.create(
            user=self.user,
            plan=self.premium_plan,
            stripe_subscription_id='sub_test123',
            end_date=timezone.now() + timedelta(days=30)
        )

        self.client = Client()
        self.client.login(username='testuser', password='testpass123')

    def test_subscription_can_be_cancelled_manually(self):
        """Test that subscription status can be changed to cancelled."""
        self.subscription.status = 'cancelled'
        self.subscription.save()

        self.subscription.refresh_from_db()
        self.assertEqual(self.subscription.status, 'cancelled')
        self.assertFalse(self.subscription.is_active())
