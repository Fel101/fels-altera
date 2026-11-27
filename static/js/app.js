// Altera — AI Companion Frontend
// Phase 3: Voice Layer added (mic recording, waveform, STT, TTS playback)

// Session ID is set from the template via window.__SESSION_ID__
let currentSessionId = typeof window.__SESSION_ID__ !== 'undefined' ? window.__SESSION_ID__ : null;
let isStreaming = false;

// ============================================================
// Voice State (Phase 3)
// ============================================================
let voiceEnabled = false;       // User preference for TTS playback
let isRecording = false;        // Currently recording mic input
let mediaRecorder = null;       // MediaRecorder instance
let audioChunks = [];           // Collected audio chunks during recording
let analyser = null;            // Web Audio API analyser for waveform
let animationId = null;         // Animation frame ID for waveform drawing
let connectionState = 'checking';
let connectionCheckInFlight = false;
let recordingStarting = false;
let voiceTranscribing = false;
let voiceCapture = null;
let voiceActivity = null;
let messageLoadRun = 0;
let healthInterval, connectionInterval;
let providerLoadRun = 0, modelLoadRun = 0, providerRevision = 0, workspaceReadRun = 0, fetchedReadRun = 0;
let workspaceOrigin = null, workspaceBaseline = '', workspaceSerial = 0, providerBaseline = null;
let credentialsChanged = false;

// ============================================================
// Initialization
// ============================================================

AlteraPage.ready(() => MainUI.run(async () => {
    // The kids view is the chat with kids pals. Settings, provider, workspace and presence are not part of it, and
    // the server refuses their APIs for this browser, so there is nothing here to load them for.
    const kidsView = document.body.classList.contains('kids-view');
    setupMainEditTracking();
    if (!kidsView) { configureVoiceNotes(); configurePresence(); }
    const voiceReady = initVoiceLayer();
    // The page opens on the companion you talked to last (see companions.js). With no avatar at all, the default chat is used.
    const opened = typeof openCurrentCompanion === 'function' ? await openCurrentCompanion(voiceReady) : false;
    MainUI.assert();
    if (!opened) {
        if (currentSessionId) await loadMessages(currentSessionId);
        else clearMessages();
    }
    MainUI.assert();
    if (typeof openRequestedMeet === 'function') openRequestedMeet();
    resumePendingTurn();
    try {
        const composer = JSON.parse(AlteraStorage.session.getItem('altera_composer_draft') || 'null');
        if (composer) { await restoreBrowserDrafts([composer]); AlteraStorage.session.removeItem('altera_composer_draft'); }
        const drafts = JSON.parse(AlteraStorage.session.getItem('altera_restored_drafts') || '[]');
        if (drafts.length) { await restoreBrowserDrafts(drafts); AlteraStorage.session.removeItem('altera_restored_drafts'); }
        const receipt = JSON.parse(AlteraStorage.session.getItem('altera_restore_receipt') || 'null');
        if (receipt) {
            showReplyNotice('Backup restored. Unsent drafts are available under Review drafts.', true);
            AlteraStorage.session.removeItem('altera_restore_receipt');
        }
    } catch (err) { if (MainUI.active) showReplyNotice('Browser drafts could not be loaded yet. They remain saved for review.'); }

    MainUI.assert();

    setupAutoResize();
    if (!kidsView && typeof PresenceUI !== 'undefined') PresenceUI.refresh();

    if (!kidsView) {
        // Phase B: Load provider/model settings
        loadProviderSettings();
        // Main surface polish and local workspace tools
        checkOnboarding();
        checkSystemHealth();
        healthInterval = MainUI.every(() => { if (!document.hidden) checkSystemHealth(); }, 30000);
        checkSuiteGpu();
        MainUI.every(() => { if (!document.hidden) checkSuiteGpu(); }, 5000);
        refreshWorkspaceFiles(false);
    }
    updateSessionSignal();
    checkConnection();
    connectionInterval = MainUI.every(() => { if (!document.hidden) checkConnection(); }, 15000);
}));

async function checkSuiteGpu() {
    const light = document.getElementById('gpuLight');
    if (!light) return;
    let state = 'unknown';
    let lines = ['GPU status is unavailable.'];
    try {
        const {res, data} = await MainUI.json('/api/gpu', {cache: 'no-store', timeoutMs: 3000});
        if (res.ok && ['free', 'shared', 'alone'].includes(data.state)) {
            state = data.state;
            const line = (holder, mode) => {
                const date = new Date(holder.since);
                const since = holder.since && Number.isFinite(date.getTime()) ? ' since ' + date.toLocaleString() : '';
                return `${holder.name}${mode}: ${holder.what || 'model'}${since}`;
            };
            lines = data.holders.map(holder => line(holder, holder.alone ? ', alone' : ''));
            if (data.waiting) lines.push(line(data.waiting, ' is making room'));
            if (!lines.length) lines.push('The suite GPU lock is free.');
        }
    } catch (error) {
        if (!MainUI.active) return;
    }
    if (!MainUI.active) return;
    const label = document.getElementById('gpuLightLabel');
    const holders = document.getElementById('gpuLightHolders');
    light.dataset.state = state;
    label.textContent = {free: 'GPU free', shared: 'GPU shared', alone: 'GPU alone', unknown: 'GPU status'}[state];
    light.querySelector('summary').title = lines.join('\n');
    if ([...holders.children].map(item => item.textContent).join('\n') !== lines.join('\n')) {
        holders.replaceChildren(...lines.map(text => {
            const row = document.createElement('li');
            row.textContent = text;
            return row;
        }));
    }
}

function connectionStatusText() {
    return ({connected: 'Connected to Altera', checking: 'Connecting...',
        reconnecting: 'Reconnecting...', offline: 'Offline', denied: 'Access denied'})[connectionState];
}

function showConnectionState(state) {
    connectionState = state;
    const status = document.getElementById('statusText');
    if (!status) return;
    status.dataset.connection = state;
    status.title = state === 'reconnecting' ? 'Altera is unreachable. Check Tailscale and the PC.' : connectionStatusText();
    if (!isStreaming && !isRecording && !status.dataset.notes) status.textContent = connectionStatusText();
    if (state === 'connected' || state === 'denied') {
        if (typeof hideOfflineBanner === 'function') hideOfflineBanner();
    } else if (state === 'offline' || state === 'reconnecting') {
        if (typeof showOfflineBanner === 'function') showOfflineBanner();
    }
    const indicator = document.querySelector('.sidebar-header .status-indicator');
    if (indicator) {
        indicator.classList.toggle('online', state === 'connected');
        indicator.title = connectionStatusText();
    }
}

async function checkConnection() {
    if (!navigator.onLine) {
        showConnectionState('offline');
        return;
    }
    if (connectionCheckInFlight) return;
    connectionCheckInFlight = true;
    try {
        const {res: response, data} = await MainUI.json('/api/offline/status', {cache: 'no-store', timeoutMs: 5000});
        showConnectionState(response.status === 403 ? 'denied' : (data?.online === true ? 'connected' : 'reconnecting'));
    } catch {
        if (!MainUI.active) return;
        showConnectionState(navigator.onLine ? 'reconnecting' : 'offline');
    } finally {
        connectionCheckInFlight = false;
    }
}

window.addEventListener('online', checkConnection);
window.addEventListener('offline', () => showConnectionState('offline'));
document.addEventListener('visibilitychange', () => { if (!document.hidden) checkConnection(); });

// Kept for the places that used to refresh the chat list: the sidebar lists your companions now.
function loadSessions() {
    if (typeof loadCompanions === 'function') loadCompanions();
}

// ============================================================
// Voice Layer (Phase 3)
// ============================================================

async function initVoiceLayer() {
    try {
        const {res, data} = await MainUI.json('/api/voice/status');
        
        if (!res.ok) throw new Error(data.error || 'Voice status is unavailable');
        voiceEnabled = Boolean(data.tts_enabled);
        document.getElementById('voiceToggle').checked = voiceEnabled;
        updateVoiceStatus(data);
        // Microphone support and local transcription are checked separately.
        if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
            document.getElementById('micBtn').disabled = true;
            document.getElementById('micBtn').title = 'Microphone not available in this browser';
        } else if (!data.stt_available) {
            document.getElementById('micBtn').disabled = true;
            document.getElementById('micBtn').title = 'Install faster-whisper to enable transcription';
        }
    } catch (err) {
        if (!MainUI.active) return;
        console.error('Failed to init voice layer:', err);
        document.getElementById('voiceStatus').textContent = 'Could not connect to voice service';
    }
}

function updateVoiceStatus(statusData) {
    const statusEl = document.getElementById('voiceStatus');
    if (statusData.tts_enabled && voiceEnabled) {
        const state = statusData.voice_state;
        const note = (state === 'warming' || state === 'starting') ? ' (getting ready...)'
            : (state === 'down' ? ' (voice service is not running)' : '');
        statusEl.textContent = statusData.tts_engine === 'voicelab'
            ? statusData.voice_message : `TTS on — ${statusData.voice_name}${note}`;
        statusEl.style.color = state === 'down' ? 'var(--warning)' : 'var(--success)';
    } else if (statusData.config_tts_enabled) {
        statusEl.textContent = 'TTS enabled in config but turned off — toggle to hear Altera speak';
        statusEl.style.color = 'var(--warning)';
    } else if (statusData.tts_engine === 'kokoro') {
        // Switching on also turns speech on in config.yaml (POST /api/voice/toggle), for the local voice only.
        statusEl.textContent = statusData.local_voice_installed === false
            ? `Off. The local voice is not installed yet. ${statusData.install_hint || 'Install it first with voice\\setup.bat.'}`
            : 'Off. Switch it on to hear replies in the local voice.';
        statusEl.style.color = 'var(--text-muted)';
    } else if (statusData.tts_engine === 'voicelab') {
        statusEl.textContent = 'Voice Lab is selected. Switch Voice on to hear replies.';
        statusEl.style.color = 'var(--text-muted)';
    } else {
        statusEl.textContent = 'Off. The online voice sends every reply to Microsoft, so it is only switched on in config.yaml (voice.tts_enabled: true).';
        statusEl.style.color = 'var(--text-muted)';
    }
    if (statusData.stt_model_state === 'unavailable') {
        statusEl.textContent += ' · Local speech recognition model is missing or incomplete';
        statusEl.style.color = 'var(--warning)';
    }
}

async function loadVoiceLabOffer() {
    const line = document.getElementById('voiceLabStatus');
    const button = document.getElementById('voiceLabUse');
    if (!line || !button) return;
    try {
        const {res, data} = await MainUI.json('/api/voice/lab');
        if (!res.ok) throw new Error();
        button.hidden = !data.ready && !data.selected;
        button.dataset.engine = data.selected ? 'kokoro' : 'voicelab';
        button.textContent = data.selected ? 'Use Altera’s Kokoro' : 'Use Voice Lab for speech';
        line.textContent = data.selected && data.ready
            ? [data.notice, 'Voice Lab is selected for speech.'].filter(Boolean).join(' ') : data.message;
    } catch (_) {
        if (!MainUI.active) return;
        line.textContent = 'Could not check Voice Lab. Reopen Settings to try again.';
        button.hidden = true;
    }
}

async function useVoiceLab() {
    const line = document.getElementById('voiceLabStatus');
    const button = document.getElementById('voiceLabUse');
    button.disabled = true;
    try {
        const {res, data} = await MainUI.json('/api/voice/engine', {method: 'POST',
            headers: {'Content-Type': 'application/json'}, body: JSON.stringify({engine: button.dataset.engine})});
        if (!res.ok) throw new Error(data.error || 'Could not change the speech engine. Try again.');
        stopSpeech();
        await Promise.all([initVoiceLayer(), loadVoiceLabOffer(), checkSystemHealth()]);
    } catch (error) {
        if (MainUI.active) line.textContent = error.message;
    } finally {
        if (MainUI.active) button.disabled = false;
    }
}

async function toggleVoiceLayer(enabled) {
    if (!enabled && voiceActivity) voiceActivity.disable('Speech output switched off');
    const previous = voiceEnabled, toggle = document.getElementById('voiceToggle');
    toggle.disabled = true;
    if (!enabled) stopSpeech();

    try {
        const {res: saved, data: savedData} = await MainUI.json('/api/voice/toggle', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ enabled })
        });
        if (!saved.ok) throw new Error(savedData?.error || 'Speech setting could not be saved');
        voiceEnabled = enabled;
        const {res, data} = await MainUI.json('/api/voice/status');
        if (!res.ok) throw new Error(data.error || 'Voice status could not be checked');
        voiceEnabled = Boolean(data.tts_enabled);
        toggle.checked = voiceEnabled;
        updateVoiceStatus(data);
        checkSystemHealth();
    } catch (err) {
        if (!MainUI.active) return;
        voiceEnabled = previous; toggle.checked = previous;
        document.getElementById('voiceStatus').textContent = err.message;
    } finally { if (MainUI.active) toggle.disabled = false; }
}

// ============================================================
// Microphone Recording (Phase 3)
// ============================================================

async function toggleAutoInterruption(enabled) {
    const toggle = document.getElementById('autoInterruptToggle');
    const status = document.getElementById('autoInterruptStatus');
    if (!enabled) { voiceActivity?.disable('Off. Use the microphone button to interrupt manually.'); return; }
    if (!voiceEnabled || typeof VoiceActivity === 'undefined') {
        toggle.checked = false;
        status.textContent = 'Enable speech output first. The microphone button remains available.';
        return;
    }
    if (!voiceActivity) {
        voiceActivity = VoiceActivity.create({
            getContext: () => ({sessionId: currentSessionId,
                datasetEpoch: typeof AlteraAPI !== 'undefined' ? AlteraAPI.epoch : null,
                companionId: typeof Companion !== 'undefined' ? Companion.active?.id : null}),
            onState: state => {
                toggle.checked = state.enabled;
                status.textContent = state.message;
                const mic = document.getElementById('micBtn');
                mic.dataset.monitoring = String(state.monitoring);
                mic.setAttribute('aria-label', state.monitoring ? 'Speech interruption microphone active; click to record' : 'Record voice message');
                const indicator = document.getElementById('autoInterruptIndicator');
                if (indicator) {
                    indicator.hidden = !state.enabled && state.phase !== 'error';
                    indicator.dataset.phase = state.phase;
                    indicator.textContent = state.monitoring ? 'Microphone active: checking for speech to interrupt Altera.' : state.message;
                }
                if (!state.captureActive && voiceCapture?.activityCapture && !voiceCapture.finishing && !voiceTranscribing) {
                    cancelAutomaticRecording(state.message);
                }
            },
            onSpeech: handoff => startRecording(handoff),
        });
    }
    toggle.disabled = true;
    try {
        await voiceActivity.enable();
        voiceActivity.setSpeaking(Boolean(speechAudio));
    } catch (error) {
        voiceActivity.disable('Speech detection is unavailable. Use the microphone button.');
    } finally { toggle.disabled = false; }
}

document.addEventListener('visibilitychange', () => {
    if (document.hidden && voiceActivity) {
        endAutomaticRecordingForContextChange('Monitoring stopped when you switched away. Enable it again here when ready.');
    }
});
window.addEventListener('pagehide', () => voiceActivity?.disable('Page closed'));

function configureVoiceNotes() {
    if (typeof VoiceNotes === 'undefined') return;
    VoiceNotes.configure({getContext: () => ({sessionId: currentSessionId,
        companionId: typeof Companion !== 'undefined' ? Companion.active?.id : null,
        companionName: typeof Companion !== 'undefined' ? Companion.active?.name : 'Default companion',
        datasetEpoch: typeof AlteraAPI !== 'undefined' ? AlteraAPI.epoch : null}), send: sendReviewedVoiceNote});
}

function configurePresence() {
    if (typeof PresenceUI === 'undefined') return;
    PresenceUI.configure({getContext: () => ({sessionId: currentSessionId,
        companionId: typeof Companion !== 'undefined' ? Companion.active?.id : null,
        companionName: typeof Companion !== 'undefined' ? Companion.active?.name : 'Default companion',
        datasetEpoch: typeof AlteraAPI !== 'undefined' ? AlteraAPI.epoch : null}),
    onAccepted: async item => {
        if (item.session_id === currentSessionId && (typeof AlteraAPI === 'undefined' || item.dataset_epoch === AlteraAPI.epoch)) {
            await loadMessages(currentSessionId);
        }
    }});
}

function openPresence() {
    configurePresence();
    if (typeof PresenceUI !== 'undefined') PresenceUI.open();
}

async function openVoiceNotes() {
    if (isRecording || recordingStarting || voiceTranscribing) {
        showReplyNotice('Finish the current microphone recording before opening voice notes.');
        return;
    }
    if (typeof VoiceNotes === 'undefined') { showReplyNotice('Voice notes could not be loaded. Reload this page to retry.'); return; }
    configureVoiceNotes();
    voiceActivity?.disable('Automatic interruption paused while voice notes are open.');
    stopSpeech();
    try { await VoiceNotes.open(); } catch (error) { showReplyNotice(error.message); }
}

async function sendReviewedVoiceNote(payload) {
    if (isStreaming || isRecording || recordingStarting || voiceTranscribing || pendingForSession()) {
        throw new Error('Finish or reconnect the current reply before sending this saved voice note.');
    }
    if (payload.session_id !== currentSessionId || (typeof AlteraAPI !== 'undefined' && payload.dataset_epoch !== AlteraAPI.epoch)) {
        throw new Error('The conversation or dataset changed. Open the original voice note context before sending.');
    }
    isStreaming = true; updateSendButton(true);
    try {
        const existing = await fetch(`/api/chat/requests/${encodeURIComponent(payload.request_id)}`, {cache: 'no-store'});
        if (existing.ok) {
            const turn = await existing.json();
            if (turn.session_id !== payload.session_id) throw new Error('This reply belongs to a different conversation.');
            await loadMessages(payload.session_id);
            if (['saved', 'failed', 'interrupted'].includes(turn.status)) {
                if (turn.error) showReplyNotice(turn.error);
                return turn;
            }
        } else if (existing.status === 404) appendMessage('user', payload.message);
        else throw new Error('The saved reply could not be checked. Refresh the voice note before trying again.');
        showTypingIndicator();
        return await runChatStream(payload);
    }
    finally { hideTypingIndicator(); isStreaming = false; updateSendButton(false); }
}

function endAutomaticRecordingForContextChange(reason = 'The conversation changed. Review the recording before sending.') {
    if (voiceCapture?.activityCapture) {
        voiceCapture.reviewOnly = true;
        if (isRecording) stopRecording();
    }
    voiceActivity?.contextChanged(reason);
}

function cancelAutomaticRecording(reason) {
    const capture = voiceCapture;
    if (!capture?.activityCapture) return;
    isRecording = false; recordingStarting = false;
    clearTimeout(capture.limitTimer);
    releaseVoiceCapture(capture);
    capture.activityCapture.cancel();
    capture.stream?.getTracks().forEach(track => track.stop());
    capture.audioContext?.close().catch(() => {});
    if (animationId) cancelAnimationFrame(animationId);
    animationId = null;
    document.getElementById('micBtn').classList.remove('recording');
    document.getElementById('statusText').classList.remove('recording');
    document.getElementById('statusText').textContent = connectionStatusText();
    document.getElementById('waveformCanvas').classList.add('hidden');
    showReplyNotice(reason || 'Automatic recording stopped. Use the microphone button to try again.');
}

async function toggleRecording() {
    if (isRecording) {
        stopRecording();
    } else {
        await startRecording();
    }
}

async function offerFailedVoiceRecording(capture, audioBlob) {
    if (typeof VoiceNotes === 'undefined' || typeof VoiceNotes.retainRecording !== 'function') return;
    let keep = false;
    try {
        keep = window.confirm('Transcription did not finish. Save this recording in this browser as a voice note for review? Cancel discards it. Nothing will be uploaded or sent automatically.');
    } catch (_) { return; }
    if (!keep) return;
    try {
        const saved = await VoiceNotes.retainRecording(audioBlob, {sessionId: capture.sessionId,
            companionId: capture.companionId, datasetEpoch: capture.datasetEpoch});
        showReplyNotice(saved
            ? 'Recording saved in this browser. Open Voice notes to review its original context before use.'
            : 'Recording NOT SAVED. Open Voice notes to download it or retry saving before leaving this page.');
    } catch (_) {
        showReplyNotice('The recording could not be saved as a voice note. Check browser storage before trying again.');
    }
}

async function startRecording(handoff = null) {
    if (recordingStarting || isRecording || voiceTranscribing) { handoff?.capture?.cancel(); return; }
    if (voiceActivity && !handoff) voiceActivity.suspend('Recording a message');
    const turn = activeChatTurn;
    const pending = pendingForSession();
    const capture = {
        sessionId: currentSessionId,
        datasetEpoch: typeof AlteraAPI !== 'undefined' ? AlteraAPI.epoch : null,
        companionId: typeof Companion !== 'undefined' ? Companion.active?.id : null,
        turn, activityCapture: handoff?.capture, chunks: [],
    };
    capture.done = new Promise(resolve => { capture.resolveDone = resolve; });
    // Do this in the microphone click, before permission or device setup can await.
    suppressSpeech(turn?.id || pending?.request_id);
    capture.stopped = typeof ChatControls !== 'undefined'
        ? ChatControls.stop(turn?.id || pending?.request_id) : Promise.resolve(!turn && !pending);
    recordingStarting = true;
    voiceCapture = capture;
    updateSendButton(isStreaming);
    try {
        const stream = handoff?.stream || await navigator.mediaDevices.getUserMedia({
            audio: { 
                channelCount: 1,
                sampleRate: 16000,
                echoCancellation: true,
                noiseSuppression: true
            } 
        });
        capture.stream = stream;
        
        // Create analyser for waveform visualization
        const audioContext = new (window.AudioContext || window.webkitAudioContext)();
        capture.audioContext = audioContext;
        const source = audioContext.createMediaStreamSource(stream);
        analyser = audioContext.createAnalyser();
        analyser.fftSize = 256;
        source.connect(analyser);
        
        // Setup MediaRecorder
        if (handoff?.capture) {
            capture.activityCapture = handoff.capture;
            capture.mimeType = 'audio/wav';
            mediaRecorder = {stream, start() {}, stop() {
                const blob = handoff.capture.finish();
                this.ondataavailable?.({data: blob});
                Promise.resolve().then(() => this.onstop?.());
            }};
        } else {
            const supported = ['audio/webm;codecs=opus', 'audio/ogg;codecs=opus', 'audio/mp4', 'audio/webm'];
            const mimeType = typeof MediaRecorder.isTypeSupported === 'function'
                ? supported.find(type => MediaRecorder.isTypeSupported(type)) : 'audio/webm';
            if (!mimeType) {
                const error = new Error('No supported audio recording format.');
                error.name = 'NotSupportedError';
                throw error;
            }
            mediaRecorder = new MediaRecorder(stream, {mimeType});
            capture.mimeType = mediaRecorder.mimeType || mimeType;
        }
        capture.recorder = mediaRecorder;
        audioChunks = capture.chunks;
        
        mediaRecorder.ondataavailable = (event) => {
            if (event.data.size > 0) {
                capture.chunks.push(event.data);
            }
        };
        
        mediaRecorder.start();
        isRecording = true;
        capture.limitTimer = setTimeout(() => {
            if (voiceCapture === capture && isRecording) stopRecording();
        }, 5 * 60 * 1000);
        
        // Update UI
        const micBtn = document.getElementById('micBtn');
        micBtn.classList.add('recording');
        micBtn.title = 'Click to stop recording';
        
        const statusText = document.getElementById('statusText');
        statusText.textContent = 'Listening...';
        statusText.classList.add('recording');
        
        // Show waveform canvas
        const canvas = document.getElementById('waveformCanvas');
        canvas.classList.remove('hidden');
        drawWaveform(audioContext, analyser);
        
    } catch (err) {
        handoff?.capture?.cancel();
        capture.stream?.getTracks().forEach(track => track.stop());
        capture.audioContext?.close().catch(() => {});
        isRecording = false;
        mediaRecorder = null;
        releaseVoiceCapture(capture);
        document.getElementById('micBtn').classList.remove('recording');
        document.getElementById('statusText').classList.remove('recording');
        document.getElementById('waveformCanvas').classList.add('hidden');
        console.error('Microphone recording could not start:', err);
        {
            const message = {
                NotAllowedError: 'Microphone access was blocked. Check this site’s microphone permission and try again.',
                SecurityError: 'Microphone access was blocked. Check this site’s permission and use a secure connection.',
                NotFoundError: 'No microphone was found. Connect one and try again.',
                DevicesNotFoundError: 'No microphone was found. Connect one and try again.',
                NotReadableError: 'The microphone is busy or unavailable. Check other apps and try again.',
                TrackStartError: 'The microphone is busy or unavailable. Check other apps and try again.',
                NotSupportedError: 'This browser has no supported audio recording format. Try another browser.',
            }[err?.name] || 'Microphone recording could not start. Check your device and browser, then try again.';
            alert(message);
        }
    } finally { recordingStarting = false; updateSendButton(isStreaming); }
}

function stopRecording() {
    if (!mediaRecorder || !isRecording) return;
    const capture = voiceCapture;
    clearTimeout(capture.limitTimer);
    const recorder = capture.recorder;
    voiceTranscribing = true;
    document.getElementById('micBtn').disabled = true;

    recorder.onstop = async () => {
        // Stop all tracks
        recorder.stream.getTracks().forEach(track => track.stop());
        capture.audioContext.close().catch(() => {});
        
        // Create audio blob from chunks
        const audioBlob = new Blob(capture.chunks, { type: capture.mimeType || 'audio/webm' });
        
        // Hide waveform canvas
        document.getElementById('waveformCanvas').classList.add('hidden');
        
        if (audioBlob.size === 0) {
            console.warn('Empty recording — nothing to send');
            releaseVoiceCapture(capture);
            return;
        }
        
        // Send audio to STT endpoint
        const formData = new FormData();
        const extension = capture.mimeType?.includes('wav') ? 'wav' : capture.mimeType?.includes('mp4') ? 'm4a' : capture.mimeType?.includes('ogg') ? 'ogg' : 'webm';
        formData.append('file', audioBlob, 'recording.' + extension);
        if (capture.activityCapture) formData.append('offline_only', 'true');
        
        let transcribed = false;
        try {
            const res = await fetch('/api/voice/stt', {
                method: 'POST',
                body: formData
            });
            
            if (!res.ok) {
                let code = '';
                if (res.status === 409 || res.status === 503) {
                    try { code = (await res.json())?.code || ''; } catch (_) { /* Use status-based guidance. */ }
                }
                const message = code === 'state_busy'
                    ? 'Altera is busy updating saved data. Wait until it finishes, then record the message again.'
                    : res.status === 413
                    ? 'The recording was too large. Record a shorter message and try again.'
                    : code === 'dataset_changed'
                        ? 'The conversation or data changed. Reload Altera and record the message again.'
                        : res.status === 500 || res.status === 503
                            ? 'Transcription is unavailable. Check Altera and the configured local speech model, then record the message again.'
                            : 'Altera could not transcribe the recording. Check the connection and record the message again.';
                throw new Error(message);
            }
            
            const data = await res.json();
            
            if (data.text && data.text.trim()) {
                transcribed = true;
                await submitVoiceTranscript(capture, data.text.trim());
            } else {
                showReplyNotice("I didn't hear anything clear enough to transcribe. Try speaking a bit louder! 🎤");
                await offerFailedVoiceRecording(capture, audioBlob);
            }
            
        } catch (err) {
            if (err.replyState !== 'interrupted') console.error('STT error:', err);
            showReplyNotice(err.replyState === 'interrupted' ? err.message : `Voice message failed: ${err.message}`);
            if (!transcribed) await offerFailedVoiceRecording(capture, audioBlob);
        } finally {
            releaseVoiceCapture(capture);
        }
    };
    
    capture.finishing = true;
    try { recorder.stop(); }
    catch (error) {
        capture.stream?.getTracks().forEach(track => track.stop());
        capture.audioContext?.close().catch(() => {});
        document.getElementById('waveformCanvas').classList.add('hidden');
        capture.activityCapture?.cancel();
        releaseVoiceCapture(capture);
        showReplyNotice('The recording was cancelled after the microphone or conversation changed. Record it again when ready.');
    }
    isRecording = false;
    
    // Update UI
    const micBtn = document.getElementById('micBtn');
    micBtn.classList.remove('recording');
    micBtn.title = 'Click to record voice';
    
    const statusText = document.getElementById('statusText');
    statusText.textContent = connectionStatusText();
    statusText.classList.remove('recording');
    
    // Stop waveform animation
    if (animationId) {
        cancelAnimationFrame(animationId);
        animationId = null;
    }
    updateSendButton(isStreaming);
}

function releaseVoiceCapture(capture) {
    if (voiceCapture !== capture) return;
    voiceTranscribing = false;
    capture.resolveDone?.();
    voiceCapture = null;
    document.getElementById('micBtn').disabled = false;
    updateSendButton(isStreaming);
}

function recordingSession(capture) {
    return capture.turn?.payload.resolved_session_id || capture.sessionId;
}

function recordingContextMatches(capture) {
    const epoch = typeof AlteraAPI !== 'undefined' ? AlteraAPI.epoch : null;
    const companion = typeof Companion !== 'undefined' ? Companion.active?.id : null;
    return currentSessionId === recordingSession(capture) && epoch === capture.datasetEpoch && companion === capture.companionId;
}

function cacheVoiceTranscript(capture, text) {
    const draft = capture.draft ||= {message: text, voice_draft_id: crypto.randomUUID()};
    draft.session_id = recordingSession(capture);
    // Keep a reload-safe fallback until the normal review queue confirms storage.
    try {
        const drafts = JSON.parse(AlteraStorage.session.getItem('altera_restored_drafts') || '[]');
        AlteraStorage.session.setItem('altera_restored_drafts', JSON.stringify([
            ...drafts.filter(item => item.voice_draft_id !== draft.voice_draft_id), draft,
        ]));
        capture.cached = true;
    } catch { capture.cached = false; }
    return draft;
}

function forgetVoiceTranscript(capture) {
    if (!capture.draft) return;
    try {
        const remaining = JSON.parse(AlteraStorage.session.getItem('altera_restored_drafts') || '[]')
            .filter(item => item.voice_draft_id !== capture.draft.voice_draft_id);
        if (remaining.length) AlteraStorage.session.setItem('altera_restored_drafts', JSON.stringify(remaining));
        else AlteraStorage.session.removeItem('altera_restored_drafts');
    } catch {}
}

async function preserveVoiceTranscript(capture, text, reason) {
    const draft = cacheVoiceTranscript(capture, text);
    let queued = false;
    if (typeof restoreBrowserDrafts === 'function') {
        try {
            await restoreBrowserDrafts([draft]);
            queued = true;
            forgetVoiceTranscript(capture);
        } catch {}
    }
    const result = queued ? 'Your transcript has been kept under Review drafts.'
        : capture.cached ? 'Your transcript is shown below and saved for review after reload.'
        : 'Your transcript could not be saved. Copy the text below before closing this page.';
    showReplyNotice(`${reason} ${result}`, queued);
    if (!queued) {
        const textArea = document.createElement('textarea');
        textArea.value = text;
        textArea.setAttribute('aria-label', 'Unsent voice transcript');
        document.getElementById('replyNotice')?.append(textArea);
    }
}

async function submitVoiceTranscript(capture, text) {
    cacheVoiceTranscript(capture, text);
    if (capture.reviewOnly) {
        await preserveVoiceTranscript(capture, text, 'Recording ended when you switched away. Review it before sending.');
        return;
    }
    if (!recordingContextMatches(capture)) {
        await preserveVoiceTranscript(capture, text, 'The conversation or data changed while recording.');
        return;
    }
    if (capture.turn && !capture.turn.settled) {
        showReplyNotice('Transcript ready. Waiting for the previous reply to stop…');
        const result = await Promise.race([
            capture.turn.finished,
            capture.stopped.then(stopped => stopped ? capture.turn.finished : {stopFailed: true}),
        ]);
        if (result.stopFailed || !['saved', 'interrupted'].includes(result.terminal)) {
            await preserveVoiceTranscript(capture, text, 'The previous reply could not be confirmed as stopped.');
            return;
        }
    } else if (capture.turn && !['saved', 'interrupted'].includes(capture.turn.terminal)) {
        await preserveVoiceTranscript(capture, text, 'Reconnect to check the previous reply first.');
        return;
    }
    // Recheck after waiting: navigation and a dataset restore can happen during transcription or cancellation.
    if (capture.reviewOnly || !recordingContextMatches(capture) || isStreaming || pendingForSession(recordingSession(capture))) {
        await preserveVoiceTranscript(capture, text, 'Review this transcript before sending another message.');
        return;
    }
    const input = document.getElementById('messageInput');
    if (input.value.trim()) {
        await preserveVoiceTranscript(capture, text, 'There is already text in the composer.');
        return;
    }
    document.getElementById('replyNotice')?.remove();
    appendMessage('user', text);
    showTypingIndicator();
    isStreaming = true;
    // Transcription has finished. The next reply can be interrupted by another mic click.
    releaseVoiceCapture(capture);
    updateSendButton(true);
    try {
        const reply = runChatStream({session_id: recordingSession(capture), dataset_epoch: capture.datasetEpoch, message: text});
        // The durable request replaces the temporary transcript before transport can fail.
        if (pendingForSession(recordingSession(capture))) forgetVoiceTranscript(capture);
        await reply;
        forgetVoiceTranscript(capture);
    } catch (err) {
        // Once a durable request exists, reconnect owns recovery; never create a second copy to send.
        if (!pendingForSession(recordingSession(capture)) && (!err.replyState || !err.userMessageId)) {
            await preserveVoiceTranscript(capture, text, err.message);
        } else showReplyNotice(err.message);
    } finally {
        hideTypingIndicator();
        isStreaming = false;
        updateSendButton(false);
    }
}

function drawWaveform(audioContext, analyserNode) {
    const canvas = document.getElementById('waveformCanvas');
    const ctx = canvas.getContext('2d');
    
    // Set canvas size to match display size
    canvas.width = canvas.offsetWidth * 2;
    canvas.height = canvas.offsetHeight * 2;
    
    const bufferLength = analyserNode.frequencyBinCount;
    const dataArray = new Uint8Array(bufferLength);
    
    function draw() {
        animationId = requestAnimationFrame(draw);
        
        analyserNode.getByteFrequencyData(dataArray);
        
        ctx.fillStyle = '#0f0f14';  // bg-primary
        ctx.fillRect(0, 0, canvas.width, canvas.height);
        
        const barWidth = (canvas.width / bufferLength) * 2.5;
        let x = 0;
        
        for (let i = 0; i < bufferLength; i++) {
            const barHeight = (dataArray[i] / 255) * canvas.height * 0.8;
            
            // Gradient from accent to purple
            const gradient = ctx.createLinearGradient(0, canvas.height - barHeight, 0, canvas.height);
            gradient.addColorStop(0, '#7c6ff7');
            gradient.addColorStop(1, '#a78bfa');
            
            ctx.fillStyle = gradient;
            ctx.fillRect(x, canvas.height - barHeight, barWidth, barHeight);
            
            x += barWidth + 1;
        }
    }
    
    draw();
}

// ============================================================
// TTS Playback (Phase 3)
// ============================================================
// Altera speaks in clips of a sentence or two. The server sends each clip as soon as it is made, while the reply is still
// being written, so speech starts on the first sentence. Clips wait in a queue and play in order. The message keeps a
// small button: "Playing..." while she talks (click to stop), "Replay" afterwards.

let speechQueue = [];          // Audio objects waiting to play, in order
let speechAudio = null;        // the clip playing now
let speechClips = [];          // URLs of every clip of the reply being spoken, kept for Replay
let speechMsgEl = null;        // the message those clips belong to
let speechMore = false;        // true while the server may still send clips for this reply
let speechBlocked = false;     // the browser refused to start playback (autoplay); wait for a click on Replay
let speechRequestId = null;    // playback belongs to a turn even after its text has been saved
let speechPlaybackEpoch = 0;   // also invalidates delayed speech without a durable reply ID

function speechButtonFor(msgEl) {
    if (!msgEl) return null;
    let btn = msgEl.querySelector('.audio-play-btn');
    if (!btn) {
        btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'audio-play-btn';
        // Beside the message text, not inside it: the text is redrawn on every streamed chunk.
        const content = msgEl.querySelector('.message-content');
        (content ? content.parentElement : msgEl).appendChild(btn);
    }
    return btn;
}

function showSpeechState(msgEl, playing, clips) {
    const btn = speechButtonFor(msgEl);
    if (!btn) return;
    if (playing) {
        btn.classList.add('playing');
        btn.textContent = '🔊 Playing... (click to stop)';
        btn.onclick = () => suppressSpeech(speechRequestId);
    } else {
        btn.classList.remove('playing');
        btn.textContent = '🔊 Replay';
        btn.onclick = () => startSpeech(msgEl, clips.slice());
    }
}

function newClip(url) {
    const audio = new Audio();
    audio.preload = 'auto';           // fetch the next clip while the current one plays, so there is no gap
    audio.src = url;
    return audio;
}

function playNextClip() {
    const next = speechQueue.shift();
    if (!next) {
        speechAudio = null;
        if (voiceActivity) voiceActivity.setSpeaking(false);
        updateCompanionState();
        if (!speechMore) showSpeechState(speechMsgEl, false, speechClips);
        return;
    }
    speechAudio = next;
    if (voiceActivity) voiceActivity.setSpeaking(true);
    updateCompanionState();
    showSpeechState(speechMsgEl, true, speechClips);
    next.onended = playNextClip;
    next.onerror = playNextClip;      // a clip that will not load is skipped
    const started = next.play();
    if (started && started.catch) {
        started.catch(err => {
            if (speechAudio !== next) return;
            console.warn('TTS playback failed:', err);
            speechBlocked = true;
            stopSpeech();             // e.g. autoplay was blocked: the Replay button plays on a click
        });
    }
}

function stopSpeech() {
    if (typeof AlteraBackchannel !== 'undefined') AlteraBackchannel.stop();
    if (voiceActivity) voiceActivity.setSpeaking(false);
    speechPlaybackEpoch++;
    speechMore = false;
    speechQueue = [];
    if (speechAudio) {
        speechAudio.onended = null;
        speechAudio.onerror = null;
        speechAudio.pause();
        speechAudio = null;
    }
    updateCompanionState();
    if (speechMsgEl && speechClips.length) showSpeechState(speechMsgEl, false, speechClips);
}

// One clip of the reply that is still being written (an 'audio' event from the server).
function queueSpeech(url, requestId = null) {
    if (!voiceEnabled || !url) return;
    const msgEl = document.getElementById('streamingMsg') || (requestId && speechRequestId === requestId ? speechMsgEl : null);
    if (!msgEl) return;
    if (speechMsgEl !== msgEl) {      // the first clip of this reply
        stopSpeech();
        speechMsgEl = msgEl;
        speechClips = [];
        speechBlocked = false;
    }
    speechRequestId = requestId;
    if (requestId) msgEl.dataset.speechRequestId = requestId;
    speechMore = true;
    speechClips.push(url);
    if ((isRecording || recordingStarting) && requestId) suppressedSpeechRequests.add(requestId);
    if (isRecording || recordingStarting || isSpeechSuppressed(requestId)) {
        showSpeechState(msgEl, false, speechClips);
        return;
    }
    speechQueue.push(newClip(url));
    if (!speechAudio && !speechBlocked) playNextClip();
}

// The reply is complete: no more clips are coming.
function finishSpeech() {
    speechMore = false;
    if (!speechAudio && speechQueue.length === 0) showSpeechState(speechMsgEl, false, speechClips);
}

function finishRequestSpeech(requestId) {
    if (speechRequestId === requestId) finishSpeech();
}

// Play a list of clips from the start (Replay, or the single file an older server sends).
function startSpeech(msgEl, urls) {
    if (isRecording || recordingStarting) {
        showReplyNotice('Stop recording before replaying speech.');
        return;
    }
    stopSpeech();
    speechMsgEl = msgEl;
    speechRequestId = msgEl?.dataset.speechRequestId || null;
    speechClips = urls.slice();
    speechMore = false;
    speechBlocked = false;
    speechQueue = urls.map(newClip);
    playNextClip();
}

function playTTS(text, audioUrl = null, msgEl = null, requestId = msgEl?.dataset.speechRequestId) {
    if (!voiceEnabled || !text || isRecording || recordingStarting || isSpeechSuppressed(requestId)) return;

    // If we have a direct URL from the server, use it
    if (audioUrl) {
        startSpeech(msgEl, [audioUrl]);
    } else {
        // Otherwise ask the server to make it
        fallbackTTS(text, msgEl, requestId);
    }
}

async function fallbackTTS(text, msgEl = null, requestId = msgEl?.dataset.speechRequestId) {
    const playbackEpoch = speechPlaybackEpoch;
    try {
        const res = await fetch('/api/voice/tts', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            // the session says whose voice to use (a pal can have its own)
            body: JSON.stringify(Number.isInteger(Number(currentSessionId)) && Number(currentSessionId) > 0
                ? { text, session_id: Number(currentSessionId) } : { text })
        });

        if (!res.ok) return;

        const data = await res.json();
        if (data.message && playbackEpoch === speechPlaybackEpoch) showVoiceNotice(data.message);
        if (data.audio_url && playbackEpoch === speechPlaybackEpoch) {
            playTTS(text, data.audio_url, msgEl, requestId);
        }
    } catch (err) {
        console.warn('Fallback TTS failed:', err);
    }
}

// ============================================================
// Appearance: theme, accent colour, the adult-mode level's colours and discreet mode (appearance.js)
// ============================================================

const appearance = Appearance.create({document, storage: AlteraStorage.local});
function setTheme(theme) { appearance.setTheme(theme); }
function setAccentColor(color, button) { appearance.setAccent(color, button); }
/** companion.js calls this whenever adult mode is loaded or changed. `label` is the level's name for the badge. */
function setCompanionMood(level, label) { appearance.setMood(level, label); }
function toggleDiscreet(force) { appearance.toggleDiscreet(force); }
/** living.js asks which level is on, to keep a flirtatious look for adult mode. */
function currentMood() { return appearance.mood(); }
AlteraPage.ready(() => MainUI.run(() => appearance.restore()));

/** Esc closes Settings; with nothing open it switches discreet mode on (the eye button switches it off again). */
document.addEventListener('keydown', (e) => {
    if (!MainUI.active) return;
    if (e.key !== 'Escape') return;
    const panel = document.getElementById('settingsPanel');
    if (panel && panel.classList.contains('open')) { panel.classList.remove('open'); return; }
    const somethingOpen = [...document.querySelectorAll('.companion-overlay')].some(el => el.style.display && el.style.display !== 'none')
        || document.querySelector('.portrait-view:not([hidden]), .birth-overlay:not([hidden])');
    if (!somethingOpen) toggleDiscreet(true);
});

/** thinking while she writes, speaking while her voice plays; the stage's light follows it (companion.css). */
function updateCompanionState() {
    const state = speechAudio ? 'speaking' : (isStreaming ? 'thinking' : 'idle');
    document.documentElement.dataset.companion = state;
    setAvatarTyping(state === 'thinking');
}

// ============================================================
// Phase 5 Polish: Sidebar Toggle (mobile)
// ============================================================

function toggleSidebar() {
    const sidebar = document.getElementById('sidebar');
    const overlay = document.getElementById('sidebarOverlay');
    
    sidebar.classList.toggle('mobile-open');
    overlay.classList.toggle('visible');
}

// Close sidebar when clicking a session link on mobile
document.addEventListener('click', (e) => {
    if (!MainUI.active) return;
    if (window.innerWidth <= 768) {
        const sidebar = document.getElementById('sidebar');
        if (sidebar.classList.contains('mobile-open') && 
            !sidebar.contains(e.target) && 
            !e.target.closest('.hamburger-btn')) {
            toggleSidebar();
        }
    }
});

// ============================================================
// Phase 5 Polish: Privacy Controls
// ============================================================

async function exportData() {
    if (!confirm('Export all your data (conversations, memories, profile)? This will download a JSON file.')) return;
    
    try {
        const {res, data: blob} = await MainUI.blob('/api/export', { method: 'POST' });
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        
        MainUI.download(blob, `altera-data-${new Date().toISOString().split('T')[0]}.json`);
    } catch (err) {
        if (!MainUI.active) return;
        console.error('Export failed:', err);
        alert(`Export failed: ${err.message}`);
    }
}

async function clearAllData() {
    if (!confirm('⚠️ This will permanently delete ALL your conversations and memories. Are you sure?')) return;
    if (!confirm('Really? This cannot be undone.')) return;

    try {
        const {res, data} = await MainUI.json('/api/clear-data', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ confirm: 'delete-all' })
        });
        if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);

        try { AlteraStorage.session.removeItem('altera_pending_turns'); } catch {}
        if (typeof clearQueuedMessages === 'function') await clearQueuedMessages();
        MainUI.assert();
        alert(data.message || 'Conversations and memories cleared. Backups and imported companions remain.');
        location.reload();
    } catch (err) {
        if (!MainUI.active) return;
        console.error('Clear failed:', err);
        alert(`Clear failed: ${err.message}`);
    }
}

// ============================================================
// Phase B: Model & Provider Settings
// ============================================================

let currentProviderKey = 'lmstudio';
let currentModel = '';
function providerDraftValues() {
    return {provider: currentProviderKey, model: currentModel, default_model: document.getElementById('customDefaultModel')?.value || ''};
}
function providerRequestValues() {
    return {...providerDraftValues(), base_url: currentProviderKey === 'custom' ? document.getElementById('customBaseUrl')?.value || '' : null,
        api_key: currentProviderKey === 'custom' ? document.getElementById('customApiKey')?.value || '' : null, revision: providerRevision};
}
const sameProviderRequest = selected => MainUI.active && JSON.stringify(selected) === JSON.stringify(providerRequestValues());

async function loadProviderSettings() {
    const run = ++providerLoadRun, revision = providerRevision;
    try {
        const {res, data} = await MainUI.json('/api/llm/providers');
        if (run !== providerLoadRun || revision !== providerRevision) return;
        if (res.ok === false || !Array.isArray(data?.providers)) throw new Error(data?.error || 'Provider settings are unavailable.');
        
        // Populate provider dropdown
        const providerSelect = document.getElementById('providerSelect');
        if (providerSelect) {
            providerSelect.replaceChildren(...data.providers.map(p => new Option(p.name, p.key, false, p.is_active)));
            
            // Set current provider
            currentProviderKey = data.active_provider;
            providerSelect.value = currentProviderKey;
        }
        
        // Set current model (needed before refreshModels so it can pre-select)
        currentModel = data.active_model || '';
        
        // Fetch all models for the active provider
        await refreshModels();
        if (!MainUI.active || run !== providerLoadRun || revision !== providerRevision) return;
        providerBaseline = JSON.stringify(providerDraftValues());
        
        updateProviderStatus(data);
        
        // Show/hide custom provider section
        toggleCustomProviderSection(currentProviderKey);
        
    } catch (err) {
        if (!MainUI.active) return;
        console.error('Failed to load provider settings:', err);
        const statusEl = document.getElementById('providerStatus');
        if (statusEl) statusEl.textContent = 'Failed to load provider settings';
    }
}

function updateProviderStatus(data) {
    const statusEl = document.getElementById('providerStatus');
    if (!statusEl) return;
    
    const activeProvider = data.providers.find(p => p.is_active);
    if (activeProvider) {
        statusEl.textContent = `${activeProvider.name} • ${data.active_model || 'No model selected'}`;
        statusEl.style.color = 'var(--success)';
    }
}

async function onProviderChange() {
    providerRevision++;
    const providerSelect = document.getElementById('providerSelect');
    if (!providerSelect) return;
    
    currentProviderKey = providerSelect.value;
    currentModel = '';
    
    // Fetch models for this provider
    await refreshModels();
    MainUI.assert();
    
    // Toggle custom provider section
    toggleCustomProviderSection(currentProviderKey);
}

function toggleCustomProviderSection(providerKey) {
    const customSection = document.getElementById('customProviderSection');
    if (customSection) {
        customSection.style.display = providerKey === 'custom' ? 'block' : 'none';
    }
}

async function refreshModels() {
    const run = ++modelLoadRun, provider = currentProviderKey, revision = providerRevision, model = currentModel;
    const fresh = () => MainUI.active && run === modelLoadRun && provider === currentProviderKey && revision === providerRevision;
    const modelSelect = document.getElementById('modelSelect');
    const modelStatus = document.getElementById('modelStatus');
    const refreshBtn = document.getElementById('refreshModelsBtn');
    
    if (!modelSelect) return;
    
    // Show loading
    modelSelect.disabled = true;
    if (modelStatus) modelStatus.textContent = 'Loading models...';
    if (refreshBtn) refreshBtn.disabled = true;
    
    try {
        const {res, data} = await MainUI.json(`/api/llm/providers/${encodeURIComponent(provider)}/models`);
        if (!fresh()) return;
        if (!res.ok) throw new Error(data?.error || 'Models could not be listed');
        
        if (data.models && data.models.length > 0) {
            modelSelect.replaceChildren(...data.models.map(m => {
                const id = m.id || m;
                return new Option(id, id, false, id === model);
            }));
            if (modelStatus) modelStatus.textContent = `${data.models.length} models available`;
        } else {
            modelSelect.replaceChildren(new Option('No models found', ''));
            if (modelStatus) modelStatus.textContent = 'No models found — check provider connection';
        }
        
        // Update current model if needed
        if (modelSelect.value) {
            currentModel = modelSelect.value;
        }
    } catch (err) {
        if (!fresh()) return;
        console.error('Failed to refresh models:', err);
        if (modelStatus) modelStatus.textContent = 'Failed to load models';
    } finally {
        if (fresh()) { modelSelect.disabled = false; if (refreshBtn) refreshBtn.disabled = false; }
    }
}

function onModelChange() {
    providerRevision++;
    const modelSelect = document.getElementById('modelSelect');
    if (modelSelect) {
        currentModel = modelSelect.value;
    }
}

async function testProviderConnection() {
    const button = document.getElementById('testConnectionBtn'), status = document.getElementById('providerStatus');
    if (!button || !status || button.disabled) return;
    const selected = providerRequestValues();
    button.disabled = true; button.textContent = 'Testing...'; status.textContent = 'Testing connection...';
    try {
        const {res, data} = await MainUI.json('/api/llm/test-connection', {method: 'POST', headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({provider: selected.provider, base_url: selected.base_url, api_key: selected.api_key})});
        if (!sameProviderRequest(selected)) return;
        if (!res.ok || !data.success) throw new Error(data?.error || 'Connection test failed');
        status.textContent = `Connected — ${data.models_count} models found`; status.style.color = 'var(--success)';
        await refreshModels();
    } catch (error) {
        if (MainUI.active && sameProviderRequest(selected)) { status.textContent = error.message; status.style.color = 'var(--error)'; }
    } finally { if (MainUI.active) { button.disabled = false; button.textContent = 'Test'; } }
}

async function applyProviderModel() {
    const button = document.getElementById('applyProviderModelBtn'), status = document.getElementById('providerStatus');
    if (!button || !status || button.disabled) return;
    const selected = providerRequestValues();
    button.disabled = true; button.textContent = 'Applying...';
    try {
        if (selected.provider === 'custom') {
            const {res, data} = await MainUI.json('/api/llm/config', {method: 'PUT', headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({provider: 'custom', base_url: selected.base_url, api_key: selected.api_key, default_model: selected.default_model, enabled: true})});
            if (!res.ok) throw new Error(data?.error || 'Custom provider could not be saved');
            if (!sameProviderRequest(selected)) return;
            credentialsChanged = false;
        }
        const {res, data} = await MainUI.json('/api/llm/switch', {method: 'POST', headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({provider: selected.provider, model: selected.model})});
        if (!sameProviderRequest(selected)) return;
        if (!res.ok || data.status !== 'ok') throw new Error(data?.error || 'Provider selection could not be applied');
        providerBaseline = JSON.stringify(providerDraftValues());
        status.textContent = `Applied: ${data.provider} • ${data.model}`; status.style.color = 'var(--success)';
        checkSystemHealth();
    } catch (error) {
        if (MainUI.active && sameProviderRequest(selected)) { status.textContent = error.message; status.style.color = 'var(--error)'; }
    } finally { if (MainUI.active) { button.disabled = false; button.textContent = 'Apply & Save'; } }
}

// Avatar state

function setAvatarTyping(typing) {
    const avatar = document.getElementById('companionAvatar');
    if (!avatar) return;
    
    if (typing) {
        avatar.classList.add('typing');
        avatar.classList.remove('breathing', 'glowing');
    } else {
        avatar.classList.remove('typing');
        avatar.classList.add('breathing', 'glowing');
    }
}

// ============================================================
// Message Loading & Display
// ============================================================

async function loadMessages(sessionId) {
    return messageHistoryOwner.run(async () => {
        const run = ++messageLoadRun;
        const epoch = typeof AlteraAPI !== 'undefined' ? AlteraAPI.epoch : null;
        if (sessionId !== currentSessionId) endAutomaticRecordingForContextChange();
        if (typeof PresenceUI !== 'undefined') PresenceUI.refresh();
        const controller = new AbortController(); messageHistoryRequests.add(controller);
        try {
            const res = await fetch(`/api/messages/${sessionId}`, {signal: controller.signal});
            const messages = await res.json();
            if (run !== messageLoadRun || sessionId !== currentSessionId ||
                epoch !== (typeof AlteraAPI !== 'undefined' ? AlteraAPI.epoch : null)) return;
            if (res.ok === false || !Array.isArray(messages)) throw new Error(messages?.error || 'Saved messages are unavailable.');
            if (typeof ChatControls !== 'undefined' && await ChatControls.prepareNavigation() !== true) return;
            if (!messageHistoryOwner.active || run !== messageLoadRun || sessionId !== currentSessionId ||
                epoch !== (typeof AlteraAPI !== 'undefined' ? AlteraAPI.epoch : null)) return;
            const container = document.getElementById('messagesContainer');

            if (messages.length === 0) {
                container.innerHTML = `
                    <div class="welcome-message">
                        <div class="avatar avatar-companion large"><span>A</span></div>
                        <h2>Who would you like to talk to?</h2>
                        <p>Make a pal, import a character card, or meet someone new. They will remember this conversation.</p>
                        <p class="welcome-hint">Choose someone to begin.</p>
                        <button type="button" class="companion-btn" data-click="openMeetModal">Add someone</button>
                    </div>
                `;
                return;
            }

            container.innerHTML = messages.map(msg => createMessageHTML(msg)).join('');
            if (typeof ChatControls !== 'undefined') ChatControls.hydrate(container, messages);
            if (typeof hydratePhotos === 'function') hydratePhotos(container);   // pictures that arrived (see companion.js)
            scrollToBottom();
        } catch (err) {
            if (messageHistoryOwner.active) console.error('Failed to load messages:', err);
        } finally { messageHistoryRequests.delete(controller); }
    }).catch(error => { if (messageHistoryOwner.active) throw error; });
}

const messageHistoryRequests = new Set(), messageHistoryTimers = new Set();
const messageHistoryOwner = AlteraPage.component('message-history', {
    stop() {
        messageLoadRun++;
        for (const controller of messageHistoryRequests) controller.abort();
        for (const timer of messageHistoryTimers) clearTimeout(timer); messageHistoryTimers.clear();
        return true;
    },
    clear() { document.getElementById('messagesContainer')?.replaceChildren(); return true; }
});

function createMessageHTML(msg) {
    const isUser = msg.role === 'user';
    const time = new Date(msg.timestamp).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });

    return `
        <div class="message ${isUser ? 'user' : 'companion'}" data-reply-state="${escapeHtml(msg.reply_state || 'saved')}"${msg.draft && ['sending', 'streaming'].includes(msg.reply_state) ? ' id="streamingMsg"' : ''}>
            ${msgAvatarHTML(isUser)}
            <div>
                <div class="message-content">${formatMessage(msg.content)}</div>
                <div class="message-time">${time}</div>
                ${msg.error ? `<div class="reply-notice" role="status">${escapeHtml(msg.error)}</div>` : ''}
            </div>
        </div>
    `;
}

function formatMessage(text) {
    // Basic formatting: convert newlines to <br>, preserve code blocks
    let formatted = escapeHtml(text);
    formatted = formatted.replace(/\n/g, '<br>');
    formatted = formatted.replace(/(?:<br>){3,}/g, '<br><br>');   // a removed [[photo: ...]] tag leaves blank lines behind
    // A picture she sent is stored as [[photo:<id>]]; companion.js turns the placeholder into the picture.
    formatted = formatted.replace(/(?:<br>)*\[\[photo:(p-[0-9a-f]{10})\]\]/g,
        (match, id) => `<div class="photo-card" data-photo-id="${id}"></div>`);
    return formatted;
}

// The avatar next to a message: the active avatar's portrait for the companion, a letter otherwise (see companion.js).
function msgAvatarHTML(isUser) {
    const letter = isUser ? 'Y' : companionInitial();
    if (!isUser && window.companionPortraitUrl) {
        return `<div class="msg-avatar has-portrait" style="background-image:url('${window.companionPortraitUrl}')">${letter}</div>`;
    }
    return `<div class="msg-avatar">${letter}</div>`;
}

// The first letter of the active companion's name, escaped for the avatar; "A" for Altera when there is none.
function companionInitial() {
    const name = typeof Companion !== 'undefined' && Companion.active ? String(Companion.active.name || '') : '';
    return escapeHtml((Array.from(name.trim())[0] || 'A').toUpperCase());
}

function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

function clearMessages() {
    const container = document.getElementById('messagesContainer');
    container.innerHTML = `
        <div class="welcome-message">
            <div class="avatar avatar-companion large"><span>A</span></div>
            <h2>Who would you like to talk to?</h2>
            <p>Make a pal, import a character card, or meet someone new. They will remember this conversation.</p>
            <p class="welcome-hint">Choose someone to begin.</p>
            <button type="button" class="companion-btn" data-click="openMeetModal">Add someone</button>
        </div>
    `;
}

function scrollToBottom() {
    if (!messageHistoryOwner.active) return;
    const container = document.getElementById('messagesContainer');
    const timer = setTimeout(() => { messageHistoryTimers.delete(timer); if (messageHistoryOwner.active) container.scrollTop = container.scrollHeight; }, 50);
    messageHistoryTimers.add(timer);
}

// ============================================================
// Sending Messages (with streaming + TTS)
// ============================================================

async function sendMessage(event) {
    event.preventDefault();

    if (isStreaming || recordingStarting || isRecording || voiceTranscribing) return;
    if (pendingForSession()) {
        showReplyNotice('Reconnect to retrieve the unfinished reply before sending another message.');
        return;
    }

    const input = document.getElementById('messageInput');
    const message = input.value.trim();
    if (!message) return;

    stopSpeech();  // she stops talking when you speak again

    // Emotion detection is now handled server-side after the stream completes
    // to avoid concurrent LLM pulls. Remove the old /api/mood call from here.

    // Show typing indicator
    showTypingIndicator();

    // Send to server with streaming
    isStreaming = true;
    updateSendButton(true);

    try {
        await runChatStream({ session_id: currentSessionId, message: message }, {onPrepared: () => {
            // Keep any newer text typed while the request was being prepared.
            if (input.value.trim() === message) {
                input.value = '';
            }
            autoResize(input);
            appendMessage('user', message);
            // The dots went up before the message was accepted: keep them below it.
            const typing = document.getElementById('typingIndicator');
            if (typing) document.getElementById('messagesContainer').appendChild(typing);
        }});
    } catch (err) {
        if (err.replyState !== 'interrupted') console.error('Chat error:', err);
        hideTypingIndicator();
        showReplyNotice(err.message);
    } finally {
        isStreaming = false;
        updateSendButton(false);
    }
}

function pendingTurns() {
    try { return JSON.parse(AlteraStorage.session.getItem('altera_pending_turns')) || {}; } catch { return {}; }
}

let activeChatTurn = null;
const suppressedSpeechRequests = new Set();

function isSpeechSuppressed(requestId) {
    return Boolean(requestId && suppressedSpeechRequests.has(requestId));
}

function suppressSpeech(requestId = null) {
    if (requestId) suppressedSpeechRequests.add(requestId);
    if (typeof speechRequestId !== 'undefined' && speechRequestId) suppressedSpeechRequests.add(speechRequestId);
    stopSpeech();
}

function keepPendingTurn(payload) {
    try { AlteraStorage.session.setItem('altera_pending_turns', JSON.stringify({ ...pendingTurns(), [payload.request_id]: payload })); }
    catch (_) { /* Without session storage an unfinished reply cannot be resumed after a reload. */ }
}

function forgetPendingTurn(requestId) {
    const pending = pendingTurns();
    delete pending[requestId];
    try {
        if (Object.keys(pending).length) AlteraStorage.session.setItem('altera_pending_turns', JSON.stringify(pending));
        else AlteraStorage.session.removeItem('altera_pending_turns');
    } catch {}
}

function pendingForSession(sessionId = currentSessionId) {
    return Object.values(pendingTurns()).find(p => (p.resolved_session_id || p.session_id) === sessionId ||
        (!p.resolved_session_id && !p.session_id));
}

function showReplyNotice(message, reviewDrafts = false) {
    document.getElementById('replyNotice')?.remove();
    const notice = document.createElement('div');
    notice.id = 'replyNotice';
    notice.className = 'reply-notice';
    notice.setAttribute('role', 'status');
    const label = document.createElement('span');
    label.textContent = message;
    notice.append(label);
    if (reviewDrafts && typeof reviewQueuedMessages === 'function') {
        const review = document.createElement('button'); review.type = 'button'; review.textContent = 'Review drafts';
        review.addEventListener('click', reviewQueuedMessages); notice.append(review);
    }
    const pending = pendingForSession();
    if (pending) {
        const reconnect = document.createElement('button');
        reconnect.type = 'button';
        reconnect.textContent = 'Reconnect';
        reconnect.addEventListener('click', resumePendingTurn);
        notice.append(reconnect);
        if (typeof pending.message === 'string' && typeof restoreBrowserDrafts === 'function') {
            const review = document.createElement('button'); review.type = 'button'; review.textContent = 'Review draft';
            review.addEventListener('click', async () => {
                await restoreBrowserDrafts([{message: pending.message, session_id: pending.resolved_session_id || pending.session_id || null}]);
                forgetPendingTurn(pending.request_id);
                reviewQueuedMessages();
            });
            notice.append(review);
        }
    }
    document.getElementById('messagesContainer').append(notice);
}

function showVoiceNotice(message) {
    let notice = document.getElementById('voiceNotice');
    if (!notice) {
        notice = document.createElement('div');
        notice.id = 'voiceNotice';
        notice.className = 'reply-notice';
        notice.setAttribute('role', 'status');
        document.getElementById('messagesContainer').append(notice);
    }
    notice.textContent = message;
    scrollToBottom();
}

async function resumePendingTurn() {
    if (isStreaming) return;
    const payload = pendingForSession();
    if (!payload) return;
    if (typeof AlteraAPI !== 'undefined' && AlteraAPI.epoch && payload.dataset_epoch !== AlteraAPI.epoch) {
        showReplyNotice('Altera’s data has changed. Review this unsent draft before sending it again.');
        return;
    }
    isStreaming = true;
    updateSendButton(true);
    document.getElementById('replyNotice')?.remove();
    // A replay replaces the on-screen draft; the server keeps only one persisted reply.
    document.getElementById('streamingMsg')?.remove();
    try {
        const response = await fetch(`/api/chat/requests/${encodeURIComponent(payload.request_id)}`, {cache: 'no-store'});
        if (response.status === 200) {
            const turn = await response.json();
            currentSessionId = turn.session_id;
            if (['saved', 'failed', 'interrupted'].includes(turn.status)) {
                // Cancellation can win before even the user's row is saved.
                // Keep that original input reviewable; replaying the terminal
                // request must not discard the only surviving copy.
                if (turn.status === 'saved' || turn.user_message_id || typeof payload.message !== 'string') {
                    forgetPendingTurn(payload.request_id);
                }
                await loadMessages(currentSessionId);
                if (turn.error) showReplyNotice(turn.error);
                return;
            }
        }
        await runChatStream(payload);
        await loadMessages(currentSessionId);
    } catch (err) {
        showReplyNotice(err.message);
    } finally {
        hideTypingIndicator();
        isStreaming = false;
        updateSendButton(false);
    }
}

/**
 * POST /api/chat/stream and put the reply into the chat as it arrives. `payload` is {session_id, message}, or
 * {session_id, greet: true} for a new companion's first words. hooks.onStatus(data) hears the status events (typing, looking,
 * notes...), hooks.onText(fullText) every piece of the reply. Throws if the request fails.
 */
async function runChatStream(payload, hooks = {}) {
    payload = { ...payload, request_id: payload.request_id || crypto.randomUUID() };
    const previous = pendingForSession(payload.session_id);
    const replaying = Boolean(previous);
    if (previous && previous.request_id !== payload.request_id) {
        throw new Error('Reconnect to retrieve the unfinished reply before sending another message.');
    }
    if (typeof AlteraAPI !== 'undefined' && AlteraAPI.epoch) {
        if (previous && payload.dataset_epoch !== AlteraAPI.epoch) throw new Error('This unfinished request belongs to older data. Review its draft before sending it again.');
        payload.dataset_epoch = payload.dataset_epoch || AlteraAPI.epoch;
    }
    keepPendingTurn(payload);
    document.getElementById('voiceNotice')?.remove();
    if (hooks.onPrepared) hooks.onPrepared();
    if (typeof ChatControls !== 'undefined') ChatControls.begin(payload.request_id);
    const turn = {id: payload.request_id, payload, terminal: null, settled: false};
    turn.finished = new Promise(resolve => { turn.resolveFinished = resolve; });
    activeChatTurn = turn;
    if (replaying || payload.voice_note_id) suppressedSpeechRequests.add(payload.request_id);
    if (!replaying && !payload.voice_note_id && typeof AlteraBackchannel !== 'undefined') AlteraBackchannel.schedule(turn);
    try {
        return await readChatStream(payload, hooks, replaying, turn);
    } finally {
        if (typeof AlteraBackchannel !== 'undefined') AlteraBackchannel.stop();
        turn.settled = true;
        if (activeChatTurn === turn) activeChatTurn = null;
        if (typeof finishRequestSpeech === 'function') finishRequestSpeech(payload.request_id);
        if (typeof ChatControls !== 'undefined') ChatControls.settled?.(payload.request_id, turn.terminal);
        turn.resolveFinished({terminal: turn.terminal});
    }
}

async function readChatStream(payload, hooks, replaying, turn) {
    const response = await fetch(payload.operation === 'regenerate' ? '/api/chat/regenerate' : '/api/chat/stream', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
    });

    if (!response.ok) {
        let reason = `HTTP ${response.status}`;
        let code = null;
        try { const body = await response.json(); if (body.error) reason = body.error; code = body.code; } catch (e) { /* no body */ }
        // Keep rejected stale requests available through Review draft, including after reload.
        if (response.status >= 400 && response.status < 500 && code !== 'dataset_changed' && response.status !== 410) {
            forgetPendingTurn(payload.request_id);
        }
        throw new Error(reason);
    }

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let fullResponse = '';
    let buffer = '';
    let audioUrl = null;  // Phase 3: capture TTS URL from server
    let terminal = null;
    let terminalError = null;
    let terminalUserMessageId = null;
    let terminalSessionId = payload.session_id;
    function consume(data) {
        if (hooks.onStatus && data.status) hooks.onStatus(data);
        if (data.status === 'accepted') {
            if (typeof ChatControls !== 'undefined') ChatControls.accepted(payload.request_id);
            payload.resolved_session_id = data.session_id;
            currentSessionId = data.session_id;
            keepPendingTurn(payload);
        } else if (data.status === 'saved') {
            terminal = 'saved';
            turn.terminal = terminal;
            if (typeof ChatControls !== 'undefined') ChatControls.terminal?.(payload.request_id, terminal);
            currentSessionId = data.session_id;
            const saved = document.getElementById('streamingMsg');
            if (typeof ChatControls !== 'undefined') ChatControls.finish(saved, data, fullResponse, payload);
            if (saved) { saved.dataset.replyState = 'saved'; saved.dataset.messageId = data.message_id; }
            if (saved) saved.dataset.speechRequestId = payload.request_id;
            finalizeStreamingMessage(fullResponse, audioUrl, payload.request_id);
            const starterGuide = document.getElementById('starterChatGuide');
            const speechGuide = document.getElementById('starterSpeechGuide');
            if (starterGuide && speechGuide) {
                starterGuide.hidden = true;
                speechGuide.hidden = false;
            }
            hideTypingIndicator();
            loadSessions();
            forgetPendingTurn(payload.request_id);
        } else if (data.status === 'failed' || data.status === 'interrupted') {
            terminalUserMessageId = data.user_message_id || null;
            terminalSessionId = data.session_id || payload.resolved_session_id || payload.session_id;
            currentSessionId = data.session_id || currentSessionId;
            terminal = data.status;
            turn.terminal = terminal;
            if (typeof ChatControls !== 'undefined') ChatControls.terminal?.(payload.request_id, terminal);
            terminalError = data.error || 'The reply did not finish.';
            hideTypingIndicator();
            stopSpeech();
            const partial = document.getElementById('streamingMsg') ||
                (data.user_message_id && typeof ChatControls !== 'undefined' ? appendMessage('companion', '') : null);
            if (partial) { partial.id = ''; partial.dataset.replyState = terminal; }
            if (typeof ChatControls !== 'undefined') ChatControls.addTools(partial, {role: 'assistant', content: fullResponse,
                request_id: data.request_id, reply_state: terminal, retry_message_id: data.user_message_id || data.target_message_id});
            if (terminalUserMessageId || typeof payload.message !== 'string' || payload.operation === 'regenerate') {
                forgetPendingTurn(payload.request_id);
            }
        } else if (data.response !== undefined && data.response !== null) {
            if (typeof AlteraBackchannel !== 'undefined') AlteraBackchannel.stop();
            fullResponse += data.response;
            updateStreamingMessage(fullResponse);
            const draft = document.getElementById('streamingMsg');
            if (draft) draft.dataset.speechRequestId = payload.request_id;
            if (hooks.onText) hooks.onText(fullResponse);
        } else if (data.status === 'gpu_wait') {
            const typing = document.getElementById('typingIndicator');
            if (!typing) showTypingIndicator();
            const waiting = document.querySelector('#typingIndicator .gpu-wait');
            if (waiting) waiting.textContent = data.message;
            updateTypingIndicator(true);
            scrollToBottom();
        } else if (data.status === 'typing') {
            const waiting = document.querySelector('#typingIndicator .gpu-wait');
            if (waiting) waiting.textContent = '';
            updateTypingIndicator(true);
        } else if (data.status === 'audio') {
            if (typeof AlteraBackchannel !== 'undefined') AlteraBackchannel.stop();
            if (!replaying) queueSpeech(data.audio_url, payload.request_id);
        } else if (data.status === 'voice_notice' && data.message) {
            showVoiceNotice(data.message);
        } else if (data.status === 'notes') {
            if (typeof watchNotes === 'function') watchNotes();
        } else if (data.photo) {
            if (typeof attachPhotoCard === 'function') attachPhotoCard(data.photo);
        } else if (data.error) {
            terminalError = data.error;
        }
    }

    while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });

        // Process complete SSE lines
        const lines = buffer.split('\n');
        buffer = lines.pop() || ''; // Keep incomplete line in buffer

        for (const line of lines) {
            if (!line.startsWith('data: ')) continue;

            let data;
            try { data = JSON.parse(line.slice(6)); }
            catch (parseErr) { throw new Error('Altera sent an unreadable reply event.'); }
            consume(data);

        }
    }

    // Process any remaining buffer
    if (buffer.startsWith('data: ')) {
        consume(JSON.parse(buffer.slice(6)));
    }
    if (terminalError) {
        const error = new Error(terminalError);
        error.replyState = terminal;
        error.userMessageId = terminalUserMessageId;
        error.sessionId = terminalSessionId;
        throw error;
    }
    if (!terminal) throw new Error('Connection interrupted. Reconnect to retrieve this reply without sending it again.');
    return fullResponse;
}

// ============================================================
// Message UI Helpers
// ============================================================

let streamingMsgEl = null;

function appendMessage(role, content) {
    const container = document.getElementById('messagesContainer');
    // Remove welcome message if present
    const welcome = container.querySelector('.welcome-message');
    if (welcome) welcome.remove();

    const isUser = role === 'user';
    const time = new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });

    const msgEl = document.createElement('div');
    msgEl.className = `message ${isUser ? 'user' : 'companion'}`;
    msgEl.innerHTML = `
        ${msgAvatarHTML(isUser)}
        <div>
            <div class="message-content">${formatMessage(content)}</div>
            <div class="message-time">${time}</div>
        </div>
    `;

    container.appendChild(msgEl);
    if (typeof ChatControls !== 'undefined') ChatControls.addTools(msgEl, {role: isUser ? 'user' : 'assistant', content});
    scrollToBottom();
    return msgEl;
}

function showTypingIndicator() {
    const container = document.getElementById('messagesContainer');
    const welcome = container.querySelector('.welcome-message');
    if (welcome) welcome.remove();

    const typingEl = document.createElement('div');
    typingEl.id = 'typingIndicator';
    typingEl.className = 'message companion';
    typingEl.innerHTML = `
        ${msgAvatarHTML(false)}
        <div class="message-content">
            <div class="typing-indicator">
                <div class="typing-dot"></div>
                <div class="typing-dot"></div>
                <div class="typing-dot"></div>
            </div>
            <div class="gpu-wait" role="status"></div>
        </div>
    `;
    container.appendChild(typingEl);
    scrollToBottom();
}

function updateTypingIndicator(show) {
    const el = document.getElementById('typingIndicator');
    if (el) el.style.display = show ? 'flex' : 'none';
}

function hideTypingIndicator() {
    const el = document.getElementById('typingIndicator');
    if (el) el.remove();
}

function updateStreamingMessage(content) {
    let msgEl = document.getElementById('streamingMsg');
    if (!msgEl) {
        msgEl = appendMessage('companion', '');
        msgEl.id = 'streamingMsg';
        msgEl.dataset.replyState = 'streaming';
    }
    const contentEl = msgEl.querySelector('.message-content');
    contentEl.innerHTML = formatMessage(content);
    scrollToBottom();
}

function finalizeStreamingMessage(content, audioUrl = null, requestId = null) {
    let msgEl = document.getElementById('streamingMsg');
    if (msgEl) {
        msgEl.id = ''; // Remove streaming ID
        const timeEl = msgEl.querySelector('.message-time');
        if (timeEl) {
            timeEl.textContent = new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
        }
        
        if (typeof livingReply === 'function') livingReply(content);      // her face follows what she said (living.js)

        // Clips that arrived while she was writing are already playing: just say the reply is complete.
        // A single file (an older server, or a reply that came without clips) is played as before.
        if (speechMsgEl === msgEl && speechClips.length) {
            finishSpeech();
        } else {
            playTTS(content, audioUrl, msgEl, requestId);
        }
    }
}

function updateSendButton(disabled) {
    document.getElementById('sendBtn').disabled = disabled || recordingStarting || isRecording || voiceTranscribing;
    if (typeof ChatControls !== 'undefined') ChatControls.sync();
    updateCompanionState();
    const statusText = document.getElementById('statusText');
    if (isRecording) {
        statusText.textContent = 'Listening...';
    } else if (recordingStarting) {
        statusText.textContent = 'Opening microphone...';
    } else if (voiceTranscribing) {
        statusText.textContent = 'Transcribing voice...';
    } else if (disabled) {
        statusText.textContent = 'Thinking...';
    } else {
        statusText.textContent = statusText.dataset.notes ? 'Writing things down…' : connectionStatusText();
    }
}

// ============================================================
// Workspace Tool Drawer
// ============================================================

let lastFetchContent = '';

function updateSessionSignal() {
    const signal = document.getElementById('sessionSignal');
    if (!signal) return;
    const companion = typeof Companion !== 'undefined' ? Companion.active : null;
    signal.textContent = companion ? companion.name : (currentSessionId ? `Chat #${currentSessionId}` : 'No chat');
}

function toggleToolDrawer() {
    const drawer = document.getElementById('toolDrawer');
    const button = document.querySelector('.tool-toggle-btn');
    if (!drawer) return;
    drawer.classList.toggle('open');
    if (button) button.classList.toggle('active', drawer.classList.contains('open'));
}

function switchToolTab(tabName) {
    document.querySelectorAll('.tool-tab').forEach(btn => {
        btn.classList.toggle('active', btn.dataset.toolTab === tabName);
    });
    document.querySelectorAll('.tool-pane').forEach(pane => pane.classList.remove('active'));
    const pane = document.getElementById(`toolPane${tabName.charAt(0).toUpperCase()}${tabName.slice(1)}`);
    if (pane) pane.classList.add('active');
}

function setToolStatus(message) {
    if (!MainUI.active) return;
    const label = document.getElementById('workspaceRootLabel');
    if (label) label.textContent = message;
}

async function refreshWorkspaceFiles(showDrawer = true) {
    try {
        const {res, data} = await MainUI.json('/api/tools/files');
        if (!res.ok) throw new Error(data.error || 'Could not list files');

        setToolStatus(`${data.files.length} files visible`);
        const list = document.getElementById('workspaceFileList');
        if (!list) return;
        list.replaceChildren(...data.files.slice(0, 32).map(file => {
            const button = document.createElement('button'); button.type = 'button'; button.className = 'tool-file-chip'; button.textContent = file.path;
            button.addEventListener('click', MainUI.action(() => selectWorkspaceFile(file.path))); return button;
        }));

        if (showDrawer) {
            document.getElementById('toolDrawer')?.classList.add('open');
            document.querySelector('.tool-toggle-btn')?.classList.add('active');
            switchToolTab('files');
        }
    } catch (err) {
        if (!MainUI.active) return;
        setToolStatus('Tools unavailable');
        console.warn('Workspace file list failed:', err);
    }
}

function selectWorkspaceFile(path) {
    const input = document.getElementById('workspaceFilePath');
    if (input) input.value = path;
    readWorkspaceFile();
}

async function readWorkspaceFile() {
    const path = document.getElementById('workspaceFilePath')?.value.trim();
    if (!path) return;
    const run = ++workspaceReadRun, field = document.getElementById('workspaceFileContent'), before = field.value, origin = mainContext();

    try {
        setToolStatus('Reading file...');
        const {res, data} = await MainUI.json(`/api/tools/file?path=${encodeURIComponent(path)}`);
        const fresh = () => MainUI.active && run === workspaceReadRun && document.getElementById('workspaceFilePath')?.value.trim() === path && mainContext().dataset_epoch === origin.dataset_epoch;
        if (!fresh()) return;
        if (!res.ok) throw new Error(data.error || 'Read failed');
        if (field.value !== before) { setToolStatus('Newer file edits have been kept. Read again after saving or reviewing them.'); return; }
        if (await MainUI.prepareWorkspace() !== true) {
            if (MainUI.active && workspaceOrigin?.loaded && fresh()) document.getElementById('workspaceFilePath').value = workspaceOrigin.path;
            return;
        }
        if (!fresh() || field.value !== before) return;
        field.value = data.content; workspaceBaseline = data.content;
        workspaceOrigin = {path, context: origin, loaded: true, slot: 'workspace-edit_' + ++workspaceSerial};
        setToolStatus(`Read ${data.path}`);
    } catch (err) {
        if (!MainUI.active) return;
        setToolStatus(err.message);
    }
}

async function writeWorkspaceFile() {
    const path = document.getElementById('workspaceFilePath')?.value.trim();
    const content = document.getElementById('workspaceFileContent')?.value ?? '';
    if (!path) return;
    const origin = mainContext();

    if (!confirm(`Write changes to ${path}?`)) return;

    try {
        setToolStatus('Writing file...');
        const {res, data} = await MainUI.json('/api/tools/file', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ path, content })
        });
        if (!res.ok) throw new Error(data.error || 'Write failed');
        if (document.getElementById('workspaceFilePath')?.value.trim() !== path || mainContext().dataset_epoch !== origin.dataset_epoch) return;
        workspaceBaseline = content; workspaceOrigin = {path, context: origin, loaded: true, slot: 'workspace-edit_' + ++workspaceSerial};
        setToolStatus(`Wrote ${data.path}` + (document.getElementById('workspaceFileContent').value !== content ? '. Newer edits are still unsaved.' : ''));
        refreshWorkspaceFiles(false);
    } catch (err) {
        if (!MainUI.active) return;
        setToolStatus(err.message);
    }
}

async function fetchToolUrl() {
    const url = document.getElementById('fetchUrlInput')?.value.trim();
    const output = document.getElementById('fetchOutput');
    if (!url || !output) return;
    const run = ++fetchedReadRun;

    output.textContent = 'Fetching...';
    try {
        const {res, data} = await MainUI.json('/api/tools/fetch', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ url })
        });
        if (run !== fetchedReadRun || document.getElementById('fetchUrlInput')?.value.trim() !== url) return;
        if (!res.ok) throw new Error(data.error || 'Fetch failed');

        lastFetchContent = data.content;
        const preview = data.content.length > 6000 ? `${data.content.slice(0, 6000)}\n\n[Preview trimmed]` : data.content;
        output.textContent = `${data.status} ${data.content_type}\n${data.url}\n\n${preview}`;
        setToolStatus('Fetched URL');
    } catch (err) {
        if (!MainUI.active || run !== fetchedReadRun || document.getElementById('fetchUrlInput')?.value.trim() !== url) return;
        output.textContent = err.message;
        setToolStatus('Fetch failed');
    }
}

function sendToolResultToChat(kind) {
    const input = document.getElementById('messageInput');
    if (!input) return;

    if (kind === 'fetch') {
        const url = document.getElementById('fetchUrlInput')?.value.trim() || 'this URL';
        const excerpt = lastFetchContent ? lastFetchContent.slice(0, 1800) : '';
        if (!insertComposerText(`Summarize this fetched page from ${url} and tell me what matters:\n\n${excerpt}`)) return;
    }
    autoResize(input);
    input.focus();
}

function insertPromptTemplate(text) {
    const input = document.getElementById('messageInput');
    if (!input) return;
    if (!insertComposerText(text)) return;
    autoResize(input);
    input.focus();
}

function insertComposerText(text) {
    const input = document.getElementById('messageInput');
    if (!input || !MainUI.active) return false;
    if (input.value.trim() && input.value !== text) { showReplyNotice('Your existing draft is still in the composer. Finish or save it before inserting another prompt.'); return false; }
    input.value = text; input.dispatchEvent(new Event('input', {bubbles: true})); return true;
}

// ============================================================
// Settings Panel
// ============================================================

function toggleSettings() {
    const panel = document.getElementById('settingsPanel');
    if (!panel) return;
    panel.classList.toggle('open');
    panel.inert = !panel.classList.contains('open');
    if (panel.classList.contains('open')) {
        document.getElementById('sidebar')?.classList.remove('mobile-open');
        document.getElementById('sidebarOverlay')?.classList.remove('visible');
        loadVoiceLabOffer();
    }
}

// Close settings when clicking outside
document.addEventListener('click', (e) => {
    if (!MainUI.active) return;
    const panel = document.getElementById('settingsPanel');
    if (!panel || e.target.closest('dialog')) return;
    if (panel.classList.contains('open') && !panel.contains(e.target) && !e.target.closest('.settings-btn, [data-settings-toggle]')) {
        panel.classList.remove('open');
    }
});

// ============================================================
// Input Helpers
// ============================================================

function setupAutoResize() {
    const input = document.getElementById('messageInput');
    autoResize(input);

    // Enter to send, Shift+Enter for new line
    input.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault();
            sendMessage(e);
        }
    });

    // Auto-resize on input
    input.addEventListener('input', () => {
        autoResize(input);
    });
}

function autoResize(el) {
    el.style.height = 'auto';
    el.style.height = Math.min(el.scrollHeight, 150) + 'px';
}

// ============================================================
// Welcome: a first run goes to /setup, then the greeting, conversation starters and feature hints (welcome.js)
// ============================================================

const welcome = Welcome.create({document, storage: AlteraStorage.local,
    json: (url, options) => MainUI.json(url, options), later: (action, delay) => MainUI.later(action, delay),
    active: () => MainUI.active, context: mainContext, insert: text => insertComposerText(text), busy: () => isStreaming,
    assign: url => window.location.assign(url), warn: (...args) => console.warn(...args)});
function checkOnboarding() { return welcome.check(); }
/** Settings -> Setup check: the same page, opened after the first run. */
function openSetupCheck() { welcome.openSetupCheck(); }
function useStarter(button) { welcome.useStarter(button); }
function dismissFeatureHint() { welcome.dismissHint(); }

// ============================================================
// System Health Indicators
// ============================================================

function renderHealth(data) {
    if (!MainUI.active) return;
    const labels = {llm: {ok: 'Listed', warning: 'Check model', error: 'Unavailable', disabled: 'Off'},
        voice: {ok: data.voice?.engine === 'edge' ? 'Cloud' : 'Ready', warming: 'Loading', error: 'Unavailable', disabled: 'Off'},
        memory: {ok: 'Readable', warning: 'Paused', error: 'Error'}};
    for (const [kind, suffix] of [['llm', 'Llm'], ['voice', 'Voice'], ['memory', 'Memory']]) {
        const component = data[kind] || {status: 'unknown', message: 'The check could not be completed.'};
        const dot = document.getElementById('health' + suffix), text = document.getElementById('health' + suffix + 'Text');
        if (!dot || !text) continue;
        dot.className = 'health-dot ' + (component.status === 'warning' ? 'warming' : component.status);
        text.textContent = labels[kind][component.status] || 'Not checked';
        text.title = component.message || '';
        const item = dot.closest('.health-item');
        if (item) {
            item.title = `${kind.toUpperCase()}: ${text.textContent} — ${component.message || ''}`;
            item.setAttribute('aria-label', item.title); item.tabIndex = 0;
        }
    }
}

async function checkSystemHealth() {
    try {
        const {res: response, data} = await MainUI.json('/api/health/details' + (currentSessionId ? `?session_id=${currentSessionId}` : ''), {cache: 'no-store', timeoutMs: 5000});
        if (!response.ok) throw new Error('Health checks are temporarily unavailable.');
        renderHealth(data);
        if (data.voice?.status === 'warming' && (checkSystemHealth.voiceRetries = (checkSystemHealth.voiceRetries || 0) + 1) <= 24) MainUI.later(checkSystemHealth, 5000);
    } catch (error) {
        renderHealth({});
    }
}

// ============================================================
// Phase 7: PWA & Offline Support
// ============================================================

async function registerServiceWorker() {
    if ('serviceWorker' in navigator) {
        try {
            const registration = await navigator.serviceWorker.register('/sw.js', { scope: '/' });
            MainUI.assert();
            const registrations = await navigator.serviceWorker.getRegistrations();
            MainUI.assert();
            for (const previous of registrations) {
                if (previous.scope === new URL('/static/', location.origin).href) {
                    await previous.unregister();
                }
            }
            console.log('[PWA] Service Worker registered with scope:', registration.scope);
            
            // Update listener for new service worker versions
            registration.addEventListener('updatefound', () => {
                if (!MainUI.active) return;
                const newWorker = registration.installing;
                newWorker.addEventListener('statechange', () => {
                    if (!MainUI.active) return;
                    if (newWorker.state === 'activated') {
                        console.log('[PWA] New content available!');
                    }
                });
            });
        } catch (err) {
            if (!MainUI.active) return;
            console.error('[PWA] Service Worker registration failed:', err);
        }
    } else {
        console.warn('[PWA] Service Workers not supported in this browser');
    }
}

// Register service worker on load
if (typeof registerServiceWorker === 'function') {
    AlteraPage.ready(() => MainUI.run(registerServiceWorker));
}



// Main page ownership. Keep this block inert until the verified page is ready.
function mainContext() {
    return {session_id: currentSessionId, dataset_epoch: typeof AlteraAPI === 'undefined' ? null : AlteraAPI.epoch,
        companion_id: typeof Companion === 'undefined' ? null : Companion.active?.id || null};
}
function workspaceDraftValues() {
    const path = document.getElementById('workspaceFilePath')?.value || '', content = document.getElementById('workspaceFileContent')?.value || '';
    if (workspaceOrigin?.loaded ? path.trim() === workspaceOrigin.path && content === workspaceBaseline : !path && !content) return null;
    if (!workspaceOrigin) workspaceOrigin = {context: mainContext(), loaded: false, slot: 'workspace-edit_' + ++workspaceSerial};
    return {slot: workspaceOrigin.slot, context: workspaceOrigin.context, source_path: workspaceOrigin.loaded ? workspaceOrigin.path : null, requested_path: path, content};
}
function setupMainEditTracking() {
    providerBaseline = JSON.stringify(providerDraftValues());
    for (const id of ['workspaceFilePath', 'workspaceFileContent']) document.getElementById(id)?.addEventListener('input', () => { if (MainUI.active) workspaceDraftValues(); });
    for (const id of ['customBaseUrl', 'customApiKey', 'customDefaultModel']) document.getElementById(id)?.addEventListener('input', () => {
        if (!MainUI.active) return;
        providerRevision++; if (id !== 'customDefaultModel') credentialsChanged = true;
    });
}
const MainUI = (() => {
    const requests = new Set(), timers = new Map(), urls = new Set();
    function snapshots(workspaceOnly = false) {
        const result = [], workspace = workspaceDraftValues();
        if (workspace) result.push({slot: workspace.slot, sessionId: workspace.context.session_id,
            message: 'Unsent workspace edits. Review the original file before writing again; a pending write may already have been accepted. This draft does not write files.\n' + JSON.stringify(workspace, null, 2)});
        if (workspaceOnly) return result;
        const provider = providerDraftValues();
        if (providerBaseline !== null && JSON.stringify(provider) !== providerBaseline) result.push({slot: 'main-provider', sessionId: null,
            message: 'Unapplied provider/model selection for manual review. Credentials and connection URL are excluded. This draft neither approves a provider nor applies settings.\n' + JSON.stringify(provider, null, 2)});
        return result;
    }
    const owner = AlteraPage.component('main-page', {
        stop() {
            providerLoadRun++; modelLoadRun++; workspaceReadRun++; fetchedReadRun++;
            for (const controller of requests) controller.abort();
            for (const [timer, repeat] of timers) repeat ? clearInterval(timer) : clearTimeout(timer); timers.clear();
            for (const url of urls) URL.revokeObjectURL(url); urls.clear();
            for (const id of ['customApiKey', 'customBaseUrl']) if (document.getElementById(id)) document.getElementById(id).value = '';
            credentialsChanged = false;
            return true;
        },
        clear() {
            for (const id of ['customApiKey', 'customBaseUrl', 'customDefaultModel', 'workspaceFilePath', 'workspaceFileContent', 'fetchUrlInput', 'messageInput']) if (document.getElementById(id)) document.getElementById(id).value = '';
            for (const id of ['providerSelect', 'modelSelect', 'workspaceFileList', 'starterButtons']) document.getElementById(id)?.replaceChildren();
            for (const id of ['providerStatus', 'modelStatus', 'workspaceRootLabel', 'fetchOutput', 'welcomeTitle', 'welcomeText', 'welcomeHint', 'hintText', 'voiceStatus', 'statusText']) if (document.getElementById(id)) document.getElementById(id).textContent = '';
            for (const node of document.querySelectorAll('.health-item, #healthLlmText, #healthVoiceText, #healthMemoryText')) { node.removeAttribute('title'); node.removeAttribute('aria-label'); if (node.id) node.textContent = ''; }
            for (const id of ['settingsPanel', 'toolDrawer']) document.getElementById(id)?.classList.remove('open');
            currentProviderKey = currentModel = lastFetchContent = ''; providerBaseline = workspaceOrigin = null;
            workspaceBaseline = '';
            connectionState = 'closed'; connectionCheckInFlight = false; checkSystemHealth.voiceRetries = 0;
            currentSessionId = null; isStreaming = false; voiceEnabled = false;
            appearance.reset(); document.documentElement.dataset.companion = 'idle';
            streamingMsgEl = speechMsgEl = speechAudio = voiceActivity = null; speechQueue = []; speechClips = []; suppressedSpeechRequests.clear();
            return true;
        }
    });
    const run = action => owner.run(action).catch(error => { if (owner.active) throw error; });
    function schedule(action, delay, repeat) {
        if (!owner.active) return;
        if (timers.size >= 64) throw new Error('Finish pending page work before scheduling more.');
        const callback = () => { if (!repeat) timers.delete(timer); if (owner.active) void run(action).catch(() => {}); };
        const timer = repeat ? setInterval(callback, delay) : setTimeout(callback, delay); timers.set(timer, repeat); return timer;
    }
    async function read(kind, url, {timeoutMs, ...options} = {}) {
        return owner.run(async () => {
            const controller = new AbortController(); requests.add(controller); let timer = null;
            if (timeoutMs) { timer = setTimeout(() => { timers.delete(timer); controller.abort(); }, timeoutMs); timers.set(timer, false); }
            try {
                const res = await fetch(url, {...options, signal: controller.signal}), data = await res[kind]();
                owner.assert(); return {res, data};
            } finally { requests.delete(controller); if (timer !== null) { clearTimeout(timer); timers.delete(timer); } }
        });
    }
    return Object.freeze({get active() { return owner.active; }, assert: owner.assert, run,
        hasUnsaved: () => credentialsChanged || snapshots().length > 0,
        action: action => function (...args) { const work = owner.run(() => action.apply(this, args)); work.catch(() => {}); return work; },
        json: (url, options) => read('json', url, options), blob: (url, options) => read('blob', url, options),
        later: (action, delay) => schedule(action, delay, false), every: (action, delay) => schedule(action, delay, true),
        async prepareWorkspace() {
            if (!owner.active) return false;
            if (!workspaceOrigin?.loaded && !document.getElementById('workspaceFileContent')?.value) return true;
            if (workspaceDraftValues() && !confirm('Discard unsaved workspace edits before replacing the editor?')) return false;
            return owner.active;
        },
        download(blob, filename) {
            owner.assert(); const url = URL.createObjectURL(blob); urls.add(url); const link = document.createElement('a');
            try { link.href = url; link.download = filename; owner.assert(); link.click(); }
            finally { link.removeAttribute('href'); URL.revokeObjectURL(url); urls.delete(url); }
        }
    });
})();
// Own public main-page actions; lifecycle stop/save hooks keep their raw functions.
for (const name of ['checkConnection', 'initVoiceLayer', 'useVoiceLab', 'toggleVoiceLayer', 'toggleAutoInterruption', 'openVoiceNotes', 'sendReviewedVoiceNote',
    'toggleRecording', 'startRecording', 'fallbackTTS', 'setTheme', 'setAccentColor', 'toggleDiscreet', 'toggleSidebar', 'exportData', 'clearAllData',
    'loadProviderSettings', 'onProviderChange', 'refreshModels', 'onModelChange', 'testProviderConnection', 'applyProviderModel',
    'sendMessage', 'resumePendingTurn', 'runChatStream', 'toggleToolDrawer', 'switchToolTab', 'refreshWorkspaceFiles', 'selectWorkspaceFile',
    'readWorkspaceFile', 'writeWorkspaceFile', 'fetchToolUrl', 'sendToolResultToChat', 'insertPromptTemplate', 'toggleSettings',
    'checkOnboarding', 'openSetupCheck', 'useStarter', 'dismissFeatureHint', 'checkSystemHealth', 'registerServiceWorker']) window[name] = MainUI.action(window[name]);
window.addEventListener('beforeunload', event => {
    if (MainUI.hasUnsaved()) { event.preventDefault(); event.returnValue = ''; }
});
