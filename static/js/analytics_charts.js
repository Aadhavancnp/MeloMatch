document.addEventListener('DOMContentLoaded', function () {
    // Helper function to get API URLs from the Django template
    function getApiUrl(elementId) {
        const element = document.getElementById(elementId);
        if (!element) {
            console.error(`API URL element not found: ${elementId}`);
            return null;
        }
        return JSON.parse(element.textContent);
    }

    const listeningTrendApiUrl = getApiUrl('listening_trend_api_url');
    const moodDistApiUrl = getApiUrl('mood_dist_api_url');
    const genreDistApiUrl = getApiUrl('genre_dist_api_url');
    const discoveryInsightsApiUrl = getApiUrl('discovery_insights_api_url');

    // Helper function to generate random colors for charts
    function getRandomColor(opacity = 1) {
        const r = Math.floor(Math.random() * 255);
        const g = Math.floor(Math.random() * 255);
        const b = Math.floor(Math.random() * 255);
        return `rgba(${r}, ${g}, ${b}, ${opacity})`;
    }

    function generateColorPalette(numColors) {
        const colors = [ // Predefined palette for better aesthetics
            'rgba(255, 99, 132, 0.7)', 'rgba(54, 162, 235, 0.7)', 'rgba(255, 206, 86, 0.7)',
            'rgba(75, 192, 192, 0.7)', 'rgba(153, 102, 255, 0.7)', 'rgba(255, 159, 64, 0.7)',
            'rgba(199, 199, 199, 0.7)', 'rgba(83, 102, 255, 0.7)', 'rgba(102, 255, 83, 0.7)',
            'rgba(255, 83, 102, 0.7)'
        ];
        if (numColors <= colors.length) {
            return colors.slice(0, numColors);
        }
        // Generate random if more colors are needed
        const additionalColors = Array.from({ length: numColors - colors.length }, () => getRandomColor(0.7));
        return [...colors, ...additionalColors];
    }


    // 1. Listening Time Trend (Line Chart)
    if (listeningTrendApiUrl && document.getElementById('listeningTimeChart')) {
        fetch(listeningTrendApiUrl)
            .then(response => response.json())
            .then(result => {
                if (result.error) {
                    document.getElementById('listeningTimeChart').parentElement.innerHTML = `<p class="text-danger text-center">Error: ${result.error}</p>`;
                    return;
                }
                const data = result.data;
                const ctx = document.getElementById('listeningTimeChart').getContext('2d');
                new Chart(ctx, {
                    type: 'line',
                    data: {
                        labels: data.map(item => item.date),
                        datasets: [{
                            label: 'Liked Songs per Day',
                            data: data.map(item => item.count),
                            borderColor: 'rgba(75, 192, 192, 1)',
                            backgroundColor: 'rgba(75, 192, 192, 0.2)',
                            tension: 0.1,
                            fill: true,
                        }]
                    },
                    options: {
                        responsive: true,
                        maintainAspectRatio: false,
                        scales: {
                            y: { beginAtZero: true, title: { display: true, text: 'Number of Liked Songs'} },
                            x: { title: { display: true, text: 'Date'} }
                        }
                    }
                });
            })
            .catch(error => {
                console.error('Error fetching listening time trend:', error);
                document.getElementById('listeningTimeChart').parentElement.innerHTML = `<p class="text-danger text-center">Could not load chart data.</p>`;
            });
    }

    // 2. Mood Distribution (Pie Chart)
    if (moodDistApiUrl && document.getElementById('moodChart')) {
        fetch(moodDistApiUrl)
            .then(response => response.json())
            .then(result => {
                if (result.error) {
                    document.getElementById('moodChart').parentElement.innerHTML = `<p class="text-danger text-center">Error: ${result.error}</p>`;
                    return;
                }
                const data = result.data;
                if (data.length === 0 || (data.length === 1 && (data[0].mood === 'No Data' || data[0].mood === 'Not Enough Data'))) {
                     document.getElementById('moodChart').parentElement.innerHTML = `<p class="text-info text-center">${data[0]?.mood || 'No mood data available to display.'}</p>`;
                     return;
                }
                const ctx = document.getElementById('moodChart').getContext('2d');
                new Chart(ctx, {
                    type: 'doughnut', // or 'pie'
                    data: {
                        labels: data.map(item => item.mood),
                        datasets: [{
                            label: 'Mood Distribution',
                            data: data.map(item => item.count),
                            backgroundColor: generateColorPalette(data.length),
                            hoverOffset: 4
                        }]
                    },
                    options: {
                        responsive: true,
                        maintainAspectRatio: false,
                        plugins: { legend: { position: 'top' } }
                    }
                });
            })
            .catch(error => {
                console.error('Error fetching mood distribution:', error);
                document.getElementById('moodChart').parentElement.innerHTML = `<p class="text-danger text-center">Could not load chart data.</p>`;
            });
    }

    // 3. Genre Distribution (Bar Chart)
    if (genreDistApiUrl && document.getElementById('genreChart')) {
        fetch(genreDistApiUrl)
            .then(response => response.json())
            .then(result => {
                 if (result.error) {
                    document.getElementById('genreChart').parentElement.innerHTML = `<p class="text-danger text-center">Error: ${result.error}</p>`;
                    return;
                }
                const data = result.data;
                 if (data.length === 0 || (data.length === 1 && (data[0].genre === 'No Liked Tracks' || data[0].genre === 'No Genre Data'))) {
                     document.getElementById('genreChart').parentElement.innerHTML = `<p class="text-info text-center">${data[0]?.genre || 'No genre data available to display.'}</p>`;
                     return;
                }
                const ctx = document.getElementById('genreChart').getContext('2d');
                new Chart(ctx, {
                    type: 'bar',
                    data: {
                        labels: data.map(item => item.genre),
                        datasets: [{
                            label: 'Top Genres by Liked Songs',
                            data: data.map(item => item.count),
                            backgroundColor: generateColorPalette(data.length),
                            borderColor: generateColorPalette(data.length).map(color => color.replace('0.7', '1')), // Darker border
                            borderWidth: 1
                        }]
                    },
                    options: {
                        indexAxis: 'y', // Horizontal bar chart
                        responsive: true,
                        maintainAspectRatio: false,
                        scales: { x: { beginAtZero: true, title: {display: true, text: 'Number of Liked Songs'} } },
                        plugins: { legend: { display: false } } // Hide legend for bar chart if too cluttered
                    }
                });
            })
            .catch(error => {
                console.error('Error fetching genre distribution:', error);
                document.getElementById('genreChart').parentElement.innerHTML = `<p class="text-danger text-center">Could not load chart data.</p>`;
            });
    }

    // 4. Music Discovery Insights (Text)
    if (discoveryInsightsApiUrl && document.getElementById('discoveryInsightsContent')) {
        const contentDiv = document.getElementById('discoveryInsightsContent');
        fetch(discoveryInsightsApiUrl)
            .then(response => response.json())
            .then(result => {
                if (result.error) {
                    contentDiv.innerHTML = `<p class="text-danger">Error: ${result.error}</p>`;
                    return;
                }
                const data = result.data;
                if (data.message) {
                    contentDiv.innerHTML = `<p class="text-info">${data.message}</p>`;
                } else if (data.new_genre_count > 0) {
                    let html = `<p>You've discovered <strong>${data.new_genre_count}</strong> new genre(s) in the last ${data.period_days} days!</p>`;
                    html += '<p>Keep exploring: <span class="text-success">';
                    html += data.newly_discovered_genres.join(', ');
                    html += '</span></p>';
                    contentDiv.innerHTML = html;
                } else {
                    contentDiv.innerHTML = `<p>No new genres discovered in the last ${data.period_days} days based on your liked songs. Time to explore something new!</p>`;
                }
            })
            .catch(error => {
                console.error('Error fetching music discovery insights:', error);
                contentDiv.innerHTML = '<p class="text-danger">Could not load discovery insights.</p>';
            });
    }
});
