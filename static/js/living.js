/**
 * Altera — Living portrait (front end).
 *
 * NPC Forge makes frames of her portrait in which only the face changes (core/living.py copies them here). This file brings
 * her picture on the stage to life with them:
 *   - she blinks every few seconds;
 *   - while her voice plays, her mouth opens and closes with it (the loudness of the audio, or a steady rhythm when the
 *     browser will not let the page listen);
 *   - while she is writing she looks thoughtful, and after she answers her face follows what she said (laughing, a blush,
 *     a smile...) for a while before it settles again.
 * The blink and talking frames are made from her resting face, so they are only used while she shows it: an expression fades
 * in over the portrait (#livingMood), a blink or a word shows over that at once (#livingQuick).
 *
 * Switched on and off with Settings > Living portrait (GET/POST /api/living). Reads html[data-companion] (idle / thinking / speaking,
 * set by app.js) and app.js's speechAudio and currentMood().
 */
'use strict';

const Living = {
    enabled: true,
    avatarId: null,
    frames: new Set(),
    urls: {},
    mood: null,                 // the expression showing now (null: her resting face)
    moodTimer: null,
    pendingMood: undefined,     // the expression her reply called for, shown once she has finished speaking (null: resting)
    blinkTimer: null,
    talkRaf: null,
    mouthOpen: false,
    audio: { ctx: null, analyser: null, data: null, connected: new WeakSet(), peak: 0.02 },
    loadRun: 0,
    expression: null,
    expressionTimer: null,
    observer: null,
};
const livingImages = new Set(), livingExtraTimers = new Set(), livingSleeps = new Set();
const LIVING_FRAMES = new Set(['blink', 'talking', 'happy', 'laughing', 'sad', 'surprised', 'smug', 'embarrassed', 'thinking', 'flirty',
    'neutral', 'angry', 'scared', 'disgusted', 'tired']);

const MOOD_HOLD_MS = 7000;      // how long her face keeps an expression after she answers

// What her reply says about her face, strongest first. Her replies are full of emoji, which say it best.
const MOOD_RULES = [
    ['laughing', /😂|🤣|😆|\b(ha){2,}|\b(he){2,}|\blol\b|\blmao\b/i],
    ['surprised', /😮|😲|😱|🤯|😯|\bwow\b|\boh my\b|\bno way\b/i],
    ['sad', /😢|😭|😔|😞|🥺|☹️|🙁|\bso sorry\b|\bsad\b|\bmiss you\b/i],
    ['embarrassed', /😳|🙈|☺️|\bblush|\bshy\b|\bembarrass/i],
    ['flirty', /😏|😘|💋|😉|😈|🔥|\btease|\bflirt|\bkiss/i],
    ['smug', /😎|\bsmirk|\btold you\b|\bobviously\b/i],
    ['happy', /😊|😄|😁|😀|🥰|😍|❤️|💕|💖|💗|✨|\bglad\b|\bhappy\b|\blove\b/i],
];

function pickMood(text) {
    for (const [frame, rule] of MOOD_RULES) {
        if (!rule.test(text || '')) continue;
        // a flirtatious look is for adult mode; outside it the same reply gets a warm smile
        const mood = frame === 'flirty' && (typeof currentMood !== 'function' || currentMood() === 'off') ? 'happy' : frame;
        if (Living.frames.has(mood)) return mood;
    }
    return null;
}

function livingOn() {
    return Living.enabled && Living.frames.size > 0 && !document.hidden;
}

// ---- layers -------------------------------------------------------------------------------------------------------

function layer(id) { return document.getElementById(id); }

function showMood(frame) {
    const el = layer('livingMood');
    if (!el) return;
    Living.mood = frame && livingOn() && Living.frames.has(frame) ? frame : null;
    if (Living.mood) {
        el.src = Living.urls[Living.mood];
        el.classList.add('on');
        hideQuick();                              // a blink or a word belongs to her resting face
    } else {
        el.classList.remove('on');
    }
}

function holdMood(frame) {
    clearTimeout(Living.moodTimer);
    showMood(frame || expressionFrame());
    Living.moodTimer = frame ? setTimeout(() => { Living.moodTimer = null; showMood(expressionFrame()); }, MOOD_HOLD_MS) : null;
}

function expressionFrame() {
    const cue = Living.expression;
    if (!cue || !cue.frame || !Living.frames.has(cue.frame)) return null;
    const elapsed = Math.max(0, Date.now() - cue.receivedAt) / 1000;
    return cue.intensity * Math.pow(2, -elapsed / (6 * 3600)) >= .2 ? cue.frame : null;
}

/** A simulated tone cue; it never interrupts speech or switches another companion's portrait. */
function setCompanionExpression(expression, scope) {
    if (!livingOwner.active) return;
    const owner = typeof Companion !== 'undefined' ? Companion.active?.id || '' : '';
    const epoch = typeof AlteraAPI !== 'undefined' ? AlteraAPI.epoch : null;
    if (!scope || scope.session_id !== currentSessionId || (scope.avatar_id || '') !== owner || scope.dataset_epoch !== epoch) return;
    const frames = new Set(['happy', 'surprised', 'sad', 'thinking']);
    const intensity = Number(expression?.intensity);
    const valid = expression?.simulated === true && Number.isFinite(intensity) && intensity >= 0 && intensity <= 1;
    Living.expression = valid ? {frame: frames.has(expression.frame) ? expression.frame : null,
        intensity, receivedAt: Date.now()} : null;
    clearTimeout(Living.expressionTimer);
    const refresh = () => {
        const state = document.documentElement.dataset.companion;
        if (state !== 'speaking' && state !== 'thinking' && !Living.moodTimer) showMood(expressionFrame());
        if (Living.expression?.frame) Living.expressionTimer = setTimeout(refresh, 60000);
    };
    refresh();
}

function showQuick(frame) {
    const el = layer('livingQuick');
    if (!el || !livingOn() || Living.mood || !Living.frames.has(frame)) return;
    if (el.dataset.frame !== frame) { el.src = Living.urls[frame]; el.dataset.frame = frame; }
    el.classList.add('on');
}

function hideQuick() {
    const el = layer('livingQuick');
    if (el) el.classList.remove('on');
}

// ---- blinking -------------------------------------------------------------------------------------------------------

function scheduleBlink() {
    clearTimeout(Living.blinkTimer);
    if (!livingOwner.active) return;
    Living.blinkTimer = setTimeout(() => {
        if (!livingOwner.active) return;
        if (livingOn() && !Living.mood && !Living.mouthOpen) {
            showQuick('blink');
            livingLater(() => {
                if (!Living.mouthOpen) hideQuick();
                if (Math.random() < 0.18) livingLater(() => {       // now and then a quick second blink
                    if (livingOn() && !Living.mood && !Living.mouthOpen) { showQuick('blink'); livingLater(hideQuick, 110); }
                }, 170);
            }, 120);
        }
        scheduleBlink();
    }, 2400 + Math.random() * 3800);
}

function livingLater(callback, delay) {
    const timer = setTimeout(() => { livingExtraTimers.delete(timer); if (livingOwner.active) callback(); }, delay);
    livingExtraTimers.add(timer);
}

function livingPause(delay) {
    return new Promise(resolve => {
        const wait = {timer: null, resolve}; livingSleeps.add(wait);
        wait.timer = setTimeout(() => { livingSleeps.delete(wait); resolve(); }, delay);
    });
}

// ---- talking ----------------------------------------------------------------------------------------------------------

/** The page may only listen to her voice once the person has touched it (the browser's autoplay rule), so the listening
 *  side is made on the first click or key. A clip is routed through it only while it is running: otherwise it would be silent. */
function ensureAudio() {
    if (Living.audio.ctx) { if (Living.audio.ctx.state === 'suspended') Living.audio.ctx.resume().catch(() => {}); return; }
    const Ctx = window.AudioContext || window.webkitAudioContext;
    if (!Ctx) return;
    try {
        const ctx = new Ctx();
        const analyser = ctx.createAnalyser();
        analyser.fftSize = 512;
        analyser.connect(ctx.destination);
        Living.audio.ctx = ctx;
        Living.audio.analyser = analyser;
        Living.audio.data = new Uint8Array(analyser.fftSize);
    } catch (e) { /* no Web Audio: she talks with the steady rhythm */ }
}

function voiceLevel() {
    const a = Living.audio, el = typeof speechAudio !== 'undefined' ? speechAudio : null;
    if (!el || !a.ctx || a.ctx.state !== 'running') return null;
    if (!a.connected.has(el)) {
        try {
            a.ctx.createMediaElementSource(el).connect(a.analyser);
            a.connected.add(el);
        } catch (e) { return null; }
    }
    a.analyser.getByteTimeDomainData(a.data);
    let sum = 0;
    for (const v of a.data) { const x = (v - 128) / 128; sum += x * x; }
    return Math.sqrt(sum / a.data.length);
}

function startTalking() {
    if (Living.talkRaf || !Living.frames.has('talking')) return;
    clearTimeout(Living.moodTimer);
    if (Living.mood && Living.mood !== 'thinking' && Living.pendingMood === undefined) Living.pendingMood = Living.mood;
    showMood(null);                               // she talks with her resting face; the expression comes after
    let flip = 0;
    const tick = now => {
        Living.talkRaf = requestAnimationFrame(tick);
        if (!livingOn()) { setMouth(false); return; }
        const level = voiceLevel();
        let open;
        if (level === null) {                     // no listening: a syllable rhythm, 110-190 ms
            if (now > flip) { Living.mouthOpen = !Living.mouthOpen; flip = now + 110 + Math.random() * 80; }
            open = Living.mouthOpen;
        } else {
            Living.audio.peak = Math.max(level, Living.audio.peak * 0.995, 0.02);
            const ratio = level / Living.audio.peak;
            open = Living.mouthOpen ? ratio > 0.28 : ratio > 0.42;     // a little hysteresis, so it does not chatter
        }
        setMouth(open);
    };
    Living.talkRaf = requestAnimationFrame(tick);
}

function setMouth(open) {
    Living.mouthOpen = open;
    if (open) showQuick('talking'); else hideQuick();
}

function stopTalking() {
    if (Living.talkRaf) cancelAnimationFrame(Living.talkRaf);
    Living.talkRaf = null;
    setMouth(false);
}

// ---- what she is doing (html[data-companion], set by app.js) -------------------------------------------------------------------

function onCompanionState(state) {
    if (!livingOn()) { stopTalking(); return; }
    if (state === 'speaking') {
        startTalking();
    } else {
        stopTalking();
        if (state === 'thinking') { clearTimeout(Living.moodTimer); Living.moodTimer = null; showMood(Living.frames.has('thinking') ? 'thinking' : null); }
        else if (Living.pendingMood !== undefined) { holdMood(Living.pendingMood); Living.pendingMood = undefined; }
        else if (Living.mood === 'thinking' || !Living.moodTimer) showMood(expressionFrame());
    }
}

/** app.js calls this with her finished reply. The expression waits until her voice has finished, if she speaks. */
function livingReply(text) {
    if (!livingOn()) return;
    const mood = pickMood(text);
    if (document.documentElement.dataset.companion === 'speaking') Living.pendingMood = mood;
    else { Living.pendingMood = undefined; holdMood(mood); }
}

// ---- loading her frames ---------------------------------------------------------------------------------------------------

function resetLiving() {
    for (const image of livingImages) image.removeAttribute('src');
    stopTalking();
    clearTimeout(Living.moodTimer);
    clearTimeout(Living.expressionTimer);
    Living.moodTimer = null;
    Living.expression = null;
    Living.pendingMood = undefined;
    showMood(null);
    hideQuick();
    Living.frames = new Set();
    Living.urls = {};
    const quick = layer('livingQuick');
    if (quick) delete quick.dataset.frame;
    for (const id of ['livingMood', 'livingQuick']) layer(id)?.removeAttribute?.('src');
}

function livingFrameUrl(avatarId, frame, syncedAt) {
    if (typeof avatarId !== 'string' || !/^[a-z0-9][a-z0-9-]{2,63}$/.test(avatarId)
            || /[^a-z0-9-]/.test(avatarId) || !LIVING_FRAMES.has(frame)) return null;
    const path = `/api/avatars/${avatarId}/living/${frame}`;
    return `${path}?v=${encodeURIComponent(syncedAt || '')}`;
}

function loadLiving(avatarId) {
    return livingOwner.run(() => loadLivingOwned(avatarId)).catch(error => {
        if (livingOwner.active) renderLivingStatus({reason: error.message});
    });
}

async function loadLivingOwned(avatarId) {
    const run = ++Living.loadRun;
    Living.avatarId = avatarId;
    resetLiving();
    if (!avatarId) { renderLivingStatus(null); return; }
    for (let tries = 0; tries < 40 && run === Living.loadRun; tries++) {
        let status;
        try {
            status = await cApi(`/api/avatars/${avatarId}/living`);
        } catch (err) {
            if (livingOwner.active && run === Living.loadRun) renderLivingStatus({ reason: err.message });
            return;
        }
        if (!livingOwner.active || run !== Living.loadRun) return;
        if (!status || !Array.isArray(status.frames) || status.frames.length > LIVING_FRAMES.size
                || status.frames.some(f => !LIVING_FRAMES.has(f)) || new Set(status.frames).size !== status.frames.length) {
            throw new Error('Living portrait metadata could not be verified.');
        }
        Living.enabled = status.enabled;
        renderLivingStatus(status);
        if (!Living.enabled) { resetLiving(); return; }
        if (status.available) {
            const urls = {};
            for (const f of status.frames) {
                urls[f] = livingFrameUrl(avatarId, f, status.synced_at);
                if (!urls[f]) throw new Error('Living portrait context could not be verified.');
            }
            // every frame is fetched and decoded before any is shown, so a blink never shows a half-loaded picture
            const loaded = await Promise.all(status.frames.map(async f => {
                const img = new Image();
                livingImages.add(img);
                try { img.src = urls[f]; await img.decode(); return f; }
                catch (_) { return null; }
                finally { livingImages.delete(img); }
            }));
            if (!livingOwner.active || run !== Living.loadRun) return;
            Living.urls = urls;
            Living.frames = new Set(loaded.filter(Boolean));
            if (Living.frames.size !== status.frames.length) {
                renderLivingStatus({...status, available: Living.frames.size > 0, frames: [...Living.frames], checked: 0,
                    reason: 'Some saved living portrait frames could not be loaded.'});
            }
            if (document.documentElement.dataset.companion === 'idle') showMood(expressionFrame());
            return;
        }
        if (!status.running) return;
        await livingPause(3000);      // NPC Forge's frames are being copied and checked: look again
    }
}

function renderLivingStatus(status) {
    if (!livingOwner.active) return;
    const toggle = document.getElementById('livingToggle');
    const hint = document.getElementById('livingStatus');
    if (toggle) toggle.checked = Living.enabled;
    if (!hint) return;
    hint.classList.remove('error');
    const name = (Companion.active && Companion.active.name) || 'The portrait';
    if (!status) { hint.textContent = 'The portrait blinks, moves its mouth while the voice plays and changes expression, when NPC Forge has made a living portrait.'; return; }
    if (status.running && !status.available) { hint.textContent = `Fetching ${name}'s living portrait from NPC Forge and age-checking it…`; return; }
    if (!status.available) {
        hint.replaceChildren(status.reason || 'No living portrait yet.', ' ', h('button', { type: 'button', class: 'link-btn', text: 'Check again',
            onclick: ev => { ev.stopPropagation(); syncLiving(); } }));
        return;
    }
    const unchecked = status.frames.length - status.checked;
    const checkNote = unchecked
        ? ` (${unchecked} not age-checked yet: the vision model did not answer; they are checked again next time).` : ', all age-checked.';
    hint.textContent = `${Living.enabled ? 'On' : 'Off'}: ${status.frames.length} frames of ${name}` + checkNote;
}

async function syncLiving() {
    if (!livingOwner.active) return;
    const id = Living.avatarId;
    if (!id) return;
    try { await cApi(`/api/avatars/${id}/living/sync`, { method: 'POST' }); } catch (err) { renderLivingStatus({ reason: err.message }); return; }
    loadLiving(id);
}

function setLivingEnabled(on) {
    return livingOwner.run(() => setLivingEnabledOwned(on)).catch(error => {
        if (livingOwner.active) renderLivingStatus({reason:error.message});
    });
}

async function setLivingEnabledOwned(on) {
    if (!livingOwner.active) return;
    try {
        Living.enabled = (await cApi('/api/living', { method: 'POST', body: { enabled: !!on } })).enabled;
    } catch (err) {
        renderLivingStatus({ reason: err.message });
        return;
    }
    if (!Living.enabled) resetLiving();
    loadLiving(Living.avatarId);
}

// ---- start-up -----------------------------------------------------------------------------------------------------------

function initLiving() {
    cApi('/api/living').then(s => {
        if (!livingOwner.active) return;
        Living.enabled = s.enabled; renderLivingStatus(null);
    }).catch(() => {});
    Living.observer = new MutationObserver(() => onCompanionState(document.documentElement.dataset.companion));
    Living.observer.observe(document.documentElement, { attributes: true, attributeFilter: ['data-companion'] });
    ['pointerdown', 'keydown'].forEach(ev => document.addEventListener(ev, ensureAudio, { passive: true }));
    document.addEventListener('visibilitychange', livingVisibility);
    scheduleBlink();
}

function livingVisibility() { if (document.hidden) { stopTalking(); hideQuick(); } }

const livingOwner = AlteraPage.component('living-portrait', {
    stop() {
        Living.enabled = false; Living.loadRun++; Living.avatarId = null;
        clearTimeout(Living.blinkTimer); Living.blinkTimer = null;
        for (const timer of livingExtraTimers) clearTimeout(timer); livingExtraTimers.clear();
        for (const wait of livingSleeps) { clearTimeout(wait.timer); wait.resolve(); } livingSleeps.clear();
        for (const image of livingImages) image.removeAttribute('src');
        Living.observer?.disconnect(); Living.observer = null;
        for (const event of ['pointerdown', 'keydown']) document.removeEventListener?.(event, ensureAudio);
        document.removeEventListener?.('visibilitychange', livingVisibility);
        resetLiving(); return true;
    },
    async clear() {
        await Living.audio.ctx?.close();
        Living.audio = {ctx: null, analyser: null, data: null, connected: new WeakSet(), peak: .02};
        return true;
    }
});

AlteraPage.ready(() => { if (livingOwner.active) initLiving(); });
