"""
Comprehensive tests for core app.

Tests dashboard, contact form, about pages, and other core functionality.
"""
from datetime import timedelta
from decimal import Decimal

from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from subscription.models import Subscription, SubscriptionPlan
from users.models import CustomUser, UserActivity


class HomeViewTests(TestCase):
    """Tests for home page."""

    def test_home_page_loads(self):
        """Test home page loads successfully."""
        response = self.client.get(reverse('home'))
        self.assertEqual(response.status_code, 200)


class ContactUsViewTests(TestCase):
    """Tests for contact us page."""

    def test_contact_page_loads(self):
        """Test contact page loads successfully."""
        response = self.client.get(reverse('contact'))
        self.assertEqual(response.status_code, 200)


class AboutUsViewTests(TestCase):
    """Tests for about us page."""

    def test_about_page_loads(self):
        """Test about page loads successfully."""
        response = self.client.get(reverse('about'))
        self.assertEqual(response.status_code, 200)


class FAQViewTests(TestCase):
    """Tests for FAQ page."""

    def test_faq_page_loads(self):
        """Test FAQ page loads successfully."""
        response = self.client.get(reverse('faq'))
        self.assertEqual(response.status_code, 200)


class DashboardViewTests(TestCase):
    """Tests for the dashboard view."""

    @classmethod
    def setUpTestData(cls):
        """Set up test data once for all tests."""
        # Create subscription plan
        cls.free_plan = SubscriptionPlan.objects.create(
            name='Free',
            price=Decimal('0.00'),
            duration_days=36500,
            description='Free tier',
            features='Basic features'
        )

    def setUp(self):
        """Set up test user and client."""
        self.user = CustomUser.objects.create_user(
            username='testuser',
            email='test@example.com',
            password='testpass123'
        )

        # Create subscription for user if not auto-created
        if not hasattr(self.user, 'subscription'):
            Subscription.objects.create(
                user=self.user,
                plan=self.free_plan,
                end_date=timezone.now() + timedelta(days=365)
            )

        self.client = Client()
        self.client.login(username='testuser', password='testpass123')

    def test_dashboard_requires_login(self):
        """Test that dashboard requires authentication."""
        self.client.logout()
        response = self.client.get(reverse('dashboard'))

        # Should redirect to login
        self.assertEqual(response.status_code, 302)
        self.assertIn('/users/login/', response.url)


class UserActivityModelTests(TestCase):
    """Tests for user activity logging."""

    def setUp(self):
        SubscriptionPlan.objects.create(
            name='Free',
            price=Decimal('0.00'),
            duration_days=36500,
            description='Free tier',
            features='Basic features'
        )
        self.user = CustomUser.objects.create_user(
            username='testuser',
            email='test@example.com',
            password='testpass123'
        )

    def test_activity_creation(self):
        """Test creating user activity."""
        activity = UserActivity.objects.create(
            user=self.user,
            activity_type='test',
            description='Test activity'
        )

        self.assertEqual(activity.user, self.user)
        self.assertEqual(activity.activity_type, 'test')
        self.assertIsNotNone(activity.timestamp)

    def test_activity_ordering(self):
        """Test activities are ordered by timestamp."""
        UserActivity.objects.create(
            user=self.user,
            activity_type='first',
            description='First activity'
        )
        UserActivity.objects.create(
            user=self.user,
            activity_type='second',
            description='Second activity'
        )

        activities = UserActivity.objects.filter(
            user=self.user).order_by('-timestamp')
        self.assertEqual(activities.first().activity_type, 'second')
