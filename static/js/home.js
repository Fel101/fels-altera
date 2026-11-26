// The start screen: Pals, Characters and Companions, the person to continue with, and everyone in one gallery.
// Reads /api/people; a tile narrows the gallery. Opening someone makes them active, then the chat at / shows them.
// Pals are made here (name, vibe, voice) and their settings changed here. Everything is rendered as text nodes, never HTML.
// A pal or a character can get a portrait from NPC Forge here too (/api/portraits/<id>; core/portraits.py).
(() => {
    const KIND_LABELS = {pal: 'Pal', character: 'Character', companion: 'Companion'};
    const PLURAL = {pal: 'Pals', character: 'Characters', companion: 'Companions'};
    const EMPTY = {
        pal: 'No pals yet. Make one in a few seconds: a name, a vibe and a voice.',
        character: 'No characters yet. Import a CHARX, PNG or JSON character card.',
        companion: 'No companions yet. Write one from a name, an age and a few words, or meet someone new in NPC Forge.',
    };
    const FEATURES = ['memory', 'notes', 'story', 'presence'];

    function create(doc, tag, attrs = {}, text = '') {
        const node = doc.createElement(tag);
        for (const [name, value] of Object.entries(attrs)) {
            if (name === 'class') node.className = value;
            else node.setAttribute(name, value);
        }
        if (text) node.textContent = text;
        return node;
    }

    function kindOf(person) {
        return Object.prototype.hasOwnProperty.call(KIND_LABELS, person?.kind) ? person.kind : 'companion';
    }

    function when(value) {
        if (!value) return '';
        const date = new Date(value);
        return Number.isNaN(date.getTime()) ? '' : date.toLocaleDateString(undefined, {month: 'short', day: 'numeric', year: 'numeric'});
    }

    function describe(person) {
        const parts = [KIND_LABELS[kindOf(person)]];
        const count = Number(person?.messages) || 0;
        parts.push(count ? `${count} message${count === 1 ? '' : 's'}` : 'Not talked yet');
        const last = when(person?.last_at);
        if (last) parts.push(`last ${last}`);
        return parts.join(' · ');
    }

    function portraitUrl(person) {
        const url = `/api/avatars/${encodeURIComponent(person.id)}/portrait`;
        return person.portrait_at ? `${url}?v=${encodeURIComponent(person.portrait_at)}` : url;   // a new picture is fetched
    }

    function start({doc = document, fetchImpl = (...args) => window.fetch(...args),
                    navigate = url => window.location.assign(url),
                    search = () => (typeof window !== 'undefined' && window.location ? window.location.search : ''),
                    wait = ms => new Promise(resolve => window.setTimeout(resolve, ms)),
                    play = url => new window.Audio(url).play()} = {}) {
        const $ = id => doc.getElementById(id);
        const on = (id, event, callback) => { const node = $(id); if (node) node.addEventListener(event, callback); };
        const state = {data: null, kind: null, busy: false, editing: null};

        function status(text) { $('homeStatus').textContent = text; }

        async function call(url, init) {
            const response = await fetchImpl(url, init);
            const body = await response.json().catch(() => ({}));
            if (!response.ok) throw new Error(body.error || `The request failed (${response.status}).`);
            return body;
        }

        function json(method, body) {
            return {method, headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)};
        }

        function setBusy(busy) {
            state.busy = busy;
            for (const button of doc.querySelectorAll('#homeGallery button, #homeContinueButton')) button.disabled = busy;
        }

        async function open(person) {
            if (state.busy) return;
            setBusy(true);
            status(`Opening ${person.name || 'them'}…`);
            try {
                await call(`/api/companions/${encodeURIComponent(person.id)}/open`, {method: 'POST'});
                navigate('/');
            } catch (error) {
                status(error.message || 'Could not open them. Try again.');
                setBusy(false);
            }
        }

        // ---- the page ------------------------------------------------------------------------------------------------
        function renderContinue() {
            const last = state.data?.last;
            const section = $('homeContinue');
            section.hidden = !last;
            if (!last) return;
            $('homeContinueName').textContent = last.name || 'Someone';
            $('homeContinueMeta').textContent = describe(last);
            const portrait = $('homeContinuePortrait');
            portrait.src = portraitUrl(last);
            portrait.hidden = false;
            $('homeContinueButton').textContent = `Continue with ${last.name || 'them'}`;
        }

        function renderTiles() {
            const counts = state.data?.counts || {};
            for (const tile of doc.querySelectorAll('.home-tile')) {
                const kind = tile.dataset.kind;
                tile.setAttribute('aria-pressed', String(state.kind === kind));
                tile.classList.toggle('selected', state.kind === kind);
            }
            for (const label of doc.querySelectorAll('[data-count]')) {
                const count = Number(counts[label.dataset.count]) || 0;
                label.textContent = count ? `${count}` : 'None yet';
            }
        }

        function card(person) {
            const kind = kindOf(person);
            const wrap = create(doc, 'div', {class: 'home-card'});
            const button = create(doc, 'button', {type: 'button', class: 'home-person', 'aria-label': `Open ${person.name || 'this person'}`});
            const image = create(doc, 'img', {class: 'home-portrait', alt: '', loading: 'lazy'});
            image.src = portraitUrl(person);
            button.append(image, create(doc, 'span', {class: 'home-person-name'}, person.name || 'Unnamed'),
                create(doc, 'span', {class: `home-badge ${kind}`}, KIND_LABELS[kind]),
                ...(kind === 'pal' && person.kids === true ? [create(doc, 'span', {class: 'home-badge kids'}, 'Kids mode')] : []),
                ...(person.licence ? [create(doc, 'span', {class: 'home-person-meta'},
                    [person.ai_generated && 'AI-generated', person.licence].filter(Boolean).join(' · '))] : []),
                create(doc, 'span', {class: 'home-person-meta'}, describe(person).split(' · ').slice(1).join(' · ')));
            button.addEventListener('click', () => open(person));
            wrap.append(button);
            const settings = create(doc, 'button', {type: 'button', class: 'home-card-settings',
                'aria-label': `Settings for ${person.name || 'this person'}`}, 'Settings');
            settings.addEventListener('click', () => openSettings(person));
            wrap.append(settings);
            return wrap;
        }

        function renderGallery() {
            const people = state.data?.people || [];
            const shown = state.kind ? people.filter(person => kindOf(person) === state.kind) : people;
            $('homeGalleryTitle').textContent = state.kind ? PLURAL[state.kind] : 'Everyone';
            $('homeShowAll').hidden = !state.kind;
            if ($('homeMakePal')) $('homeMakePal').hidden = state.kind !== 'pal' || !state.data || shown.length === 0;
            if ($('homeImportCard')) $('homeImportCard').hidden = state.kind !== 'character' || !state.data || shown.length === 0;
            if ($('homeWriteCompanion')) {
                $('homeWriteCompanion').hidden = state.kind !== 'companion' || !state.data || shown.length === 0;
            }
            const note = $('homeKindNote');
            note.replaceChildren();
            note.hidden = shown.length > 0;
            if (!shown.length) {
                note.append(create(doc, 'p', {}, state.kind ? EMPTY[state.kind] : 'Nobody here yet.'));
                if (!state.kind || state.kind === 'pal') {
                    const make = create(doc, 'button', {type: 'button', class: 'home-primary'}, 'Make a pal');
                    make.addEventListener('click', () => openMakePal());
                    note.append(make);
                }
                if (!state.kind || state.kind === 'character') {
                    const upload = create(doc, 'button', {type: 'button', class: 'home-primary'}, 'Import a character card');
                    upload.addEventListener('click', () => chooseCard());
                    note.append(upload);
                }
                if (!state.kind || state.kind === 'companion') {
                    const write = create(doc, 'button', {type: 'button', class: 'home-primary'}, 'Write a companion');
                    write.addEventListener('click', () => openWriteCompanion());
                    note.append(write);
                    note.append(create(doc, 'a', {href: '/?meet=1', class: 'home-primary'}, 'Meet someone new'));
                }
            }
            $('homeGallery').replaceChildren(...shown.map(card));
        }

        function render() {
            renderContinue();
            renderTiles();
            renderGallery();
        }

        function choose(kind) {
            state.kind = state.kind === kind ? null : kind;
            render();
        }

        async function load() {
            try {
                state.data = await call('/api/people');
                const total = (state.data.people || []).length;
                status(total ? `${total} ${total === 1 ? 'person' : 'people'}` : 'Nobody here yet.');
                render();
            } catch (error) {
                status(error.message || 'Could not load people. Reload to try again.');
            }
        }

        // ---- characters: importing a card ---------------------------------------------------------------------------
        function chooseCard() {
            const input = $('homeCardFile');
            if (!input || state.busy) return;
            input.value = '';
            input.click?.();
        }

        async function importCard(file) {
            if (!file || state.busy) return;
            state.busy = true;
            status(`Importing ${file.name || 'the card'}…`);
            try {
                const form = new FormData();
                form.append('file', file, file.name || 'card');
                finishCard(await call('/api/characters/import', {method: 'POST', body: form}));
            } catch (error) {
                status(error.message || 'Could not import the card. Try again.');
                state.busy = false;
            }
        }

        function finishCard(result) {
            if (result?.card_voice?.name && $('cardVoiceDialog')) {
                state.busy = false;
                openCardVoice(result.card_id, result.card_voice);
                if (result.inbox_notice) $('cardVoiceStatus').textContent =
                    `${result.inbox_notice} ${$('cardVoiceStatus').textContent}`;
            } else if (result?.inbox_notice) {
                status(result.inbox_notice);
                state.busy = false;
                load().then(() => status(result.inbox_notice));
                loadInbox(true);
            } else navigate('/');
        }

        // ---- cards sent to this Altera -----------------------------------------------------------------------------
        const inboxProblems = new Map();
        let inboxChecked = -Infinity;
        let inboxStamp;
        let inboxBusy = false;
        async function loadInbox(force = false) {
            if (!$('homeInbox') || inboxBusy || (!force && Date.now() - inboxChecked < 10000)) return;
            inboxChecked = Date.now();
            try {
                const {cards = []} = await call('/api/characters/inbox');
                const stamp = JSON.stringify(cards);
                if (inboxBusy || (stamp === inboxStamp && !force)) return;
                inboxStamp = stamp;
                const n = cards.length, noun = n === 1 ? 'character' : 'characters';
                const sender = cards[0]?.sender_name;
                const from = sender && cards.every(card => card.sender_name === sender) ? ` from ${sender}` : '';
                $('homeInboxTitle').textContent = `${n} ${noun}${from} waiting`;
                $('homeInbox').hidden = !n;
                $('homeInboxCards').replaceChildren(...cards.map(card => {
                    const row = create(doc, 'li');
                    const info = create(doc, 'div');
                    info.append(create(doc, 'strong', {}, card.label || card.name));
                    const problem = card.problem || inboxProblems.get(card.id);
                    if (problem) info.append(create(doc, 'p', {class: 'home-muted'}, problem));
                    const actions = create(doc, 'div', {class: 'home-inbox-actions'});
                    for (const [label, method] of [['Import', 'POST'], ['Dismiss', 'DELETE']]) {
                        const button = create(doc, 'button', {type: 'button', 'aria-label': `${label} ${card.label || card.name}`,
                            class: label === 'Import' ? 'home-primary' : 'home-inbox-secondary'}, label);
                        button.disabled = method === 'POST' && !!card.problem;
                        button.addEventListener('click', () => actOnInbox(card, method));
                        actions.append(button);
                    }
                    row.append(info, actions);
                    return row;
                }));
            } catch (_) {
                if (!$('homeInbox').hidden) $('homeInboxStatus').textContent = 'The inbox could not refresh. Try again shortly.';
            }
        }

        async function actOnInbox(card, method) {
            if (inboxBusy || state.busy) return;
            inboxBusy = true;
            state.busy = true;
            $('homeInbox').setAttribute('aria-busy', 'true');
            const previousFocus = doc.activeElement;
            $('homeInboxStatus').textContent = method === 'POST'
                ? 'Altera is checking the card and its pictures. Wait for the import to finish.' : 'Dismissing the card…';
            let result;
            try {
                result = await call(`/api/characters/inbox/${encodeURIComponent(card.id)}`, {method});
                inboxProblems.delete(card.id);
                $('homeInboxStatus').textContent = result.inbox_notice || (method === 'DELETE' ? 'Card dismissed.' : 'Card imported.');
            } catch (error) {
                inboxProblems.set(card.id, error.message);
                $('homeInboxStatus').textContent = error.message;
            } finally {
                inboxBusy = false;
                state.busy = false;
                $('homeInbox').removeAttribute('aria-busy');
            }
            if (result && method === 'POST') {
                finishCard(result);
                if (result.card_voice?.name) loadInbox(true);
            } else if (result && method === 'DELETE') {
                await loadInbox(true);
                (doc.querySelector('#homeInboxCards button:not(:disabled)') || $('homeChatLink'))?.focus();
            } else {
                previousFocus?.focus();
            }
        }

        // ---- optional voice selection after importing a card --------------------------------------------------------
        function openCardVoice(id, offer, fromSettings = false) {
            state.cardVoice = {id, offer, fromSettings, review: null, busy: false};
            if (fromSettings) closeDialog('palSettingsDialog');
            $('cardVoiceTitle').textContent = 'Voice from the card';
            $('cardVoiceStatus').textContent = offer.archive
                ? `The card includes ${offer.name}. Preview its voice file in Voice Lab on this computer.`
                : `The card lists ${offer.name}. Check whether the preset is ready in Voice Lab.`;
            $('cardVoiceTerms').hidden = $('cardVoiceUse').hidden = $('cardVoiceEnable').hidden = true;
            $('cardVoicePreview').hidden = false;
            $('cardVoicePreview').textContent = offer.archive ? 'Preview voice file' : 'Check Voice Lab';
            $('cardVoiceLeave').textContent = fromSettings ? 'Back to settings' : 'Open chat';
            $('cardVoiceConsent').checked = false;
            showDialog('cardVoiceDialog');
            $('cardVoicePreview').focus();
        }

        function leaveCardVoice() {
            const context = state.cardVoice;
            if (!context || context.busy) return;
            closeDialog('cardVoiceDialog');
            state.cardVoice = null;
            if (!context.fromSettings) {
                navigate('/');
                return;
            }
            showDialog('palSettingsDialog');
            $('palSettingsCardVoice').focus();
            if (context.saved) options().then(({voices}) => fillVoices($('palSettingsVoice'), voices, context.saved))
                .catch(() => {});
        }

        function cardVoiceBusy(context, busy) {
            context.busy = busy;
            for (const id of ['cardVoicePreview', 'cardVoiceEnable', 'cardVoiceLeave']) $(id).disabled = busy;
            $('cardVoiceUse').disabled = busy || (context.review?.cloned && !$('cardVoiceConsent').checked);
        }

        async function doCardVoice(action) {
            const context = state.cardVoice;
            if (!context || context.busy || (action === 'use' && $('cardVoiceUse').disabled)) return;
            const message = $('cardVoiceStatus');
            cardVoiceBusy(context, true);
            message.textContent = action === 'preview' ? 'Checking with Voice Lab…'
                : 'Saving the voice. Wait for Voice Lab to finish.';
            try {
                if (action === 'enable') {
                    await call('/api/voice/engine', json('POST', {engine: 'voicelab'}));
                    message.textContent = context.review.remote ? 'Voice Lab is on. Speech text will go to your configured server.'
                        : 'Voice Lab is on. Its voices will speak your replies.';
                    $('cardVoiceEnable').hidden = true;
                } else {
                    const body = {action, ...(action === 'use' ? {review: context.review.review, permission: true} : {})};
                    const result = await call(`/api/characters/${encodeURIComponent(context.id)}/card-voice`, json('POST', body));
                    if (action === 'preview') {
                        context.review = result;
                        // A card made from this Lab's own Cast voice uses that voice: nothing is imported.
                        const inLab = `${result.name} is already in Voice Lab’s Cast.`;
                        message.textContent = result.cloned
                            ? `${result.in_lab ? `${inLab} It includes` : `${result.name} includes`} a person’s recording. Review the speaker and allowed use.`
                            : result.in_lab ? inLab
                            : result.archive ? `${result.name} is ready to import into Voice Lab’s Cast.`
                                : `${result.name} is ready ${result.remote ? 'on your configured Voice Lab server' : 'in Voice Lab'}.`;
                        $('cardVoiceTerms').hidden = !(result.origin || result.allowed_use || result.cloned);
                        $('cardVoiceOrigin').textContent = result.origin ? `Source — ${result.origin}` : '';
                        $('cardVoiceAllowed').textContent = result.allowed_use ? `Allowed use — ${result.allowed_use}` : '';
                        $('cardVoiceConsentRow').hidden = !result.cloned;
                        $('cardVoiceConsent').checked = false;
                        $('cardVoiceUse').hidden = false;
                        $('cardVoiceUse').textContent = result.archive && !result.in_lab ? 'Import and use this voice' : 'Use this voice';
                        $('cardVoicePreview').hidden = true;
                    } else {
                        context.saved = result.name;
                        message.textContent = result.selected ? `${result.name} is set for this character.`
                            : context.review.remote ? `${result.name} is saved. Turning on Voice Lab sends speech text to your configured server.`
                                : `${result.name} is saved. Turn on Voice Lab to use it for spoken replies across Altera.`;
                        $('cardVoiceUse').hidden = $('cardVoiceTerms').hidden = true;
                        $('cardVoiceEnable').hidden = result.selected;
                    }
                }
            } catch (error) {
                message.textContent = error.message || 'Voice Lab could not complete the request. Try again.';
                if (action === 'use') {
                    $('cardVoicePreview').hidden = false;
                    $('cardVoiceUse').hidden = true;
                }
            } finally {
                cardVoiceBusy(context, false);
                if (action === 'preview' && !$('cardVoiceUse').hidden) {
                    (context.review.cloned ? $('cardVoiceConsent') : $('cardVoiceUse')).focus();
                } else if ($('cardVoicePreview').hidden && $('cardVoiceUse').hidden) {
                    ($('cardVoiceEnable').hidden ? $('cardVoiceLeave') : $('cardVoiceEnable')).focus();
                } else if (!$('cardVoicePreview').hidden) $('cardVoicePreview').focus();
            }
        }

        // ---- kids mode: locked until a grown-up enables it, then a switch per pal --------------------------------------
        const validPin = value => /^[0-9]{4,8}$/.test(typeof value === 'string' ? value : '');

        async function kidsState() {
            try {
                const current = await call('/api/kids');
                state.kids = {enabled: current?.enabled === true, enabled_at: current?.enabled_at || null,
                    pin_set: current?.pin_set === true, blocked: current?.blocked_reason || ''};
            } catch (_) {
                state.kids = {enabled: false, enabled_at: null, pin_set: false};        // unknown counts as locked
            }
            return state.kids;
        }

        function lockPresence() {
            const kids = $('palSettingsKids')?.checked === true && !$('palSettingsKidsSwitch')?.hidden;
            for (const box of doc.querySelectorAll('#palSettingsDialog [data-feature]')) {
                if (box.dataset.feature !== 'presence') continue;
                if (kids) box.checked = false;
                box.disabled = kids;                                                     // check-ins stay off in kids mode
            }
        }

        function renderKidsRow(isKids) {
            const unlocked = state.kids?.enabled === true;
            $('palSettingsKidsSwitch').hidden = !unlocked && !isKids;
            $('palSettingsKids').checked = isKids;
            $('palSettingsKids').disabled = !unlocked;                                     // the switch only moves while unlocked
            $('palSettingsKidsLocked').hidden = unlocked;
            $('palSettingsKidsAbout').textContent = unlocked ? 'About kids mode' : 'Set up kids mode…';
            // Switching a pal into or out of kids mode needs the PIN, so the field is there when the switch is.
            if ($('palSettingsKidsPinRow')) $('palSettingsKidsPinRow').hidden = !unlocked;
            if ($('palSettingsKidsPinHint')) $('palSettingsKidsPinHint').hidden = !unlocked;
            lockPresence();
        }

        async function openKids() {
            await kidsState();
            const unlocked = state.kids?.enabled === true;
            const pinSet = state.kids?.pin_set === true;
            const blocked = !unlocked && state.kids?.blocked;
            $('kidsUnderstood').checked = false;
            $('kidsUnderstoodRow').hidden = unlocked || !!blocked;
            $('kidsSetupFields').hidden = (unlocked && pinSet) || !!blocked;
            $('kidsChangeFields').hidden = !unlocked || !pinSet;
            $('kidsEnable').hidden = (unlocked && pinSet) || !!blocked;
            $('kidsEnable').textContent = unlocked ? 'Set a PIN' : 'Enable kids mode';
            $('kidsLock').hidden = !unlocked || !pinSet;
            $('kidsChangePin').hidden = !unlocked || !pinSet;
            $('kidsStatus').textContent = blocked || (unlocked
                ? (pinSet ? 'Kids mode is set up on this Altera.'
                    : 'Kids mode is on, but no PIN is set yet. Set one so a grown-up can leave kids mode.')
                : '');
            for (const id of ['kidsPin', 'kidsPinRepeat', 'kidsCurrentPin', 'kidsNewPin', 'kidsNewPinRepeat']) {
                if ($(id)) $(id).value = '';
            }
            // One dialog at a time: stacked modals do not always draw in order. The pal's settings come back on close.
            state.kidsFromSettings = $('palSettingsDialog')?.open === true;
            if (state.kidsFromSettings) closeDialog('palSettingsDialog');
            showDialog('kidsDialog');
            $('kidsDialog').scrollTop = 0;                     // "a guardrail, not a certification" is read first, not skipped
            if (blocked) $('kidsCancel').focus?.();
            else if (unlocked && pinSet) $('kidsCurrentPin').focus?.();
            else $('kidsPin').focus?.();
        }

        function closeKids() {
            closeDialog('kidsDialog');
            if (state.kidsFromSettings) {
                state.kidsFromSettings = false;
                if (state.editing && kindOf(state.editing) === 'pal') renderKidsRow($('palSettingsKids').checked === true);
                showDialog('palSettingsDialog');                                          // what was typed there is kept
            }
        }

        async function setKids(event) {
            event?.preventDefault?.();
            if (state.kids?.enabled && !state.kids?.pin_set) {     // enabled before PINs existed: set one now
                await submitNewPin($('kidsPin').value, $('kidsPinRepeat').value);
                return;
            }
            if ($('kidsUnderstood').checked !== true) {
                $('kidsStatus').textContent = 'Tick the box to confirm you understand what kids mode is first.';
                return;
            }
            const pin = $('kidsPin').value, repeat = $('kidsPinRepeat').value;
            if (!validPin(pin) || pin !== repeat) {
                $('kidsStatus').textContent = validPin(pin) ? 'The two PINs do not match.' : 'Choose 4 to 8 digits for the PIN.';
                return;
            }
            await changeKids({enabled: true, understood: true, pin});
        }

        async function submitNewPin(pin, repeat) {
            if (!validPin(pin) || pin !== repeat) {
                $('kidsStatus').textContent = validPin(pin) ? 'The two PINs do not match.' : 'Choose 4 to 8 digits for the PIN.';
                return;
            }
            $('kidsStatus').textContent = 'Saving the PIN…';
            try {
                await call('/api/lock/pin', json('POST', {new: pin}));
                await kidsState();
                closeKids();
            } catch (error) {
                $('kidsStatus').textContent = error.message || 'Could not set the PIN. Try again.';
            }
        }

        async function submitChangePin() {
            const current = $('kidsCurrentPin').value, next = $('kidsNewPin').value, repeat = $('kidsNewPinRepeat').value;
            if (!validPin(next) || next !== repeat) {
                $('kidsStatus').textContent = validPin(next) ? 'The two new PINs do not match.'
                    : 'Choose 4 to 8 digits for the new PIN.';
                return;
            }
            $('kidsStatus').textContent = 'Changing the PIN…';
            try {
                await call('/api/lock/pin', json('POST', {current, new: next}));
                await kidsState();
                closeKids();
            } catch (error) {
                $('kidsStatus').textContent = error.message || 'Could not change the PIN. Try again.';
            }
        }

        async function lockKids() {
            const pin = $('kidsChangeFields').hidden ? $('kidsPin').value : $('kidsCurrentPin').value;
            await changeKids({enabled: false, pin});
        }

        async function changeKids(body) {
            $('kidsStatus').textContent = body.enabled ? 'Enabling…' : 'Locking…';
            try {
                const current = await call('/api/kids', json('POST', body));
                state.kids = {enabled: current?.enabled === true, enabled_at: current?.enabled_at || null,
                    pin_set: current?.pin_set === true};
                closeKids();
            } catch (error) {
                $('kidsStatus').textContent = error.message || 'Could not change kids mode. Try again.';
            }
        }

        // ---- pals: making one and its settings -----------------------------------------------------------------------
        async function options() {
            return call('/api/pals/options');
        }

        function fillVoices(select, voices, chosen = '') {
            const choices = (voices?.options || []).map(voice => {
                const licence = voice.licence;
                const option = create(doc, 'option', {value: voice.id}, licence?.label ? `${voice.label} · ${licence.label}` : voice.label);
                if (licence?.label) {
                    option.dataset.licence = licence.label;
                    option.dataset.reason = licence.reason || '';
                }
                return option;
            });
            const unavailable = !!chosen && !choices.some(voice => voice.value === chosen);
            if (unavailable) choices.push(create(doc, 'option', {value: chosen}, `${chosen} (saved, unavailable)`));
            select.replaceChildren(create(doc, 'option', {value: ''}, `Default: ${voices?.default || 'the voice in Settings'}`), ...choices);
            select.value = chosen || '';
            select.dataset.savedUnavailable = unavailable ? chosen : '';
            select.dataset.voiceMessage = voices?.message || '';
            showVoiceLicence(select);
        }

        function showVoiceLicence(select) {
            const note = $(`${select.id}Info`);
            if (!note) return;
            const selected = [...select.children].find(option => option.value === select.value);
            const {licence, reason} = selected?.dataset || {};
            const message = select.dataset.voiceMessage;
            note.hidden = !(licence || message);
            note.replaceChildren();
            if (licence) note.append(create(doc, 'span', {class: 'home-badge'}, licence));
            if (reason || message) note.append(create(doc, 'span', {}, ` ${reason || message}`));
        }

        function showDialog(id) {
            const dialog = $(id);
            if (typeof dialog?.showModal === 'function') dialog.showModal(); else if (dialog) dialog.open = true;
        }

        function closeDialog(id) {
            const dialog = $(id);
            if (typeof dialog?.close === 'function') dialog.close(); else if (dialog) dialog.open = false;
        }

        async function openMakePal() {
            $('palStatus').textContent = '';
            $('palName').value = '';
            $('palCreate').disabled = true;
            if ($('palKids')) $('palKids').checked = false;
            if ($('palKidsRow')) $('palKidsRow').hidden = true;
            showDialog('palDialog');
            try {
                const [{vibes, voices}, kids] = await Promise.all([options(), kidsState()]);
                if ($('palKidsRow')) $('palKidsRow').hidden = !kids.enabled;
                const box = $('palVibes');
                box.replaceChildren(...vibes.map((vibe, index) => {
                    const label = create(doc, 'label', {class: 'home-vibe'});
                    const input = create(doc, 'input', {type: 'radio', name: 'palVibe', value: vibe.id});
                    input.checked = index === 0;
                    const text = create(doc, 'span');
                    text.append(create(doc, 'strong', {}, vibe.label), create(doc, 'span', {}, vibe.description));
                    label.append(input, text);
                    return label;
                }));
                fillVoices($('palVoice'), voices);
                $('palCreate').disabled = !vibes.length;
                if (!vibes.length) $('palStatus').textContent = 'No vibes were found in content/pals/.';
                $('palName').focus?.();
            } catch (error) {
                $('palStatus').textContent = error.message || 'Could not load the vibes. Try again.';
            }
        }

        function chosenVibe() {
            return [...doc.querySelectorAll('input[name="palVibe"]')].find(input => input.checked)?.value || '';
        }

        async function makePal(event) {
            event?.preventDefault?.();
            if (state.busy) return;
            const name = $('palName').value.trim();
            if (!name) { $('palStatus').textContent = 'Give your pal a name.'; return; }
            state.busy = true;
            $('palCreate').disabled = true;
            $('palStatus').textContent = `Making ${name}…`;
            try {
                const body = {name, vibe: chosenVibe(), voice: $('palVoice').value};
                if (state.kids?.enabled && $('palKids')?.checked === true) body.kids = true;
                await call('/api/pals', json('POST', body));
                navigate('/');
            } catch (error) {
                $('palStatus').textContent = error.message || 'Could not make the pal. Try again.';
                $('palCreate').disabled = false;
                state.busy = false;
            }
        }

        // ---- a sample of the chosen voice -----------------------------------------------------------------------------
        async function playSample(selectId, statusId, name) {
            const voice = $(selectId)?.value || '';
            const line = `Hi, I'm ${name || 'your pal'}. This is how I sound.`;
            $(statusId).textContent = 'Getting a sample…';
            try {
                const body = voice ? {text: line, voice} : {text: line};             // no voice: the one chosen in Settings
                const answer = await call('/api/voice/tts', json('POST', body));
                if (!answer.audio_url) {                                            // the server says why it stayed silent
                    $(statusId).textContent = answer.message || 'The voice could not speak just now. Try again.';
                    return;
                }
                await play(answer.audio_url);
                $(statusId).textContent = answer.message || '';
            } catch (error) {
                $(statusId).textContent = error.message || 'Could not play a sample. Try again.';
            }
        }

        // ---- a portrait from NPC Forge ---------------------------------------------------------------------------------
        const portraitApi = person => `/api/portraits/${encodeURIComponent(person.id)}`;
        const WORKING = ['queued', 'running'];

        function renderPortraitRow() {
            const person = state.editing;
            const current = state.portrait;
            if (!person || !current || current.id !== person.id) return;
            const character = current.kind === 'character';
            const url = portraitUrl(person);
            if (state.portraitShown !== `${url}|${current.from}`) {                       // only when the picture changed
                state.portraitShown = `${url}|${current.from}`;
                $('palSettingsPortrait').src = `${url}${url.includes('?') ? '&' : '?'}t=${Date.now()}`;
            }
            const words = {
                forge: current.living ? 'From NPC Forge, with a living portrait.' : 'From NPC Forge.',
                card: "The card's picture.",
                upload: 'A picture you uploaded.',
                none: current.removed?.from === 'card' ? 'Home shows the initial.'
                    : current.kind === 'companion' ? 'No portrait yet, so Home shows the initial. Upload a picture or make one in NPC Forge.'
                    : character ? 'The card has no picture, so Home shows the initial.' : 'No portrait yet, so Home shows the initial.',
            };
            const working = current.job ? (current.job.kind === 'living' ? 'NPC Forge is making the living portrait…'
                : 'NPC Forge is painting a portrait…') : '';
            const checks = [];
            if (current.age_source === 'rolled' && current.age != null) {
                checks.push(`NPC Forge rolled an age of ${current.age} as a guess.`);
            }
            if (current.age_checked === 'unchecked') {
                checks.push('Not age-checked yet: the model checks it when it is running, and takes it down if it looks under 18.');
            }
            if (current.removed) {
                const age = current.removed.apparent_age;
                checks.push(age ? `The last portrait was taken down: the age check read it as about ${age}.`
                    : 'The last portrait was taken down: the age check could not see an adult in it.');
            }
            $('palSettingsPortraitFrom').textContent = state.portraitNote || working
                || [words[current.from] || '', ...checks].filter(Boolean).join(' ');
            $('palSettingsPortraitMake').disabled = Boolean(current.job);
            if ($('palSettingsPortraitLibrary')) $('palSettingsPortraitLibrary').disabled = Boolean(current.job);
            $('palSettingsPortraitLife').hidden = !current.can_bring_to_life;
            $('palSettingsLivingEngineRow').hidden = !current.can_bring_to_life || !state.forgeStatus;
            if (current.can_bring_to_life && !state.forgeStatus) loadEngines();
            if (state.forgeStatus && current.can_bring_to_life && state.livingEngineFor !== current.id) {
                state.livingEngineFor = current.id;                                       // filled once per person shown
                fillEngines($('palSettingsLivingEngine'), 'edit', current.edit_model);
            }
            $('palSettingsPortraitLife').textContent = current.living ? 'Make the living portrait again' : 'Bring it to life';
            const hasPortrait = current.from === 'forge' || current.from === 'upload';
            $('palSettingsPortraitUndo').hidden = !hasPortrait || Boolean(current.job);
            $('palSettingsPortraitUndo').textContent = character && current.card_picture ? "Use the card's picture again"
                : 'Remove the portrait';
            if ($('palSettingsPortraitUpload')) $('palSettingsPortraitUpload').hidden = !current.can_upload;
        }

        async function loadPortrait(person) {
            if ($('palSettingsPortraitRow')) $('palSettingsPortraitRow').hidden = true;
            try {
                const current = await call(portraitApi(person));
                if (state.editing !== person) return;
                state.portrait = current;
                state.portraitNote = '';
                $('palSettingsPortraitRow').hidden = false;
                renderPortraitRow();
                if (current.job) watchPortrait(person);
            } catch (_) {
                state.portrait = null;                                                    // the rest of the settings still work
            }
        }

        function portraitSays(text) {
            state.portraitNote = text;
            if ($('portraitStatus')) $('portraitStatus').textContent = text;
            renderPortraitRow();
        }

        async function refreshPerson(person) {
            await load();
            const fresh = (state.data?.people || []).find(p => p.id === person.id);
            if (fresh && state.editing?.id === person.id) state.editing = fresh;
            renderPortraitRow();
        }

        // Look at NPC Forge's job every few seconds until it ends. One watcher per person; it stops when the dialogs move on.
        async function watchPortrait(person) {
            if (state.watching?.id === person.id) return;
            const mine = {id: person.id};                                                 // one watcher: an older one stops
            state.watching = mine;
            try {
                for (let round = 0; round < 1200; round += 1) {
                    await wait(3000);
                    if (state.watching !== mine) return;
                    const answer = await call(`${portraitApi(person)}/job`);
                    if (state.portrait?.id === person.id && answer.portrait) state.portrait = answer.portrait;
                    if (WORKING.includes(answer.state)) {
                        portraitSays(answer.note ? `NPC Forge: ${answer.note}` : '');
                        continue;
                    }
                    if (answer.state === 'unreachable') {
                        portraitSays(`${answer.note} The job is kept: open these settings again later to bring the picture in.`);
                        return;
                    }
                    if (answer.state === 'ready') {
                        portraitSays(answer.note || (answer.portrait?.living ? 'The living portrait is ready.' : 'The new portrait is in.'));
                        await refreshPerson(person);
                    } else if (answer.state !== 'none') {
                        portraitSays(answer.note || 'NPC Forge could not make the picture.');
                    }
                    $('portraitStop').hidden = true;
                    $('portraitPaint').disabled = false;
                    return;
                }
            } catch (error) {
                portraitSays(error.message || 'Lost track of NPC Forge. Open these settings again to look.');
            } finally {
                if (state.watching === mine) state.watching = null;
            }
        }

        // ---- NPC Forge's engines: its own default, or one it lists (a lighter one when memory is short) --------------
        function fillEngines(select, kind, chosen) {
            const list = state.forgeStatus?.engines?.[kind] || [];
            const standard = list.find(engine => engine.id === state.forgeStatus?.default_engines?.[kind]);
            select.replaceChildren(create(doc, 'option', {value: ''}, standard ? `NPC Forge's default (${standard.label})` : "NPC Forge's default"),
                ...list.map(engine => create(doc, 'option', {value: engine.id}, engine.label)));
            select.value = list.some(engine => engine.id === chosen) ? chosen : '';
        }

        function engineNote(kind, chosen) {
            const list = state.forgeStatus?.engines?.[kind] || [];
            const engine = list.find(item => item.id === (chosen || state.forgeStatus?.default_engines?.[kind]));
            return engine?.note ? `${engine.label}: ${engine.note}` : '';
        }

        function renderEngines() {
            const fromCard = chosenFrom() === 'card';
            $('portraitImageRow').hidden = fromCard;                                     // words are drawn by a picture engine
            const note = fromCard ? engineNote('edit', $('portraitEditModel').value) : engineNote('image', $('portraitImageModel').value);
            $('portraitEngineNote').textContent = [note, 'The redraw engine also makes the living portrait.'].filter(Boolean).join(' ');
        }

        async function loadEngines() {
            if (state.loadingEngines || state.enginesTried) return;           // once each time the settings open
            state.loadingEngines = state.enginesTried = true;
            try {
                state.forgeStatus = await call('/api/forge/status');
                if ((state.forgeStatus.engines?.edit || []).length) renderPortraitRow();
                else state.forgeStatus = null;                                            // NPC Forge is not there: no list
            } catch (_) {
                state.forgeStatus = null;
            } finally {
                state.loadingEngines = false;
            }
        }

        function chosenFrom() {
            return [...doc.querySelectorAll('input[name="portraitFrom"]')].find(input => input.checked)?.value || 'description';
        }

        function renderFrom() {
            const fromCard = chosenFrom() === 'card';
            const hasWords = state.portrait?.kind === 'character' || state.portrait?.kind === 'companion';
            $('portraitAskRow').hidden = fromCard || !hasWords;
            $('portraitDescription').placeholder = fromCard ? 'Optional: kept with the character in NPC Forge'
                : 'Short curly red hair, green eyes, freckles, a yellow raincoat';
            renderEngines();
        }

        function fillChoices(select, choices, chosen) {
            select.replaceChildren(...(choices || []).map(choice => create(doc, 'option', {value: choice.id}, choice.label)));
            if ((choices || []).some(choice => choice.id === chosen)) select.value = chosen;
        }

        async function forgeReady() {
            $('portraitForge').textContent = 'Looking for NPC Forge…';
            $('portraitForgeStart').hidden = true;
            $('portraitPaint').disabled = true;
            try {
                const forge = await call('/api/forge/status');
                const current = state.portrait || {};
                fillChoices($('portraitStyle'), forge.styles, current.style);
                fillChoices($('portraitSetting'), forge.settings, current.setting);
                state.forgeStatus = forge;
                state.livingEngineFor = null;
                fillEngines($('portraitImageModel'), 'image', current.image_model);
                fillEngines($('portraitEditModel'), 'edit', current.edit_model);
                renderEngines();
                const ready = forge.online === true && forge.comfy_online !== false && (forge.styles || []).length > 0;
                const message = ready ? 'NPC Forge is ready.' : (forge.message || 'NPC Forge is not ready.');
                $('portraitForge').textContent = [message, ...(forge.notices || [])].join(' ');
                $('portraitForgeStart').hidden = forge.online === true;
                $('portraitPaint').disabled = !ready || Boolean(current.job);
            } catch (error) {
                $('portraitForge').textContent = error.message || 'Could not reach NPC Forge.';
            }
        }

        async function openPortrait() {
            const current = state.portrait;
            if (!current) return;
            $('portraitTitle').textContent = `A portrait of ${current.name || 'them'} from NPC Forge`;
            $('portraitFromCardRow').hidden = !current.card_picture;
            $('portraitFromCard').checked = current.card_picture === true;
            $('portraitFromWords').checked = current.card_picture !== true;
            $('portraitDescription').value = current.description || '';
            $('portraitGender').value = current.gender || '';
            $('portraitStop').hidden = !current.job;
            $('portraitStatus').textContent = current.job ? 'NPC Forge is painting…' : '';
            renderFrom();
            closeDialog('palSettingsDialog');                                            // one dialog at a time
            showDialog('portraitDialog');
            $('portraitDialog').scrollTop = 0;
            await forgeReady();
        }

        function closePortrait() {
            closeDialog('portraitDialog');
            if (state.editing) {
                renderPortraitRow();
                showDialog('palSettingsDialog');                                         // what was typed there is kept
            }
        }

        async function startForge() {
            $('portraitForge').textContent = 'Starting NPC Forge…';
            try {
                const answer = await call('/api/forge/start', {method: 'POST'});
                $('portraitForge').textContent = answer.message || '';
                for (let round = 0; answer.started && round < 30; round += 1) {     // it takes up to a minute to answer
                    await wait(3000);
                    await forgeReady();
                    if ($('portraitForgeStart').hidden) return;                          // it answers now
                    $('portraitForge').textContent = 'Starting NPC Forge… this can take a minute.';
                    $('portraitForgeStart').hidden = true;                               // a second copy would fight the first
                }
                if (answer.started) {
                    $('portraitForge').textContent = 'NPC Forge has not answered yet. Try Start NPC Forge again in a moment.';
                    $('portraitForgeStart').hidden = false;
                }
            } catch (error) {
                $('portraitForge').textContent = error.message || 'Could not start NPC Forge.';
            }
        }

        async function askModel() {
            const person = state.editing;
            if (!person) return;
            $('portraitAsk').disabled = true;
            $('portraitStatus').textContent = 'Asking the model…';
            try {
                const answer = await call(`${portraitApi(person)}/describe`, {method: 'POST'});
                if (answer.description) $('portraitDescription').value = answer.description;
                $('portraitStatus').textContent = answer.description ? '' : 'The card does not say how they look. Write it yourself.';
            } catch (error) {
                $('portraitStatus').textContent = error.message || 'The model did not answer.';
            } finally {
                $('portraitAsk').disabled = false;
            }
        }

        async function paintPortrait(event) {
            event?.preventDefault?.();
            const person = state.editing;
            if (!person) return;
            const body = {from: chosenFrom(), description: $('portraitDescription').value.trim(),
                          style: $('portraitStyle').value, setting: $('portraitSetting').value, gender: $('portraitGender').value,
                          image_model: $('portraitImageModel').value, edit_model: $('portraitEditModel').value};
            if (body.from === 'description' && !body.description) {
                $('portraitStatus').textContent = 'Describe how they look first.';
                return;
            }
            $('portraitPaint').disabled = true;
            $('portraitStatus').textContent = 'Asking NPC Forge…';
            try {
                state.portrait = await call(portraitApi(person), json('POST', body));
                $('portraitStop').hidden = false;
                portraitSays('NPC Forge is painting. This takes a minute or two; you can close this and keep going.');
                watchPortrait(person);
            } catch (error) {
                $('portraitStatus').textContent = error.message || 'Could not ask NPC Forge. Try again.';
                $('portraitPaint').disabled = false;
            }
        }

        async function stopPortrait() {
            const person = state.editing;
            if (!person) return;
            try {
                state.watching = null;
                state.portrait = await call(`${portraitApi(person)}/job`, {method: 'DELETE'});
                $('portraitStop').hidden = true;
                $('portraitPaint').disabled = false;
                portraitSays('Stopped.');
            } catch (error) {
                $('portraitStatus').textContent = error.message || 'Could not stop it.';
            }
        }

        async function bringToLife() {
            const person = state.editing;
            if (!person) return;
            try {
                const body = $('palSettingsLivingEngineRow').hidden ? {} : {edit_model: $('palSettingsLivingEngine').value};
                state.portrait = await call(`${portraitApi(person)}/living`, json('POST', body));
                portraitSays('');
                watchPortrait(person);
            } catch (error) {
                portraitSays(error.message || 'Could not ask NPC Forge. Try again.');
            }
        }

        async function stopUsingPortrait() {
            const person = state.editing;
            if (!person) return;
            try {
                state.portrait = await call(`${portraitApi(person)}/forge`, {method: 'DELETE'});
                portraitSays('');
                await refreshPerson(person);
            } catch (error) {
                portraitSays(error.message || 'Could not change the portrait. Try again.');
            }
        }

        // ---- a companion written here: the card, an uploaded picture, or NPC Forge ---------------------------
        const portraitTarget = person => `/api/avatars/${encodeURIComponent(person.id)}/portrait`;

        function fillCompanionForm(values) {
            const value = values || {};
            $('companionName').value = value.name || '';
            $('companionAge').value = value.age == null ? '' : String(value.age);
            $('companionGender').value = value.gender || '';
            $('companionBio').value = value.bio || '';
            $('companionHobbies').value = (value.hobbies || []).join(', ');
            $('companionSpeech').value = value.speech || '';
            $('companionDemeanor').value = value.demeanor || '';
            $('companionQuirk').value = value.quirk || '';
            $('companionGoal').value = value.goal || '';
            $('companionPeeve').value = value.peeve || '';
        }

        function openWriteCompanion(person = null, fromSettings = false) {
            if (state.busy) return;
            state.writing = person ? {id: person.id, name: person.name} : null;
            state.writingFromSettings = fromSettings;
            fillCompanionForm(person);
            $('companionDialogTitle').textContent = person ? `Edit ${person.name || 'this companion'}` : 'Write a companion';
            $('companionSubmit').textContent = person ? 'Save' : 'Make companion';
            $('companionStatus').textContent = '';
            showDialog('companionDialog');
            $('companionName').focus?.();
        }

        function closeWriteCompanion() {
            closeDialog('companionDialog');
            if (state.writingFromSettings) {                                   // the settings dialog comes back as it was
                state.writingFromSettings = false;
                state.writing = null;
                if (state.editing) showDialog('palSettingsDialog');
            }
        }

        async function openEditCompanion() {
            const person = state.editing;
            if (!person) return;
            $('palSettingsStatus').textContent = 'Loading…';
            try {
                const current = await call(settingsUrl(person));
                state.editingSettings = current;
                closeDialog('palSettingsDialog');
                openWriteCompanion(current, true);
            } catch (error) {
                $('palSettingsStatus').textContent = error.message || 'Could not load who they are. Try again.';
            }
        }

        async function submitCompanion(event) {
            event?.preventDefault?.();
            if (state.busy) return;
            const name = $('companionName').value.trim();
            const age = String($('companionAge').value ?? '').trim();
            if (!name) { $('companionStatus').textContent = 'Give your companion a name.'; return; }
            if (!age) { $('companionStatus').textContent = 'Give their age (18 or older).'; return; }
            const body = {
                name, age, gender: $('companionGender').value,
                bio: $('companionBio').value.trim(),
                hobbies: $('companionHobbies').value.split(/[,\n]/).map(item => item.trim()).filter(Boolean),
                speech: $('companionSpeech').value.trim(), demeanor: $('companionDemeanor').value.trim(),
                quirk: $('companionQuirk').value.trim(), goal: $('companionGoal').value.trim(),
                peeve: $('companionPeeve').value.trim(),
            };
            const editing = state.writing;
            state.busy = true;
            $('companionSubmit').disabled = true;
            $('companionStatus').textContent = editing ? 'Saving…' : `Writing ${name}…`;
            try {
                if (editing) {
                    await call(`/api/companions/${encodeURIComponent(editing.id)}`, json('PUT', body));
                    state.writing = state.writingFromSettings = null;
                    closeDialog('companionDialog');
                    state.editing = null;
                    await load();
                    state.busy = false;
                } else {
                    await call('/api/companions', json('POST', body));
                    navigate('/');
                }
            } catch (error) {
                $('companionStatus').textContent = error.message || 'Could not save. Try again.';
                $('companionSubmit').disabled = false;
                state.busy = false;
            }
        }

        async function uploadPortrait(file) {
            const person = state.editing;
            if (!person || !file || state.busy) return;
            state.busy = true;
            $('palSettingsStatus').textContent = `Uploading ${file.name || 'the picture'}…`;
            try {
                const form = new FormData();
                form.append('file', file, file.name || 'portrait.png');
                await call(portraitTarget(person), {method: 'POST', body: form});
                state.busy = false;
                await loadPortrait(person);
                await refreshPerson(person);
                portraitSays('The picture is in. Every new portrait goes through the age check.');
            } catch (error) {
                state.busy = false;
                $('palSettingsStatus').textContent = error.message || 'Could not upload the picture. Try again.';
            }
        }

        // ---- a character already in NPC Forge's library lends its portrait --------------------------------------------
        async function openLibrary() {
            const person = state.editing;
            if (!person || state.portrait?.job) return;
            const grid = $('libraryGrid');
            grid.replaceChildren();
            $('libraryStatus').textContent = 'Looking in NPC Forge\'s library…';
            closeDialog('palSettingsDialog');                                            // one dialog at a time
            showDialog('libraryDialog');
            try {
                const answer = await call('/api/avatars/forge');
                if (state.editing !== person) return;
                if (!answer.available) {
                    $('libraryStatus').textContent = "Start NPC Forge with character card support to see its library.";
                    return;
                }
                const usable = (answer.characters || []).filter(row => row.has_portrait);
                grid.replaceChildren(...usable.map(row => {
                    const button = create(doc, 'button', {type: 'button', role: 'listitem', 'data-npc': row.id});
                    button.append(create(doc, 'img', {src: `/api/forge/${encodeURIComponent(row.id)}/thumb`, alt: '', loading: 'lazy'}),
                                  create(doc, 'span', {}, row.name || row.id));
                    if (row.reason) {
                        button.disabled = true;
                        button.title = row.reason;
                    }
                    button.addEventListener('click', () => chooseFromLibrary(row));
                    return button;
                }));
                $('libraryStatus').textContent = usable.length ? ''
                    : 'No characters with a portrait in NPC Forge\'s library yet. Make one there, or use "Make a portrait in NPC Forge".';
            } catch (error) {
                $('libraryStatus').textContent = error.message || "Could not read NPC Forge's library. Try again.";
            }
        }

        function closeLibrary() {
            closeDialog('libraryDialog');
            if (state.editing) {
                renderPortraitRow();
                showDialog('palSettingsDialog');                                         // what was typed there is kept
            }
        }

        async function chooseFromLibrary(row) {
            const person = state.editing;
            if (!person || state.busy) return;
            state.busy = true;
            $('libraryStatus').textContent = `Checking ${row.name ? `${row.name}'s` : 'the'} portrait…`;
            try {
                state.portrait = await call(`${portraitApi(person)}/library`, json('POST', {npc_id: row.id}));
                state.busy = false;
                closeLibrary();
                portraitSays(`${row.name ? `${row.name}'s` : 'Their'} portrait from NPC Forge's library is in.`);
                await refreshPerson(person);
            } catch (error) {
                state.busy = false;
                $('libraryStatus').textContent = error.message || 'Could not use that portrait. Try another.';
            }
        }

        async function removePortrait() {
            const person = state.editing;
            if (!person) return;
            const uploaded = state.portrait?.from === 'upload';
            try {
                state.portrait = uploaded
                    ? await call(portraitTarget(person), {method: 'DELETE'})
                    : await call(`${portraitApi(person)}/forge`, {method: 'DELETE'});
                portraitSays('');
                await refreshPerson(person);
            } catch (error) {
                portraitSays(error.message || 'Could not change the portrait. Try again.');
            }
        }

        // ---- a character's lorebook: a switch, and what is in it (read only) --------------------------------------------
        function entryLine(entry) {
            const parts = [entry.name || 'An entry'];
            if (entry.constant) parts.push('always on');
            else if (entry.keys?.length) parts.push(`keys: ${entry.keys.join(', ')}`);
            if (entry.enabled === false) parts.push('switched off in the card');
            if (entry.regex) parts.push('uses a regular expression, so Altera does not use it');
            return parts.join(' · ');
        }

        function renderBook(current) {
            const entries = current.book?.entries || [];
            const usable = entries.filter(entry => entry.enabled !== false && !entry.regex).length;
            $('palSettingsLorebookRow').hidden = !entries.length;
            $('palSettingsBookBox').hidden = !entries.length;
            $('palSettingsLorebook').checked = current.lorebook === true;
            $('palSettingsLorebook').disabled = !usable;
            $('palSettingsLorebookCount').textContent = `${entries.length} ${entries.length === 1 ? 'entry' : 'entries'}`
                + (usable === entries.length ? '' : `, ${usable} usable`) + '. Altera\'s own rules still apply.';
            $('palSettingsBook').replaceChildren(...entries.map(entry => create(doc, 'li',
                entry.enabled === false || entry.regex ? {class: 'off'} : {}, entryLine(entry))));
        }

        const settingsUrl = person => `/api/${kindOf(person) === 'character' ? 'characters'
            : kindOf(person) === 'companion' ? 'companions' : 'pals'}/${encodeURIComponent(person.id)}`;
        const repliesUrl = person => `/api/people/${encodeURIComponent(person.id)}/replies`;

        // ---- a person's own temperature and reply length (docs/REPLY_SETTINGS.md) ---------------------------------------
        function renderReplies(replies) {
            const own = replies?.own || {};
            const app = replies?.app || {};
            state.editingReplies = {temperature: own.temperature ?? null, max_tokens: own.max_tokens ?? null};
            $('palSettingsTemperature').value = own.temperature ?? '';
            $('palSettingsLength').value = own.max_tokens ?? '';
            $('palSettingsTemperature').placeholder = app.temperature != null ? `App: ${app.temperature}` : '';
            $('palSettingsLength').placeholder = app.max_tokens != null ? `App: ${app.max_tokens}` : '';
            $('palSettingsRepliesHint').textContent = app.temperature != null
                ? `Leave a box empty to use the app's setting (temperature ${app.temperature}, ${app.max_tokens} tokens), set in Settings → Replies.`
                : 'Leave a box empty to use the setting in Settings → Replies.';
        }

        // ---- who a pal is: about, traits, way of talking, interests, quirks (pal.yaml) ---------------------------------
        const lines = id => String($(id).value ?? '').split('\n').map(line => line.trim()).filter(Boolean);

        function renderPersona(current) {
            const style = current?.speech_style || {};
            $('palSettingsAbout').value = current?.about || '';
            $('palSettingsTraits').value = (current?.traits || []).join('\n');
            $('palSettingsTone').value = style.tone || '';
            $('palSettingsFormality').value = style.formality || '';
            $('palSettingsHumor').value = style.humor || '';
            $('palSettingsEmojis').checked = style.emojis === true;
            $('palSettingsInterests').value = (current?.interests || []).join('\n');
            $('palSettingsQuirks').value = (current?.quirks || []).join('\n');
            state.editingPersona = JSON.stringify(readPersona());
        }

        function readPersona() {
            return {about: String($('palSettingsAbout').value ?? '').trim(), traits: lines('palSettingsTraits'),
                speech_style: {tone: String($('palSettingsTone').value ?? '').trim(),
                    formality: String($('palSettingsFormality').value ?? '').trim(),
                    humor: String($('palSettingsHumor').value ?? '').trim(), emojis: $('palSettingsEmojis').checked === true},
                interests: lines('palSettingsInterests'), quirks: lines('palSettingsQuirks')};
        }

        // {changes} (only what differs from what was loaded; null = the app's setting again) or {error}
        function readReplies() {
            const changes = {};
            for (const [key, id, label] of [['temperature', 'palSettingsTemperature', 'Temperature'],
                                            ['max_tokens', 'palSettingsLength', 'Reply length']]) {
                const text = String($(id).value ?? '').trim();
                const value = text ? Number(text) : null;
                if (text && !Number.isFinite(value)) return {error: `${label} must be a number.`};
                if (value !== (state.editingReplies?.[key] ?? null)) changes[key] = value;
            }
            return {changes};
        }

        async function openSettings(person) {
            const character = kindOf(person) === 'character';
            const written = kindOf(person) === 'companion';
            state.editing = person;
            $('palSettingsStatus').textContent = 'Loading…';
            $('palSettingsSave').disabled = true;
            $('palSettingsTitle').textContent = `${person.name || (character ? 'Character' : written ? 'Companion' : 'Pal')} settings`;
            if ($('palSettingsNameRow')) $('palSettingsNameRow').hidden = character;      // a character keeps the card's name
            if ($('palSettingsCardRow')) $('palSettingsCardRow').hidden = !character;
            if ($('palSettingsCardVoice')) $('palSettingsCardVoice').hidden = true;
            if ($('palSettingsCardLicence')) {
                $('palSettingsCardLicence').hidden = !person.licence;
                $('palSettingsCardLicence').textContent = [person.ai_generated && 'AI-generated', person.licence]
                    .filter(Boolean).join(' · ');
            }
            if ($('palSettingsLorebookRow')) $('palSettingsLorebookRow').hidden = $('palSettingsBookBox').hidden = true;
            if ($('palSettingsFeaturesRow')) $('palSettingsFeaturesRow').hidden = written;  // a companion keeps all four
            if ($('palSettingsWrittenRow')) $('palSettingsWrittenRow').hidden = !written;
            if ($('palSettingsPortraitUpload')) $('palSettingsPortraitUpload').hidden = true;   // until the portrait says it can
            if ($('palSettingsKeeps')) $('palSettingsKeeps').textContent = `What this ${character ? 'character' : written ? 'companion' : 'pal'} keeps`;
            if ($('palSettingsKidsRow')) $('palSettingsKidsRow').hidden = true;
            $('palSettingsVoiceRow').hidden = false;
            $('palSettingsPersonaRow').hidden = true;                     // a pal's only: the others have their card
            state.editingPersona = null;
            state.forgeCompanion = false;
            renderReplies(null);
            state.portrait = null;
            state.portraitShown = null;
            state.livingEngineFor = null;
            state.enginesTried = false;
            state.watching = null;
            showDialog('palSettingsDialog');
            loadPortrait(person);
            try {
                const [current, {voices}, , replies] = await Promise.all([call(settingsUrl(person)), options(),
                    (character || written) ? null : kidsState(), call(repliesUrl(person)).catch(() => null)]);
                if (state.editing !== person) return;
                // A companion from NPC Forge is changed in NPC Forge: here only what Altera keeps itself (its replies)
                state.forgeCompanion = written && current.written === false;
                if (state.forgeCompanion) {
                    $('palSettingsNameRow').hidden = $('palSettingsVoiceRow').hidden = true;
                    $('palSettingsWrittenRow').hidden = $('palSettingsPortraitUpload').hidden = true;
                }
                renderReplies(replies);
                if (!character && !written) {
                    renderPersona(current);
                    $('palSettingsPersonaRow').hidden = false;
                }
                $('palSettingsName').value = current.name || '';
                $('palSettingsVibe').textContent = character ? (current.creator ? `Card by ${current.creator}` : '')
                    : state.forgeCompanion ? 'Made in NPC Forge: change who they are there.'
                    : written ? 'Written here, without NPC Forge.'
                    : (current.vibe_label ? `Vibe: ${current.vibe_label}` : '');
                if (character) {
                    state.settingsCardVoice = current.card_voice;
                    if ($('palSettingsCardVoice')) $('palSettingsCardVoice').hidden = !current.card_voice?.name;
                    const instructions = $('palSettingsInstructions');
                    instructions.checked = current.card_instructions === true;
                    instructions.disabled = !current.has_instructions;                  // nothing to switch off
                    renderBook(current);
                    $('palSettingsNotes').textContent = current.creator_notes || '';
                    $('palSettingsNotesBox').hidden = !current.creator_notes;
                }
                fillVoices($('palSettingsVoice'), voices, current.voice);
                if (state.forgeCompanion && $('palSettingsVoiceInfo')) $('palSettingsVoiceInfo').hidden = true;
                for (const box of doc.querySelectorAll('#palSettingsDialog [data-feature]')) {
                    box.checked = current.features?.[box.dataset.feature] === true;
                    box.disabled = false;
                }
                if (!character && $('palSettingsKidsRow')) {
                    state.editingKids = current.kids === true;
                    $('palSettingsKidsRow').hidden = false;
                    renderKidsRow(current.kids === true);
                }
                $('palSettingsStatus').textContent = '';
                $('palSettingsSave').disabled = false;
            } catch (error) {
                $('palSettingsStatus').textContent = error.message || 'Could not load the settings. Try again.';
            }
        }

        async function saveSettings(event) {
            event?.preventDefault?.();
            const person = state.editing;
            if (!person) return;
            const character = kindOf(person) === 'character';
            const written = kindOf(person) === 'companion';
            const features = {};
            for (const box of doc.querySelectorAll('#palSettingsDialog [data-feature]')) {
                if (FEATURES.includes(box.dataset.feature)) features[box.dataset.feature] = box.checked === true;
            }
            const replies = readReplies();
            if (replies.error) { $('palSettingsStatus').textContent = replies.error; return; }
            $('palSettingsSave').disabled = true;
            $('palSettingsStatus').textContent = 'Saving…';
            try {
                if (Object.keys(replies.changes).length) {
                    await call(repliesUrl(person), json('PUT', replies.changes));
                    state.editingReplies = {...state.editingReplies, ...replies.changes};     // a retry sends only the rest
                }
                if (state.forgeCompanion) {                                     // nothing else here belongs to Altera
                    closeDialog('palSettingsDialog');
                    state.editing = null;
                    await load();
                    return;
                }
                const body = character
                    ? {voice: $('palSettingsVoice').value, features, card_instructions: $('palSettingsInstructions').checked === true}
                    : written ? {name: $('palSettingsName').value.trim(), voice: $('palSettingsVoice').value}
                    : {name: $('palSettingsName').value.trim(), voice: $('palSettingsVoice').value, features};
                if (body.voice && body.voice === $('palSettingsVoice').dataset.savedUnavailable) delete body.voice;
                if (!character && !written && state.editingPersona !== null) {
                    const persona = readPersona();
                    if (JSON.stringify(persona) !== state.editingPersona) body.persona = persona;   // only when changed
                }
                if (!character && !written && state.kids?.enabled && $('palSettingsKids')
                        && (($('palSettingsKids').checked === true) !== (state.editingKids === true))) {
                    body.kids = $('palSettingsKids').checked === true;
                    body.pin = $('palSettingsKidsPin')?.value || '';
                }
                if (character && !$('palSettingsLorebookRow').hidden) body.lorebook = $('palSettingsLorebook').checked === true;
                await call(settingsUrl(person), json('PUT', body));
                closeDialog('palSettingsDialog');
                state.editing = null;
                await load();
            } catch (error) {
                $('palSettingsStatus').textContent = error.message || 'Could not save. Try again.';
                $('palSettingsSave').disabled = false;
            }
        }

        for (const tile of doc.querySelectorAll('.home-tile')) tile.addEventListener('click', () => choose(tile.dataset.kind));
        on('homeShowAll', 'click', () => choose(null));
        on('homeMakePal', 'click', () => openMakePal());
        on('homeWriteCompanion', 'click', () => openWriteCompanion());
        on('homeContinueButton', 'click', () => state.data?.last && open(state.data.last));
        on('homeImportCard', 'click', () => chooseCard());
        on('homeCardFile', 'change', event => importCard(event?.target?.files?.[0] || $('homeCardFile').files?.[0]));
        on('palSettingsCardVoice', 'click', () => openCardVoice(state.editing.id, state.settingsCardVoice, true));
        on('cardVoicePreview', 'click', () => doCardVoice('preview'));
        on('cardVoiceUse', 'click', () => doCardVoice('use'));
        on('cardVoiceEnable', 'click', () => doCardVoice('enable'));
        on('cardVoiceLeave', 'click', leaveCardVoice);
        on('cardVoiceConsent', 'change', () => cardVoiceBusy(state.cardVoice, false));
        on('cardVoiceDialog', 'cancel', event => { event.preventDefault(); leaveCardVoice(); });
        on('palForm', 'submit', makePal);
        on('palCancel', 'click', () => closeDialog('palDialog'));
        on('palSettingsForm', 'submit', saveSettings);
        on('palSettingsCancel', 'click', () => { state.editing = null; closeDialog('palSettingsDialog'); });
        on('palVoiceSample', 'click', () => playSample('palVoice', 'palStatus', $('palName').value.trim()));
        on('palSettingsVoiceSample', 'click', () => playSample('palSettingsVoice', 'palSettingsStatus', state.editing?.name));
        on('palVoice', 'change', event => showVoiceLicence(event.target));
        on('palSettingsVoice', 'change', event => showVoiceLicence(event.target));
        on('palSettingsPortraitMake', 'click', openPortrait);
        on('palSettingsPortraitLife', 'click', bringToLife);
        on('palSettingsPortraitUndo', 'click', removePortrait);
        on('palSettingsPortraitLibrary', 'click', openLibrary);
        on('libraryClose', 'click', closeLibrary);
        on('libraryDialog', 'cancel', event => { event?.preventDefault?.(); closeLibrary(); });     // Escape
        on('palSettingsPortraitUpload', 'click', () => {
            const input = $('palSettingsPortraitFile');
            if (!input) return;
            input.value = '';
            input.click?.();
        });
        on('palSettingsPortraitFile', 'change', event => uploadPortrait(
            event?.target?.files?.[0] || $('palSettingsPortraitFile')?.files?.[0]));
        on('palSettingsEditCard', 'click', openEditCompanion);
        on('portraitForm', 'submit', paintPortrait);
        on('portraitClose', 'click', closePortrait);
        on('portraitDialog', 'cancel', event => { event?.preventDefault?.(); closePortrait(); });     // Escape
        on('portraitStop', 'click', stopPortrait);
        on('portraitAsk', 'click', askModel);
        on('portraitForgeStart', 'click', startForge);
        on('portraitFromCard', 'change', renderFrom);
        on('portraitFromWords', 'change', renderFrom);
        on('portraitImageModel', 'change', renderEngines);
        on('portraitEditModel', 'change', renderEngines);
        on('palSettingsKids', 'change', lockPresence);
        on('palSettingsKidsAbout', 'click', openKids);
        on('kidsForm', 'submit', setKids);
        on('kidsLock', 'click', lockKids);
        on('kidsChangePin', 'click', submitChangePin);
        on('kidsCancel', 'click', closeKids);
        on('kidsDialog', 'cancel', event => { event?.preventDefault?.(); closeKids(); });     // Escape
        on('companionForm', 'submit', submitCompanion);
        on('companionCancel', 'click', closeWriteCompanion);
        on('companionDialog', 'cancel', event => { event?.preventDefault?.(); closeWriteCompanion(); });

        const loaded = load();
        if ($('homeInbox')) {
            loadInbox();
            window.addEventListener('focus', () => loadInbox());
            window.setInterval(() => { if (!doc.hidden) loadInbox(); }, 10000);
        }
        // The first run ends here with "make a pal" as the obvious next step; the sidebar can also ask for "write a companion".
        const params = new URLSearchParams(search());
        const make = params.get('make');
        const incoming = params.get('cardVoice');
        if (incoming && $('cardVoiceDialog')) {
            loaded.then(async () => {
                try {
                    const person = await call(`/api/characters/${encodeURIComponent(incoming)}`);
                    if (person.card_voice?.name) openCardVoice(person.id, person.card_voice);
                } catch (error) {
                    status(error.message || 'Could not read the card voice. Open the character’s settings to try again.');
                }
                params.delete('cardVoice');
                const query = params.toString();
                window.history?.replaceState(null, '', query ? `/home?${query}` : '/home');
            });
        }
        if (make === 'pal' || make === 'companion') {
            loaded.then(() => {
                if (state.busy) return;
                if (make === 'pal') openMakePal(); else openWriteCompanion();
            });
        }
        return {state, loaded, choose, open, openMakePal, makePal, openWriteCompanion, submitCompanion, closeWriteCompanion,
                openEditCompanion, uploadPortrait, removePortrait, openSettings, saveSettings, importCard, openKids, setKids,
                playSample, openPortrait, paintPortrait, closePortrait, watchPortrait, stopPortrait, bringToLife,
                stopUsingPortrait, askModel};
    }

    window.HomePage = Object.freeze({start, describe, kindOf});
    if (typeof window.AlteraPage?.ready === 'function') window.AlteraPage.ready(() => start());
})();
