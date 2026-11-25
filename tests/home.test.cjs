const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const source = fs.readFileSync('static/js/home.js', 'utf8');
const template = fs.readFileSync('templates/home.html', 'utf8');
const tick = () => new Promise(resolve => setImmediate(resolve));

class Element {
    constructor(tag = 'div', attrs = {}) {
        this.tag = tag; this.children = []; this.listeners = {}; this.attributes = {...attrs}; this.dataset = {};
        this.textContent = ''; this.hidden = false; this.disabled = false; this.src = ''; this.value = ''; this.checked = false;
        this.open = false; this.className = attrs.class || '';
        this.classList = {toggle: (name, on) => { const names = new Set(this.className.split(' ').filter(Boolean)); on ? names.add(name) : names.delete(name); this.className = [...names].join(' '); },
            contains: name => this.className.split(' ').includes(name)};
    }
    set innerHTML(_) { throw new Error('The start screen must render text, never HTML'); }
    setAttribute(name, value) { this.attributes[name] = String(value); if (name === 'value') this.value = String(value); if (name === 'name') this.name = String(value); }
    addEventListener(name, callback) { (this.listeners[name] ||= []).push(callback); }
    append(...children) { this.children.push(...children); }
    replaceChildren(...children) { this.children = children; }
    showModal() { this.open = true; }
    close() { this.open = false; }
    focus() {}
    async fire(name) { for (const callback of this.listeners[name] || []) await callback({target: this, preventDefault() {}}); }
    click() { return this.fire('click'); }
}
const descendants = node => [node, ...node.children.flatMap(descendants)];
const text = node => descendants(node).map(item => item.textContent).filter(Boolean).join(' ');
const IDS = ['homeStatus', 'homeContinue', 'homeContinuePortrait', 'homeContinueName', 'homeContinueMeta', 'homeContinueButton',
    'homeGalleryTitle', 'homeShowAll', 'homeMakePal', 'homeImportCard', 'homeWriteCompanion', 'homeCardFile', 'homeKindNote', 'homeGallery',
    'palDialog', 'palForm', 'palName', 'palVibes', 'palVoice', 'palStatus', 'palCancel', 'palCreate',
    'palSettingsDialog', 'palSettingsForm', 'palSettingsTitle', 'palSettingsVibe', 'palSettingsName', 'palSettingsVoice',
    'palSettingsStatus', 'palSettingsCancel', 'palSettingsSave', 'palSettingsNameRow', 'palSettingsCardRow',
    'palSettingsInstructions', 'palSettingsNotesBox', 'palSettingsNotes', 'palSettingsKeeps',
    'palKidsRow', 'palKids', 'palSettingsKidsRow', 'palSettingsKidsSwitch', 'palSettingsKids', 'palSettingsKidsLocked',
    'palSettingsKidsAbout', 'palSettingsKidsPinRow', 'palSettingsKidsPinHint', 'palSettingsKidsPin',
    'kidsDialog', 'kidsForm', 'kidsUnderstoodRow', 'kidsUnderstood', 'kidsStatus', 'kidsCancel',
    'kidsLock', 'kidsEnable', 'kidsSetupFields', 'kidsChangeFields', 'kidsPin', 'kidsPinRepeat', 'kidsCurrentPin',
    'kidsNewPin', 'kidsNewPinRepeat', 'kidsChangePin', 'palVoiceSample', 'palSettingsVoiceSample',
    'palSettingsPortraitRow', 'palSettingsPortrait',
    'palSettingsPortraitFrom', 'palSettingsPortraitMake', 'palSettingsPortraitLife', 'palSettingsPortraitUndo', 'portraitDialog',
    'portraitForm', 'portraitTitle', 'portraitForge', 'portraitForgeStart', 'portraitFromRow', 'portraitFromCardRow',
    'portraitFromCard', 'portraitFromWords', 'portraitDescriptionRow', 'portraitDescription', 'portraitAskRow', 'portraitAsk',
    'portraitStyle', 'portraitSetting', 'portraitGender', 'portraitStatus', 'portraitClose', 'portraitStop', 'portraitPaint',
    'portraitImageRow', 'portraitImageModel', 'portraitEditRow', 'portraitEditModel', 'portraitEngineNote',
    'palSettingsLivingEngineRow', 'palSettingsLivingEngine', 'palSettingsLorebookRow', 'palSettingsLorebook',
    'palSettingsLorebookCount', 'palSettingsBookBox', 'palSettingsBook',
    'palSettingsFeaturesRow', 'palSettingsWrittenRow', 'palSettingsEditCard', 'palSettingsPortraitUpload',
    'palSettingsPortraitFile', 'palSettingsPortraitLibrary', 'libraryDialog', 'libraryGrid', 'libraryStatus', 'libraryClose',
    'companionDialog', 'companionForm', 'companionDialogTitle', 'companionName', 'companionAge', 'companionGender',
    'companionBio', 'companionHobbies', 'companionSpeech', 'companionDemeanor', 'companionQuirk', 'companionGoal',
    'companionPeeve', 'companionStatus', 'companionCancel', 'companionSubmit',
    'palSettingsVoiceRow', 'palSettingsRepliesRow', 'palSettingsTemperature', 'palSettingsLength', 'palSettingsRepliesHint',
    'palSettingsPersonaRow', 'palSettingsAbout', 'palSettingsTraits', 'palSettingsTone', 'palSettingsFormality',
    'palSettingsHumor', 'palSettingsEmojis', 'palSettingsInterests', 'palSettingsQuirks'];

function page() {
    const byId = Object.fromEntries(IDS.map(id => [id, new Element(/Button|ShowAll|MakePal|Cancel|Create|Save/.test(id) ? 'button' : 'div')]));
    byId.homeContinue.hidden = byId.homeShowAll.hidden = byId.homeKindNote.hidden = byId.homeMakePal.hidden = true;
    byId.homeImportCard.hidden = byId.homeWriteCompanion.hidden = true;
    byId.palSettingsCardRow.hidden = byId.palKidsRow.hidden = byId.palSettingsKidsRow.hidden = true;
    byId.palSettingsWrittenRow.hidden = byId.palSettingsPortraitUpload.hidden = true;
    byId.palSettingsFeaturesRow.hidden = false;
    byId.palSettingsPortraitFile.clicks = 0;
    byId.palSettingsPortraitFile.click = () => { byId.palSettingsPortraitFile.clicks += 1; };
    byId.palSettingsPortraitFile.files = [];
    byId.homeCardFile.clicks = 0; byId.homeCardFile.click = () => { byId.homeCardFile.clicks += 1; };
    byId.kidsSetupFields.hidden = false; byId.kidsChangeFields.hidden = true; byId.kidsChangePin.hidden = true;
    byId.portraitFromCard.value = 'card'; byId.portraitFromWords.value = 'description';
    const tiles = ['pal', 'character', 'companion'].map(kind => { const tile = new Element('button'); tile.dataset.kind = kind; return tile; });
    const counts = tiles.map(tile => { const label = new Element('span'); label.dataset.count = tile.dataset.kind; tile.append(label); return label; });
    const features = ['memory', 'notes', 'story', 'presence'].map(name => { const box = new Element('input'); box.dataset.feature = name; return box; });
    byId.palSettingsDialog.append(...features);
    const doc = {
        getElementById: id => byId[id],
        createElement: tag => new Element(tag),
        querySelectorAll: selector => {
            if (selector === '.home-tile') return tiles;
            if (selector === '[data-count]') return counts;
            if (selector === 'input[name="palVibe"]') return descendants(byId.palVibes).filter(item => item.name === 'palVibe');
            if (selector === '#palSettingsDialog [data-feature]') return features;
            if (selector === 'input[name="portraitFrom"]') return [byId.portraitFromCard, byId.portraitFromWords];
            return [...descendants(byId.homeGallery).filter(item => item.tag === 'button'), byId.homeContinueButton];
        },
    };
    return {doc, byId, tiles, counts, features};
}

class FakeForm { constructor() { this.parts = []; } append(name, value, filename) { this.parts.push([name, value, filename]); } }

function load() {
    const window = {};
    vm.runInNewContext(source, {window, URLSearchParams, encodeURIComponent, Object, Number, Date, String, Promise, JSON, FormData: FakeForm});
    return window.HomePage;
}

const people = [
    {id: 'mara-1a', name: 'Mara', kind: 'companion', messages: 4, last_at: '2026-11-09T10:00:00'},
    {id: 'pal-pip-2b', name: 'Pip', kind: 'pal', messages: 0, last_at: ''},
    {id: 'old-3c', name: 'Old <b>friend</b>', messages: 1, last_at: '2026-11-01T10:00:00'},     // saved before kinds
];
const CHARACTER = {id: 'chr-vale-4d', name: 'Captain Vale', voice: '', features: {memory: true, notes: true, story: true, presence: true},
    card_instructions: true, has_instructions: true, creator_notes: 'Best with <b>13B</b>.', creator: 'someone', spec: 'v2'};
const OPTIONS = {vibes: [{id: 'sunny', label: 'Sunny', description: 'Upbeat'}, {id: 'chill', label: 'Chill', description: 'Calm'}],
    voices: {engine: 'kokoro', default: 'Kokoro af_heart (local)', options: [{id: 'af_bella', label: 'Bella'}, {id: 'am_adam', label: 'Adam'}]}};

const LIBRARY = {available: true, characters: [
    {id: 'mara-bell-47ab97', name: 'Mara Bell', has_portrait: true, eligible: true, reason: ''},
    {id: 'kit-bell-222222', name: 'Kit Bell', has_portrait: true, eligible: false, reason: 'Avatars must be 18 or older (this one is 16).'},
    {id: 'lee-bell-111111', name: 'Lee Bell', has_portrait: false, eligible: false, reason: 'No portrait yet.'}]};
const PAL_PORTRAIT = {id: 'pal-pip-2b', kind: 'pal', name: 'Pip', from: 'none', card_picture: false, job: null, living: false,
    can_bring_to_life: false, description: '', gender: '', style: 'semi_real', setting: 'modern'};
const FORGE = {online: true, comfy_online: true, message: '', styles: [{id: 'semi_real', label: 'Semi-realistic'}, {id: 'anime', label: 'Anime'}],
    settings: [{id: 'modern', label: 'Modern'}, {id: 'fantasy', label: 'Fantasy'}],
    engines: {image: [{id: 'mage', label: 'Mage-Flow', note: 'About 20 s, light on memory.'}, {id: 'qwen2512', label: 'Qwen-Image 2512', note: 'Needs about 29 GB of free VRAM.'}],
              edit: [{id: 'mage', label: 'Mage-Flow-Edit', note: 'Fast.'}, {id: 'qwen2511', label: 'Qwen-Image-Edit 2511', note: 'Needs about 29 GB of free VRAM.'}]},
    default_engines: {image: 'qwen2512', edit: 'qwen2511'}};

function server({library = LIBRARY, character = CHARACTER, portrait = PAL_PORTRAIT, forge = FORGE, jobs = [], tts = {audio_url: '/audio/sample.wav'}, status = 200, body = {people, counts: {pal: 1, character: 0, companion: 2}, last: people[0]}, open = 200, make = 201,
                 kids = {enabled: false, enabled_at: null, pin_set: false, pals: []}, kidsChange = 200,
                 settings = {id: 'pal-pip-2b', name: 'Pip', vibe: 'sunny', vibe_label: 'Sunny', voice: 'am_adam', features: {memory: false, notes: true, story: false, presence: false}},
                 writtenSettings = {id: 'cpt-mira-1a', name: 'Mira', age: 34, gender: 'female', voice: '', features: {memory: true, notes: true, story: true, presence: true},
                     bio: 'A cartographer.', hobbies: ['maps'], speech: '', demeanor: '', quirk: '', goal: '', peeve: ''},
                 written = 201,
                 replies = {own: {temperature: null, max_tokens: null}, app: {temperature: 0.85, max_tokens: 1024}}, repliesStatus = 200} = {}) {
    const calls = [];
    const reply = (code, payload) => ({ok: code < 400, status: code, json: async () => payload});
    const fetchImpl = async (url, init = {}) => {
        calls.push({url, method: init.method || 'GET', body: init.body instanceof FakeForm ? init.body.parts : init.body ? JSON.parse(init.body) : undefined});
        if (url === '/api/people') return reply(status, body);
        if (url === '/api/kids') {
            if (init.method !== 'POST') return reply(200, kids);
            if (kidsChange >= 400) return reply(kidsChange, {error: 'Switch kids mode off for Pip first.'});
            const sent = JSON.parse(init.body);
            kids = {...kids, enabled: sent.enabled, enabled_at: '2026-11-10T12:00:00',
                pin_set: sent.enabled ? true : kids.pin_set};
            return reply(200, kids);
        }
        if (url === '/api/pals/options') return reply(200, OPTIONS);
        if (url === '/api/voice/tts') return reply(200, tts);
        if (url === '/api/forge/status') return reply(200, forge);
        if (url === '/api/avatars/forge') return reply(200, library);
        if (url.startsWith('/api/portraits/')) {
            const tail = url.split('/').slice(4).join('/');
            if (tail === 'job' && init.method === 'DELETE') { portrait = {...portrait, job: null}; return reply(200, portrait); }
            if (tail === 'job') { const next = jobs.shift() || {state: 'none'}; portrait = next.portrait || portrait; return reply(200, {portrait, ...next}); }
            if (tail === 'describe') return reply(200, {description: 'Silver hair, grey eyes.'});
            if (tail === 'library') { portrait = {...portrait, from: 'forge', can_bring_to_life: true}; return reply(200, portrait); }
            if (tail === 'living') { portrait = {...portrait, job: {kind: 'living'}, can_bring_to_life: false}; return reply(202, portrait); }
            if (tail === 'forge') { portrait = {...portrait, from: portrait.card_picture ? 'card' : 'none'}; return reply(200, portrait); }
            if (init.method === 'POST') { portrait = {...portrait, job: {kind: 'portrait'}}; return reply(202, portrait); }
            return reply(200, portrait);
        }
        if (url === '/api/pals') return reply(make, make < 400 ? {id: 'pal-new-000001', kind: 'pal'} : {error: 'Give your pal a name.'});
        if (url === '/api/companions' && init.method === 'POST') {
            return reply(written, written < 400 ? {id: 'cpt-new-000001', kind: 'companion', name: JSON.parse(init.body).name}
                : {error: 'Give their age (18 or older).'});
        }
        if (url.startsWith('/api/people/') && url.endsWith('/replies')) {
            if (init.method === 'PUT') replies = {...replies, own: {...replies.own, ...JSON.parse(init.body)}};
            return reply(repliesStatus, repliesStatus < 400 ? replies : {error: 'Temperature must be from 0 to 2.'});
        }
        if (url.startsWith('/api/companions/') && !url.includes('/open') && !url.includes('/notes') && !url.includes('/status')) {
            return reply(200, init.method === 'PUT' ? {...writtenSettings, ...JSON.parse(init.body)} : writtenSettings);
        }
        if (url.startsWith('/api/avatars/') && url.endsWith('/portrait') && init.method === 'POST') {
            return reply(200, {...PAL_PORTRAIT, id: 'cpt-mira-1a', kind: 'companion', from: 'upload'});
        }
        if (url.startsWith('/api/avatars/') && url.endsWith('/portrait') && init.method === 'DELETE') {
            return reply(200, {...portrait, from: 'none'});
        }
        if (url === '/api/characters/import') return reply(make, make < 400 ? {id: 'chr-new-000001', kind: 'character'} : {error: 'This PNG has no character card in it.'});
        if (url.startsWith('/api/characters/')) return reply(200, init.method === 'PUT' ? {...character, ...JSON.parse(init.body)} : character);
        if (url.startsWith('/api/pals/')) return reply(200, init.method === 'PUT' ? {...settings, ...JSON.parse(init.body)} : settings);
        return reply(open, open < 400 ? {id: 'x'} : {error: 'That companion was not found.'});
    };
    return {fetchImpl, calls};
}

const openButtons = gallery => descendants(gallery).filter(item => item.className === 'home-person');

test('the template has every element the script reads', () => {
    for (const id of IDS) assert.match(template, new RegExp(`id="${id}"`), id);
    for (const kind of ['pal', 'character', 'companion']) assert.match(template, new RegExp(`data-kind="${kind}"[\\s\\S]*?data-count="${kind}"`));
    for (const feature of ['memory', 'notes', 'story', 'presence']) assert.match(template, new RegExp(`data-feature="${feature}"`));
});

test('the start screen shows counts, who to continue with and everyone as text', async () => {
    const {doc, byId, counts} = page();
    const home = load().start({doc, fetchImpl: server().fetchImpl, navigate() {}});
    await home.loaded;
    assert.deepEqual(counts.map(label => label.textContent), ['1', 'None yet', '2']);
    assert.equal(byId.homeContinue.hidden, false);
    assert.equal(byId.homeContinueName.textContent, 'Mara');
    assert.equal(byId.homeContinueButton.textContent, 'Continue with Mara');
    assert.equal(byId.homeContinuePortrait.src, '/api/avatars/mara-1a/portrait');
    assert.match(byId.homeContinueMeta.textContent, /^Companion · 4 messages · last /);
    assert.equal(byId.homeGallery.children.length, 3);
    assert.match(text(byId.homeGallery), /Old <b>friend<\/b> Companion/);                 // untyped records read as companions, literally
    assert.match(text(byId.homeGallery), /Pip Pal Not talked yet/);
    assert.deepEqual(descendants(byId.homeGallery).filter(item => item.className === 'home-card-settings').map(b => b.attributes['aria-label']),
        ['Settings for Mara', 'Settings for Pip', 'Settings for Old <b>friend</b>']);    // everyone has settings now
    assert.equal(byId.homeStatus.textContent, '3 people');
});

test('a tile narrows the gallery, a second press or Show everyone widens it again', async () => {
    const {doc, byId, tiles} = page();
    const home = load().start({doc, fetchImpl: server().fetchImpl, navigate() {}});
    await home.loaded;
    await tiles[0].click();
    assert.equal(byId.homeGalleryTitle.textContent, 'Pals');
    assert.deepEqual(openButtons(byId.homeGallery).map(card => card.attributes['aria-label']), ['Open Pip']);
    assert.equal(tiles[0].attributes['aria-pressed'], 'true');
    assert.equal(byId.homeShowAll.hidden, false);
    assert.equal(byId.homeMakePal.hidden, false);
    await tiles[0].click();
    assert.equal(byId.homeGallery.children.length, 3);
    assert.equal(byId.homeMakePal.hidden, true);
    await tiles[1].click();
    assert.equal(byId.homeGallery.children.length, 0);
    assert.equal(byId.homeKindNote.hidden, false);
    assert.match(text(byId.homeKindNote), /No characters yet/);
    assert.deepEqual(descendants(byId.homeKindNote).filter(item => item.tag === 'button').map(b => b.textContent), ['Import a character card']);
    assert.equal(byId.homeImportCard.hidden, true);                                       // the note's own button is enough
    await byId.homeShowAll.click();
    assert.equal(byId.homeGalleryTitle.textContent, 'Everyone');
});

test('with nobody at all, both making a pal and meeting a companion are offered', async () => {
    const {doc, byId, tiles} = page();
    const body = {people: [], counts: {pal: 0, character: 0, companion: 0}, last: null};
    const home = load().start({doc, fetchImpl: server({body}).fetchImpl, navigate() {}});
    await home.loaded;
    assert.equal(byId.homeContinue.hidden, true);
    assert.equal(byId.homeStatus.textContent, 'Nobody here yet.');
    assert.deepEqual(descendants(byId.homeKindNote).filter(item => item.tag === 'button').map(b => b.textContent),
        ['Make a pal', 'Import a character card', 'Write a companion']);
    await tiles[2].click();
    const link = descendants(byId.homeKindNote).find(item => item.tag === 'a');
    assert.equal(link.attributes.href, '/?meet=1');
    await tiles[0].click();
    assert.match(text(byId.homeKindNote), /No pals yet/);
    assert.equal(byId.homeMakePal.hidden, true);                                           // the note's own button is enough
});

test('opening someone posts once and then goes to the chat', async () => {
    const {doc, byId} = page();
    const {fetchImpl, calls} = server();
    const visits = [];
    const home = load().start({doc, fetchImpl, navigate: url => visits.push(url)});
    await home.loaded;
    const pip = openButtons(byId.homeGallery)[1];
    await Promise.all([pip.click(), pip.click()]);
    assert.deepEqual(calls.filter(call => call.method === 'POST').map(call => call.url), ['/api/companions/pal-pip-2b/open']);
    assert.deepEqual(visits, ['/']);
});

test('a failed open says why and lets you try again', async () => {
    const {doc, byId} = page();
    const visits = [];
    const home = load().start({doc, fetchImpl: server({open: 404}).fetchImpl, navigate: url => visits.push(url)});
    await home.loaded;
    await byId.homeContinueButton.click();
    await tick();
    assert.equal(byId.homeStatus.textContent, 'That companion was not found.');
    assert.equal(byId.homeContinueButton.disabled, false);
    assert.deepEqual(visits, []);
});

test('a refused listing is reported and nothing is drawn', async () => {
    const {doc, byId} = page();
    const body = {error: 'The start screen is not available right now. Open the chat instead.'};
    const home = load().start({doc, fetchImpl: server({status: 409, body}).fetchImpl, navigate() {}});
    await home.loaded;
    assert.match(byId.homeStatus.textContent, /not available right now/);
    assert.equal(byId.homeGallery.children.length, 0);
    assert.equal(byId.homeContinue.hidden, true);
});

test('making a pal offers the vibes and voices, sends the choices and opens the chat', async () => {
    const {doc, byId} = page();
    const {fetchImpl, calls} = server();
    const visits = [];
    const home = load().start({doc, fetchImpl, navigate: url => visits.push(url)});
    await home.loaded;
    await home.openMakePal();
    assert.equal(byId.palDialog.open, true);
    const radios = descendants(byId.palVibes).filter(item => item.name === 'palVibe');
    assert.deepEqual(radios.map(r => [r.value, r.checked]), [['sunny', true], ['chill', false]]);
    assert.match(text(byId.palVibes), /Sunny Upbeat/);
    assert.deepEqual(byId.palVoice.children.map(o => o.textContent), ['Default: Kokoro af_heart (local)', 'Bella', 'Adam']);
    await home.makePal();
    assert.equal(byId.palStatus.textContent, 'Give your pal a name.');                     // nothing sent without a name
    byId.palName.value = '  Pip  '; radios[0].checked = false; radios[1].checked = true; byId.palVoice.value = 'am_adam';
    await byId.palForm.fire('submit');
    assert.deepEqual(calls.filter(call => call.method === 'POST'), [{url: '/api/pals', method: 'POST', body: {name: 'Pip', vibe: 'chill', voice: 'am_adam'}}]);
    assert.deepEqual(visits, ['/']);
});

test('the first run lands here with Make a pal already open', async () => {
    const {doc, byId} = page();
    const home = load().start({doc, fetchImpl: server().fetchImpl, navigate() {}, search: () => '?make=pal'});
    await home.loaded;
    await tick();
    await tick();
    assert.equal(byId.palDialog.open, true);
});

test('a refused pal says why and can be tried again', async () => {
    const {doc, byId} = page();
    const home = load().start({doc, fetchImpl: server({make: 400}).fetchImpl, navigate() { throw new Error('must not navigate'); }});
    await home.loaded;
    await home.openMakePal();
    byId.palName.value = 'Pip';
    await home.makePal();
    assert.equal(byId.palStatus.textContent, 'Give your pal a name.');
    assert.equal(byId.palCreate.disabled, false);
});

test('pal settings show what it keeps and save every switch with its name and voice', async () => {
    const {doc, byId, features} = page();
    const {fetchImpl, calls} = server();
    const home = load().start({doc, fetchImpl, navigate() {}});
    await home.loaded;
    const settings = settingsButton(byId);
    await settings.click();
    assert.equal(byId.palSettingsDialog.open, true);
    assert.equal(byId.palSettingsTitle.textContent, 'Pip settings');
    assert.equal(byId.palSettingsVibe.textContent, 'Vibe: Sunny');
    assert.equal(byId.palSettingsVoice.value, 'am_adam');
    assert.deepEqual(features.map(box => box.checked), [false, true, false, false]);
    features[0].checked = true; byId.palSettingsName.value = 'Pippa ';
    await byId.palSettingsForm.fire('submit');
    const put = calls.find(call => call.method === 'PUT');
    assert.deepEqual(put, {url: '/api/pals/pal-pip-2b', method: 'PUT',
        body: {name: 'Pippa', voice: 'am_adam', features: {memory: true, notes: true, story: false, presence: false}}});
    assert.equal(byId.palSettingsDialog.open, false);
    assert.equal(calls.filter(call => call.url === '/api/people').length, 2);              // the gallery is read again
});

test('importing a card opens the file picker, uploads the file and opens the chat', async () => {
    const {doc, byId, tiles} = page();
    const {fetchImpl, calls} = server();
    const visits = [];
    const home = load().start({doc, fetchImpl, navigate: url => visits.push(url)});
    await home.loaded;
    await tiles[1].click();
    const button = descendants(byId.homeKindNote).find(item => item.tag === 'button');
    await button.click();
    assert.equal(byId.homeCardFile.clicks, 1);
    const file = {name: 'vale.png'};
    byId.homeCardFile.files = [file];
    await byId.homeCardFile.fire('change');
    const post = calls.find(call => call.method === 'POST');
    assert.deepEqual(post, {url: '/api/characters/import', method: 'POST', body: [['file', file, 'vale.png']]});
    assert.deepEqual(visits, ['/']);
});

test('a refused card says why and nothing opens', async () => {
    const {doc, byId} = page();
    const home = load().start({doc, fetchImpl: server({make: 400}).fetchImpl, navigate() { throw new Error('must not navigate'); }});
    await home.loaded;
    await home.importCard({name: 'plain.png'});
    assert.equal(byId.homeStatus.textContent, 'This PNG has no character card in it.');
    assert.equal(home.state.busy, false);
});

test('character settings hide the name, show the card switch and the creator notes as text, and save them', async () => {
    const {doc, byId, features} = page();
    const {fetchImpl, calls} = server();
    const home = load().start({doc, fetchImpl, navigate() {}});
    await home.loaded;
    await home.openSettings({id: 'chr-vale-4d', name: 'Captain Vale', kind: 'character'});
    assert.ok(calls.some(call => call.url === '/api/characters/chr-vale-4d' && call.method === 'GET'));
    assert.equal(byId.palSettingsTitle.textContent, 'Captain Vale settings');
    assert.equal(byId.palSettingsNameRow.hidden, true);
    assert.equal(byId.palSettingsCardRow.hidden, false);
    assert.equal(byId.palSettingsKeeps.textContent, 'What this character keeps');
    assert.equal(byId.palSettingsVibe.textContent, 'Card by someone');
    assert.equal(byId.palSettingsInstructions.checked, true);
    assert.equal(byId.palSettingsInstructions.disabled, false);
    assert.equal(byId.palSettingsNotes.textContent, 'Best with <b>13B</b>.');                // text, never HTML
    assert.equal(byId.palSettingsNotesBox.hidden, false);
    assert.deepEqual(features.map(box => box.checked), [true, true, true, true]);
    byId.palSettingsInstructions.checked = false; features[3].checked = false;
    await byId.palSettingsForm.fire('submit');
    assert.deepEqual(calls.find(call => call.method === 'PUT'), {url: '/api/characters/chr-vale-4d', method: 'PUT',
        body: {voice: '', features: {memory: true, notes: true, story: true, presence: false}, card_instructions: false}});
});

const presence = features => features.find(box => box.dataset.feature === 'presence');

test('while kids mode is locked a pal shows how to set it up, and saving never sends it', async () => {
    const {doc, byId} = page();
    const {fetchImpl, calls} = server();
    const home = load().start({doc, fetchImpl, navigate() {}});
    await home.loaded;
    await home.openSettings({id: 'pal-pip-2b', name: 'Pip', kind: 'pal'});
    assert.equal(byId.palSettingsKidsRow.hidden, false);
    assert.equal(byId.palSettingsKidsSwitch.hidden, true);
    assert.equal(byId.palSettingsKidsLocked.hidden, false);
    assert.equal(byId.palSettingsKidsAbout.textContent, 'Set up kids mode…');
    await byId.palSettingsForm.fire('submit');
    assert.ok(!('kids' in calls.find(call => call.method === 'PUT').body));
});

test('setting kids mode up needs the guardrail box ticked, then unlocks the switch', async () => {
    const {doc, byId} = page();
    const {fetchImpl, calls} = server();
    const home = load().start({doc, fetchImpl, navigate() {}});
    await home.loaded;
    await home.openSettings({id: 'pal-pip-2b', name: 'Pip', kind: 'pal'});
    await byId.palSettingsKidsAbout.click();
    assert.equal(byId.kidsDialog.open, true);
    assert.equal(byId.palSettingsDialog.open, false);                                    // one dialog at a time
    assert.equal(byId.kidsUnderstoodRow.hidden, false);
    assert.equal(byId.kidsLock.hidden, true);
    await byId.kidsForm.fire('submit');
    assert.match(byId.kidsStatus.textContent, /Tick the box/);
    assert.ok(!calls.some(call => call.url === '/api/kids' && call.method === 'POST'));
    byId.kidsUnderstood.checked = true;
    byId.kidsPin.value = '1234'; byId.kidsPinRepeat.value = '1234';
    await byId.kidsForm.fire('submit');
    assert.deepEqual(calls.find(call => call.method === 'POST').body, {enabled: true, understood: true, pin: '1234'});
    assert.equal(byId.kidsDialog.open, false);
    assert.equal(byId.palSettingsDialog.open, true);                                     // back to the pal's settings
    assert.equal(byId.palSettingsKidsSwitch.hidden, false);
    assert.equal(byId.palSettingsKids.disabled, false);
    assert.equal(byId.palSettingsKidsLocked.hidden, true);
    assert.equal(byId.palSettingsKidsAbout.textContent, 'About kids mode');
    assert.equal(byId.palSettingsKidsPinRow.hidden, false);
});

test('a pal in kids mode keeps check-ins off, and the switch is saved with the settings', async () => {
    const {doc, byId, features} = page();
    const settings = {id: 'pal-pip-2b', name: 'Pip', vibe: 'sunny', vibe_label: 'Sunny', voice: '', kids: true,
        features: {memory: false, notes: false, story: false, presence: false}};
    const {fetchImpl, calls} = server({settings, kids: {enabled: true, enabled_at: '2026-11-10T12:00:00',
        pin_set: true, pals: ['Pip']}});
    const home = load().start({doc, fetchImpl, navigate() {}});
    await home.loaded;
    await home.openSettings({id: 'pal-pip-2b', name: 'Pip', kind: 'pal'});
    assert.equal(byId.palSettingsKids.checked, true);
    assert.equal(presence(features).disabled, true);
    byId.palSettingsKids.checked = false;
    await byId.palSettingsKids.fire('change');
    assert.equal(presence(features).disabled, false);                                    // off again: check-ins can be chosen
    byId.palSettingsKids.checked = true;
    presence(features).checked = true;
    await byId.palSettingsKids.fire('change');
    assert.equal(presence(features).checked, false);
    // The switch ends off, so the change is sent with the parent PIN.
    byId.palSettingsKids.checked = false;
    byId.palSettingsKidsPin.value = '1234';
    await byId.palSettingsForm.fire('submit');
    const put = calls.find(call => call.method === 'PUT').body;
    assert.equal(put.kids, false);
    assert.equal(put.pin, '1234');
    assert.equal(put.features.presence, false);
});

test('making a pal offers kids mode only once it is set up', async () => {
    const locked = page();
    const lockedHome = load().start({doc: locked.doc, fetchImpl: server().fetchImpl, navigate() {}});
    await lockedHome.loaded;
    await lockedHome.openMakePal();
    assert.equal(locked.byId.palKidsRow.hidden, true);

    const {doc, byId} = page();
    const {fetchImpl, calls} = server({kids: {enabled: true, enabled_at: '2026-11-10T12:00:00', pals: []}});
    const home = load().start({doc, fetchImpl, navigate() {}});
    await home.loaded;
    await home.openMakePal();
    assert.equal(byId.palKidsRow.hidden, false);
    byId.palName.value = 'Kit';
    byId.palKids.checked = true;
    await byId.palForm.fire('submit');
    assert.deepEqual(calls.find(call => call.url === '/api/pals' && call.method === 'POST').body,
        {name: 'Kit', vibe: 'sunny', voice: '', kids: true});
});

test('locking kids mode again says why when a pal still uses it', async () => {
    const {doc, byId} = page();
    const home = load().start({doc, fetchImpl: server({kids: {enabled: true, enabled_at: 'x', pin_set: true,
        pals: ['Pip']}, kidsChange: 409}).fetchImpl, navigate() {}});
    await home.loaded;
    await home.openSettings({id: 'pal-pip-2b', name: 'Pip', kind: 'pal'});
    await home.openKids();
    assert.equal(byId.kidsLock.hidden, false);
    assert.equal(byId.kidsEnable.hidden, true);
    byId.kidsCurrentPin.value = '1234';
    await byId.kidsLock.click();
    assert.equal(byId.kidsStatus.textContent, 'Switch kids mode off for Pip first.');
    assert.equal(byId.kidsDialog.open, true);
});

test('a pal in kids mode carries a Kids mode badge on Home', async () => {
    const {doc, byId} = page();
    const kidsPeople = [{id: 'pal-kit-1', name: 'Kit', kind: 'pal', kids: true, messages: 0, last_at: ''}, ...people];
    const home = load().start({doc, fetchImpl: server({body: {people: kidsPeople, counts: {pal: 2}, last: null}}).fetchImpl,
        navigate() {}});
    await home.loaded;
    const badges = descendants(byId.homeGallery).filter(item => item.className === 'home-badge kids');
    assert.deepEqual(badges.map(badge => badge.textContent), ['Kids mode']);
});

// ---------------------------------------------------------------- a voice sample and a portrait from NPC Forge
const settle = async () => { for (let i = 0; i < 12; i += 1) await tick(); };
const settingsButton = (byId, name = 'Pip') => descendants(byId.homeGallery)
    .find(item => item.className === 'home-card-settings' && item.attributes['aria-label'] === `Settings for ${name}`);

test('a voice can be heard before it is chosen, in the chosen voice or the default one', async () => {
    const {doc, byId} = page();
    const plays = [];
    const {fetchImpl, calls} = server();
    const home = load().start({doc, fetchImpl, navigate() {}, play: async url => { plays.push(url); }});
    await home.loaded;
    await home.openMakePal();
    byId.palName.value = 'Pip';
    byId.palVoice.value = 'af_bella';
    await byId.palVoiceSample.click();
    const samples = () => calls.filter(call => call.url === '/api/voice/tts').map(call => call.body);
    assert.deepEqual(samples(), [{text: "Hi, I'm Pip. This is how I sound.", voice: 'af_bella'}]);
    assert.deepEqual(plays, ['/audio/sample.wav']);
    byId.palVoice.value = '';
    await byId.palVoiceSample.click();
    assert.deepEqual(samples()[1], {text: "Hi, I'm Pip. This is how I sound."});               // the voice chosen in Settings
    const why = 'Michael is not installed in the voice service. Add it with …, or choose another.';
    const off = server({tts: {audio_url: null, message: why}});
    const quiet = load().start({doc, fetchImpl: off.fetchImpl, navigate() {}, play: async () => { throw new Error('played'); }});
    await quiet.loaded;
    await settingsButton(byId).click();
    await byId.palSettingsVoiceSample.click();
    assert.equal(byId.palSettingsStatus.textContent, why);                  // the server's reason, not a guess
    const bare = server({tts: {audio_url: null}});
    const older = load().start({doc, fetchImpl: bare.fetchImpl, navigate() {}, play: async () => { throw new Error('played'); }});
    await older.loaded;
    await settingsButton(byId).click();
    await byId.palSettingsVoiceSample.click();
    assert.equal(byId.palSettingsStatus.textContent, 'The voice could not speak just now. Try again.');
});

test('a pal takes a portrait from NPC Forge\'s library: only characters with a portrait, the ineligible ones say why', async () => {
    const {doc, byId} = page();
    const {fetchImpl, calls} = server({portrait: {...PAL_PORTRAIT, can_upload: true}});
    const home = load().start({doc, fetchImpl, navigate() {}, wait: async () => {}});
    await home.loaded;
    await settingsButton(byId).click();
    await settle();
    assert.equal(byId.palSettingsPortraitUpload.hidden, false);                    // a pal can have a picture uploaded too
    await byId.palSettingsPortraitLibrary.click();
    assert.equal(byId.palSettingsDialog.open, false);                              // one dialog at a time
    assert.equal(byId.libraryDialog.open, true);
    const tiles = byId.libraryGrid.children;
    assert.deepEqual(tiles.map(tile => text(tile)), ['Mara Bell', 'Kit Bell']);     // Lee has no portrait yet
    assert.equal(tiles[0].children[0].attributes.src, '/api/forge/mara-bell-47ab97/thumb');
    assert.equal(tiles[1].disabled, true);
    assert.equal(tiles[1].title, 'Avatars must be 18 or older (this one is 16).');
    await tiles[0].click();
    await settle();
    assert.deepEqual(calls.find(call => call.url.endsWith('/library')).body, {npc_id: 'mara-bell-47ab97'});
    assert.equal(byId.libraryDialog.open, false);
    assert.equal(byId.palSettingsDialog.open, true);
    assert.equal(byId.palSettingsPortraitFrom.textContent, "Mara Bell's portrait from NPC Forge's library is in.");
    assert.equal(byId.palSettingsPortraitLife.hidden, false);                      // it can be brought to life
});

test('an unavailable NPC Forge library tells the user how to connect', async () => {
    const {doc, byId} = page();
    const {fetchImpl} = server({library: {available: false, characters: []}});
    const home = load().start({doc, fetchImpl, navigate() {}, wait: async () => {}});
    await home.loaded;
    await settingsButton(byId).click();
    await settle();
    await byId.palSettingsPortraitLibrary.click();
    assert.equal(byId.libraryGrid.children.length, 0);
    assert.match(byId.libraryStatus.textContent, /Start NPC Forge with character card support/);
    await byId.libraryClose.click();
    assert.equal(byId.palSettingsDialog.open, true);
});

test('a pal gets a portrait from words: one dialog at a time, NPC Forge is waited on, then it can be removed', async () => {
    const {doc, byId} = page();
    const forged = {...PAL_PORTRAIT, from: 'forge', can_bring_to_life: true, description: 'Short red hair.'};
    const {fetchImpl, calls} = server({jobs: [{state: 'running', note: 'Portrait'}, {state: 'ready', portrait: forged}]});
    const home = load().start({doc, fetchImpl, navigate() {}, wait: async () => {}});
    await home.loaded;
    await settingsButton(byId).click();
    await settle();
    assert.equal(byId.palSettingsPortraitRow.hidden, false);
    assert.equal(byId.palSettingsPortraitFrom.textContent, 'No portrait yet, so Home shows the initial.');
    assert.equal(byId.palSettingsPortraitUndo.hidden, true);
    assert.equal(byId.palSettingsPortraitLife.hidden, true);
    await byId.palSettingsPortraitMake.click();
    assert.equal(byId.palSettingsDialog.open, false);
    assert.equal(byId.portraitDialog.open, true);
    assert.equal(byId.portraitFromCardRow.hidden, true);                                        // a pal has no card
    assert.equal(byId.portraitFromWords.checked, true);
    assert.equal(byId.portraitAskRow.hidden, true);
    assert.deepEqual(byId.portraitStyle.children.map(option => option.value), ['semi_real', 'anime']);
    assert.deepEqual(byId.portraitSetting.children.map(option => option.value), ['modern', 'fantasy']);
    assert.equal(byId.portraitPaint.disabled, false);
    await byId.portraitForm.fire('submit');
    assert.equal(byId.portraitStatus.textContent, 'Describe how they look first.');
    assert.equal(calls.some(call => call.url === '/api/portraits/pal-pip-2b' && call.method === 'POST'), false);
    byId.portraitDescription.value = ' Short red hair. ';
    byId.portraitStyle.value = 'anime';
    byId.portraitGender.value = 'female';
    await byId.portraitForm.fire('submit');
    const post = calls.find(call => call.url === '/api/portraits/pal-pip-2b' && call.method === 'POST');
    assert.deepEqual(post.body, {from: 'description', description: 'Short red hair.', style: 'anime', setting: 'modern', gender: 'female',
        image_model: '', edit_model: ''});                                                    // NPC Forge's own engines
    await settle();
    assert.equal(calls.filter(call => call.url === '/api/portraits/pal-pip-2b/job').length, 2);
    assert.equal(byId.portraitStatus.textContent, 'The new portrait is in.');
    assert.equal(calls.filter(call => call.url === '/api/people').length, 2);                  // Home shows the new picture
    await byId.portraitClose.click();
    assert.equal(byId.portraitDialog.open, false);
    assert.equal(byId.palSettingsDialog.open, true);                                            // back to the pal's settings
    assert.equal(byId.palSettingsPortraitUndo.hidden, false);
    assert.equal(byId.palSettingsPortraitUndo.textContent, 'Remove the portrait');
    assert.equal(byId.palSettingsPortraitLife.textContent, 'Bring it to life');
    await byId.palSettingsPortraitUndo.click();
    assert.ok(calls.some(call => call.url === '/api/portraits/pal-pip-2b/forge' && call.method === 'DELETE'));
    assert.equal(byId.palSettingsPortraitUndo.hidden, true);
});

test('a character is redrawn from its card by default, and the model can write how it looks', async () => {
    const {doc, byId} = page();
    const portrait = {...PAL_PORTRAIT, id: 'chr-vale-4d', kind: 'character', name: 'Captain Vale', from: 'card', card_picture: true,
        description: 'She has silver hair.', gender: 'female'};
    const {fetchImpl, calls} = server({portrait});
    const home = load().start({doc, fetchImpl, navigate() {}, wait: async () => {}});
    await home.loaded;
    await home.openSettings({id: 'chr-vale-4d', name: 'Captain Vale', kind: 'character'});
    await settle();
    assert.equal(byId.palSettingsPortraitFrom.textContent, "The card's picture.");
    await byId.palSettingsPortraitMake.click();
    assert.equal(byId.portraitTitle.textContent, 'A portrait of Captain Vale from NPC Forge');
    assert.equal(byId.portraitFromCardRow.hidden, false);
    assert.equal(byId.portraitFromCard.checked, true);
    assert.equal(byId.portraitDescription.value, 'She has silver hair.');
    assert.equal(byId.portraitGender.value, 'female');
    assert.equal(byId.portraitAskRow.hidden, true);                                            // the picture is drawn from
    byId.portraitFromCard.checked = false;
    byId.portraitFromWords.checked = true;
    await byId.portraitFromWords.fire('change');
    assert.equal(byId.portraitAskRow.hidden, false);
    await byId.portraitAsk.click();
    assert.equal(byId.portraitDescription.value, 'Silver hair, grey eyes.');
    assert.ok(calls.some(call => call.url === '/api/portraits/chr-vale-4d/describe' && call.method === 'POST'));
    byId.portraitFromCard.checked = true;
    byId.portraitFromWords.checked = false;
    await byId.portraitForm.fire('submit');
    assert.equal(calls.find(call => call.url === '/api/portraits/chr-vale-4d' && call.method === 'POST').body.from, 'card');
});

test('a finished living portrait says its frames are still being age-checked and copied in', async () => {
    const {doc, byId} = page();
    const forged = {...PAL_PORTRAIT, from: 'forge', can_bring_to_life: true, image_model: 'mage', edit_model: 'mage'};
    const copying = 'NPC Forge made the living portrait. Altera is age-checking its frames and copying them in.';
    const jobs = [{state: 'running', note: 'Expression: happy'}, {state: 'ready', note: copying}];
    const {fetchImpl} = server({portrait: forged, jobs});
    const home = load().start({doc, fetchImpl, navigate() {}, wait: async () => {}});
    await home.loaded;
    await settingsButton(byId).click();
    await settle();
    await byId.palSettingsPortraitLife.click();
    await settle();
    assert.equal(byId.portraitStatus.textContent, copying);                                 // not "The new portrait is in."
});

test('when NPC Forge is not running the dialog says so, offers to start it and cannot paint', async () => {
    const {doc, byId} = page();
    const forge = {online: false, comfy_online: null, message: 'NPC Forge is not running. Start it, then try again.', styles: [], settings: []};
    const {fetchImpl} = server({forge});
    const home = load().start({doc, fetchImpl, navigate() {}, wait: async () => {}});
    await home.loaded;
    await settingsButton(byId).click();
    await settle();
    await byId.palSettingsPortraitMake.click();
    assert.equal(byId.portraitForge.textContent, 'NPC Forge is not running. Start it, then try again.');
    assert.equal(byId.portraitForgeStart.hidden, false);
    assert.equal(byId.portraitPaint.disabled, true);
});

test('a new picture is asked for by its date, so Home never shows the old one', async () => {
    const {doc, byId} = page();
    const painted = [{...people[1], portrait_at: '2026-11-10T15:00:00'}];
    const home = load().start({doc, fetchImpl: server({body: {people: painted, counts: {pal: 1}, last: null}}).fetchImpl, navigate() {}});
    await home.loaded;
    const image = descendants(byId.homeGallery).find(item => item.className === 'home-portrait');
    assert.equal(image.src, '/api/avatars/pal-pip-2b/portrait?v=2026-11-10T15%3A00%3A00');
});

test('a lighter NPC Forge engine can be chosen for the picture, the redraw and the living portrait', async () => {
    const {doc, byId} = page();
    const forged = {...PAL_PORTRAIT, from: 'forge', can_bring_to_life: true, image_model: 'mage', edit_model: ''};
    const {fetchImpl, calls} = server({portrait: forged});
    const home = load().start({doc, fetchImpl, navigate() {}, wait: async () => {}});
    await home.loaded;
    await settingsButton(byId).click();
    await settle();
    assert.equal(byId.palSettingsLivingEngineRow.hidden, false);                             // beside Bring it to life
    assert.deepEqual(byId.palSettingsLivingEngine.children.map(o => [o.value, o.textContent]),
        [['', "NPC Forge's default (Qwen-Image-Edit 2511)"], ['mage', 'Mage-Flow-Edit'], ['qwen2511', 'Qwen-Image-Edit 2511']]);
    byId.palSettingsLivingEngine.value = 'mage';
    await byId.palSettingsPortraitLife.click();
    assert.deepEqual(calls.find(call => call.url === '/api/portraits/pal-pip-2b/living').body, {edit_model: 'mage'});

    await byId.palSettingsPortraitMake.click();
    assert.equal(byId.portraitImageModel.value, 'mage');                                      // the last choice comes back
    assert.equal(byId.portraitEditModel.value, '');
    assert.equal(byId.portraitImageRow.hidden, false);
    assert.equal(byId.portraitEngineNote.textContent, 'Mage-Flow: About 20 s, light on memory. The redraw engine also makes the living portrait.');
    byId.portraitImageModel.value = '';
    await byId.portraitImageModel.fire('change');
    assert.match(byId.portraitEngineNote.textContent, /^Qwen-Image 2512: Needs about 29 GB/);  // the default's own warning
    byId.portraitDescription.value = 'Red hair.';
    byId.portraitEditModel.value = 'mage';
    await byId.portraitForm.fire('submit');
    const post = calls.find(call => call.url === '/api/portraits/pal-pip-2b' && call.method === 'POST');
    assert.equal(post.body.image_model, '');
    assert.equal(post.body.edit_model, 'mage');
});

test('painting from the card hides the picture engine: only the redraw engine is used', async () => {
    const {doc, byId} = page();
    const portrait = {...PAL_PORTRAIT, id: 'chr-vale-4d', kind: 'character', name: 'Captain Vale', from: 'card', card_picture: true};
    const home = load().start({doc, fetchImpl: server({portrait}).fetchImpl, navigate() {}, wait: async () => {}});
    await home.loaded;
    await home.openSettings({id: 'chr-vale-4d', name: 'Captain Vale', kind: 'character'});
    await settle();
    assert.equal(byId.palSettingsLivingEngineRow.hidden, true);                              // nothing to bring to life yet
    await byId.palSettingsPortraitMake.click();
    assert.equal(byId.portraitImageRow.hidden, true);
    assert.match(byId.portraitEngineNote.textContent, /^Qwen-Image-Edit 2511: /);
});

// ---------------------------------------------------------------- a character's lorebook
test('a character with a lorebook lists its entries as text and saves the switch', async () => {
    const {doc, byId} = page();
    const book = {name: 'Kestrel lore', entries: [
        {name: 'The ship', keys: ['Kestrel'], constant: false, enabled: true, regex: false},
        {name: 'World', keys: [], constant: true, enabled: true, regex: false},
        {name: '<b>Gold</b>', keys: ['gold'], constant: false, enabled: false, regex: false},
        {name: 'Dragons', keys: ['/drag(on)/i'], constant: false, enabled: true, regex: true}]};
    const {fetchImpl, calls} = server({character: {...CHARACTER, lorebook: true, book}});
    const home = load().start({doc, fetchImpl, navigate() {}, wait: async () => {}});
    await home.loaded;
    await home.openSettings({id: 'chr-vale-4d', name: 'Captain Vale', kind: 'character'});
    assert.equal(byId.palSettingsLorebookRow.hidden, false);
    assert.equal(byId.palSettingsLorebook.checked, true);
    assert.equal(byId.palSettingsLorebookCount.textContent, "4 entries, 2 usable. Altera's own rules still apply.");
    assert.deepEqual(byId.palSettingsBook.children.map(item => [item.textContent, item.className]), [
        ['The ship · keys: Kestrel', ''], ['World · always on', ''],
        ['<b>Gold</b> · keys: gold · switched off in the card', 'off'],                     // text, never HTML
        ['Dragons · keys: /drag(on)/i · uses a regular expression, so Altera does not use it', 'off']]);
    byId.palSettingsLorebook.checked = false;
    await byId.palSettingsForm.fire('submit');
    assert.equal(calls.find(call => call.method === 'PUT').body.lorebook, false);
});

test('a character without a lorebook shows no switch and never sends one', async () => {
    const {doc, byId} = page();
    const {fetchImpl, calls} = server({character: {...CHARACTER, lorebook: true, book: {name: '', entries: []}}});
    const home = load().start({doc, fetchImpl, navigate() {}, wait: async () => {}});
    await home.loaded;
    await home.openSettings({id: 'chr-vale-4d', name: 'Captain Vale', kind: 'character'});
    assert.equal(byId.palSettingsLorebookRow.hidden, true);
    assert.equal(byId.palSettingsBookBox.hidden, true);
    await byId.palSettingsForm.fire('submit');
    assert.equal('lorebook' in calls.find(call => call.method === 'PUT').body, false);
});

// ---------------------------------------------------------------- a portrait's later age check
test('settings say a portrait is not age-checked yet, and that one was taken down', async () => {
    const {doc, byId} = page();
    const unchecked = {...PAL_PORTRAIT, from: 'forge', can_bring_to_life: true, age_checked: 'unchecked', removed: null};
    const home = load().start({doc, fetchImpl: server({portrait: unchecked}).fetchImpl, navigate() {}, wait: async () => {}});
    await home.loaded;
    await settingsButton(byId).click();
    await settle();
    assert.equal(byId.palSettingsPortraitFrom.textContent, 'From NPC Forge. Not age-checked yet: the model checks it when it '
        + 'is running, and takes it down if it looks under 18.');

    const again = page();
    const removed = {...PAL_PORTRAIT, age_checked: 'none', removed: {at: '2026-11-10T21:00:00', from: 'forge', apparent_age: 11}};
    const other = load().start({doc: again.doc, fetchImpl: server({portrait: removed}).fetchImpl, navigate() {}, wait: async () => {}});
    await other.loaded;
    await settingsButton(again.byId).click();
    await settle();
    assert.equal(again.byId.palSettingsPortraitFrom.textContent, 'No portrait yet, so Home shows the initial. '
        + 'The last portrait was taken down: the age check read it as about 11.');

    const third = page();
    const noAdult = {...removed, removed: {...removed.removed, from: 'card', apparent_age: 0}};   // nobody in the card's picture
    const last = load().start({doc: third.doc, fetchImpl: server({portrait: noAdult}).fetchImpl, navigate() {}, wait: async () => {}});
    await last.loaded;
    await settingsButton(third.byId).click();
    await settle();
    assert.equal(third.byId.palSettingsPortraitFrom.textContent, 'Home shows the initial. '
        + 'The last portrait was taken down: the age check could not see an adult in it.');
});

// ---------------------------------------------------------------- a companion written here
test('a companion can be written from Home: a name, an age and the card, then the chat opens', async () => {
    const {doc, byId} = page();
    const {fetchImpl, calls} = server();
    const visits = [];
    const home = load().start({doc, fetchImpl, navigate: url => visits.push(url)});
    await home.loaded;
    await home.openWriteCompanion();
    assert.equal(byId.companionDialog.open, true);
    await byId.companionForm.fire('submit');
    assert.equal(byId.companionStatus.textContent, 'Give your companion a name.');
    byId.companionName.value = 'Mira';
    await byId.companionForm.fire('submit');
    assert.equal(byId.companionStatus.textContent, 'Give their age (18 or older).');
    byId.companionAge.value = '34';
    byId.companionGender.value = 'female';
    byId.companionBio.value = ' A cartographer. ';
    byId.companionHobbies.value = 'maps, tea';
    byId.companionSpeech.value = 'dry and warm';
    byId.companionDemeanor.value = 'calm';
    await byId.companionForm.fire('submit');
    await settle();
    const post = calls.find(call => call.url === '/api/companions' && call.method === 'POST');
    assert.deepEqual(post.body, {name: 'Mira', age: '34', gender: 'female', bio: 'A cartographer.', hobbies: ['maps', 'tea'],
        speech: 'dry and warm', demeanor: 'calm', quirk: '', goal: '', peeve: ''});
    assert.deepEqual(visits, ['/']);
});

test('a companion settings page says it was written here, hides what it always keeps, and can edit the card', async () => {
    const {doc, byId} = page();
    const {fetchImpl, calls} = server({portrait: {...PAL_PORTRAIT, id: 'cpt-mira-1a', kind: 'companion', can_upload: true}});
    const home = load().start({doc, fetchImpl, navigate() {}});
    await home.loaded;
    await home.openSettings({id: 'cpt-mira-1a', name: 'Mira', kind: 'companion'});
    await settle();
    assert.equal(byId.palSettingsTitle.textContent, 'Mira settings');
    assert.equal(byId.palSettingsVibe.textContent, 'Written here, without NPC Forge.');
    assert.equal(byId.palSettingsFeaturesRow.hidden, true);                     // a companion keeps all four
    assert.equal(byId.palSettingsWrittenRow.hidden, false);
    assert.equal(byId.palSettingsPortraitUpload.hidden, false);
    assert.equal(byId.palSettingsName.value, 'Mira');
    byId.palSettingsName.value = 'Mira the Elder';
    await byId.palSettingsForm.fire('submit');
    await settle();
    assert.deepEqual(calls.find(call => call.method === 'PUT'), {url: '/api/companions/cpt-mira-1a', method: 'PUT',
        body: {name: 'Mira the Elder', voice: ''}});

    await home.openSettings({id: 'cpt-mira-1a', name: 'Mira', kind: 'companion'});
    await settle();
    await byId.palSettingsEditCard.click();
    await settle();
    assert.equal(byId.palSettingsDialog.open, false);
    assert.equal(byId.companionDialog.open, true);
    assert.equal(byId.companionDialogTitle.textContent, 'Edit Mira');
    assert.equal(byId.companionBio.value, 'A cartographer.');
    byId.companionBio.value = 'A cartographer of the north.';
    await byId.companionForm.fire('submit');
    await settle();
    assert.equal(byId.companionDialog.open, false);
    assert.equal(calls.filter(call => call.method === 'PUT').pop().body.bio, 'A cartographer of the north.');
});

test('a companion settings page can upload a portrait, then take it away again', async () => {
    const {doc, byId} = page();
    const portrait = {...PAL_PORTRAIT, id: 'cpt-mira-1a', kind: 'companion', from: 'none', can_upload: true};
    const {fetchImpl, calls} = server({portrait});
    const home = load().start({doc, fetchImpl, navigate() {}, wait: async () => {}});
    await home.loaded;
    await home.openSettings({id: 'cpt-mira-1a', name: 'Mira', kind: 'companion'});
    await settle();
    assert.equal(byId.palSettingsPortraitFrom.textContent,
        'No portrait yet, so Home shows the initial. Upload a picture or make one in NPC Forge.');
    assert.equal(byId.palSettingsPortraitUndo.hidden, true);
    await byId.palSettingsPortraitUpload.click();
    assert.equal(byId.palSettingsPortraitFile.clicks, 1);
    byId.palSettingsPortraitFile.files = [{name: 'mira.png'}];
    await byId.palSettingsPortraitFile.fire('change');
    await settle();
    const upload = calls.find(call => call.url === '/api/avatars/cpt-mira-1a/portrait' && call.method === 'POST');
    assert.ok(upload && Array.isArray(upload.body) && upload.body[0][0] === 'file');
    // The fake server says the picture is in; the settings read the portrait again.
    assert.equal(calls.filter(call => call.url === '/api/portraits/cpt-mira-1a').length, 2);
});

test('a companion with no portrait says that pictures need one, and the portrait asks for a picture or NPC Forge', async () => {
    const {doc, byId} = page();
    const portrait = {...PAL_PORTRAIT, id: 'cpt-mira-1a', kind: 'companion', from: 'none', can_upload: true, can_bring_to_life: false};
    const {fetchImpl} = server({portrait});
    const home = load().start({doc, fetchImpl, navigate() {}, wait: async () => {}});
    await home.loaded;
    await home.openSettings({id: 'cpt-mira-1a', name: 'Mira', kind: 'companion'});
    await settle();
    assert.equal(byId.palSettingsPortraitUndo.hidden, true);
    assert.equal(byId.palSettingsPortraitLife.hidden, true);
    await byId.palSettingsPortraitMake.click();
    await settle();
    assert.equal(byId.portraitFromCardRow.hidden, true);                        // there is no card picture
    assert.equal(byId.portraitAskRow.hidden, false);                            // the background can be described
});

test('a person can have their own temperature and reply length, and only what changed is sent', async () => {
    const {doc, byId} = page();
    const {fetchImpl, calls} = server({replies: {own: {temperature: 1.1, max_tokens: null}, app: {temperature: 0.85, max_tokens: 1024}}});
    const home = load().start({doc, fetchImpl, navigate() {}});
    await home.loaded;
    await home.openSettings({id: 'pal-pip-2b', name: 'Pip', kind: 'pal'});
    assert.equal(byId.palSettingsTemperature.value, 1.1);
    assert.equal(byId.palSettingsLength.value, '');                              // empty: the app's setting
    assert.equal(byId.palSettingsLength.placeholder, 'App: 1024');
    assert.match(byId.palSettingsRepliesHint.textContent, /temperature 0\.85, 1024 tokens/);
    await byId.palSettingsForm.fire('submit');
    assert.ok(!calls.some(call => call.url.endsWith('/replies') && call.method === 'PUT'));   // nothing changed: nothing sent

    await home.openSettings({id: 'pal-pip-2b', name: 'Pip', kind: 'pal'});
    byId.palSettingsTemperature.value = '';
    byId.palSettingsLength.value = '300';
    await byId.palSettingsForm.fire('submit');
    assert.deepEqual(calls.find(call => call.url === '/api/people/pal-pip-2b/replies' && call.method === 'PUT').body,
        {temperature: null, max_tokens: 300});
});

test('a reply setting that is not a number is caught before anything is saved', async () => {
    const {doc, byId} = page();
    const {fetchImpl, calls} = server();
    const home = load().start({doc, fetchImpl, navigate() {}});
    await home.loaded;
    await home.openSettings({id: 'pal-pip-2b', name: 'Pip', kind: 'pal'});
    byId.palSettingsTemperature.value = 'warm';
    await byId.palSettingsForm.fire('submit');
    assert.equal(byId.palSettingsStatus.textContent, 'Temperature must be a number.');
    assert.ok(!calls.some(call => call.method === 'PUT'));
});

test('a companion from NPC Forge opens its settings, offers only its replies, and saves only those', async () => {
    const {doc, byId} = page();
    const {fetchImpl, calls} = server({writtenSettings: {id: 'mara-1a', name: 'Mara', voice: '', written: false,
        features: {memory: true, notes: true, story: true, presence: true}}});
    const home = load().start({doc, fetchImpl, navigate() {}});
    await home.loaded;
    await home.openSettings({id: 'mara-1a', name: 'Mara', kind: 'companion'});
    await settle();
    assert.equal(byId.palSettingsStatus.textContent, '');
    assert.equal(byId.palSettingsVibe.textContent, 'Made in NPC Forge: change who they are there.');
    assert.equal(byId.palSettingsNameRow.hidden, true);
    assert.equal(byId.palSettingsVoiceRow.hidden, true);
    assert.equal(byId.palSettingsWrittenRow.hidden, true);
    assert.equal(byId.palSettingsRepliesRow.hidden, false);
    byId.palSettingsTemperature.value = '0.6';
    await byId.palSettingsForm.fire('submit');
    await settle();
    const puts = calls.filter(call => call.method === 'PUT');
    assert.deepEqual(puts.map(call => call.url), ['/api/people/mara-1a/replies']);
    assert.deepEqual(puts[0].body, {temperature: 0.6});
    assert.equal(byId.palSettingsDialog.open, false);
});

const PIP = {id: 'pal-pip-2b', name: 'Pip', vibe: 'sunny', vibe_label: 'Sunny', voice: '', kids: false,
    features: {memory: false, notes: false, story: false, presence: false},
    about: 'Upbeat and encouraging.', traits: ['cheerful', 'warm'],
    speech_style: {tone: 'bright and friendly', formality: 'casual', humor: 'light jokes', emojis: true},
    interests: ['your wins, big and small'], quirks: ['celebrates small victories']};

test('a pal shows who it is, one line per trait, and a companion or a character does not', async () => {
    const {doc, byId} = page();
    const {fetchImpl} = server({settings: PIP});
    const home = load().start({doc, fetchImpl, navigate() {}});
    await home.loaded;
    await home.openSettings({id: 'pal-pip-2b', name: 'Pip', kind: 'pal'});
    assert.equal(byId.palSettingsPersonaRow.hidden, false);
    assert.equal(byId.palSettingsAbout.value, 'Upbeat and encouraging.');
    assert.equal(byId.palSettingsTraits.value, 'cheerful\nwarm');
    assert.equal(byId.palSettingsInterests.value, 'your wins, big and small');           // a comma stays inside one line
    assert.equal(byId.palSettingsTone.value, 'bright and friendly');
    assert.equal(byId.palSettingsEmojis.checked, true);
    await home.openSettings({id: 'chr-vale-4d', name: 'Captain Vale', kind: 'character'});
    assert.equal(byId.palSettingsPersonaRow.hidden, true);
    await home.openSettings({id: 'cpt-mira-1a', name: 'Mira', kind: 'companion'});
    await settle();
    assert.equal(byId.palSettingsPersonaRow.hidden, true);
});

test('saving a pal sends who it is only when it changed, as lists without empty lines', async () => {
    const {doc, byId} = page();
    const {fetchImpl, calls} = server({settings: PIP});
    const home = load().start({doc, fetchImpl, navigate() {}});
    await home.loaded;
    await home.openSettings({id: 'pal-pip-2b', name: 'Pip', kind: 'pal'});
    await byId.palSettingsForm.fire('submit');
    assert.ok(!('persona' in calls.filter(call => call.method === 'PUT').pop().body));

    await home.openSettings({id: 'pal-pip-2b', name: 'Pip', kind: 'pal'});
    byId.palSettingsTraits.value = 'cheerful\n\n  curious  \nwarm';
    byId.palSettingsEmojis.checked = false;
    await byId.palSettingsForm.fire('submit');
    assert.deepEqual(calls.filter(call => call.method === 'PUT').pop().body.persona, {
        about: 'Upbeat and encouraging.', traits: ['cheerful', 'curious', 'warm'],
        speech_style: {tone: 'bright and friendly', formality: 'casual', humor: 'light jokes', emojis: false},
        interests: ['your wins, big and small'], quirks: ['celebrates small victories']});
});
