/**
 * Mathia AI Voice Assistant
 * Handles recording, visualization, and uploading of voice notes.
 */

class VoiceAssistant {
    constructor() {
        this.mediaRecorder = null;
        this.audioChunks = [];
        this.isRecording = false;
        this.recordingTimer = null;
        this.startTime = 0;

        // UI Elements
        this.recordBtn = document.getElementById('voice-record-btn');
        this.cancelBtn = document.getElementById('cancel-recording');
        this.overlay = document.getElementById('voice-recording-overlay');
        this.timerDisplay = this.overlay.querySelector('.recording-timer');
        this.inputGroup = document.querySelector('.chat-message .input-group');

        this.init();
    }

    async init() {
        if (!this.recordBtn) return;

        this.recordBtn.addEventListener('click', () => this.toggleRecording());
        if (this.cancelBtn) {
            this.cancelBtn.addEventListener('click', () => this.cancelRecording());
        }
    }

    async toggleRecording() {
        if (this.isRecording) {
            this.stopRecording();
        } else {
            await this.startRecording();
        }
    }

    async startRecording() {
        try {
            const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
            this.mediaRecorder = new MediaRecorder(stream);
            this.audioChunks = [];

            this.mediaRecorder.ondataavailable = (event) => {
                this.audioChunks.push(event.data);
            };

            this.mediaRecorder.onstop = () => this.handleRecordingStop();

            this.mediaRecorder.start();
            this.isRecording = true;
            this.showOverlay();
            this.startTimer();

            // Visual feedback
            this.recordBtn.classList.add('recording');
            this.recordBtn.innerHTML = '<i class="fas fa-stop"></i>';
            this.recordBtn.style.color = '#ff4757';
        } catch (err) {
            console.error("Error accessing microphone:", err);
            alert("Please allow microphone access to record voice notes.");
        }
    }

    stopRecording() {
        if (this.mediaRecorder && this.isRecording) {
            this.mediaRecorder.stop();
            this.mediaRecorder.stream.getTracks().forEach(track => track.stop());
            this.isRecording = false;
            this.hideOverlay();
            this.stopTimer();

            this.recordBtn.classList.remove('recording');
            this.recordBtn.innerHTML = '<i class="fas fa-microphone"></i>';
            this.recordBtn.style.color = '';
        }
    }

    cancelRecording() {
        if (this.mediaRecorder && this.isRecording) {
            this.mediaRecorder.stop();
            this.mediaRecorder.stream.getTracks().forEach(track => track.stop());
            this.isRecording = false;
            this.audioChunks = [];
            this.hideOverlay();
            this.stopTimer();

            this.recordBtn.classList.remove('recording');
            this.recordBtn.innerHTML = '<i class="fas fa-microphone"></i>';
            this.recordBtn.style.color = '';
        }
    }

    showOverlay() {
        this.overlay.style.display = 'flex';
        this.inputGroup.style.opacity = '0.3';
        this.inputGroup.style.pointerEvents = 'none';
    }

    hideOverlay() {
        this.overlay.style.display = 'none';
        this.inputGroup.style.opacity = '1';
        this.inputGroup.style.pointerEvents = 'all';
    }

    startTimer() {
        this.startTime = Date.now();
        this.recordingTimer = setInterval(() => {
            const elapsed = Math.floor((Date.now() - this.startTime) / 1000);
            const mins = Math.floor(elapsed / 60);
            const secs = elapsed % 60;
            this.timerDisplay.textContent = `${mins}:${secs.toString().padStart(2, '0')}`;
        }, 1000);
    }

    stopTimer() {
        clearInterval(this.recordingTimer);
        this.timerDisplay.textContent = '0:00';
    }

    async handleRecordingStop() {
        if (this.audioChunks.length === 0) return;

        const audioBlob = new Blob(this.audioChunks, { type: 'audio/webm' });
        await this.uploadVoiceNote(audioBlob);
    }

    async uploadVoiceNote(blob) {
        const roomId = window.currentRoomId; // Assuming this is set globally in main.js
        if (!roomId) return;

        const formData = new FormData();
        formData.append('audio', blob);

        try {
            const response = await fetch(`/chatbot/api/rooms/${roomId}/voice/upload/`, {
                method: 'POST',
                headers: {
                    'X-CSRFToken': this.getCookie('csrftoken')
                },
                body: formData
            });

            const data = await response.json();
            if (data.success) {
                console.log("Voice note uploaded:", data);
                // Optionally show a placeholder in chat or wait for transcription
            }
        } catch (err) {
            console.error("Error uploading voice note:", err);
        }
    }

    getCookie(name) {
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
}

// Initialize on DOMContentLoaded
document.addEventListener('DOMContentLoaded', () => {
    window.voiceAssistant = new VoiceAssistant();
});
