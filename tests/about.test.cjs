// About, and the π in its corner that opens adult mode's box (static/js/about.js).
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const source = fs.readFileSync('static/js/about.js', 'utf8');
const page = fs.readFileSync('templates/index.html', 'utf8');

function node() {
    const classes = new Set();
    return {style: {display: 'none'}, hidden: false, textContent: '', focused: false, focus() { this.focused = true; },
        classList: {add: name => classes.add(name), remove: name => classes.delete(name), contains: name => classes.has(name)}};
}

function load({kids = false, reduced = false} = {}) {
    const ids = ['settingsPanel', 'aboutOverlay', 'aboutPi', 'aboutClose', 'vaultOverlay', 'vaultScreen', 'vaultBody'];
    const byId = Object.fromEntries(ids.map(id => [id, node()]));
    byId.settingsPanel.classList.add('open');
    const keys = [];
    const timers = [];
    const body = node();
    if (kids) body.classList.add('kids-view');
    const root = {
        document: {getElementById: id => byId[id] || null, body, addEventListener: (type, fn) => { if (type === 'keydown') keys.push(fn); }},
        matchMedia: () => ({matches: reduced}),
        setTimeout: fn => { timers.push(fn); return timers.length; },
        clearTimeout: () => {},
        refreshed: 0,
    };
    root.refreshAdult = () => { root.refreshed += 1; };
    root.globalThis = root;
    vm.runInNewContext(source, root);
    const run = () => { while (timers.length) timers.shift()(); };
    return {root, byId, run, escape: () => keys.forEach(fn => fn({key: 'Escape'}))};
}

test('Settings ends with an underlined About link, and adult mode is no longer in Settings', () => {
    const settings = page.slice(page.indexOf('id="settingsPanel"'), page.indexOf('id="aboutOverlay"'));
    assert.match(settings, /id="aboutLink" data-click="openAbout">About</);
    // the one hint left is the accent note, which style.css shows only while a level's colours are on
    const shown = settings.replace(/<p class="setting-hint accent-mood-hint">[^<]*<\/p>/, '');
    assert.notEqual(shown, settings);
    assert.doesNotMatch(shown, /adult|18\+/i);
    const about = page.slice(page.indexOf('id="aboutOverlay"'), page.indexOf('id="vaultOverlay"'));
    assert.doesNotMatch(about.replace(/<!--[\s\S]*?-->/g, ''), /adult|18\+/i);           // the About box says nothing about it
    assert.match(about, /id="aboutPi" data-click="openVault" aria-label="π">π</);
    for (const id of ['adultToggle', 'adultLevel', 'privacyBlurToggle', 'adultStatus', 'adultPin', 'vaultLockAway']) {
        const at = page.indexOf(`id="${id}"`);
        assert.ok(at > page.indexOf('id="vaultOverlay"') && at < page.indexOf('id="adultConfirmOverlay"'), id);
    }
});

test("About describes Fel's Studio and links to all four apps", () => {
    const about = page.slice(page.indexOf('id="aboutOverlay"'), page.indexOf('id="vaultOverlay"'));
    assert.match(about, /Part of Fel's Studio/);
    assert.deepEqual(Array.from(about.matchAll(/<a href="([^"]+)"[^>]*>([^<]+)<\/a>/g),
        ([, url, label]) => [label, url]), [
        ['Voice Slate', 'https://github.com/Fel101/fels-voice-slate'],
        ['Voice Lab', 'https://github.com/Fel101/fels-voice-lab'],
        ['NPC Forge', 'https://github.com/Fel101/fels-npc-forge'],
        ['Altera', 'https://github.com/Fel101/Altera'],
    ]);
});

test('About closes Settings and opens; the π plays the intro, then shows the settings', () => {
    const {root, byId, run} = load();
    root.openAbout();
    assert.equal(byId.settingsPanel.classList.contains('open'), false);
    assert.equal(byId.aboutOverlay.style.display, 'flex');
    assert.equal(byId.aboutPi.hidden, false);
    root.openVault();
    assert.equal(byId.aboutOverlay.style.display, 'none');
    assert.equal(byId.vaultOverlay.style.display, 'flex');
    assert.equal(byId.vaultBody.hidden, true);                       // the settings wait for the intro
    assert.equal(root.refreshed, 1);
    run();
    assert.equal(byId.vaultBody.hidden, false);
    assert.match(byId.vaultScreen.textContent, /ACCESS GRANTED$/);
});

test('a click finishes the intro at once, and the badge or reduced motion skip it', () => {
    const first = load();
    first.root.openVault();
    first.root.finishVaultIntro();
    assert.equal(first.byId.vaultBody.hidden, false);
    assert.match(first.byId.vaultScreen.textContent, /ACCESS GRANTED$/);
    const quick = load();
    quick.root.openVault({quick: true});
    assert.equal(quick.byId.vaultBody.hidden, false);
    const calm = load({reduced: true});
    calm.root.openVault();
    assert.equal(calm.byId.vaultBody.hidden, false);
});

test('Escape closes both boxes', () => {
    const {root, byId, escape} = load();
    root.openVault({quick: true});
    escape();
    assert.equal(byId.vaultOverlay.style.display, 'none');
    root.openAbout();
    escape();
    assert.equal(byId.aboutOverlay.style.display, 'none');
});

test('the kids view has no π, and the box never opens there', () => {
    const {root, byId} = load({kids: true});
    root.openAbout();
    assert.equal(byId.aboutPi.hidden, true);
    root.openVault();
    assert.equal(byId.vaultOverlay.style.display, 'none');
    assert.equal(root.refreshed, 0);
});
