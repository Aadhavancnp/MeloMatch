from django.urls import path
from . import views

app_name = 'live_sync_api'

urlpatterns = [
    path('session/start/', views.start_session_api, name='start_session'),
    path('session/<str:session_id>/update_host_status/', views.update_host_status_api, name='update_host_status'),
    path('session/<str:session_id>/status/', views.get_session_status_api, name='get_session_status'),
    path('session/<str:session_id>/join/', views.join_session_api, name='join_session'),
    path('session/<str:session_id>/leave/', views.leave_session_api, name='leave_session'),
    path('player/', views.sync_session_player_view, name='sync_player'), # Page to view/interact with a session
]
