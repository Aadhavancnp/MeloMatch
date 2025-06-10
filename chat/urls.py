from django.urls import path
from . import views

app_name = 'chat_api' # Changed to be more specific for API endpoints

urlpatterns = [
    path('history/<str:room_id>/', views.get_chat_history_api, name='get_chat_history'),
    # Add other chat-related API URLs here if any in the future
]
