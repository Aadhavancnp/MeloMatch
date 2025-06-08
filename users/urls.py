from django.urls import path
from . import views

urlpatterns = [
    path('signup/', views.signup, name='signup'),
    path('login/', views.user_login, name='login'),
    path('logout/', views.user_logout, name='logout'),
    path('user_activities/', views.user_activities, name='user_activities'),
    path('settings/', views.settings, name='settings'),
    path('change-theme/', views.change_theme, name='change_theme'),

    # Profile URLs
    path('profile/', views.profile, name='profile'), # Logged-in user's own profile
    path('profile/<str:username>/', views.profile, name='user_profile_view'), # To view other users' profiles

    # Follow system URLs
    # Note: The <str:username> in profile-related follow/unfollow actions refers to the user being actioned upon.
    path('<str:username_to_follow>/follow/', views.follow_user, name='follow_user'),
    path('<str:username_to_unfollow>/unfollow/', views.unfollow_user, name='unfollow_user'),
    # For viewing lists, <str:username> is the user whose lists are being viewed.
    path('<str:username>/following/', views.user_following_list, name='user_following_list'),
    path('<str:username>/followers/', views.user_followers_list, name='user_followers_list'),
]
