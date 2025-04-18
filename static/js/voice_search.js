class MusicVoiceSearch {
    constructor() {
        this.voiceSearchBtn = document.getElementById('voice-search-btn');
        this.searchInput = document.getElementById('search-input');
        this.voiceAnimation = document.getElementById('voice-animation');
        this.searchResults = document.getElementById('search-results');
        this.loadingSpinner = document.getElementById('loading');
        this.sortSelect = document.getElementById('sort-select');
        this.queryContainer = document.getElementById('query');
        this.recognition = null;
        this.isListening = false;

        this.init();
    }

    init() {
        if (!('SpeechRecognition' in window || 'webkitSpeechRecognition' in window)) {
            this.voiceSearchBtn.style.display = 'none';
            console.log('Web Speech API is not supported in this browser.');
            return;
        }

        const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
        this.recognition = new SpeechRecognition();
        this.setupRecognition();
        this.setupEventListeners();
    }

    setupRecognition() {
        this.recognition.continuous = false;
        this.recognition.interimResults = false;
        this.recognition.lang = 'en-US';

        this.recognition.onstart = () => this.handleRecognitionStart();
        this.recognition.onresult = (event) => this.handleRecognitionResult(event);
        this.recognition.onerror = (event) => this.handleRecognitionError(event);
        this.recognition.onend = () => this.handleRecognitionEnd();
    }

    setupEventListeners() {
        this.voiceSearchBtn.addEventListener('click', () => this.toggleRecognition());
        this.searchInput.form?.addEventListener('submit', (e) => {
            if (!this.searchInput.value.trim()) {
                e.preventDefault();
            }
        });
        this.searchInput.addEventListener('input', () => this.resetUI());
        this.sortSelect.addEventListener('change', () => this.performSearch(this.searchInput.value));

    }

    toggleRecognition() {
        if (this.isListening) {
            this.recognition.stop();
        } else {
            this.recognition.start();
        }
        this.isListening = !this.isListening;
    }

    handleRecognitionStart() {
        this.voiceAnimation.style.display = 'flex';
        this.voiceSearchBtn.classList.remove('btn-secondary');
        this.voiceSearchBtn.classList.add('btn-danger');
        this.voiceSearchBtn.innerHTML = '<i class="fa-solid fa-stop" aria-hidden="true"></i><span class="visually-hidden">Stop Voice Search</span>';
        this.searchResults.classList.add('loading');
    }

    handleRecognitionResult(event) {
        const transcript = event.results[0][0].transcript;
        this.searchInput.value = transcript;
        this.resetUI();

        if (transcript.trim()) {
            this.performSearch(transcript);
        }
    }

    async performSearch(query) {
        this.searchResults.classList.add('loading');
        this.loadingSpinner.classList.add('active');

        try {
            const params = new URLSearchParams({
                q: query,
                sort: this.sortSelect.value
            });

            const response = await fetch(`${window.location.pathname}?${params.toString()}`, {
                headers: {
                    'X-Requested-With': 'XMLHttpRequest'
                }
            });

            this.queryContainer.textContent = "Search results for: " + query;

            if (!response.ok) throw new Error('Search failed');

            const data = await response.json();
            this.updateSearchResults(data.tracks);

            // Update URL without page reload
            const newUrl = `${window.location.pathname}?${params.toString()}`;
            window.history.pushState({path: newUrl}, '', newUrl);

        } catch (error) {
            console.error('Search error:', error);
            this.showError('Search failed. Please try again.');
        } finally {
            this.searchResults.classList.remove('loading');
            this.loadingSpinner.classList.remove('active');
        }
    }

    updateSearchResults(tracks) {
        this.searchResults.innerHTML = '';

        if (!tracks || tracks.length === 0) {
            this.searchResults.innerHTML = `
                <div class="col-12 fade-in">
                    <p class="text-muted">No tracks found.</p>
                </div>`;
            return;
        }

        tracks.forEach(track => {
            const artistsHtml = track.artists.join(', ');
            const releaseYear = new Date(track.release_date).getFullYear();

            const trackHtml = `
                <div class="col fade-in">
                    <div class="card h-100 shadow-sm">
                        <img src="${track.image_url}" class="card-img-top" alt="Album art for ${track.title}">
                        <div class="card-body d-flex flex-column">
                            <h3 class="card-title h5 mb-1">${track.title}</h3>
                            <p class="card-text text-muted mb-1">${artistsHtml}</p>
                            <p class="card-text small text-muted mb-2">${track.album} (${releaseYear})</p>
                            <div class="d-flex justify-content-between align-items-center mt-auto">
                                <span class="badge bg-primary">Popularity: ${track.popularity}</span>
                                <a href="/music/track/${track.id}" class="btn btn-outline-primary btn-sm">View Details</a>
                            </div>
                        </div>
                        ${track.preview_url ? `
                            <div class="card-footer bg-transparent border-0 p-2">
                                <audio controls class="w-100">
                                    <source src="${track.preview_url}" type="audio/mpeg">
                                    Your browser does not support the audio element.
                                </audio>
                            </div>
                        ` : ''}
                    </div>
                </div>`;

            this.searchResults.insertAdjacentHTML('beforeend', trackHtml);
        });
    }

    showError(message) {
        this.searchResults.innerHTML = `
            <div class="col-12 fade-in">
                <div class="alert alert-danger" role="alert">
                    ${message}
                </div>
            </div>`;
    }

    handleRecognitionError(event) {
        console.error('Speech recognition error:', event.error);
        this.resetUI();
        this.showError('Voice recognition failed. Please try again.');
    }

    handleRecognitionEnd() {
        this.resetUI();
    }

    resetUI() {
        this.voiceAnimation.style.display = 'none';
        this.voiceSearchBtn.classList.remove('btn-danger');
        this.voiceSearchBtn.classList.add('btn-secondary');
        this.voiceSearchBtn.innerHTML = '<i class="fa-solid fa-microphone" aria-hidden="true"></i><span class="visually-hidden">Voice Search</span>';
        this.isListening = false;
    }
}

document.addEventListener('DOMContentLoaded', () => new MusicVoiceSearch());