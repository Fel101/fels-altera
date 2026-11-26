(() => {
    const element = name => document.getElementById('updates' + name);
    const dialog = element('Dialog');
    if (!dialog) return;

    let timer;
    let generation = 0;
    let confirmUndo = false;
    let current;

    function render(state) {
        current = state;
        const {release, previous, phase, managed} = state;
        const busy = ['checking', 'downloading', 'restarting'].includes(phase);
        const size = release?.size;
        const instructions = [];
        if (state.notice) instructions.push(state.notice);
        if (release && size) instructions.push(`Version ${release.version} · about ${Math.ceil(size / 1e6)} MB`);
        if (release && (!managed || !size)) instructions.push('Open the releases page to see available downloads.');
        if (confirmUndo && previous) {
            instructions.push(`Return to version ${previous}? Your conversations, settings and backups stay where they are.`);
        }
        element('Status').textContent = state.message || `You have version ${state.current}.`;
        element('Details').textContent = instructions.join(' ');
        element('Check').disabled = busy;
        element('Download').hidden = busy || phase === 'downloaded' || !managed || !size;
        element('Install').hidden = phase !== 'downloaded';
        element('Stop').hidden = phase !== 'downloading';
        element('Undo').hidden = busy || !previous || confirmUndo;
        element('ConfirmUndo').hidden = busy || !previous || !confirmUndo;
        const progress = element('Progress');
        progress.hidden = phase !== 'downloading';
        if (size) {
            progress.max = size;
            progress.value = state.received;
        }
        const releases = element('Releases');
        releases.href = state.page;
        releases.hidden = !release;
        if (phase === 'checking' || phase === 'downloading') timer = setTimeout(refresh, 800);
    }

    async function refresh(action) {
        clearTimeout(timer);
        const requested = ++generation;
        try {
            const options = action ? {
                method: 'POST', headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({action})
            } : {cache: 'no-store'};
            const response = await fetch('/api/updates', options);
            const state = await response.json();
            if (!dialog.open || generation !== requested) return;
            if (!response.ok) throw new Error(state.error || 'Altera could not complete the update action.');
            render(state);
            return state;
        } catch (error) {
            if (dialog.open && generation === requested) {
                if (current) render(current);
                element('Status').textContent = error.message || 'Altera could not complete the update action.';
                element('Check').disabled = false;
            }
        }
    }

    element('Open').addEventListener('click', async () => {
        dialog.showModal();
        element('Status').textContent = 'Checking GitHub for a newer version…';
        element('Check').disabled = true;
        const state = await refresh();
        if (dialog.open && state?.phase === 'idle') refresh('check');
    });
    element('Check').addEventListener('click', () => refresh('check'));
    element('Download').addEventListener('click', () => refresh('download'));
    element('Stop').addEventListener('click', () => refresh('stop'));
    element('Install').addEventListener('click', () => refresh('install'));
    element('Undo').addEventListener('click', () => {
        confirmUndo = true;
        render(current);
        element('ConfirmUndo').focus();
    });
    element('ConfirmUndo').addEventListener('click', () => refresh('undo'));
    dialog.addEventListener('close', () => {
        clearTimeout(timer);
        generation++;
        confirmUndo = false;
    });
})();
