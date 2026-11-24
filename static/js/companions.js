/**
 * Altera — Companions (front end).
 *
 * Every avatar is a companion with a chat of her own. This file is the sidebar list, opening one, her picture, her first
 * moments (she wakes up and speaks first), meeting someone new through NPC Forge, and the notes she keeps about you.
 * Everything shown comes from JSON through textContent, never innerHTML. Helpers cApi() and h() come from companion.js.
 */
'use strict';

Companion.companions = [];
Companion.current = null;            // the companion whose chat is open (the summary the server gave)
Companion.forge = null;              // what NPC Forge said last time

const byId = id => document.getElementById(id);
const companionSleeps = new Set(), companionViewTimers = new Set(), companionFrames = new Set();
const sleep = ms => new Promise(resolve => {
    const wait = {resolve, timer: null}; companionSleeps.add(wait);
    wait.timer = setTimeout(() => { companionSleeps.delete(wait); resolve(); }, ms);
});
const companionViewTracked = action => function (...args) {
    return companionViewOwner.run(() => action.apply(this, args)).catch(error => { if (companionViewOwner.active) throw error; });
};
function companionViewLater(action, delay) {
    if (!companionViewOwner.active) return;
    const item = {id: null}; companionViewTimers.add(item);
    item.id = setTimeout(() => { companionViewTimers.delete(item); if (companionViewOwner.active) action(); }, delay);
}
function companionFrame(action) {
    const item = {id: null}; companionFrames.add(item);
    item.id = requestAnimationFrame(() => { companionFrames.delete(item); if (companionViewOwner.active) action(); });
}

function timeAgo(stamp) {
    if (!stamp) return '';
    const then = new Date(String(stamp).replace(' ', 'T') + (/[zZ]|[+-]\d\d:?\d\d$/.test(stamp) ? '' : 'Z'));
    const seconds = Math.max(0, (Date.now() - then.getTime()) / 1000);
    if (isNaN(seconds)) return '';
    if (seconds < 90) return 'just now';
    if (seconds < 3600) return `${Math.round(seconds / 60)} min ago`;
    if (seconds < 86400) return `${Math.round(seconds / 3600)} h ago`;
    return `${Math.round(seconds / 86400)} d ago`;
}

function closeSidebarOnMobile() {
    const sidebar = byId('sidebar');
    if (sidebar && sidebar.classList.contains('mobile-open') && typeof toggleSidebar === 'function') toggleSidebar();
}

// The sidebar holds pals, characters and companions, so a row names which is which (and kids mode when it is on).
const PERSON_KIND_LABELS = {pal: 'Pal', character: 'Character', companion: 'Companion'};
const kindLabel = record => PERSON_KIND_LABELS[(record || {}).kind] || 'Companion';

// ============================================================
// The list, opening a companion
// ============================================================

async function loadCompanions() {
    try {
        Companion.companions = (await cApi('/api/companions')).companions;
    } catch (err) {
        if (!companionViewOwner.active) return;
        console.warn('Could not load companions:', err);
    }
    renderCompanionList();
}

function renderCompanionList() {
    if (!companionViewOwner.active) return;
    const box = byId('companionList');
    if (!box) return;
    box.replaceChildren();
    if (!Companion.companions.length) {
        const kidsView = document.body.classList.contains('kids-view');
        box.append(h('p', { class: 'companion-empty',
            text: kidsView ? 'No kids pals yet.' : 'Nobody yet. Add someone to begin.' }));
        return;
    }
    for (const c of Companion.companions) {
        const active = Companion.current && Companion.current.id === c.id;
        const line = c.met ? `${c.messages} message${c.messages === 1 ? '' : 's'} · ${timeAgo(c.last_at)}` : 'Not met yet';
        const meta = [c.kind === 'pal' || c.kind === 'character' ? kindLabel(c) : '', c.kids === true ? 'Kids mode' : ''];
        box.append(h('button', { type: 'button', class: 'companion-item' + (active ? ' active' : ''),
            onclick: () => switchCompanion(c.id) },
            h('img', { class: 'companion-thumb', src: portraitUrl(c.id), alt: '', loading: 'lazy' }),
            h('span', { class: 'companion-text' }, h('strong', { text: c.name }), h('span', { text: [line, ...meta].filter(Boolean).join(' · ') }))));
    }
}

/** The page opens on the companion you talked to last. Returns false when there is nobody (the default chat is used). */
async function openCurrentCompanion(voiceReady) {
    let current;
    const selection = Companion.selection;
    try {
        current = await cApi('/api/companions/current');
        if (!companionViewOwner.active || selection !== Companion.selection) return true;
    } catch (err) {
        console.warn('Could not open the last companion:', err);
        return false;
    }
    if (!current.avatar) {
        loadCompanions();
        return false;
    }
    await enterCompanion(current.avatar, voiceReady);
    return true;
}

async function switchCompanion(id) {
    if (typeof isStreaming !== 'undefined' && isStreaming) return;
    if (typeof AlteraSharedScene !== 'undefined' && !AlteraSharedScene.prepareNavigation()) return;
    closeSidebarOnMobile();
    if (Companion.current && Companion.current.id === id) return;
    try {
        if (typeof ChatControls !== 'undefined' && await ChatControls.prepareNavigation() !== true) return;
        if (!companionViewOwner.active) return;
        await enterCompanion(await cApi(`/api/companions/${id}/open`, { method: 'POST' }));
    } catch (err) {
        if (companionViewOwner.active) { console.warn('Could not open that person:', err); showReplyNotice('The companion change was not confirmed. Your unsaved edits have been kept for review.'); }
    }
}

/** Show a companion and her chat. A companion nobody has spoken with yet wakes up and says hello first. */
async function enterCompanion(summary, voiceReady) {
    if (typeof AlteraSharedScene !== 'undefined' && !AlteraSharedScene.prepareNavigation()) return;
    if (typeof endAutomaticRecordingForContextChange === 'function') endAutomaticRecordingForContextChange();
    if (typeof stopSpeech === 'function') stopSpeech();
    if (typeof ChatControls !== 'undefined' && await ChatControls.prepareNavigation() !== true) return;
    if (typeof AlteraSharedScene !== 'undefined' && !AlteraSharedScene.prepareNavigation()) return;
    resetCompanionEditors();
    if (typeof clearPhotoGallery === 'function') clearPhotoGallery();
    Companion.selection = (Companion.selection || 0) + 1;
    const selection = Companion.selection;
    Companion.current = summary;
    Companion.active = summary;
    currentSessionId = summary.session_id;
    if (typeof updateSessionSignal === 'function') updateSessionSignal();
    applyActiveAvatar();
    if (typeof refreshAdult === 'function') refreshAdult();
    byId('messagesContainer').replaceChildren();
    loadCompanions();
    if (summary.messages === 0) {
        await beginFirstMeeting(summary, voiceReady);
    } else {
        await loadMessages(summary.session_id);
        if (selection !== Companion.selection) return;
        if (typeof resumePendingTurn === 'function') await resumePendingTurn();
    }
    if (selection !== Companion.selection) return;
    refreshCompanionStatus(summary.id);
}

// ============================================================
// Her picture
// ============================================================

function renderPortraitPanel() {
    if (!companionViewOwner.active) return;
    const a = Companion.active;
    const panel = byId('portraitPanel');
    const area = document.querySelector('.chat-area');
    if (!panel || !area) return;
    if (!a) {
        panel.hidden = true;
        area.classList.remove('has-companion');
        area.style.removeProperty('--companion-portrait');
        if (typeof loadLiving === 'function' && Living.avatarId) loadLiving(null);
        return;
    }
    panel.hidden = false;
    area.classList.add('has-companion');
    const url = portraitUrl(a.id);
    if (url) area.style.setProperty('--companion-portrait', `url("${url}")`); else area.style.removeProperty('--companion-portrait');
    setPortraitImage(byId('portraitImg'), a.id);
    byId('portraitImg').alt = `${a.name}'s portrait`;
    byId('portraitName').textContent = a.name;
    byId('portraitLine').textContent = (a.kind === 'pal' ? ['Pal', a.vibe || '', a.kids ? 'Kids mode' : ''] : a.kind === 'character' ? ['Character'] : [forgeAgeLabel(a), a.occupation || '']).filter(Boolean).join(' · ');
    const chips = byId('portraitTraits');
    chips.replaceChildren(...(a.traits || []).map(t => h('span', { class: 'trait-chip', text: t })));
    byId('portraitBio').textContent = a.bio || '';
    byId('portraitNote').textContent = '';
    if (typeof loadLiving === 'function' && Living.avatarId !== a.id) loadLiving(a.id);     // her living portrait (living.js)
}

function openPortraitView() {
    const a = Companion.active;
    if (!a) return;
    setPortraitImage(byId('portraitViewImg'), a.id);
    byId('portraitViewCaption').textContent = [a.name, forgeAgeLabel(a), a.occupation || ''].filter(Boolean).join(' · ');
    byId('portraitView').hidden = false;
}

function closePortraitView() {
    byId('portraitView').hidden = true;
}

// ============================================================
// Her first moments
// ============================================================

function setBirthCaption(text) {
    if (!companionViewOwner.active) return;
    byId('birthCaption').textContent = text;
}

function hideBirth() {
    const overlay = byId('birthOverlay');
    overlay.classList.remove('show');
    companionViewLater(() => { if (!overlay.classList.contains('show')) overlay.hidden = true; }, 900);
}

/** She has just been made: her picture fades in, she looks at herself, and she speaks first. */
async function beginFirstMeeting(summary, voiceReady) {
    const overlay = byId('birthOverlay');
    setPortraitImage(byId('birthImg'), summary.id);
    byId('birthName').textContent = summary.name;
    const pal = summary.kind === 'pal' || summary.kind === 'character';   // they just say hello; a companion wakes up
    setBirthCaption(pal ? 'Saying hello…' : 'Waking up…');
    overlay.hidden = false;
    companionFrame(() => companionFrame(() => overlay.classList.add('show')));
    await Promise.all([sleep(pal ? 700 : 2200), voiceReady]);      // show the arrival and load speech preferences before hello
    if (!companionViewOwner.active || Companion.current?.id !== summary.id || currentSessionId !== summary.session_id) return;
    isStreaming = true;
    updateSendButton(true);
    let handedOver = false;
    try {
        await runChatStream({ session_id: summary.session_id, greet: true }, {
            onStatus: data => {
                if (!companionViewOwner.active || Companion.current?.id !== summary.id || currentSessionId !== summary.session_id) return;
                if (pal) return;
                if (data.status === 'looking') setBirthCaption('Looking at themselves for the first time…');
                else if (data.status === 'typing') setBirthCaption('Finding their first words…');
            },
            onText: () => { if (companionViewOwner.active && Companion.current?.id === summary.id && currentSessionId === summary.session_id && !handedOver) { handedOver = true; companionViewLater(hideBirth, 700); } },
        });
    } catch (err) {
        if (companionViewOwner.active) {
            if (err.replyState !== 'interrupted') console.error('First meeting failed:', err);
            showReplyNotice(err.message);
        }
    } finally {
        if (companionViewOwner.active && Companion.current?.id === summary.id && currentSessionId === summary.session_id) {
            isStreaming = false; updateSendButton(false); hideBirth(); loadCompanions();
        }
    }
}

// ============================================================
// Meeting someone new
// ============================================================

let meetRun = 0;                                                  // changes when the window closes, which ends any waiting

function openMeetModal() {
    closeSidebarOnMobile();
    byId('meetOverlay').style.display = 'flex';
    meetProgress('', false);
    showMeetTab('make');
    loadMeetStatus();
    loadMeetLibrary();
}

// The sidebar's Add someone button offers the same three choices Home does: a pal, a character card, a companion.
function openAddPersonMenu() {
    const menu = byId('addPersonMenu'), button = byId('addPersonButton');
    if (!menu) return;
    const open = menu.hidden;
    menu.hidden = !open;
    button?.setAttribute('aria-expanded', String(open));
    if (open) menu.querySelector('button')?.focus();
}

function closeAddPersonMenu() {
    const menu = byId('addPersonMenu');
    if (menu) menu.hidden = true;
    byId('addPersonButton')?.setAttribute('aria-expanded', 'false');
}

function addPersonFromMenu(kind) {
    closeAddPersonMenu();
    closeSidebarOnMobile();
    if (kind === 'pal') { window.location.assign('/home?make=pal'); return; }
    if (kind === 'companion') { window.location.assign('/home?make=companion'); return; }
    if (kind === 'character') { byId('sidebarCardFile')?.click(); return; }
    openMeetModal();
}

async function importSidebarCard(file) {
    if (!file) return;
    const form = new FormData();
    form.append('file', file, file.name || 'card');
    try {
        await cApi('/api/characters/import', {method: 'POST', body: form});
        window.location.assign('/');
    } catch (err) {
        showReplyNotice(err.message || 'Could not import the card. Try again.');
    }
}

/** The start screen links to /?meet=1: open "Meet someone new" once, then drop the flag so a reload does not reopen it. */
function openRequestedMeet() {
    let params;
    try { params = new URLSearchParams(window.location.search); } catch (_) { return false; }
    if (params.get('meet') !== '1') return false;
    params.delete('meet');
    const rest = params.toString();
    try { window.history.replaceState(null, '', window.location.pathname + (rest ? `?${rest}` : '') + window.location.hash); } catch (_) { /* keep going */ }
    openMeetModal();
    return true;
}

function closeMeetModal() {
    meetRun++;
    byId('meetOverlay').style.display = 'none';
}

function showMeetTab(tab) {
    byId('meetMake').hidden = tab !== 'make';
    byId('meetLibrary').hidden = tab !== 'library';
    byId('meetTabMake').classList.toggle('active', tab === 'make');
    byId('meetTabLibrary').classList.toggle('active', tab === 'library');
}

function fillSelect(select, first, options) {
    select.replaceChildren(h('option', { value: '', text: first }), ...options.map(o => h('option', { value: o.id, text: o.label })));
}

async function loadMeetStatus() {
    const note = byId('meetNote');
    const go = byId('meetGo');
    const start = byId('meetStart');
    note.textContent = 'Asking NPC Forge…';
    note.classList.remove('error');
    go.disabled = true;
    start.hidden = true;
    let status;
    try {
        status = await cApi('/api/forge/status');
    } catch (err) {
        if (!companionViewOwner.active) return;
        note.textContent = err.message;
        note.classList.add('error');
        return;
    }
    Companion.forge = status;
    const options = status.options || {};
    fillSelect(byId('meetOrigin'), 'Surprise me', options.origins || []);
    fillSelect(byId('meetHair'), 'Surprise me', (options.hair_colors || []).map(c => ({ id: c, label: c })));
    fillSelect(byId('meetAge'), 'Surprise me', options.age_groups || []);
    const ready = status.online && status.meet_supported && status.comfy_online !== false;
    go.disabled = !ready;
    start.hidden = !status.not_running;
    note.classList.toggle('error', !ready);
    const message = ready
        ? 'NPC Forge is ready.'
        : (status.meet_message || status.message || 'NPC Forge cannot make someone new right now.');
    note.textContent = [message, ...(status.notices || [])].join(' ');
}

async function startForgeFromHer() {
    const note = byId('meetNote');
    byId('meetStart').disabled = true;
    try {
        const answer = await cApi('/api/forge/start', { method: 'POST' });
        note.textContent = answer.message;
        if (!answer.started && !/already running/.test(answer.message)) {
            note.classList.add('error');
            return;
        }
    } catch (err) {
        if (!companionViewOwner.active) return;
        note.textContent = err.message;
        note.classList.add('error');
        return;
    } finally {
        if (companionViewOwner.active) byId('meetStart').disabled = false;
    }
    const run = meetRun;
    for (let i = 0; i < 20 && run === meetRun; i++) {                // up to about 40 seconds
        await sleep(2000);
        if (!companionViewOwner.active || run !== meetRun) return;
        await loadMeetStatus();
        if (Companion.forge && Companion.forge.online) return;
    }
}

function meetProgress(text, isError, cancel, elapsed = '') {
    if (!companionViewOwner.active) return;
    const box = byId('meetProgress');
    box.hidden = !text;
    const note = byId('meetProgressNote');
    if (note.textContent !== text) note.textContent = text;
    byId('meetSpinner').hidden = !text || isError;
    byId('meetElapsed').textContent = elapsed;
    const button = box.querySelector('button');
    if (cancel && !button) box.append(h('button', { type: 'button', class: 'small', text: 'Cancel', onclick: cancel }));
    if (!cancel) button?.remove();
    box.classList.toggle('error', !!isError);
}

async function makeSomeone() {
    const body = { gender: byId('meetGender').value, origin: byId('meetOrigin').value,
        hair_color: byId('meetHair').value, age_group: byId('meetAge').value };
    const run = ++meetRun;
    const go = byId('meetGo');
    const hadFocus = document.activeElement === go;
    go.disabled = true;
    meetProgress('NPC Forge is starting the portrait. You can cancel once it’s queued.', false);
    let started;
    try {
        started = await cApi('/api/companions/make', { method: 'POST', body });
    } catch (err) {
        if (!companionViewOwner.active || run !== meetRun) return;
        meetProgress(err.message, true);
        go.disabled = false;
        if (hadFocus && document.activeElement === document.body) go.focus();
        return;
    }
    if (!companionViewOwner.active || run !== meetRun) return;
    const url = `/api/companions/make/${started.npc_id}/${started.job_id}`;
    const began = Date.now();
    let retried = false;
    const cancel = async () => {
        if (run !== meetRun) return;
        const cancelling = ++meetRun;
        meetProgress('Cancelling the portrait…', false);
        try {
            await cApi(url, { method: 'DELETE' });
            if (cancelling === meetRun) meetProgress('Cancelled.', true);
        } catch (err) {
            if (cancelling === meetRun) meetProgress(err.message, true);
        }
        if (companionViewOwner.active && cancelling === meetRun) {
            go.disabled = false;
            go.focus();
        }
    };
    meetProgress('NPC Forge is making their portrait…', false, cancel);
    if (hadFocus && document.activeElement === document.body) byId('meetProgress').querySelector('button')?.focus();
    while (run === meetRun) {
        let answer;
        try {
            answer = await cApi(url);
        } catch (err) {
            if (run === meetRun) meetProgress(err.message, true);
            break;
        }
        if (!companionViewOwner.active || run !== meetRun) break;
        if (answer.state === 'ready') {
            meetRun++;
            byId('meetOverlay').style.display = 'none';
            await enterCompanion(answer.companion);
            break;
        }
        if (answer.state === 'error' && answer.code === 'gpu_busy' && !retried) {
            retried = true;
            meetProgress('Checking whether NPC Forge can resume the portrait…', false, cancel);
            try {
                await cApi(`${url}/retry`, { method: 'POST' });
            } catch (err) {
                if (companionViewOwner.active && run === meetRun) meetProgress(err.message, true);
                break;
            }
            continue;
        }
        if (['error', 'rejected', 'cancelled'].includes(answer.state)) {
            meetProgress(answer.note || 'That did not work.', true);
            break;
        }
        const seconds = Math.round((Date.now() - began) / 1000);
        const step = answer.note && answer.note !== 'Portrait' ? ` (${answer.note})` : '';
        meetProgress(`NPC Forge is making their portrait${step}…`, false, cancel, ` ${seconds}s`);
        await sleep(2000);
    }
    if (run === meetRun) {
        go.disabled = false;
        if (document.activeElement === document.body) go.focus();
    }
}

async function loadMeetLibrary() {
    const grid = byId('meetLibraryGrid');
    const hint = byId('meetLibraryHint');
    grid.replaceChildren();
    hint.textContent = 'Loading NPC Forge characters…';
    let data;
    try {
        data = await cApi('/api/avatars/forge');
    } catch (err) {
        if (!companionViewOwner.active) return;
        hint.textContent = err.message;
        return;
    }
    if (!data.available) {
        hint.textContent = 'NPC Forge\'s library folder was not found. Set imagegen.npc_forge_library in config.yaml.';
        return;
    }
    hint.textContent = data.characters.length
        ? 'Characters you made in NPC Forge. Only adults with a portrait can be met.'
        : 'NPC Forge has no characters yet. Make someone new instead, or create one in NPC Forge.';
    for (const c of data.characters) {
        const button = h('button', { class: 'primary', text: c.imported ? 'Open' : 'Open them', disabled: !c.eligible, onclick: ev => meetFromLibrary(c, ev.currentTarget) });
        grid.append(h('div', { class: 'avatar-card' + (c.eligible ? '' : ' disabled') },
            h('img', { src: `/api/forge/${c.id}/thumb`, alt: '', loading: 'lazy' }),
            h('strong', { text: c.name }),
            h('div', { class: 'meta' }, `${forgeAgeLabel(c, 'age not set')}${c.gender ? ', ' + c.gender : ''}`),
            c.imported ? h('div', {}, h('span', { class: 'badge', text: 'Already here' })) : null,
            c.reason ? h('div', { class: 'reason', text: c.reason }) : null,
            h('div', { class: 'card-actions' }, button)));
    }
}

async function meetFromLibrary(character, button) {
    const body = { npc_id: character.id };
    if (character.needs_age) {
        const answer = window.prompt(`${character.name} has no age in NPC Forge. Enter an age (18 or older):`);
        if (answer === null) return;
        body.age = answer.trim();
    }
    button.disabled = true;
    button.textContent = 'One moment…';
    const hint = byId('meetLibraryHint');
    try {
        if (!character.imported) {
            hint.textContent = 'A vision model checks this portrait, which can take a little while.';
            await cApi('/api/avatars/import', { method: 'POST', body });
        }
        const summary = await cApi(`/api/companions/${character.id}/open`, { method: 'POST' });
        byId('meetOverlay').style.display = 'none';
        await enterCompanion(summary);
    } catch (err) {
        if (!companionViewOwner.active) return;
        hint.textContent = err.message;
        hint.classList.add('error');
        button.disabled = false;
        button.textContent = character.imported ? 'Open' : 'Open them';
    }
}

// ============================================================
// What she knows (her notes)
// ============================================================

let notesWatcher = 0, notesScope = null, notesBaseline = null, notesWatchScope = null;
const notesValues = () => ({about_you: byId('notesAbout')?.value || '', our_story: byId('notesStory')?.value || ''});
const notesDirty = () => Boolean(notesScope && Object.entries(notesValues()).some(([key, value]) => value !== (notesBaseline?.[key] || '')));
const notesContextMatches = scope => scope && ['avatar_id', 'session_id', 'dataset_epoch'].every(key => companionContext()[key] === scope[key]);
function resetCompanionEditors() {
    notesWatcher++; notesWatchScope = null;
    if (!notesDirty()) {
        notesScope = notesBaseline = null;
        for (const id of ['notesAbout', 'notesStory']) if (byId(id)) byId(id).value = '';
    }
}
async function openNotes() {
    const context = companionContext(); if (!context.avatar_id) return;
    if (notesContextMatches(notesScope) && notesDirty()) { byId('notesOverlay').style.display = 'flex'; return; }
    if (notesDirty() && !window.confirm('Discard the unsaved notebook edits before opening another notebook? Cancel to keep them.')) return;
    notesScope = {...context}; notesBaseline = null;
    for (const id of ['notesAbout', 'notesStory']) { byId(id).value = ''; byId(id).readOnly = true; }
    byId('notesTitle').textContent = `What ${context.avatar_name} knows`;
    byId('notesOverlay').style.display = 'flex'; await loadNotes();
}
function closeNotes() { byId('notesOverlay').style.display = 'none'; }
function notesStatusText(status) {
    if (status.busy) return 'Notes are being written right now…';
    const words = status.words || {};
    const parts = [`${words.about_you || 0} words about you`, `${words.our_story || 0} about your story`];
    let text = parts.join(', ') + '.';
    if (status.updated_at) text += ` Last updated ${timeAgo(status.updated_at.replace('T', ' '))}.`;
    if (status.waiting) text += ` ${status.waiting} new message${status.waiting === 1 ? '' : 's'} not written up yet.`;
    if (status.failures && status.last_error) text += ` The last update did not work (${status.last_error}); it will try again.`;
    return text;
}
async function loadNotes() {
    const scope = notesScope, status = byId('notesStatus'); if (!scope || !notesContextMatches(scope)) return;
    if (notesDirty()) { status.textContent = 'Unsaved notebook edits are kept here. Save or review them before loading another version.'; return; }
    status.classList.remove('error');
    try {
        const data = await cApi(`/api/companions/${encodeURIComponent(scope.avatar_id)}/notes`);
        if (!companionViewOwner.active || scope !== notesScope || !notesContextMatches(scope) || notesDirty()) return;
        notesBaseline = {about_you: data.notes.about_you, our_story: data.notes.our_story};
        byId('notesAbout').value = notesBaseline.about_you; byId('notesStory').value = notesBaseline.our_story;
        for (const id of ['notesAbout', 'notesStory']) byId(id).readOnly = false;
        status.textContent = notesStatusText(data.status); byId('notesNow').disabled = data.status.busy;
    } catch (err) { if (companionViewOwner.active && scope === notesScope) { status.textContent = err.message; status.classList.add('error'); } }
}
async function saveNotes() {
    const scope = notesScope, values = notesValues(), status = byId('notesStatus');
    try {
        if (!scope || !notesBaseline || !notesContextMatches(scope)) throw new Error('Open the original person and reload their notes before saving. Your edits are kept.');
        const data = await cApi(`/api/companions/${encodeURIComponent(scope.avatar_id)}/notes`, {method:'PUT', body:values});
        if (!companionViewOwner.active || scope !== notesScope || !notesContextMatches(scope)) return;
        notesBaseline = values; status.classList.remove('error');
        status.textContent = notesDirty() ? 'The submitted version was saved. Newer edits are still unsaved.' : 'Saved. ' + notesStatusText(data.status);
    } catch (err) { if (companionViewOwner.active && scope === notesScope) { status.classList.add('error'); status.textContent = err.message; } }
}
async function updateNotesNow() {
    const scope = notesScope, status = byId('notesStatus');
    try {
        if (!scope || !notesContextMatches(scope)) throw new Error('Open the original companion before updating this notebook.');
        await cApi(`/api/companions/${encodeURIComponent(scope.avatar_id)}/notes/update`, {method:'POST'});
        if (!companionViewOwner.active || scope !== notesScope || !notesContextMatches(scope)) return;
    } catch (err) { if (companionViewOwner.active && scope === notesScope) { status.classList.add('error'); status.textContent = err.message; } return; }
    status.classList.remove('error'); status.textContent = 'Writing notes… (this can take a minute)';
    byId('notesNow').disabled = true; watchNotes();
}

/** Poll until her notes are written (started by a reply or by Update now), then say so. */
async function watchNotes() {
    const a = Companion.active;
    if (!a) return;
    if (notesWatchScope?.avatar_id === String(a.id)) return;
    const watching = {avatar_id: String(a.id)}; notesWatchScope = watching;
    const run = ++notesWatcher;
    try {
    showNotesBusy(true);
    for (let i = 0; i < 90 && run === notesWatcher; i++) {          // up to about 4.5 minutes
        await sleep(3000);
        if (!companionViewOwner.active || run !== notesWatcher || Companion.active?.id !== a.id) return;
        let status;
        try {
            status = (await cApi(`/api/companions/${a.id}/status`)).notes;
        } catch (err) {
            break;
        }
        if (!status.busy) break;
    }
    if (run !== notesWatcher) return;
    showNotesBusy(false);
    if (byId('notesOverlay').style.display === 'flex') loadNotes();
    await refreshCompanionStatus(a.id, false);
    } finally { if (notesWatchScope === watching) notesWatchScope = null; }
}

function showNotesBusy(busy) {
    const text = byId('statusText');
    if (!text) return;
    text.dataset.notes = busy ? '1' : '';
    if (busy && !(typeof isStreaming !== 'undefined' && isStreaming)) text.textContent = 'Writing things down…';
    else if (!busy && !(typeof isStreaming !== 'undefined' && isStreaming)) text.textContent = typeof connectionStatusText === 'function' ? connectionStatusText() : 'Connecting to Altera…';
}

async function refreshCompanionStatus(avatarId, pollNotes = true) {
    try {
        const data = await cApi(`/api/companions/${avatarId}/status`);
        if (!companionViewOwner.active || Companion.active?.id !== avatarId) return;
        const bits = [];
        if (data.pictures && data.pictures.running) bits.push('Fetching pictures from NPC Forge…');
        else if (data.gallery) bits.push(`${data.gallery} picture${data.gallery === 1 ? '' : 's'} ready to send`);
        const waiting = data.pictures && data.pictures.last && data.pictures.last.waiting;
        if (waiting) bits.push(`${waiting} waiting for the age check`);
        if (data.notes && data.notes.busy) { bits.push('Writing things down…'); if (pollNotes) watchNotes(); }
        else if (data.notes && data.notes.updated_at) bits.push(`Notes updated ${timeAgo(data.notes.updated_at.replace('T', ' '))}`);
        byId('portraitNote').textContent = bits.join(' · ');
    } catch (err) {
        /* the panel just shows no note */
    }
}

// Pictures approved in NPC Forge's studio while this page was in the background: fetch them when the page comes back to the front
let lastPictureSync = 0;
document.addEventListener('visibilitychange', () => {
    if (!companionViewOwner.active || document.visibilityState !== 'visible' || !Companion.current || Date.now() - lastPictureSync < 30000) return;
    lastPictureSync = Date.now();
    const id = Companion.current.id;
    cApi(`/api/companions/${id}/pictures/sync`, { method: 'POST' })
        .then(() => companionViewLater(() => { if (Companion.current && Companion.current.id === id) refreshCompanionStatus(id); }, 5000))
        .catch(() => {});
});

// ============================================================
// Start-up
// ============================================================

document.addEventListener('keydown', ev => {
    if (!companionViewOwner.active) return;
    if (ev.key !== 'Escape') return;
    closePortraitView();
    closeNotes();
    if (byId('meetOverlay') && byId('meetOverlay').style.display !== 'none') closeMeetModal();
});

const companionViewOwner = AlteraPage.component('companion-views', {
    stop() {
        meetRun++; notesWatcher++;
        for (const wait of companionSleeps) { clearTimeout(wait.timer); wait.resolve(); } companionSleeps.clear();
        for (const timer of companionViewTimers) clearTimeout(timer.id); companionViewTimers.clear();
        for (const frame of companionFrames) cancelAnimationFrame(frame.id); companionFrames.clear();
        for (const id of ['portraitImg', 'portraitViewImg', 'birthImg']) { byId(id)?.removeAttribute('src'); byId(id)?.removeAttribute('srcset'); }
        const area = document.querySelector('.chat-area'); area?.style.removeProperty('--companion-portrait');
        return true;
    },
    clear() {
        Companion.companions = []; Companion.current = Companion.forge = null;
        notesScope = notesBaseline = notesWatchScope = null; lastPictureSync = 0;
        for (const id of ['notesAbout', 'notesStory']) if (byId(id)) byId(id).value = '';
        for (const id of ['meetOrigin', 'meetHair', 'meetAge']) if (byId(id)) { byId(id).replaceChildren(); byId(id).value = ''; }
        for (const id of ['companionList', 'meetLibraryGrid', 'portraitTraits']) byId(id)?.replaceChildren();
        for (const id of ['notesTitle', 'notesStatus', 'portraitName', 'portraitLine', 'portraitBio', 'portraitNote', 'portraitViewCaption', 'birthName', 'birthCaption', 'meetProgress', 'meetNote', 'meetLibraryHint']) {
            if (byId(id)) byId(id).textContent = '';
        }
        for (const id of ['notesOverlay', 'meetOverlay']) if (byId(id)) byId(id).style.display = 'none';
        if (byId('portraitView')) byId('portraitView').hidden = true;
        if (byId('birthOverlay')) { byId('birthOverlay').hidden = true; byId('birthOverlay').classList.remove('show'); }
        return true;
    }
});
for (const name of ['loadCompanions', 'openCurrentCompanion', 'switchCompanion', 'enterCompanion', 'openPortraitView', 'closePortraitView',
    'hideBirth', 'beginFirstMeeting', 'openMeetModal', 'closeMeetModal', 'showMeetTab', 'loadMeetStatus', 'startForgeFromHer',
    'makeSomeone', 'loadMeetLibrary', 'meetFromLibrary', 'openNotes', 'closeNotes', 'loadNotes', 'saveNotes', 'updateNotesNow',
    'watchNotes', 'refreshCompanionStatus', 'openAddPersonMenu', 'closeAddPersonMenu', 'addPersonFromMenu',
    'importSidebarCard']) window[name] = companionViewTracked(window[name]);
document.addEventListener('click', event => {
    const menu = byId('addPersonMenu');
    if (!menu || menu.hidden) return;
    if (menu.contains(event.target) || byId('addPersonButton')?.contains(event.target)) return;
    closeAddPersonMenu();
});
document.addEventListener('keydown', event => {
    if (event.key === 'Escape') closeAddPersonMenu();
});
if (byId('sidebarCardFile')) byId('sidebarCardFile').addEventListener('change', event => {
    importSidebarCard(event?.target?.files?.[0] || byId('sidebarCardFile').files?.[0]);
    event.target.value = '';
});
window.addEventListener('beforeunload', event => { if (notesDirty()) { event.preventDefault(); event.returnValue = ''; } });
