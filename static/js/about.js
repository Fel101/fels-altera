// About is available from the Settings panel.
(function (root, factory) {
    'use strict';
    const api = factory(root);
    if (typeof module === 'object' && module.exports) module.exports = api;
    else Object.assign(root, api.globals);
})(typeof globalThis !== 'undefined' ? globalThis : this, function (root) {
    'use strict';
    const byId = id => root.document.getElementById(id);
    function openAbout() {
        byId('settingsPanel')?.classList.remove('open');
        byId('aboutOverlay').style.display = 'flex';
        byId('aboutClose')?.focus();
    }
    function closeAbout() {
        const node = byId('aboutOverlay');
        if (node) node.style.display = 'none';
    }
    root.document?.addEventListener('keydown', event => {
        if (event.key === 'Escape') closeAbout();
    });
    return {globals: {openAbout, closeAbout}};
});
