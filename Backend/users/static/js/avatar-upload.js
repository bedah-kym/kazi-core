/**
 * Avatar Upload Widget with Cropper.js
 * Usage: Include this script on pages with [data-avatar-widget] element.
 * Requires: Cropper.js CSS+JS loaded via CDN
 */
(function() {
    'use strict';

    const widget = document.querySelector('[data-avatar-widget]');
    if (!widget) return;

    const currentImg = widget.querySelector('[data-avatar-img]');
    const fileInput = widget.querySelector('[data-avatar-input]');
    const csrfToken = document.querySelector('[name=csrfmiddlewaretoken]')?.value
        || document.cookie.match(/csrftoken=([^;]+)/)?.[1] || '';

    let cropperModal = null;
    let cropperInstance = null;

    // Click on avatar circle opens file picker
    widget.addEventListener('click', function(e) {
        if (e.target.closest('[data-avatar-save]') || e.target.closest('[data-avatar-cancel]')) return;
        fileInput.click();
    });

    fileInput.addEventListener('change', function() {
        const file = this.files[0];
        if (!file) return;

        if (file.size > 5 * 1024 * 1024) {
            alert('Image must be under 5MB');
            return;
        }

        const reader = new FileReader();
        reader.onload = function(e) {
            openCropperModal(e.target.result);
        };
        reader.readAsDataURL(file);
    });

    function openCropperModal(imageSrc) {
        // Create modal if it doesn't exist
        if (!document.getElementById('avatarCropModal')) {
            const modalHtml = `
            <div class="modal fade" id="avatarCropModal" tabindex="-1">
                <div class="modal-dialog modal-dialog-centered">
                    <div class="modal-content">
                        <div class="modal-header">
                            <h5 class="modal-title">Crop Avatar</h5>
                            <button type="button" class="btn-close" data-bs-dismiss="modal" data-avatar-cancel></button>
                        </div>
                        <div class="modal-body" style="max-height:400px;overflow:hidden;">
                            <img id="cropperImage" style="max-width:100%;display:block;" />
                        </div>
                        <div class="modal-footer">
                            <button type="button" class="btn btn-light" data-bs-dismiss="modal" data-avatar-cancel>Cancel</button>
                            <button type="button" class="btn btn-primary" data-avatar-save>
                                <i class="fas fa-check me-1"></i>Save
                            </button>
                        </div>
                    </div>
                </div>
            </div>`;
            document.body.insertAdjacentHTML('beforeend', modalHtml);
        }

        const cropImg = document.getElementById('cropperImage');
        cropImg.src = imageSrc;

        cropperModal = new bootstrap.Modal(document.getElementById('avatarCropModal'));
        cropperModal.show();

        // Initialize Cropper after modal is shown
        document.getElementById('avatarCropModal').addEventListener('shown.bs.modal', function onShown() {
            this.removeEventListener('shown.bs.modal', onShown);
            if (cropperInstance) cropperInstance.destroy();
            cropperInstance = new Cropper(cropImg, {
                aspectRatio: 1,
                viewMode: 1,
                dragMode: 'move',
                cropBoxResizable: true,
                cropBoxMovable: true,
                minCropBoxWidth: 64,
                minCropBoxHeight: 64,
            });
        });

        // Handle save
        document.querySelector('[data-avatar-save]').onclick = function() {
            if (!cropperInstance) return;
            const canvas = cropperInstance.getCroppedCanvas({
                width: 256,
                height: 256,
            });
            canvas.toBlob(function(blob) {
                uploadAvatar(blob);
            }, 'image/webp', 0.85);
        };
    }

    function uploadAvatar(blob) {
        const formData = new FormData();
        formData.append('avatar', blob, 'avatar.webp');

        const saveBtn = document.querySelector('[data-avatar-save]');
        saveBtn.disabled = true;
        saveBtn.innerHTML = '<i class="fas fa-spinner fa-spin me-1"></i>Saving...';

        fetch('/accounts/avatar/upload/', {
            method: 'POST',
            headers: { 'X-CSRFToken': csrfToken },
            body: formData,
        })
        .then(r => r.json())
        .then(data => {
            if (data.url) {
                // Update all avatar images on page
                document.querySelectorAll('[data-avatar-img]').forEach(img => {
                    img.src = data.url;
                });
                if (cropperModal) cropperModal.hide();
                if (cropperInstance) { cropperInstance.destroy(); cropperInstance = null; }
            } else {
                alert(data.error || 'Upload failed');
            }
        })
        .catch(() => alert('Upload failed'))
        .finally(() => {
            saveBtn.disabled = false;
            saveBtn.innerHTML = '<i class="fas fa-check me-1"></i>Save';
        });
    }

    // Cleanup on modal hidden
    document.addEventListener('hidden.bs.modal', function(e) {
        if (e.target.id === 'avatarCropModal' && cropperInstance) {
            cropperInstance.destroy();
            cropperInstance = null;
        }
    });
})();
