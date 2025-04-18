document.addEventListener('DOMContentLoaded', function () {
    const form = document.querySelector('form');
    const playlistName = document.getElementById('playlist-name');
    const createBtn = document.querySelector('.create-btn');
    const coverImageInput = document.getElementById('cover-image');
    const coverImagePreview = document.getElementById('cover-image-preview');

    form.addEventListener('submit', function (e) {
        e.preventDefault();
        if (playlistName.value.trim() !== '') {
            createBtn.innerHTML = '<i class="fa-solid fa-spinner fa-spin me-2"></i>Creating...';
            createBtn.disabled = true;
            setTimeout(() => {
                this.submit();
            }, 1000);
        }
    });

    coverImageInput.addEventListener('change', function (e) {
        const file = e.target.files[0];
        if (file) {
            const reader = new FileReader();
            reader.onload = function (e) {
                coverImagePreview.style.backgroundImage = `url(${e.target.result})`;
                coverImagePreview.innerHTML = '';
            }
            reader.readAsDataURL(file);
        }
    });

    coverImagePreview.addEventListener('click', function () {
        coverImageInput.click();
    });

});

