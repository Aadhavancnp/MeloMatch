document.addEventListener('DOMContentLoaded', function () {
    const searchInput = document.getElementById('track-search');
    const trackList = document.getElementById('track-list');
    const trackItems = trackList.getElementsByClassName('track-item');
    const editButton = document.getElementById('editButton');
    const deleteButton = document.getElementById('deleteButton');
    const deleteTrackButtons = document.querySelectorAll('.delete-track-button');
    const confirmDeletePlaylistButton = document.getElementById('confirmDeletePlaylist');
    let playlistId = document.getElementById('playlist-id').textContent;
    playlistId = playlistId.substring(1, playlistId.length - 1);
    let isEditMode = false;

    searchInput.addEventListener('input', function () {
        const searchTerm = this.value.toLowerCase();

        Array.from(trackItems).forEach(function (item) {
            const title = item.querySelector('.card-title').textContent.toLowerCase();
            const artists = item.querySelector('.card-text').textContent.toLowerCase();
            const album = item.querySelector('.text-body-secondary').textContent.toLowerCase();

            if (title.includes(searchTerm) || artists.includes(searchTerm) || album.includes(searchTerm)) {
                item.style.display = '';
                item.style.animation = 'fadeIn 0.5s ease-in-out';
            } else {
                item.style.display = 'none';
            }
        });
    });

    editButton.addEventListener('click', function () {
        isEditMode = !isEditMode;
        deleteTrackButtons.forEach(button => {
            button.style.display = isEditMode ? 'block' : 'none';
        });
        this.textContent = isEditMode ? 'Done Editing' : 'Edit Playlist';
    });

    deleteButton.addEventListener('click', function () {
        const deletePlaylistModal = new bootstrap.Modal(document.getElementById('deletePlaylistModal'));
        deletePlaylistModal.show();
    });

    confirmDeletePlaylistButton.addEventListener('click', function () {
        // Send a DELETE request to the server to delete the playlist
        fetch(`/music/playlist/${playlistId}/delete/`, {
            method: 'DELETE',
            headers: {
                'X-CSRFToken': getCookie('csrftoken')
            }
        }).then(response => {
            if (response.ok) {
                window.location.href = '/dashboard/';  // Redirect to dashboard after successful deletion
            } else {
                alert('Failed to delete playlist. Please try again.');
            }
        });
    });

    document.querySelectorAll('.delete-track').forEach(button => {
        button.addEventListener('click', function () {
            const trackId = this.dataset.trackId;
            // Send a DELETE request to the server to delete the track from the playlist
            fetch(`/music/playlist/${playlistId}/delete-track/${trackId}/`, {
                method: 'DELETE',
                headers: {
                    'X-CSRFToken': getCookie('csrftoken')
                }
            }).then(response => {
                if (response.ok) {
                    const trackCard = document.querySelector(`[data-track-id="${trackId}"]`);
                    trackCard.closest('.track-item').remove();
                    // update the track count
                    const trackCount = document.getElementById('track-count');
                    trackCount.textContent = parseInt(trackCount.textContent) - 1;

                } else {
                    alert('Failed to delete track. Please try again.');
                }
            });
        });
    });

    // Helper function to get CSRF token
    function getCookie(name) {
        let cookieValue = null;
        if (document.cookie && document.cookie !== '') {
            const cookies = document.cookie.split(';');
            for (let i = 0; i < cookies.length; i++) {
                const cookie = cookies[i].trim();
                if (cookie.substring(0, name.length + 1) === (name + '=')) {
                    cookieValue = decodeURIComponent(cookie.substring(name.length + 1));
                    break;
                }
            }
        }
        return cookieValue;
    }
});

