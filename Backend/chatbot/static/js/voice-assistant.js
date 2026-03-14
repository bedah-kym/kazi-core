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
        this.audioContext = null;
        this.analyser = null;
        this.waveformCanvas = null;
        this.waveformCtx = null;
        this.waveformAnim = null;
        this.resizeHandler = null;

        // UI Elements
        this.recordBtn = document.getElementById('voice-record-btn');
        this.cancelBtn = document.getElementById('cancel-recording');
        this.overlay = document.getElementById('voice-recording-overlay');
        this.waveformContainer = document.getElementById('recording-waveform');
        // Guard the timerDisplay and inputGroup in case the fixture or page
        // doesn't include them (tests/headless envs may have a minimal DOM).
        this.timerDisplay = this.overlay ? this.overlay.querySelector('.recording-timer') : null;
        this.inputGroup = document.querySelector('.chat-message .input-group') || null;

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
            this.startWaveform(stream);

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
            this.stopWaveform();

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
            this.stopWaveform();

            this.recordBtn.classList.remove('recording');
            this.recordBtn.innerHTML = '<i class="fas fa-microphone"></i>';
            this.recordBtn.style.color = '';
        }
    }

    showOverlay() {
        if (this.overlay) {
            this.overlay.style.display = 'flex';
        }
        // Only disable the text input while recording so the record/stop button
        // remains clickable. Previously disabling the whole input group made
        // the stop button unresponsive which broke the UX.
        if (this.inputGroup) {
            this.inputGroup.style.opacity = '0.8';
        }
        const textInput = document.getElementById('chat-message-input');
        if (textInput) textInput.disabled = true;
    }

    hideOverlay() {
        if (this.overlay) {
            this.overlay.style.display = 'none';
        }
        if (this.inputGroup) {
            this.inputGroup.style.opacity = '1';
        }
        const textInput = document.getElementById('chat-message-input');
        if (textInput) textInput.disabled = false;
    }

    startWaveform(stream) {
        if (!this.waveformContainer || !(window.AudioContext || window.webkitAudioContext)) {
            return;
        }
        try {
            this.audioContext = new (window.AudioContext || window.webkitAudioContext)();
            this.analyser = this.audioContext.createAnalyser();
            this.analyser.fftSize = 2048;
            const source = this.audioContext.createMediaStreamSource(stream);
            source.connect(this.analyser);

            if (!this.waveformCanvas) {
                this.waveformCanvas = document.createElement('canvas');
                this.waveformCanvas.className = 'recording-canvas';
                this.waveformContainer.innerHTML = '';
                this.waveformContainer.appendChild(this.waveformCanvas);
                this.waveformCtx = this.waveformCanvas.getContext('2d');
            }

            const resizeCanvas = () => {
                if (!this.waveformCanvas || !this.waveformContainer) return;
                const width = this.waveformContainer.clientWidth || 240;
                const height = this.waveformContainer.clientHeight || 32;
                this.waveformCanvas.width = width;
                this.waveformCanvas.height = height;
            };
            resizeCanvas();
            this.resizeHandler = resizeCanvas;
            window.addEventListener('resize', this.resizeHandler);

            const bufferLength = this.analyser.fftSize;
            const dataArray = new Uint8Array(bufferLength);

            const draw = () => {
                this.waveformAnim = requestAnimationFrame(draw);
                if (!this.waveformCtx || !this.waveformCanvas || !this.analyser) return;
                this.analyser.getByteTimeDomainData(dataArray);
                const width = this.waveformCanvas.width;
                const height = this.waveformCanvas.height;
                this.waveformCtx.clearRect(0, 0, width, height);
                this.waveformCtx.lineWidth = 2;
                this.waveformCtx.strokeStyle = '#667eea';
                this.waveformCtx.beginPath();
                const sliceWidth = width / bufferLength;
                let x = 0;
                for (let i = 0; i < bufferLength; i++) {
                    const v = dataArray[i] / 128.0;
                    const y = (v * height) / 2;
                    if (i === 0) {
                        this.waveformCtx.moveTo(x, y);
                    } else {
                        this.waveformCtx.lineTo(x, y);
                    }
                    x += sliceWidth;
                }
                this.waveformCtx.lineTo(width, height / 2);
                this.waveformCtx.stroke();
            };
            draw();
        } catch (err) {
            console.error('Waveform init failed:', err);
        }
    }

    stopWaveform() {
        if (this.waveformAnim) {
            cancelAnimationFrame(this.waveformAnim);
            this.waveformAnim = null;
        }
        if (this.resizeHandler) {
            window.removeEventListener('resize', this.resizeHandler);
            this.resizeHandler = null;
        }
        if (this.waveformCtx && this.waveformCanvas) {
            this.waveformCtx.clearRect(0, 0, this.waveformCanvas.width, this.waveformCanvas.height);
        }
        if (this.audioContext) {
            this.audioContext.close().catch(() => {});
            this.audioContext = null;
            this.analyser = null;
        }
    }

    startTimer() {
        this.startTime = Date.now();
        this.recordingTimer = setInterval(() => {
            const elapsed = Math.floor((Date.now() - this.startTime) / 1000);
            const mins = Math.floor(elapsed / 60);
            const secs = elapsed % 60;
            if (this.timerDisplay) {
                this.timerDisplay.textContent = `${mins}:${secs.toString().padStart(2, '0')}`;
            }
        }, 1000);
    }

    stopTimer() {
        clearInterval(this.recordingTimer);
        if (this.timerDisplay) this.timerDisplay.textContent = '0:00';
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
