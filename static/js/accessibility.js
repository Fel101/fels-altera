// Focus stays in the frontmost custom dialog. Native <dialog> keeps browser behavior.
(() => {
    let active = null, returnTo = null, inhibited = [];
    const closers = {settingsPanel: 'toggleSettings', notesOverlay: 'closeNotes', meetOverlay: 'closeMeetModal',
        aboutOverlay: 'closeAbout', avatarOverlay: 'closeAvatarModal', portraitView: 'closePortraitView', onboardingOverlay: 'skipOnboarding'};
    const visible = node => node.getClientRects().length && getComputedStyle(node).visibility !== 'hidden';
    const controls = node => [...node.querySelectorAll('button, a[href], input, select, textarea, [tabindex]')]
        .filter(el => !el.disabled && el.tabIndex >= 0 && !el.closest('[inert]') && visible(el));
    function restore() { for (const [node, was] of inhibited) node.inert = was; inhibited = []; }
    function sidebarState() {
        const sidebar = document.getElementById('sidebar');
        if (sidebar) sidebar.inert = window.matchMedia('(max-width: 768px)').matches && !sidebar.classList.contains('mobile-open');
    }
    function update() {
        const settings = document.getElementById('settingsPanel');
        if (settings) settings.inert = !settings.classList.contains('open');
        const shown = [...document.querySelectorAll('[role="dialog"]')].filter(node => node.tagName !== 'DIALOG' &&
            (node.id === 'settingsPanel' ? node.classList.contains('open') : visible(node)));
        const next = shown.at(-1) || null;
        if (next === active) { if (!active) sidebarState(); return; }
        restore();
        if (active && returnTo?.isConnected && !next) returnTo.focus();
        if (!active && next) returnTo = document.activeElement;
        active = next;
        if (!active) { sidebarState(); return; }
        for (let node = active; node.parentElement; node = node.parentElement) {
            for (const sibling of node.parentElement.children) {
                if (sibling === node || ['SCRIPT', 'STYLE', 'LINK', 'DIALOG'].includes(sibling.tagName)) continue;
                inhibited.push([sibling, sibling.inert]); sibling.inert = true;
            }
        }
        (controls(active)[0] || active).focus();
    }
    document.addEventListener('keydown', event => {
        if (document.querySelector('dialog[open]')) {
            if (event.key === 'Escape') event.stopImmediatePropagation();
            return;
        }
        if (!active) return;
        if (event.key === 'Escape' && closers[active.id]) {
            event.preventDefault(); event.stopImmediatePropagation(); window[closers[active.id]](); update();
        } else if (event.key === 'Tab') {
            const list = controls(active), index = list.indexOf(document.activeElement);
            if (!list.length) { event.preventDefault(); return; }
            if (event.shiftKey && index <= 0) { event.preventDefault(); list.at(-1).focus(); }
            else if (!event.shiftKey && (index < 0 || index === list.length - 1)) { event.preventDefault(); list[0].focus(); }
        }
    }, true);
    AlteraPage.ready(() => {
        const names = {voiceToggle: 'Speak replies', proactiveToggle: 'Proactive check-ins',
            proactiveInterval: 'Hours between check-ins', customBaseUrl: 'Custom provider base URL', customApiKey: 'Custom provider API key',
            customDefaultModel: 'Custom provider default model', notesAbout: 'Notes about you', notesStory: 'Notes about your story together'};
        for (const [id, name] of Object.entries(names)) document.getElementById(id)?.setAttribute('aria-label', name);
        new MutationObserver(update).observe(document.body, {subtree: true, attributes: true, attributeFilter: ['style', 'class', 'hidden']});
        window.addEventListener('resize', update);
        update();
    });
})();
