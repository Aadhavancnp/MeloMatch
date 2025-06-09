import logging
from celery import shared_task
from django.contrib.auth import get_user_model
# from users.models import Follow # Follow model is in users.models
# CustomUser is fetched via get_user_model()

from channels.layers import get_channel_layer
from asgiref.sync import async_to_sync

logger = logging.getLogger(__name__)

@shared_task(max_retries=3, default_retry_delay=60) # Retry after 1 min, up to 3 times
def fanout_listening_status_to_followers(acting_user_id: int, acting_user_username: str,
                                         acting_user_profile_pic_url: str | None,
                                         status_data: dict):
    """
    Celery task to broadcast a user's listening status to their followers.

    Args:
        acting_user_id: ID of the user whose status is being broadcasted.
        acting_user_username: Username of the acting user.
        acting_user_profile_pic_url: URL of the acting user's profile picture.
        status_data: Dictionary containing the track data and playback status.
                     (e.g., from services.live_updates_service.status_updater.update_redis_listening_status)
    """
    CustomUser = get_user_model()
    try:
        acting_user = CustomUser.objects.get(id=acting_user_id)
    except CustomUser.DoesNotExist:
        logger.error(f"Fanout task: Acting user with ID {acting_user_id} not found. Cannot fanout status.")
        return f"User {acting_user_id} not found."

    # Get all users who are following the 'acting_user'
    # The Follow model has 'follower' (who does the following) and 'following' (who is being followed).
    # So, if acting_user is being followed, we need to find Follow objects where 'following' is acting_user.
    # The 'followers_set' related_name on CustomUser (from Follow.following field) gives Follow objects.
    # Each object in 'followers_set.all()' is a Follow instance, where `follow_instance.follower` is the actual follower user.

    # Alternative way to get follower users directly:
    # This queryset directly fetches CustomUser objects that are followers.
    followers_users = CustomUser.objects.filter(following_set__following=acting_user)
    # This relies on CustomUser.following_set (default related_name for Follow.follower referencing CustomUser)
    # and Follow.following (field name for the user being followed).
    # If Follow model is: follower = FK(User, related_name='is_following'), following = FK(User, related_name='followed_by')
    # then it would be acting_user.followed_by.all(), and each item's `follower` field.
    # Given the re-added Follow model: follower = FK(CustomUser, related_name='following_set'), following = FK(CustomUser, related_name='followers_set')
    # So, to get users following `acting_user`, we query through `acting_user.followers_set.all()`
    # each item of which is a Follow object, and we need `item.follower`.

    # Correct way based on re-added Follow model:
    # followers_qs = acting_user.followers_set.select_related('follower').all()
    # follower_users_list = [follow_obj.follower for follow_obj in followers_qs]

    # Or, more directly using a queryset if the related names are clear:
    # If Follow.following has related_name='followers_of_this_user'
    # and Follow.follower has related_name='users_i_am_following'
    # Then: followers_users = acting_user.followers_of_this_user.select_related('follower').all() -> [Follow objects]
    # and then [f.follower for f in followers_users]

    # Let's use the clear approach based on the Follow model structure:
    # Follow.following is ForeignKey to CustomUser, related_name='followers_set'
    # Follow.follower is ForeignKey to CustomUser, related_name='following_set'
    # We want users who have `acting_user` in their `following_set__following` field.
    # No, simpler: iterate through Follow objects where `following == acting_user`.

    from users.models import Follow # Import here to avoid potential circularity at module level if models.py imports tasks
    actual_follower_users = []
    for follow_relation in Follow.objects.filter(following=acting_user).select_related('follower'):
        actual_follower_users.append(follow_relation.follower)

    if not actual_follower_users:
        logger.info(f"User {acting_user.username} has no followers. No fanout needed.")
        return f"User {acting_user.username} has no followers."

    channel_layer = get_channel_layer()
    event_payload = {
        'type': 'friend.listening.update', # This calls friend_listening_update in FriendActivityConsumer
        'broadcasting_user_id': acting_user_id,
        'broadcasting_user_username': acting_user_username,
        'broadcasting_user_profile_pic_url': acting_user_profile_pic_url,
        'status_data': status_data
    }

    sent_to_count = 0
    for follower_user in actual_follower_users:
        if follower_user.is_active: # Only send to active users
            follower_feed_group = f"friend_activity_feed__{follower_user.id}"
            async_to_sync(channel_layer.group_send)(follower_feed_group, event_payload)
            sent_to_count += 1
            logger.debug(f"Sent listening status of {acting_user_username} to follower {follower_user.username} (Group: {follower_feed_group})")

    logger.info(f"Fanned out listening status of {acting_user_username} to {sent_to_count} active followers.")
    return f"Fanned out status to {sent_to_count} followers."
