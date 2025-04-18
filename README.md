# MeloMatch 🎵: Personalized Music Streaming Platform

**MeloMatch** is a Django-based web application that provides a personalized music streaming experience. It integrates with
Spotify's API to offer music recommendations, playlist creation, and listening statistics.

## Project Description

MeloMatch aims to enhance the music listening experience by providing personalized recommendations and insights based on a
user's listening history. The application integrates with Spotify's API to fetch user data, including top tracks,
recently played songs, and recommended tracks. It also calculates listening time, determines favorite genres, and
displays user activities and playlists.

Key features include:

- User authentication and profile management
- Integration with Spotify API for music data
- Personalized music recommendations
- Playlist creation and management
- Listening statistics and insights
- FAQ and contact support

## Usage Instructions

### Installation

1. Clone the repository:
   ```
   git clone https://github.com/Aadhavancnp/MeloMatch.git
   cd MeloMatch
   ```

2. Create and activate a virtual environment:
   ```
   python -m venv venv
   source venv/bin/activate  # On Windows, use `venv\Scripts\activate`
   ```

3. Install the required dependencies:
   ```
   pip install -r requirements.txt
   ```

4. Set up the database:
   ```
   python manage.py migrate
   ```

5. Create a superuser:
   ```
   python manage.py createsuperuser
   ```

6. Run the development server:
   ```
   python manage.py runserver
   ```

### Configuration

1. Create a `.env` file in the project root and add your Spotify API credentials:
   ```
   SPOTIFY_CLIENT_ID=your_client_id
   SPOTIFY_CLIENT_SECRET=your_client_secret
   SPOTIFY_REDIRECT_URI=http://localhost:8000/music/callback
   ```

2. Update `MeloMatch/settings.py` with your database configuration if needed.

### Getting Started

1. Access the admin interface at `http://localhost:8000/admin/` and log in with your superuser credentials.

2. Use the `populate_faqs` management command to add sample FAQ items:
   ```
   python manage.py populate_faqs
   ```

3. Use the `populate_subscriptions` management command to add sample subscription plans:
   ```
   python manage.py populate_subscriptions
   ```

4. Visit `http://localhost:8000` to access the main application.

5. Log in with your Spotify account to start using the personalized features.