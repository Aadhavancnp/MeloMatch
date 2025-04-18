let currentTrackId = '';

function addToPlaylist(trackId) {
    currentTrackId = trackId;
    $('#addToPlaylistModal').modal('show');
}

function confirmAddToPlaylist() {
    const playlistId = document.getElementById('playlistSelect').value;

    fetch('http://127.0.0.1:8000/music/playlist/add-track', {
        method: 'POST',
        headers: {
            'X-Requested-With': 'XMLHttpRequest',
            'Content-Type': 'application/x-www-form-urlencoded',
            'X-CSRFToken': getCookie('csrftoken')
        },
        body: `track_id=${currentTrackId}&playlist_id=${playlistId}`
    })
        .then(response => response.json())
        .then(data => {
            if (data.status === 'success') {
                showToast('Success', 'Track added to playlist successfully!', 'success');
            } else {
                showToast('Error', 'Failed to add track to playlist. Please try again.', 'error');
            }
            $('#addToPlaylistModal').modal('hide');
            currentTrackId = '';
            window.location.reload();
        })
        .catch(error => {
            console.error('Error:', error);
            showToast('Error', 'An error occurred. Please try again.', 'error');
            $('#addToPlaylistModal').modal('hide');
        });
}

function showToast(title, message, type) {
    const toastContainer = document.getElementById('toast-container');
    if (!toastContainer) {
        const container = document.createElement('div');
        container.id = 'toast-container';
        container.className = 'position-fixed bottom-0 end-0 p-3';
        document.body.appendChild(container);
    }

    const toastId = `toast-${Date.now()}`;
    const toastHtml = `
        <div id="${toastId}" class="toast" role="alert" aria-live="assertive" aria-atomic="true">
            <div class="toast-header bg-${type === 'success' ? 'success' : 'danger'} text-white">
                <strong class="me-auto">${title}</strong>
                <button type="button" class="btn-close btn-close-white" data-bs-dismiss="toast" aria-label="Close"></button>
            </div>
            <div class="toast-body">
                ${message}
            </div>
        </div>
    `;

    document.getElementById('toast-container').insertAdjacentHTML('beforeend', toastHtml);
    const toastElement = document.getElementById(toastId);
    const toast = new bootstrap.Toast(toastElement);
    toast.show();
}

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

