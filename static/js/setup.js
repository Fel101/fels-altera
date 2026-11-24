// First-run setup: a name, one tiny model check, and which optional pieces are up. It ends on Home.
// Everything is rendered as text nodes, never HTML. The check is POST /api/setup/check; it sends one short fixed
// question and never anything the person typed. `?check=1` is the same page opened from Settings as "Setup check".
(() => {
    const PIECES = [
        {name: 'Speech', url: '/api/voice/status',
            up: data => Boolean(data.tts_enabled), on: () => 'Ready.', off: () => 'Off.',
            hint: 'Optional: install the local voice with voice\\setup.bat, then switch Voice on in Settings.'},
        {name: 'NPC Forge', url: '/api/forge/status', link: 'https://github.com/Fel101/fels-npc-forge',
            up: data => Boolean(data.online),
            on: data => [data.comfy_online === true ? 'Running, with ComfyUI.' :
                data.comfy_online === false ? 'Running, but ComfyUI is not reachable.' : 'Running. ComfyUI status is unknown.',
                ...(data.notices || [])].join(' '),
            // The install link is useful even when the service is unavailable.
            off: data => data.not_running ? 'Not running.' : data.message || 'Could not reach NPC Forge.',
            hint: 'Optional. Paints portraits from descriptions and creates characters with personalities.'},
        {name: 'ComfyUI', url: '/api/imagegen/status',
            up: data => Boolean((data.comfy || {}).online), on: () => 'Reachable.', off: () => 'Not running.',
            hint: 'Optional: pictures in the chat need it, and so does NPC Forge.'},
    ];

    function start({doc = document, fetchImpl = (...args) => window.fetch(...args),
                    navigate = url => window.location.assign(url),
                    search = () => (window.location ? window.location.search : '')} = {}) {
        const $ = id => doc.getElementById(id);
        const checking = new URLSearchParams(search()).get('check') === '1';
        const state = {name: '', busy: false, saving: false, result: null, providers: [], activeModel: ''};

        function say(id, value) { const node = $(id); if (node) node.textContent = value; }

        async function call(url, init) {
            const response = await fetchImpl(url, init);
            const body = await response.json().catch(() => ({}));
            if (!response.ok) throw new Error(body.error || `The request failed (${response.status}).`);
            return body;
        }

        // Where a provider runs, from its address: chats to a provider off this PC leave it, and the page says so.
        function whereIs(baseUrl) {
            const host = (String(baseUrl || '').match(/^[a-z]+:\/\/(\[[^\]]*\]|[^/:?#]*)/i) || [])[1] || '';
            const name = host.toLowerCase();
            if (!name) return '';
            if (['localhost', '127.0.0.1', '[::1]', '0.0.0.0'].includes(name) || name.endsWith('.localhost')) {
                return 'This provider runs on this PC: your chats stay here.';
            }
            if (/^(10\.|192\.168\.|172\.(1[6-9]|2\d|3[01])\.)/.test(name) || name.endsWith('.local')) {
                return `This provider runs on your local network (${host}): your chats go to that machine.`;
            }
            return `This provider is not on this PC: your chats are sent to ${host}.`;
        }

        function option(value, label, selected) {
            const item = doc.createElement('option');
            item.value = value;
            item.textContent = label;
            if (selected) item.selected = true;
            return item;
        }

        async function models(key) {
            const select = $('setupModel');
            if (!select) return;
            select.replaceChildren(option('', 'Loading models…'));
            const provider = state.providers.find(item => item.key === key) || {};
            say('setupWhere', whereIs(provider.base_url));
            let found = [];
            try { found = (await call(`/api/llm/providers/${encodeURIComponent(key)}/models`)).models || []; } catch (_) { found = []; }
            if (($('setupProvider')?.value || key) !== key) return;
            const current = provider.is_active ? state.activeModel : (provider.default_model || '');
            const ids = found.map(item => item.id).filter(Boolean);
            if (provider.is_active && current && !ids.length) ids.push(current);   // the server is not answering: show what is chosen
            select.replaceChildren(...(ids.length ? ids.map(id => option(id, id, id === current))
                : [option('', 'No models found: start the server and load a model')]));
            if (ids.length) select.value = ids.includes(current) ? current : ids[0];
            if ($('setupUse')) $('setupUse').disabled = !ids.length;
        }

        async function providers() {
            const select = $('setupProvider');
            if (!select) return;
            try {
                const data = await call('/api/llm/providers');
                state.providers = data.providers || [];
                state.activeModel = data.active_model || '';
            } catch (_) { state.providers = []; }
            if (!state.providers.length) {
                select.replaceChildren(option('', 'No providers configured'));
                if ($('setupUse')) $('setupUse').disabled = true;
                return;
            }
            const active = state.providers.find(item => item.is_active) || state.providers[0];
            select.replaceChildren(...state.providers.map(item => option(item.key, item.name || item.key, item.key === active.key)));
            select.value = active.key;
            await models(active.key);
        }

        async function use() {
            const provider = $('setupProvider')?.value, model = $('setupModel')?.value;
            if (!provider || !model || state.busy) return;
            if ($('setupUse')) $('setupUse').disabled = true;
            say('setupModelState', `Switching to ${model}…`);
            try {
                await call('/api/llm/switch', {method: 'POST', headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({provider, model})});
                state.activeModel = model;
                state.providers = state.providers.map(item => ({...item, is_active: item.key === provider}));
                await check();
            } catch (error) {
                render({ok: false, hint: 'That model could not be chosen. Try again.', detail: error.message || ''});
            } finally {
                if ($('setupUse')) $('setupUse').disabled = false;
            }
        }

        function render(result) {
            state.result = result;
            if (result.ok) {
                const seconds = Math.max(0.1, Math.round((result.ms || 0) / 100) / 10);
                say('setupModelState', `Ready — ${result.model} answered.`);
                say('setupModelDetail', `Test reply: “${result.reply}” (${seconds} s).`);
                say('setupModelNote', '');
            } else {
                say('setupModelState', 'Not ready yet.');
                say('setupModelDetail', result.hint || 'Choose a model above, then check again.');
                say('setupModelNote', result.detail || '');
            }
        }

        async function check() {
            if (state.busy) return;
            state.busy = true;
            if ($('setupCheck')) $('setupCheck').disabled = true;
            say('setupModelState', 'Checking the model… this can take up to a minute the first time.');
            say('setupModelDetail', '');
            say('setupModelNote', '');
            try {
                render(await call('/api/setup/check', {method: 'POST'}));
            } catch (error) {
                render({ok: false, hint: 'The check could not run. Try again in a moment.',
                    detail: error.message || ''});
            } finally {
                state.busy = false;
                if ($('setupCheck')) $('setupCheck').disabled = false;
            }
        }

        function row(piece, data) {
            const item = doc.createElement('li');
            const label = doc.createElement('strong');
            if (piece.link) {
                const link = doc.createElement('a');
                link.href = piece.link;
                link.target = '_blank';
                link.rel = 'noreferrer';
                link.textContent = piece.name;
                label.append(link);
            } else {
                label.textContent = piece.name;
            }
            const line = doc.createElement('span');
            const up = Boolean(data && piece.up(data));
            line.className = 'setup-state ' + (data ? (up ? 'up' : 'down') : 'down');
            line.textContent = data ? ` — ${up ? piece.on(data) : piece.off(data)}` : ' — Unknown right now.';
            const hint = doc.createElement('p');
            hint.className = 'home-muted';
            hint.textContent = piece.hint;
            item.append(label, line, hint);
            return item;
        }

        async function optional() {
            const list = $('setupOptional');
            if (!list) return;
            const found = await Promise.all(PIECES.map(async piece => {
                try { return [piece, await call(piece.url)]; } catch (_) { return [piece, null]; }
            }));
            list.replaceChildren(...found.map(([piece, data]) => row(piece, data)));
        }

        async function labOffer() {
            const useLab = $('setupLabUse');
            if (!useLab) return;
            try {
                const lab = await call('/api/voice/lab');
                useLab.hidden = lab.selected || !lab.ready;
                say('setupLabStatus', lab.selected && lab.ready
                    ? [lab.notice, 'Voice Lab is selected for speech.'].filter(Boolean).join(' ') : lab.message);
            } catch (_) {
                useLab.hidden = true;
                say('setupLabStatus', 'Could not check Voice Lab. You can try again from Settings.');
            }
        }

        async function useVoiceLab() {
            const useLab = $('setupLabUse');
            useLab.disabled = true;
            try {
                await call('/api/voice/engine', {method: 'POST', headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({engine: 'voicelab'})});
                useLab.hidden = true;
                say('setupLabStatus', 'Voice Lab is selected for speech.');
                await optional();
            } catch (error) {
                say('setupLabStatus', error.message);
            } finally {
                useLab.disabled = false;
            }
        }

        function name() { return ($('setupName')?.value || '').trim(); }

        async function save(then) {
            // Finishing does not wait for the model check: none of the pieces is required, and Skip must always work.
            if (state.saving) return;
            state.saving = true;
            if ($('setupDone')) $('setupDone').disabled = true;
            if ($('setupSkip')) $('setupSkip').disabled = true;
            say('setupStatus', 'Saving…');
            try {
                const body = name() ? {name: name()} : {};
                await call('/api/onboarding/complete', {method: 'POST', headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify(body)});
                navigate(then);
            } catch (error) {
                say('setupStatus', error.message || 'Setup could not be saved. Try again.');
                if ($('setupDone')) $('setupDone').disabled = false;
                if ($('setupSkip')) $('setupSkip').disabled = false;
                state.saving = false;
            }
        }

        const finish = () => save(checking ? '/' : '/home?make=pal');
        const skip = () => save('/home');

        async function loaded() {
            if (checking) {
                say('setupTitle', 'Setup check');
                say('setupIntro', 'Check that a model answers, and see which optional pieces are running.');
                if ($('setupDone')) $('setupDone').textContent = 'Done';
            }
            try {
                const data = await call('/api/onboarding/status');
                state.name = data.name || '';
                if (state.name && $('setupName') && !$('setupName').value) $('setupName').value = state.name;
            } catch (_) { /* the model check below still runs */ }
            await Promise.all([optional(), providers(), labOffer()]);
            await check();
            return state;
        }

        if ($('setupCheck')) $('setupCheck').addEventListener('click', check);
        if ($('setupUse')) $('setupUse').addEventListener('click', use);
        if ($('setupLabUse')) $('setupLabUse').addEventListener('click', useVoiceLab);
        if ($('setupProvider')) $('setupProvider').addEventListener('change', event => models(event.target.value));
        if ($('setupDone')) $('setupDone').addEventListener('click', finish);
        if ($('setupSkip')) $('setupSkip').addEventListener('click', skip);
        if ($('setupSkipLink')) $('setupSkipLink').addEventListener('click', event => { event.preventDefault(); return skip(); });
        if ($('setupName')) $('setupName').addEventListener('keydown', event => {
            if (event.key === 'Enter') { event.preventDefault(); return finish(); }
        });

        const ready = loaded();
        return {state, loaded: ready, check, optional, providers, use, finish, skip};
    }

    window.SetupPage = Object.freeze({start});
    if (typeof window.AlteraPage?.ready === 'function') window.AlteraPage.ready(() => start());
})();
