from django.urls import reverse
from django.test import Client, TestCase
from .models import CustomUser, UserActivity

class UserSocialTestCase(TestCase):
    def setUp(self):
        self.user1 = CustomUser.objects.create_user(username='user1', email='user1@example.com', password='password')
        self.user2 = CustomUser.objects.create_user(username='user2', email='user2@example.com', password='password')
        self.user3 = CustomUser.objects.create_user(username='user3', email='user3@example.com', password='password') # For follower list test

        self.client = Client()
        self.client.login(username='user1', password='password')

    def test_follow_user(self):
        """Test following a user."""
        response = self.client.post(reverse('follow_user', args=[self.user2.username]))
        self.assertEqual(response.status_code, 302) # Should redirect to profile
        self.assertTrue(self.user1.following.filter(username=self.user2.username).exists())
        self.assertTrue(self.user2.followers.filter(username=self.user1.username).exists())

        # Check UserActivity creation
        self.assertTrue(UserActivity.objects.filter(user=self.user1, activity_type='follow', description=f"Started following {self.user2.username}").exists())
        self.assertTrue(UserActivity.objects.filter(user=self.user2, activity_type='new_follower', description=f"Is now followed by {self.user1.username}").exists())

    def test_unfollow_user(self):
        """Test unfollowing a user."""
        # First, follow the user
        self.user1.following.add(self.user2)

        response = self.client.post(reverse('unfollow_user', args=[self.user2.username]))
        self.assertEqual(response.status_code, 302) # Should redirect to profile
        self.assertFalse(self.user1.following.filter(username=self.user2.username).exists())
        self.assertFalse(self.user2.followers.filter(username=self.user1.username).exists())

        # Check UserActivity creation for unfollow
        self.assertTrue(UserActivity.objects.filter(user=self.user1, activity_type='unfollow', description=f"Unfollowed {self.user2.username}").exists())

    def test_follow_self_not_allowed(self):
        """Test that a user cannot follow themselves."""
        response = self.client.post(reverse('follow_user', args=[self.user1.username]))
        # Check for a redirect (or specific status code if view handles it differently)
        # and that user1 is not in their own following list.
        self.assertFalse(self.user1.following.filter(username=self.user1.username).exists())
        # Check for a message if your view adds one (optional to test directly)
        # For example, if using Django messages framework:
        # messages = list(get_messages(response.wsgi_request))
        # self.assertTrue(any(message.level == messages_constants.WARNING and "You cannot follow yourself" in message.message for message in messages))


    def test_list_followers(self):
        """Test the list_followers view."""
        # user2 follows user1
        self.user2.following.add(self.user1)
        # user3 follows user1
        self.user3.following.add(self.user1)

        response = self.client.get(reverse('list_followers', args=[self.user1.username]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.user2.username)
        self.assertContains(response, self.user3.username)
        self.assertContains(response, "Followers for user1") # Based on list_type in context

    def test_list_following(self):
        """Test the list_following view."""
        # user1 follows user2 and user3
        self.user1.following.add(self.user2)
        self.user1.following.add(self.user3)

        response = self.client.get(reverse('list_following', args=[self.user1.username]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.user2.username)
        self.assertContains(response, self.user3.username)
        self.assertContains(response, "Following for user1") # Based on list_type in context

    def test_user_profile_view_own(self):
        """Test viewing own profile."""
        response = self.client.get(reverse('my_profile')) # URL for logged-in user's profile
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.user1.username)
        self.assertContains(response, "Edit Profile") # Own profile should have edit form

    def test_user_profile_view_other_not_following(self):
        """Test viewing another user's profile (not following)."""
        response = self.client.get(reverse('user_profile', args=[self.user2.username]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.user2.username)
        self.assertContains(response, "Follow</button>") # Should show Follow button

    def test_user_profile_view_other_following(self):
        """Test viewing another user's profile (currently following)."""
        self.user1.following.add(self.user2)
        response = self.client.get(reverse('user_profile', args=[self.user2.username]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.user2.username)
        self.assertContains(response, "Unfollow</button>") # Should show Unfollow button

    def test_user_profile_follower_following_counts(self):
        """Test follower and following counts on profile page."""
        # user1 follows user2
        self.user1.following.add(self.user2)
        # user3 follows user1
        self.user3.following.add(self.user1)

        # Check user1's profile (the logged-in user)
        response_user1 = self.client.get(reverse('my_profile'))
        self.assertContains(response_user1, f"<strong>{self.user1.followers.count()}</strong> Followers") # Should be 1 (user3)
        self.assertContains(response_user1, f"<strong>{self.user1.following.count()}</strong> Following") # Should be 1 (user2)

        # Check user2's profile
        response_user2 = self.client.get(reverse('user_profile', args=[self.user2.username]))
        self.assertContains(response_user2, f"<strong>{self.user2.followers.count()}</strong> Followers") # Should be 1 (user1)
        self.assertContains(response_user2, f"<strong>{self.user2.following.count()}</strong> Following") # Should be 0

        # Check user3's profile
        response_user3 = self.client.get(reverse('user_profile', args=[self.user3.username]))
        self.assertContains(response_user3, f"<strong>{self.user3.followers.count()}</strong> Followers") # Should be 0
        self.assertContains(response_user3, f"<strong>{self.user3.following.count()}</strong> Following") # Should be 1 (user1)
