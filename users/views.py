from django.contrib import messages
from django.http import JsonResponse
from django.shortcuts import render, redirect
from django.contrib.auth import login, logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth import get_user_model
from django.shortcuts import get_object_or_404
from django.http import HttpResponseForbidden, HttpResponse # For simple responses
from django.db import IntegrityError
import logging # For logger

from services.spotify_service.client import get_spotify_client # Updated import
from .forms import SignUpForm, LoginForm, UserProfileForm, UserPreferencesForm
from .models import UserActivity, Follow # Added Follow model
# from ratelimit.decorators import ratelimit # Import removed
from services.notification_service.tasks import send_email_task # Import the new task

logger = logging.getLogger(__name__) # Added logger

# @ratelimit(key='ip', rate='10/h', block=True) # Decorator removed
def signup(request):
    if request.method == 'POST':
        form = SignUpForm(request.POST, request.FILES)
        if form.is_valid():
            user = form.save()
            # user.refresh_from_db() # Not strictly necessary before accessing user.email if form.save() commits
            # user.profile_picture = form.cleaned_data.get('profile_picture') # This should be handled by the form's save method ideally
            # user.save() # User is already saved by form.save()

            login(request, user) # Log in the new user

            # Send welcome email
            subject = "Welcome to MeloMatch!"
            message = f"Hi {user.username},\n\nThank you for signing up for MeloMatch. We're excited to have you!"
            # In a real app, use HTML templates for richer emails:
            # html_message = render_to_string('emails/welcome_email.html', {'user': user})
            send_email_task.delay(subject, message, [user.email])

            # Redirect to Spotify authorization, then to dashboard
            # The message about account creation is good, maybe add one about Spotify link-up
            try:
                spotify_auth_url = get_spotify_client(request).auth_manager.get_authorize_url()
                messages.success(request, 'Account created! Please authorize with Spotify to complete your profile.')
                return redirect(spotify_auth_url)
            except Exception as e:
                logger.error(f"Error getting Spotify auth URL for new user {user.username}: {str(e)}")
                messages.warning(request, "Account created, but couldn't connect to Spotify right now. You can link your account later from settings.")
                return redirect('dashboard') # Fallback to dashboard
    else:
        form = SignUpForm()
    return render(request, 'users/signup.html', {'form': form})


# @ratelimit(key='ip', rate='10/m', block=True) # Decorator removed
def user_login(request):
    if request.method == 'POST':
        form = LoginForm(request, data=request.POST)
        if form.is_valid():
            user = form.get_user()
            login(request, user)
            sp = get_spotify_client(request).auth_manager.get_authorize_url()
            messages.success(request, 'You have successfully logged in.')
            # return redirect('dashboard')
            return redirect(sp)
    else:
        form = LoginForm()
    return render(request, 'users/login.html', {'form': form})


def user_logout(request):
    logout(request)
    messages.success(request, 'You have successfully logged out.')
    return redirect('login')


@login_required
def user_activities(request):
    activities = UserActivity.objects.filter(user=request.user).order_by('-timestamp')
    return render(request, 'users/user_activities.html', {'activities': activities})


@login_required
def profile(request, username=None): # Added username parameter, defaults to None
    User = get_user_model()
    if username:
        # Viewing another user's profile
        profile_user = get_object_or_404(User, username=username)
        is_own_profile = (request.user == profile_user)
    else:
        # Viewing own profile
        profile_user = request.user
        is_own_profile = True

    is_following = False
    if request.user.is_authenticated and not is_own_profile:
        is_following = Follow.objects.filter(follower=request.user, following=profile_user).exists()

    if request.method == 'POST' and is_own_profile: # Only allow editing own profile
        profile_form = UserProfileForm(request.POST, request.FILES, instance=profile_user)
        if profile_form.is_valid():
            profile_form.save()
            messages.success(request, 'Your profile has been updated successfully.')
            return redirect('profile') # Redirect to own profile URL
    else:
        profile_form = UserProfileForm(instance=profile_user) if is_own_profile else None
        # If viewing someone else's profile, don't show the form or show a read-only version.
        # For simplicity, we'll just not pass the form if it's not their own profile being edited.

    context = {
        'profile_user': profile_user,
        'is_own_profile': is_own_profile,
        'profile_form': profile_form, # Will be None if not is_own_profile and not POST
        'is_following': is_following,
        # followers_count and following_count are properties on CustomUser model
    }
    return render(request, 'users/profile.html', context)


@login_required
def settings(request):
    if request.method == 'POST':
        form = UserPreferencesForm(request.POST)
        if form.is_valid():
            # Save preferences to user's session or database
            theme = request.POST.get('theme')
            if theme in ['light', 'dark', 'system']:
                request.user.theme_preference = theme
                request.user.save()
            request.session['language'] = form.cleaned_data['language']
            return redirect('settings')
    else:
        # Load current preferences
        initial_data = {
            'language': request.session.get('language', 'en'),
        }
        form = UserPreferencesForm(initial=initial_data)
    return render(request, 'users/settings.html', {'form': form})


@login_required
def change_theme(request):
    if request.method == 'POST':
        theme = request.POST.get('theme')
        if theme in ['light', 'dark', 'system']:
            request.user.theme_preference = theme
            request.user.save()
    return JsonResponse({'status': 'success'})


@login_required
def follow_user(request, username_to_follow):
    if request.method == 'POST':
        User = get_user_model()
        try:
            user_to_follow = User.objects.get(username=username_to_follow)
        except User.DoesNotExist:
            messages.error(request, f"User @{username_to_follow} not found.")
            # Redirect to a sensible page, e.g., previous page or user's own profile
            return redirect(request.META.get('HTTP_REFERER', 'dashboard'))

        if request.user == user_to_follow:
            messages.warning(request, "You cannot follow yourself.")
            return redirect('profile', username=username_to_follow) # Assuming profile view takes username

        follow_obj, created = Follow.objects.get_or_create(follower=request.user, following=user_to_follow)

        if created:
            messages.success(request, f"You are now following @{username_to_follow}.")
            # Optionally, create a UserActivity record
            UserActivity.objects.create(
                user=request.user,
                activity_type='follow',
                description=f"Started following @{user_to_follow.username}"
            )
        else:
            messages.info(request, f"You are already following @{username_to_follow}.")

        # Redirect to the previous page or dashboard
        return redirect(request.META.get('HTTP_REFERER', 'dashboard'))
    return HttpResponseForbidden("Only POST requests are allowed for this action.")


@login_required
def unfollow_user(request, username_to_unfollow):
    if request.method == 'POST':
        User = get_user_model()
        try:
            user_to_unfollow = User.objects.get(username=username_to_unfollow)
        except User.DoesNotExist:
            messages.error(request, f"User @{username_to_unfollow} not found.")
            return redirect(request.META.get('HTTP_REFERER', 'dashboard'))

        deleted_count, _ = Follow.objects.filter(follower=request.user, following=user_to_unfollow).delete()

        if deleted_count > 0:
            messages.success(request, f"You have unfollowed @{username_to_unfollow}.")
            UserActivity.objects.create(
                user=request.user,
                activity_type='unfollow',
                description=f"Unfollowed @{user_to_unfollow.username}"
            )
        else:
            messages.info(request, f"You were not following @{username_to_unfollow}.")

        # Redirect to the previous page or dashboard
        return redirect(request.META.get('HTTP_REFERER', 'dashboard'))
    return HttpResponseForbidden("Only POST requests are allowed for this action.")


@login_required # Or allow anonymous viewing depending on requirements
def user_following_list(request, username):
    User = get_user_model()
    try:
        target_user = User.objects.get(username=username)
    except User.DoesNotExist:
        messages.error(request, f"User @{username} not found.")
        return redirect(request.META.get('HTTP_REFERER', 'dashboard'))

    following_relations = target_user.following_set.select_related('following').all()
    users_target_is_following = [relation.following for relation in following_relations]

    # Render the template with the context
    context = {
        'target_user': target_user,
        'users_list': users_target_is_following
    }
    return render(request, 'users/following_list.html', context)


@login_required # Or allow anonymous viewing
def user_followers_list(request, username):
    User = get_user_model()
    try:
        target_user = User.objects.get(username=username)
    except User.DoesNotExist:
        messages.error(request, f"User @{username} not found.")
        return redirect(request.META.get('HTTP_REFERER', 'dashboard'))

    follower_relations = target_user.followers_set.select_related('follower').all()
    followers_of_target = [relation.follower for relation in follower_relations]

    # Render the template with the context
    context = {
        'target_user': target_user,
        'users_list': followers_of_target
    }
    return render(request, 'users/followers_list.html', context)
