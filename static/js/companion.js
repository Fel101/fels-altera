/**
 * Altera — Avatars and pictures.
 *
 * Avatars are characters made in NPC Forge and imported here. Pictures arrive under her messages as photo cards: a card
 * starts as a placeholder, polls the server while the picture is being made, and ends as the picture, or as a notice that says
 * why there is none. Everything shown comes from JSON through textContent, never innerHTML.
 */
'use strict';

const Companion = { avatars: [], active: null, photosReady: false, forgeAvailable: false, selection: 0 };
window.companionPortraitUrl = null;
const PHOTO_ID = /^p-[0-9a-f]{10}$/;
const revealedPhotos = new Set();
const companionRequests = new Set(), companionTimers = new Set(), photoWaiters = [];
const observedPhotos = new Set(); let photoObserver = null;
let photoReads = 0, avatarRead = 0;
let photoGalleryRead = 0;
function photoImageUrl(id) {
    if (typeof id !== 'string' || !PHOTO_ID.test(id) || /[^a-z0-9-]/.test(id)) return null;
    return `/api/photos/${id}/image`;
}
function portraitUrl(avatarId) {
    if (typeof avatarId !== 'string' || !/^[a-z0-9][a-z0-9-]{2,63}$/.test(avatarId) || /[^a-z0-9-]/.test(avatarId)) return null;
    return `/api/avatars/${avatarId}/portrait`;
}
function setPortraitImage(node, avatarId) {
    const url = portraitUrl(avatarId);
    if (url) node.src = url; else node.removeAttribute('src');
}
function companionContext() {
    return {avatar_id: String(Companion.active?.id || ''), avatar_name: Companion.active?.name || 'Default companion',
        session_id: typeof currentSessionId === 'undefined' ? null : currentSessionId,
        dataset_epoch: typeof AlteraAPI === 'undefined' ? window.__DATA_EPOCH__ || null : AlteraAPI.epoch, selection: Companion.selection};
}
const sameCompanion = context => ['avatar_id', 'session_id', 'dataset_epoch', 'selection'].every(key => companionContext()[key] === context[key]);
const companionTracked = action => function (...args) {
    return companionOwner.run(() => action.apply(this, args)).catch(error => { if (companionOwner.active) throw error; });
};
function companionLater(action, delay) {
    if (!companionOwner.active) return;
    const timer = {id: null}; companionTimers.add(timer);
    timer.id = setTimeout(() => { companionTimers.delete(timer); if (companionOwner.active) action(); }, delay); return timer.id;
}
function photoSlot() {
    return new Promise((resolve, reject) => {
        const start = () => {
            if (!companionOwner.active) { reject(new Error('The page is closing.')); return; }
            photoReads++; let released = false;
            resolve(() => { if (released) return; released = true; photoReads--; photoWaiters.shift()?.start(); });
        };
        if (photoReads < 8) start(); else photoWaiters.push({start, reject});
    });
}

async function cApi(path, options = {}) {
    return companionOwner.run(async () => {
        companionOwner.assert(); const controller = new AbortController(); companionRequests.add(controller);
        try {
            const res = await fetch(path, {
                method: options.method || 'GET',
                headers: options.body ? { 'Content-Type': 'application/json' } : undefined,
                body: options.body ? JSON.stringify(options.body) : undefined,
                signal: controller.signal,
            });
            let data = null;
            try { data = await res.json(); } catch (e) { /* no body */ }
            companionOwner.assert();
            if (!res.ok) {
                const err = new Error((data && data.error) || `Request failed (HTTP ${res.status})`);
                err.status = res.status;
                err.data = data;
                throw err;
            }
            return data;
        } finally { companionRequests.delete(controller); }
    });
}

function h(tag, attrs, ...children) {
    const node = document.createElement(tag);
    if (tag === 'img' || tag === 'a') node.setAttribute('data-companion-media', '');
    if (tag === 'img') node.setAttribute('loading', 'lazy');
    for (const [key, value] of Object.entries(attrs || {})) {
        if (value === false || value == null) continue;
        if (key === 'class') node.className = value;
        else if (key === 'text') node.textContent = value;
        else if (key.startsWith('on') && typeof value === 'function') node.addEventListener(key.slice(2), companionTracked(value));
        else node.setAttribute(key, value === true ? '' : value);
    }
    for (const child of children.flat()) {
        if (child == null || child === false) continue;
        node.append(child.nodeType ? child : document.createTextNode(String(child)));
    }
    return node;
}

// ============================================================
// Avatars
// ============================================================

async function refreshAvatars() {
    const run = ++avatarRead, selection = Companion.selection;
    try {
        const data = await cApi('/api/avatars');
        if (run !== avatarRead || selection !== Companion.selection) return;
        Companion.avatars = data.avatars;
        const active = data.avatars.find(a => a.id === data.active) || null;
        if (Companion.active?.id !== active?.id) clearPhotoGallery();
        Companion.active = active;
        Companion.photosReady = !!data.photos_ready;
        Companion.forgeAvailable = !!data.forge_available;
    } catch (err) {
        if (!companionOwner.active) return;
        console.warn('Could not load avatars:', err);
    }
    renderAvatarSummary();
    applyActiveAvatar();
}

function applyActiveAvatar() {
    if (!companionOwner.active) return;
    const active = Companion.active;
    window.companionPortraitUrl = active ? portraitUrl(active.id) : null;

    const header = document.getElementById('companionAvatar');
    if (header) {
        header.classList.toggle('has-portrait', !!window.companionPortraitUrl);
        header.style.backgroundImage = window.companionPortraitUrl ? `url("${window.companionPortraitUrl}")` : '';
    }
    const title = document.querySelector('.companion-info h2');
    if (title && active) title.textContent = active.name;
    document.querySelectorAll('.message.companion .msg-avatar').forEach(node => {
        node.classList.toggle('has-portrait', !!window.companionPortraitUrl);
        node.style.backgroundImage = window.companionPortraitUrl ? `url("${window.companionPortraitUrl}")` : '';
    });
    const photoBtn = document.getElementById('photoBtn');
    const sharedSceneButton = document.getElementById('sharedSceneOpen');
    const kidsMode = active?.kids === true;
    if (sharedSceneButton) sharedSceneButton.hidden = kidsMode;
    if (photoBtn) {
        photoBtn.classList.toggle('available', Companion.photosReady && !kidsMode);
        photoBtn.title = Companion.photosReady ? 'Ask for a photo'
            : 'Ask for a photo (a portrait is needed first: upload one or make one in NPC Forge)';
    }
    if (!Companion.photosReady || kidsMode) togglePhotoBar(false);
    if (typeof renderPortraitPanel === 'function') renderPortraitPanel();
}

function renderAvatarSummary() {
    if (!companionOwner.active) return;
    const box = document.getElementById('avatarSummary');
    if (!box) return;
    box.replaceChildren();
    const active = Companion.active;
    if (!active) {
        box.append(h('span', { text: 'No one here yet. Make a pal or write a companion on Home.' }));
        return;
    }
    box.append(
        h('img', { src: portraitUrl(active.id), alt: `${active.name}'s portrait` }),
        h('div', {}, h('strong', { text: active.name }), `${forgeAgeLabel(active, 'age not set')}${active.gender ? ', ' + active.gender : ''}`),
        !Companion.photosReady ? h('p', { class: 'setting-hint',
            text: active.written ? 'No portrait yet: upload a picture or make one in NPC Forge before asking for photos.'
                : 'No portrait yet, so there are no pictures to make.' }) : null,
    );
}

function openAvatarModal() {
    document.getElementById('avatarOverlay').style.display = 'flex';
    renderAvatarGrid();
    loadForge();
    loadPremade();
}

function closeAvatarModal() {
    document.getElementById('avatarOverlay').style.display = 'none';
}

function setAvatarHint(text, isError) {
    if (!companionOwner.active) return;
    const hint = document.getElementById('forgeHint');
    hint.textContent = text || '';
    hint.classList.toggle('error', !!isError);
}

function forgeAgeLabel(person, empty = '') {
    if (person.age == null) return empty;
    return person.age_source === 'rolled' ? `${person.age} (rolled guess)` : String(person.age);
}

function renderAvatarGrid() {
    if (!companionOwner.active) return;
    const grid = document.getElementById('avatarGrid');
    grid.replaceChildren();
    if (!Companion.avatars.length) {
        grid.append(h('p', { class: 'avatar-empty', text: 'Nothing imported yet.' }));
        return;
    }
    for (const a of Companion.avatars) {
        const isActive = Companion.active && Companion.active.id === a.id;
        grid.append(h('div', { class: 'avatar-card' + (isActive ? ' active' : '') },
            h('img', { src: portraitUrl(a.id), alt: '' }),
            h('strong', { text: a.name }),
            h('div', { class: 'meta' }, `${forgeAgeLabel(a, 'age not set')}${a.gender ? ', ' + a.gender : ''} · ${a.style || ''}`),
            h('div', {}, isActive ? h('span', { class: 'badge', text: 'Active' }) : null,
                a.age_checked ? h('span', { class: 'badge', text: 'Age checked' }) : null),
            h('div', { class: 'card-actions' },
                isActive ? null : h('button', { class: 'primary', text: 'Open chat', onclick: () => { closeAvatarModal(); switchCompanion(a.id); } }),
                h('button', { text: 'Remove', onclick: () => deleteAvatar(a) }))));
    }
}

async function loadForge() {
    const grid = document.getElementById('forgeGrid');
    grid.replaceChildren();
    setAvatarHint('Loading NPC Forge characters…');
    let data;
    try {
        data = await cApi('/api/avatars/forge');
    } catch (err) {
        setAvatarHint(err.message, true);
        return;
    }
    if (!data.available) {
        setAvatarHint('NPC Forge\'s library folder was not found. Set imagegen.npc_forge_library in config.yaml.', true);
        return;
    }
    setAvatarHint(data.characters.length
        ? 'Only characters aged 18 or older, with a portrait, can be imported.'
        : 'NPC Forge has no characters yet. Make one there, generate its portrait, then refresh.');
    for (const c of data.characters) {
        const button = h('button', {
            class: 'primary', text: c.imported ? 'Update' : 'Import', disabled: !c.eligible,
            onclick: ev => importAvatar(c, ev.currentTarget),
        });
        grid.append(h('div', { class: 'avatar-card' + (c.eligible ? '' : ' disabled') },
            h('img', { src: `/api/forge/${c.id}/thumb`, alt: '', loading: 'lazy' }),
            h('strong', { text: c.name }),
            h('div', { class: 'meta' }, `${forgeAgeLabel(c, 'age not set')}${c.gender ? ', ' + c.gender : ''} · ${c.style || ''}`),
            c.reason ? h('div', { class: 'reason', text: c.reason }) : null,
            h('div', { class: 'card-actions' }, button)));
    }
}

// ---- ready-made pictures: approved in NPC Forge's studio, imported one at a time (each is age-checked) ----

function setPremadeHint(text, isError) {
    if (!companionOwner.active) return;
    const hint = document.getElementById('premadeHint');
    hint.textContent = text || '';
    hint.classList.toggle('error', !!isError);
}

async function loadPremade() {
    const section = document.getElementById('premadeSection');
    const grid = document.getElementById('premadeGrid');
    const active = Companion.active;
    const context = companionContext();
    if (!grid || !section) return;
    grid.replaceChildren();
    if (!active) { section.style.display = 'none'; return; }
    let data;
    try {
        data = await cApi(`/api/avatars/${active.id}/premade`);
    } catch (err) {
        if (!companionOwner.active || !sameCompanion(context)) return;
        section.style.display = 'none';
        return;
    }
    if (!companionOwner.active || !sameCompanion(context)) return;
    section.style.display = data.items.length || data.photos.length ? 'block' : 'none';
    const waiting = data.items.filter(i => !i.imported);
    document.getElementById('premadeTitle').textContent = `Ready-made pictures for ${active.name}`;
    setPremadeHint(`${data.photos.length} imported. ${data.items.length} approved in NPC Forge` +
        (waiting.length ? `, ${waiting.length} not imported yet.` : '.'));
    const all = document.getElementById('premadeAll');
    all.style.display = waiting.length ? 'inline-block' : 'none';
    all.disabled = false;
    all.textContent = `Import all (${waiting.length})`;

    const controls = window.AlteraContentControls;
    for (const item of data.items) {
        const img = h('img', { src: item.image_url || undefined, alt: '', loading: 'lazy' });
        const thumb = h('div', { class: 'premade-thumb' + (controls?.blur(item.level) ? ' blurred' : '') }, img);
        thumb.addEventListener('click', companionTracked(() => thumb.classList.remove('blurred')));
        const button = h('button', { class: 'primary', text: item.imported ? 'Imported' : 'Import', disabled: item.imported,
            onclick: ev => importPremadeItem(active.id, item, ev.currentTarget, true) });
        grid.append(h('div', { class: 'avatar-card' + (item.imported ? ' active' : '') }, thumb,
            h('strong', { text: item.label || item.scene.slice(0, 40) }),
            controls?.enabled() ? h('div', { class: 'meta', text: controls.label(item.level) }) : null,
            h('div', { class: 'card-actions' }, button)));
    }
    grid.dataset.pending = JSON.stringify(waiting);
}

async function importPremadeItem(avatarId, item, button, reload) {
    button.disabled = true;
    button.textContent = 'Checking…';
    try {
        await cApi(`/api/avatars/${avatarId}/premade/import`, { method: 'POST', body: { item_id: item.item_id } });
        button.textContent = 'Imported';
        if (reload) loadPremade();
        return null;
    } catch (err) {
        if (!companionOwner.active) return;
        button.disabled = false;
        button.textContent = 'Import';
        return `${item.label || item.scene.slice(0, 40)}: ${err.message}`;
    }
}

async function importAllPremade() {
    const active = Companion.active;
    const context = companionContext();
    if (!active) return;
    const all = document.getElementById('premadeAll');
    const pending = JSON.parse(document.getElementById('premadeGrid').dataset.pending || '[]');
    all.disabled = true;
    const problems = [];
    for (let i = 0; i < pending.length; i++) {
        if (!companionOwner.active || !sameCompanion(context)) return;
        setPremadeHint(`Importing ${i + 1} of ${pending.length}. Each picture is age-checked, which takes a few seconds.`);
        const fake = document.createElement('button');
        const problem = await importPremadeItem(active.id, pending[i], fake, false);
        if (problem) problems.push(problem);
    }
    if (!companionOwner.active || !sameCompanion(context)) return;
    await loadPremade();
    if (problems.length) setPremadeHint(`${pending.length - problems.length} imported. Not imported: ${problems.join(' | ')}`, true);
}

async function importAvatar(character, button) {
    const body = { npc_id: character.id };
    if (character.needs_age) {
        const answer = window.prompt(`${character.name} has no age in NPC Forge. Enter an age (18 or older):`);
        if (answer === null) return;
        body.age = answer.trim();
    }
    button.disabled = true;
    button.textContent = 'Importing…';
    setAvatarHint('Importing. If a vision model checks the portrait, this can take a minute.');
    try {
        const avatar = await cApi('/api/avatars/import', { method: 'POST', body });
        setAvatarHint(`${avatar.name} imported.${avatar.age_checked ? '' : ' The portrait was not age-checked (no vision model answered); some pictures stay blocked until it can be.'}`);
        await refreshAvatars();
        renderAvatarGrid();
        loadForge();
        loadPremade();
    } catch (err) {
        setAvatarHint(err.message, true);
        button.disabled = false;
        button.textContent = character.imported ? 'Update' : 'Import';
    }
}

async function activateAvatar(id) {
    try {
        await cApi(`/api/avatars/${id}/activate`, { method: 'POST' });
        await refreshAvatars();
        renderAvatarGrid();
        loadPremade();
    } catch (err) {
        setAvatarHint(err.message, true);
    }
}

async function deleteAvatar(avatar) {
    if (!window.confirm(`Remove ${avatar.name} for good? Their chat, notes, memories and pictures are deleted. NPC Forge's copy is not touched.`)) return;
    try {
        await cApi(`/api/companions/${avatar.id}`, { method: 'DELETE' });
        await refreshAvatars();
        renderAvatarGrid();
        loadForge();
        loadPremade();
        if (typeof loadCompanions === 'function') loadCompanions();
        if (Companion.current && Companion.current.id === avatar.id) {              // it was the one open: go to someone else
            Companion.current = null;
            const opened = typeof openCurrentCompanion === 'function' ? await openCurrentCompanion() : false;
            if (!opened) {
                applyActiveAvatar();
                if (typeof clearMessages === 'function') clearMessages();
            }
        }
    } catch (err) {
        setAvatarHint(err.message, true);
    }
}

async function refreshImagegenStatus() {
    const box = document.getElementById('imagegenStatus');
    if (!box) return;
    box.classList.remove('error');
    box.textContent = 'Checking ComfyUI…';
    try {
        const s = await cApi('/api/imagegen/status');
        if (!s.comfy.online) {
            box.textContent = 'ComfyUI is not reachable, so pictures cannot be made. Start it, then reopen Settings.';
            box.classList.add('error');
        } else if (s.comfy.missing.length) {
            box.textContent = 'ComfyUI is running but is missing: ' + s.comfy.missing.join(', ');
            box.classList.add('error');
        } else {
            box.textContent = `ComfyUI is ready. Age check uses "${s.age_check_model || 'the chat model'}" (it must accept images).`;
        }
    } catch (err) {
        if (!companionOwner.active) return;
        box.textContent = err.message;
        box.classList.add('error');
    }
}

// ============================================================
// Pictures in the chat
// ============================================================

function photoCardHtml(id) {
    return `<div class="photo-card" data-photo-id="${id}"></div>`;
}

function hydratePhotos(root) {
    if (!companionOwner.active) return;
    for (const card of observedPhotos) if (!document.body.contains(card)) { photoObserver?.unobserve(card); observedPhotos.delete(card); }
    if (!photoObserver && typeof IntersectionObserver === 'function') {
        photoObserver = new IntersectionObserver(entries => {
            for (const entry of entries) {
                const card = entry.target; card.dataset.inView = entry.isIntersecting ? '1' : '0';
                if (entry.isIntersecting && companionOwner.active && document.body.contains(card)) loadPhotoCard(card, card.dataset.photoId, 0);
            }
        }, {rootMargin: '300px'});
    }
    (root || document).querySelectorAll('.photo-card:not([data-hydrated])').forEach(card => {
        card.dataset.hydrated = '1';
        card.dataset.started = String(Date.now());
        if (photoObserver) { observedPhotos.add(card); photoObserver.observe(card); }
        else loadPhotoCard(card, card.dataset.photoId, 0);
    });
}

function refreshPhotoCards() {
    if (!companionOwner.active) return;
    document.querySelectorAll('.photo-figure').forEach(fig => {
        const card = fig.closest('.photo-card');
        if (card && card.dataset.level) applyBlur(fig, card.dataset.level, card.dataset.photoId);
    });
}

function applyBlur(figure, level, id) {
    const blur = window.AlteraContentControls?.blur(level) && !revealedPhotos.has(id);
    figure.classList.toggle('blurred', blur);
}

async function loadPhotoCard(card, id, hops) {
    if (!PHOTO_ID.test(id || '')) return renderPhotoNotice(card, 'This photo is not available.');
    if (!document.body.contains(card) || card.dataset.inView === '0' || card.dataset.photoFinished === '1') return;
    let rec;
    try {
        for (let next = hops; next <= 10; next++) {
            if (!PHOTO_ID.test(id || '')) throw new Error('Invalid photo identity');
            const release = await photoSlot();
            try { rec = await cApi(`/api/photos/${id}`); } finally { release(); }
            if (!rec.superseded_by || next === 10) break;
            id = rec.superseded_by;
        }
    } catch (err) {
        return renderPhotoNotice(card, 'This photo is no longer available.');
    }
    if (!companionOwner.active || !document.body.contains(card) || !PHOTO_ID.test(rec?.id || '')) return;
    card.dataset.photoId = rec.id;
    if (rec.status === 'pending' || rec.status === 'running') {
        if (Date.now() - Number(card.dataset.started || Date.now()) > 15 * 60 * 1000) {
            return renderPhotoNotice(card, 'This photo is taking too long. Check that ComfyUI is running.');
        }
        renderPhotoLoading(card, rec);
        companionLater(() => loadPhotoCard(card, rec.id, hops), 2000);
        return;
    }
    card.dataset.photoFinished = '1'; photoObserver?.unobserve(card); observedPhotos.delete(card);
    if (rec.status === 'ready') return renderPhotoReady(card, rec);
    renderPhotoNotice(card, rec.reason || 'The photo could not be made.', rec.status === 'blocked', rec);
}

function renderPhotoLoading(card, rec) {
    if (!companionOwner.active) return;
    if (card.querySelector('.photo-loading')) return;
    card.replaceChildren(h('div', { class: 'photo-loading' }, h('span', { class: 'photo-spinner' }),
        h('span', { text: rec.status === 'running' ? 'Taking a photo…' : 'Getting ready to take a photo…' })));
}

function renderPhotoNotice(card, text, warn, rec) {
    if (!companionOwner.active) return;
    delete card.dataset.level;
    const hint = rec?.required_level && window.AlteraContentControls?.hint(rec.required_level);
    card.replaceChildren(h('div', { class: 'photo-notice' + (warn ? ' warn' : '') + (hint ? ' stack' : '') },
        h('div', { text }),
        hint ? h('div', { class: 'photo-notice-help', text: hint }) : null,
        hint ? window.AlteraContentControls.button('photo-notice-btn') : null));
}

function renderPhotoReady(card, rec) {
    if (!companionOwner.active) return;
    const imageUrl = photoImageUrl(rec.id);
    if (!imageUrl) return renderPhotoNotice(card, 'This picture is unavailable.');
    card.dataset.level = rec.level;
    const figure = h('figure', { class: 'photo-figure' });
    const img = h('img', { src: imageUrl, alt: rec.scene ? `Imagined image: ${rec.scene}` : 'Imagined image' });
    const veil = h('div', { class: 'photo-veil', text: 'Picture hidden. Click to reveal.' });
    img.addEventListener('click', companionTracked(() => {
        if (figure.classList.contains('blurred')) {
            revealedPhotos.add(rec.id);
            applyBlur(figure, rec.level, rec.id);
        } else {
            window.open(imageUrl, '_blank', 'noopener');
        }
    }));
    const redo = h('button', { type: 'button', text: '↻ Another', title: 'Make another with the same scene' });
    redo.addEventListener('click', companionTracked(async () => {
        redo.disabled = true;
        try {
            const next = await cApi(`/api/photos/${rec.id}/reroll`, { method: 'POST' });
            card.dataset.started = String(Date.now());
            delete card.dataset.photoFinished;
            card.replaceChildren();
            loadPhotoCard(card, next.id, 0);
        } catch (err) {
            redo.disabled = false;
            redo.textContent = err.message.length < 60 ? err.message : 'Not allowed';
        }
    }));
    const download = h('a', { href: imageUrl, download: `${rec.id}.png`, text: '⬇ Save' });
    const remove = h('button', { type: 'button', text: '🗑 Delete' });
    remove.addEventListener('click', companionTracked(async () => {
        if (!window.confirm('Delete this photo?')) return;
        try {
            await cApi(`/api/photos/${rec.id}`, { method: 'DELETE' });
            renderPhotoNotice(card, 'Photo deleted.');
        } catch (err) {
            renderPhotoNotice(card, err.message);
        }
    }));
    // A ready-made picture is part of the avatar's set (removed in the avatar picker), so the chat offers no Delete for it.
    const reused = card.dataset.reused === '1';
    const scene = rec.premade ? (rec.label || rec.scene) : rec.scene;
    const caption = 'Imagined image' + (rec.source === 'shared-scene' ? ' · Together' : '')
        + (rec.premade || reused ? ' · From their collection' : '') + (scene ? ` · ${scene}` : '');
    figure.append(img, veil,
        h('figcaption', { class: 'photo-caption', text: caption }),
        h('div', { class: 'photo-tools' }, rec.source === 'shared-scene' ? null : redo, download,
            rec.premade ? null : remove));
    card.replaceChildren(figure);
    applyBlur(figure, rec.level, rec.id);
}

function clearPhotoGallery() {
    photoGalleryRead++;
    const items = document.getElementById('photoGalleryItems');
    for (const node of items?.querySelectorAll('img, a') || []) {
        node.removeAttribute('src'); node.removeAttribute('srcset'); node.removeAttribute('href'); node.removeAttribute('download');
    }
    items?.replaceChildren();
    const status = document.getElementById('photoGalleryStatus'); if (status) status.textContent = '';
    const dialog = document.getElementById('photoGalleryDialog'); if (dialog?.open) dialog.close();
}

function closePhotoGallery() { clearPhotoGallery(); }

async function openPhotoGallery() {
    const origin = companionContext(), dialog = document.getElementById('photoGalleryDialog');
    const status = document.getElementById('photoGalleryStatus'), items = document.getElementById('photoGalleryItems');
    if (!dialog || !status || !items || !/^[a-z0-9][a-z0-9-]{2,63}$/.test(origin.avatar_id)) {
        if (typeof showReplyNotice === 'function') showReplyNotice('Open a companion before viewing saved pictures.');
        return;
    }
    clearPhotoGallery();
    const serial = ++photoGalleryRead;
    dialog.showModal(); status.textContent = 'Loading saved pictures…';
    try {
        const data = await cApi(`/api/photos?avatar=${encodeURIComponent(origin.avatar_id)}&limit=60`);
        if (!companionOwner.active || serial !== photoGalleryRead || !dialog.open || !sameCompanion(origin)) return;
        if (!Array.isArray(data?.photos) || data.photos.length > 60) throw new Error('The picture list could not be verified.');
        for (const rec of data.photos) {
            if (!PHOTO_ID.test(rec?.id || '') || rec.avatar_id !== origin.avatar_id
                    || (rec.level !== 'off' && !window.AlteraContentControls?.allows(rec.level))) continue;
            const card = h('div', {class: 'photo-card', 'data-photo-id': rec.id});
            items.append(card);
            if (rec.status === 'ready') renderPhotoReady(card, rec);
            else if (rec.status === 'pending' || rec.status === 'running') renderPhotoLoading(card, rec);
            else renderPhotoNotice(card, typeof rec.reason === 'string' ? rec.reason : 'This picture is unavailable.');
        }
        status.textContent = items.childElementCount ? `Showing ${items.childElementCount} saved pictures.` : 'No saved pictures for this companion.';
    } catch (error) {
        if (companionOwner.active && serial === photoGalleryRead && dialog.open && sameCompanion(origin)) {
            status.textContent = 'Saved pictures could not be loaded. Please try again.';
        }
    }
}

/** A picture she just started (an SSE event): add its card under the message that is being written. */
function attachPhotoCard(photo) {
    if (!companionOwner.active) return;
    if (!photo || !PHOTO_ID.test(photo.id || '')) return;
    let message = document.getElementById('streamingMsg');
    if (!message) {
        const all = document.querySelectorAll('#messagesContainer .message.companion');
        message = all.length ? all[all.length - 1] : null;
    }
    if (!message && typeof appendMessage === 'function') message = appendMessage('companion', '');
    if (!message) return;
    const holder = message.querySelector('.message-content');
    const card = h('div', { class: 'photo-card', 'data-photo-id': photo.id });
    if (photo.reused) card.dataset.reused = '1';
    holder.append(card);
    hydratePhotos(message);
    if (typeof scrollToBottom === 'function') scrollToBottom();
}

// ---- the camera button ----

function togglePhotoBar(force) {
    const bar = document.getElementById('photoBar');
    if (!bar) return;
    const open = typeof force === 'boolean' ? force : !bar.classList.contains('open');
    bar.classList.toggle('open', open);
    if (open) {
        setPhotoHint('');
        document.getElementById('photoScene').focus();
    }
}

function setPhotoHint(text, isError, requiredLevel) {
    if (!companionOwner.active) return;
    const hint = document.getElementById('photoBarHint');
    hint.textContent = text;
    hint.classList.toggle('error', !!isError);
    const extra = requiredLevel && window.AlteraContentControls?.hint(requiredLevel);
    if (extra) hint.append(' ', extra, ' ', window.AlteraContentControls.button('link-btn'));
}

async function requestPhoto() {
    const input = document.getElementById('photoScene');
    const context = companionContext(), scene = input.value;
    const button = document.getElementById('photoGo');
    button.disabled = true;
    setPhotoHint('');
    try {
        const rec = await cApi('/api/photos', {
            method: 'POST',
            body: { scene: scene.trim(), session_id: context.session_id },
        });
        if (!sameCompanion(context)) return;
        if (input.value === scene) { input.value = ''; togglePhotoBar(false); }
        const message = appendMessage('companion', '');
        message.querySelector('.message-content').append(h('div', { class: 'photo-card', 'data-photo-id': rec.id }));
        hydratePhotos(message);
    } catch (err) {
        if (sameCompanion(context)) setPhotoHint(err.message, true, err.data && err.data.required_level);
    } finally {
        button.disabled = false;
    }
}

// ============================================================
// Start-up
// ============================================================

function initCompanion() {
    refreshAvatars();
    window.AlteraContentControls?.refresh();
    const scene = document.getElementById('photoScene');
    document.getElementById('photoGalleryDialog')?.addEventListener('cancel', event => { event.preventDefault(); closePhotoGallery(); });
    if (scene) scene.addEventListener('keydown', ev => { if (ev.key === 'Enter') { ev.preventDefault(); requestPhoto(); } });
    document.addEventListener('keydown', ev => {
        if (ev.key !== 'Escape') return;
        closeAvatarModal();
    });

    // Opening Settings also checks that ComfyUI is reachable.
    if (typeof window.toggleSettings === 'function') {
        const original = window.toggleSettings;
        window.toggleSettings = function () {
            original.apply(this, arguments);
            const panel = document.getElementById('settingsPanel');
            if (panel && panel.classList.contains('open')) { refreshAvatars(); refreshImagegenStatus(); }
        };
    }
    hydratePhotos();
}

const companionOwner = AlteraPage.component('avatar-photos', {
    stop() {
        avatarRead++; Companion.selection++;
        clearPhotoGallery();
        for (const controller of companionRequests) controller.abort();
        for (const timer of companionTimers) clearTimeout(timer.id); companionTimers.clear();
        photoObserver?.disconnect(); photoObserver = null; observedPhotos.clear();
        for (const waiter of photoWaiters.splice(0)) waiter.reject(new Error('The page is closing.'));
        for (const node of document.querySelectorAll('[data-companion-media], #companionAvatar, .msg-avatar')) {
            node.removeAttribute('src'); node.removeAttribute('srcset'); node.removeAttribute('href'); node.removeAttribute('download');
            if (node.style) node.style.backgroundImage = '';
        }
        return true;
    },
    clear() {
        Companion.avatars = []; Companion.active = null;
        Companion.photosReady = Companion.forgeAvailable = false; revealedPhotos.clear(); window.companionPortraitUrl = null;
        if (document.getElementById('photoScene')) document.getElementById('photoScene').value = '';
        for (const id of ['forgeHint', 'premadeHint', 'premadeTitle', 'imagegenStatus', 'photoBarHint']) {
            if (document.getElementById(id)) document.getElementById(id).textContent = '';
        }
        const premadeGrid = document.getElementById('premadeGrid'); if (premadeGrid) delete premadeGrid.dataset.pending;
        for (const id of ['avatarOverlay']) if (document.getElementById(id)) document.getElementById(id).style.display = 'none';
        for (const id of ['avatarSummary', 'avatarGrid', 'forgeGrid', 'premadeGrid']) document.getElementById(id)?.replaceChildren();
        return true;
    }
});
// Preserve the existing classic-script/inline-handler API while owning the
// complete action, rather than only its shared JSON request.
for (const name of ['refreshAvatars', 'openAvatarModal', 'closeAvatarModal', 'loadForge', 'loadPremade', 'importPremadeItem', 'importAllPremade',
    'importAvatar', 'activateAvatar', 'deleteAvatar', 'refreshImagegenStatus', 'loadPhotoCard', 'togglePhotoBar', 'requestPhoto', 'openPhotoGallery', 'closePhotoGallery', 'initCompanion']) {
    window[name] = companionTracked(window[name]);
}
AlteraPage.ready(initCompanion);
