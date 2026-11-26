const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const source = fs.readFileSync('static/js/about.js', 'utf8');
const page = fs.readFileSync('templates/index.html', 'utf8');

test('About stays available while the core template has no optional content controls', () => {
    assert.match(page, /id="aboutLink" data-click="openAbout">About</);
    assert.doesNotMatch(page, /aboutPi|adultToggle|adultConfirmBox|vaultOverlay|18\+/i);
    assert.match(page, /\{\{ panel\.trigger \}\}/);
    assert.match(page, /\{\{ panel\.html \}\}/);
});

test("About describes Fel's Studio and links to all four apps", () => {
    const about = page.slice(page.indexOf('id="aboutOverlay"'), page.indexOf('<!-- Avatar picker -->'));
    assert.match(about, /Part of Fel's Studio/);
    assert.deepEqual(Array.from(about.matchAll(/<a href="([^"]+)"[^>]*>([^<]+)<\/a>/g),
        ([, url, label]) => [label, url]), [
        ['Voice Slate', 'https://github.com/Fel101/fels-voice-slate'],
        ['Voice Lab', 'https://github.com/Fel101/fels-voice-lab'],
        ['NPC Forge', 'https://github.com/Fel101/fels-npc-forge'],
        ['Altera', 'https://github.com/Fel101/fels-altera'],
    ]);
});

test('Opening About closes Settings, focuses Close and supports Escape', () => {
    let open = true, focused = false, keydown;
    const byId = {
        settingsPanel: {classList: {remove: () => { open = false; }}},
        aboutOverlay: {style: {display: 'none'}},
        aboutClose: {focus: () => { focused = true; }},
    };
    const root = {document: {getElementById: id => byId[id], addEventListener: (_, callback) => { keydown = callback; }}};
    vm.runInNewContext(source, root);
    root.openAbout();
    assert.equal(open, false);
    assert.equal(focused, true);
    assert.equal(byId.aboutOverlay.style.display, 'flex');
    keydown({key: 'Escape'});
    assert.equal(byId.aboutOverlay.style.display, 'none');
});
