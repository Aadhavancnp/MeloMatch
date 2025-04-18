document.addEventListener('DOMContentLoaded', function () {
    const popularityMeter = document.querySelector('.popularity-meter');
    const popularityFill = popularityMeter.querySelector('.popularity-fill');
    const popularity = popularityMeter.dataset.popularity;

    setTimeout(() => {
        popularityFill.style.width = `${popularity}%`;
    }, 500);
});

