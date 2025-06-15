from django.contrib import messages
from django.http import JsonResponse, HttpResponseForbidden
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth import login, logout, get_user_model
from django.contrib.auth.decorators import login_required

from music.spotify import get_spotify_client
from .forms import SignUpForm, LoginForm, UserProfileForm, UserPreferencesForm
from .models import UserActivity, CustomUser # Added CustomUser

User = get_user_model() # Standard way to get the User model


def signup(request):
    if request.method == 'POST':
        form = SignUpForm(request.POST, request.FILES)
        if form.is_valid():
            user = form.save()
            user.refresh_from_db()
            user.profile_picture = form.cleaned_data.get('profile_picture')
            user.save()
            login(request, user)
            sp = get_spotify_client(request).auth_manager.get_authorize_url()
            messages.success(request, 'Account created successfully. Welcome to MeloMatch!')
            # return redirect('dashboard')
            return redirect(sp)
    else:
        form = SignUpForm()
    return render(request, 'users/signup.html', {'form': form})


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
def user_profile(request, username=None):
    if username:
        # Optimized query: select_related for one-to-one/foreignkey, prefetch_related for many-to-many/reverse foreignkey
        # Assuming 'subscription' is a OneToOneField or ForeignKey on CustomUser to a Subscription model,
        # and Subscription model has a ForeignKey 'plan' to a SubscriptionPlan model.
        # The 'following' and 'followers' are ManyToManyFields.
        profile_user = get_object_or_404(
            User.objects.select_related('subscription__plan').prefetch_related('following', 'followers'),
            username=username
        )
    else:
        # For the request.user, if these related objects are accessed, they'd also benefit from prefetching.
        # However, request.user is often already a fully materialized object.
        # For consistency and if template accesses these, prefetch:
        current_user_pk = request.user.pk
        profile_user = User.objects.select_related('subscription__plan').prefetch_related('following', 'followers').get(pk=current_user_pk)
        # request.user = profile_user # Optionally replace request.user if it's a SimpleLazyObject without these prefetches

    is_own_profile = (request.user == profile_user)
    is_following = False
    if request.user.is_authenticated and not is_own_profile:
        is_following = request.user.following.filter(pk=profile_user.pk).exists()

    if request.method == 'POST' and is_own_profile: # Profile update only for own profile
        profile_form = UserProfileForm(request.POST, request.FILES, instance=request.user)
        if profile_form.is_valid():
            profile_form.save()
            messages.success(request, 'Your profile has been updated successfully.')
            return redirect('user_profile', username=request.user.username) # Redirect to own profile
    else:
        profile_form = UserProfileForm(instance=profile_user) if is_own_profile else None


    context = {
        'profile_user': profile_user,
        'profile_form': profile_form, # Will be None if not own profile
        'is_own_profile': is_own_profile,
        'is_following': is_following,
        'followers_count': profile_user.followers.count(),
        'following_count': profile_user.following.count(),
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
    user_to_follow = get_object_or_404(User, username=username_to_follow)
    if request.user == user_to_follow:
        messages.warning(request, "You cannot follow yourself.")
    else:
        request.user.following.add(user_to_follow)
        messages.success(request, f"You are now following {username_to_follow}.")
        UserActivity.objects.create(user=request.user, activity_type='follow', description=f"Started following {user_to_follow.username}")
        UserActivity.objects.create(user=user_to_follow, activity_type='new_follower', description=f"Is now followed by {request.user.username}")
    return redirect('user_profile', username=username_to_follow)


@login_required
def unfollow_user(request, username_to_unfollow):
    user_to_unfollow = get_object_or_404(User, username=username_to_unfollow)
    if request.user.following.filter(pk=user_to_unfollow.pk).exists():
        request.user.following.remove(user_to_unfollow)
        messages.success(request, f"You have unfollowed {username_to_unfollow}.")
        UserActivity.objects.create(user=request.user, activity_type='unfollow', description=f"Unfollowed {user_to_unfollow.username}")
    else:
        messages.warning(request, f"You are not following {username_to_unfollow}.")
    return redirect('user_profile', username=username_to_unfollow)


@login_required
def list_followers(request, username):
    user = get_object_or_404(User, username=username)
    followers = user.followers.all()
    context = {
        'profile_user': user,
        'users_list': followers,
        'list_type': 'Followers'
    }
    return render(request, 'users/user_follow_list.html', context)


@login_required
def list_following(request, username):
    user = get_object_or_404(User, username=username)
    following = user.following.all()
    context = {
        'profile_user': user,
        'users_list': following,
        'list_type': 'Following'
    }
    return render(request, 'users/user_follow_list.html', context)
