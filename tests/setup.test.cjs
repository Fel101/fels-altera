const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const source = fs.readFileSync('static/js/setup.js', 'utf8');
const template = fs.readFileSync('templates/setup.html', 'utf8');
const tick = () => new Promise(resolve => setImmediate(resolve));

class Element {
    constructor(tag = 'div') {
        this.tag = tag; this.children = []; this.listeners = {}; this.attributes = {}; this.dataset = {};
        this.textContent = ''; this.value = ''; this.hidden = false; this.disabled = false; this.className = '';
    }
    set innerHTML(_) { throw new Error('The setup page must render text, never HTML'); }
    setAttribute(name, value) { this.attributes[name] = String(value); }
    addEventListener(name, callback) { (this.listeners[name] ||= []).push(callback); }
    append(...nodes) { this.children.push(...nodes); }
    replaceChildren(...nodes) { this.children = nodes; }
    focus() {}
    async fire(name, event = {}) { for (const callback of this.listeners[name] || []) await callback({target: this, preventDefault() {}, ...event}); }
    click() { return this.fire('click'); }
}
const text = node => [node.textContent, ...node.children.flatMap(text)].filter(Boolean).join(' ');

const IDS = ['setupTitle', 'setupIntro', 'setupName', 'setupModelState', 'setupModelDetail', 'setupModelNote',
    'setupCheck', 'setupOptional', 'setupStatus', 'setupSkip', 'setupDone', 'setupSkipLink',
    'setupProvider', 'setupModel', 'setupUse', 'setupWhere'];

function page() {
    const byId = Object.fromEntries(IDS.map(id => [id, new Element(/^(setup(Name|Check|Done|Skip)$)/.test(id) ? 'button' : 'div')]));
    byId.setupName.tag = 'input';
    const doc = {
        getElementById: id => byId[id] || null,
        createElement: tag => new Element(tag),
        querySelectorAll: () => [],
    };
    return {doc, byId};
}

function load() {
    const window = {};
    vm.runInNewContext(source, {window, URLSearchParams, Object, Math, String, Promise, JSON});
    return window.SetupPage;
}

function server({check = {ok: true, model: 'local-chat', reply: 'OK', ms: 320},
                 status = {complete: false, name: ''}, voice = {tts_enabled: false},
                 forge = {online: true, comfy_online: true}, comfy = {online: false}, complete = 200} = {}) {
    const calls = [];
    const reply = (code, payload) => ({ok: code < 400, status: code, json: async () => payload});
    const fetchImpl = async (url, init = {}) => {
        calls.push({url, method: init.method || 'GET', body: init.body ? JSON.parse(init.body) : undefined});
        if (url === '/api/onboarding/status') return reply(200, status);
        if (url === '/api/setup/check') return reply(200, check);
        if (url === '/api/voice/status') return reply(200, voice);
        if (url === '/api/forge/status') return reply(200, forge);
        if (url === '/api/imagegen/status') return reply(200, {comfy});
        if (url === '/api/onboarding/complete') return reply(complete, complete < 400 ? {status: 'ok'} : {error: 'Synthetic refusal'});
        return reply(404, {error: 'Not found.'});
    };
    return {fetchImpl, calls};
}

test('the template has every element the script reads', () => {
    for (const id of IDS) assert.match(template, new RegExp(`id="${id}"`), id);
    assert.match(template, /js\/setup\.js/);
});

test('the saved name comes back and a model that answers is reported as a test', async () => {
    const {doc, byId} = page();
    const {fetchImpl} = server({status: {complete: false, name: 'Robin'}});
    const setup = load().start({doc, fetchImpl, navigate() {}, search: () => ''});
    await setup.loaded;
    assert.equal(byId.setupName.value, 'Robin');
    assert.equal(byId.setupModelState.textContent, 'Ready — local-chat answered.');
    assert.match(byId.setupModelDetail.textContent, /Test reply: “OK” \(0.3 s\)/);
    assert.equal(byId.setupModelNote.textContent, '');
});

test('a model that is not reachable says what to start, with the reason underneath', async () => {
    const {doc, byId} = page();
    const check = {ok: false, reason: 'unreachable', detail: 'connection refused',
        hint: 'Start LM Studio and load a chat model, or start Ollama.'};
    const {fetchImpl} = server({check});
    const setup = load().start({doc, fetchImpl, navigate() {}, search: () => ''});
    await setup.loaded;
    assert.equal(byId.setupModelState.textContent, 'Not ready yet.');
    assert.match(byId.setupModelDetail.textContent, /Start LM Studio and load a chat model, or start Ollama/);
    assert.match(byId.setupModelNote.textContent, /connection refused/);
});

test('the check can be run again without reloading the page', async () => {
    const {doc, byId} = page();
    const {fetchImpl, calls} = server();
    const setup = load().start({doc, fetchImpl, navigate() {}, search: () => ''});
    await setup.loaded;
    assert.equal(calls.filter(call => call.url === '/api/setup/check').length, 1);
    await byId.setupCheck.click();
    assert.equal(calls.filter(call => call.url === '/api/setup/check').length, 2);
    assert.equal(byId.setupCheck.disabled, false);
});

test('the optional pieces are listed with what is up, and none of them blocks', async () => {
    const {doc, byId} = page();
    const {fetchImpl} = server({voice: {tts_enabled: false}, forge: {online: true, comfy_online: false, message: ''},
        comfy: {online: false}});
    const setup = load().start({doc, fetchImpl, navigate() {}, search: () => ''});
    await setup.loaded;
    assert.equal(byId.setupOptional.children.length, 3);
    assert.match(text(byId.setupOptional.children[0]), /Speech\s+— Off/);
    assert.match(text(byId.setupOptional.children[1]), /NPC Forge\s+— Running, but ComfyUI is not reachable/);
    assert.match(text(byId.setupOptional.children[2]), /ComfyUI\s+— Not running/);
    assert.match(template, /Planned support for <a href="https:\/\/github\.com\/Fel101\/fels-voice-lab"[^>]*>Voice Lab<\/a> will let you give each pal a voice/);
    assert.equal(byId.setupDone.disabled, false);
});

test('NPC Forge that is not running is just "Not running", never an instruction to start a tool most people lack', async () => {
    const {doc, byId} = page();
    const {fetchImpl} = server({forge: {online: false, comfy_online: null, not_running: true,
        message: 'NPC Forge is not running. Start it, then try again.'}});
    const setup = load().start({doc, fetchImpl, navigate() {}, search: () => ''});
    await setup.loaded;
    const forge = byId.setupOptional.children[1];
    const link = forge.children[0].children[0];
    assert.equal(link?.tag, 'a');
    assert.equal(link.textContent, 'NPC Forge');
    assert.equal(link.href, 'https://github.com/Fel101/fels-npc-forge');
    assert.match(text(forge), /NPC Forge\s+— Not running\./);
    assert.match(text(forge), /paints portraits from descriptions and creates characters with personalities/i);
    assert.doesNotMatch(text(forge), /Start it/);
    assert.match(text(byId.setupOptional.children[2]), /pictures in the chat need it/);
});

test('Setup reports a different app at the NPC Forge address', async () => {
    const {doc, byId} = page();
    const {fetchImpl} = server({forge: {online: false, message: 'Voice Lab answered at the NPC Forge address.'}});
    const setup = load().start({doc, fetchImpl, navigate() {}, search: () => ''});
    await setup.loaded;
    assert.match(text(byId.setupOptional.children[1]), /Voice Lab answered/);
    assert.doesNotMatch(text(byId.setupOptional.children[1]), /Not running/);
});

test('an optional piece that cannot be read says so and still lets the first run finish', async () => {
    const {doc, byId} = page();
    const {fetchImpl} = server({voice: {tts_enabled: true}});
    const original = fetchImpl;
    const broken = async (url, init) => (url === '/api/forge/status' ? {ok: false, status: 500, json: async () => ({})}
        : original(url, init));
    const setup = load().start({doc, fetchImpl: broken, navigate() {}, search: () => ''});
    await setup.loaded;
    assert.match(text(byId.setupOptional.children[1]), /NPC Forge\s+— Unknown right now/);
    assert.match(text(byId.setupOptional.children[0]), /Speech\s+— Ready/);
    assert.equal(byId.setupDone.disabled, false);
});

test('finishing saves the name and ends on Home with Make a pal', async () => {
    const {doc, byId} = page();
    const {fetchImpl, calls} = server();
    const visits = [];
    const setup = load().start({doc, fetchImpl, navigate: url => visits.push(url), search: () => ''});
    await setup.loaded;
    byId.setupName.value = '  Robin  ';
    await byId.setupDone.click();
    assert.deepEqual(calls.find(call => call.url === '/api/onboarding/complete').body, {name: 'Robin'});
    assert.deepEqual(visits, ['/home?make=pal']);
});

test('the name can be submitted with Enter', async () => {
    const {doc, byId} = page();
    const {fetchImpl, calls} = server();
    const visits = [];
    const setup = load().start({doc, fetchImpl, navigate: url => visits.push(url), search: () => ''});
    await setup.loaded;
    byId.setupName.value = 'Robin';
    await byId.setupName.fire('keydown', {key: 'Enter'});
    assert.deepEqual(calls.find(call => call.url === '/api/onboarding/complete').body, {name: 'Robin'});
    assert.deepEqual(visits, ['/home?make=pal']);
});

test('skipping is remembered without saving a name and goes to Home', async () => {
    const {doc, byId} = page();
    const {fetchImpl, calls} = server();
    const visits = [];
    const setup = load().start({doc, fetchImpl, navigate: url => visits.push(url), search: () => ''});
    await setup.loaded;
    await byId.setupSkip.click();
    assert.deepEqual(calls.find(call => call.url === '/api/onboarding/complete').body, {});
    assert.deepEqual(visits, ['/home']);
});

test('the skip link marks the first run finished instead of leaving it to come back', async () => {
    const {doc, byId} = page();
    const {fetchImpl, calls} = server();
    const visits = [];
    const setup = load().start({doc, fetchImpl, navigate: url => visits.push(url), search: () => ''});
    await setup.loaded;
    await byId.setupSkipLink.fire('click');
    assert.equal(calls.filter(call => call.url === '/api/onboarding/complete').length, 1);
    assert.deepEqual(visits, ['/home']);
});

test('finishing does not wait for the model check to come back', async () => {
    const {doc, byId} = page();
    let release;
    const waiting = new Promise(resolve => { release = resolve; });
    const {fetchImpl} = server();
    const gated = async (url, init) => {
        if (url !== '/api/setup/check') return fetchImpl(url, init);
        await waiting;
        return {ok: true, status: 200, json: async () => ({ok: true, model: 'local-chat', reply: 'OK', ms: 1})};
    };
    const visits = [];
    const setup = load().start({doc, fetchImpl: gated, navigate: url => visits.push(url), search: () => ''});
    for (let i = 0; i < 4; i += 1) await tick();
    byId.setupName.value = 'Robin';
    await byId.setupDone.click();
    assert.deepEqual(visits, ['/home?make=pal']);
    release();
    await setup.loaded;
});

test('a refused save stays on the page and can be tried again', async () => {
    const {doc, byId} = page();
    const {fetchImpl} = server({complete: 500});
    const visits = [];
    const setup = load().start({doc, fetchImpl, navigate: url => visits.push(url), search: () => ''});
    await setup.loaded;
    await byId.setupDone.click();
    assert.match(byId.setupStatus.textContent, /Synthetic refusal/);
    assert.deepEqual(visits, []);
    assert.equal(byId.setupDone.disabled, false);
    assert.equal(byId.setupSkip.disabled, false);
});

test('opened from Settings it is worded as a check and goes back to the chat', async () => {
    const {doc, byId} = page();
    const {fetchImpl} = server({status: {complete: true, name: 'Robin'}});
    const visits = [];
    const setup = load().start({doc, fetchImpl, navigate: url => visits.push(url), search: () => '?check=1'});
    await setup.loaded;
    assert.equal(byId.setupTitle.textContent, 'Setup check');
    assert.equal(byId.setupDone.textContent, 'Done');
    await byId.setupDone.click();
    assert.deepEqual(visits, ['/']);
});

function withModels(base, {providers, models, switched = []}) {
    return async (url, init = {}) => {
        if (url === '/api/llm/providers') return {ok: true, status: 200, json: async () => providers};
        const listed = url.match(/^\/api\/llm\/providers\/([^/]+)\/models$/);
        if (listed) return {ok: true, status: 200, json: async () => ({models: (models[listed[1]] || []).map(id => ({id}))})};
        if (url === '/api/llm/switch') { switched.push(JSON.parse(init.body)); return {ok: true, status: 200, json: async () => ({status: 'ok'})}; }
        return base(url, init);
    };
}

test('a model can be chosen on the setup page, and the check runs again with it', async () => {
    const {doc, byId} = page();
    const {fetchImpl, calls} = server({check: {ok: false, reason: 'no_model', hint: 'Choose a model served by LM Studio above, then check again.'}});
    const switched = [];
    const providers = {providers: [{key: 'lmstudio', name: 'LM Studio', base_url: 'http://127.0.0.1:1234/v1', is_active: true}],
        active_model: ''};
    const setup = load().start({doc, fetchImpl: withModels(fetchImpl, {providers, models: {lmstudio: ['qwen3-8b', 'gemma-3-12b']}, switched}),
        navigate() {}, search: () => ''});
    await setup.loaded;
    assert.equal(byId.setupModel.children.length, 2);
    assert.equal(byId.setupModel.value, 'qwen3-8b');
    assert.match(byId.setupWhere.textContent, /runs on this PC/);
    byId.setupModel.value = 'gemma-3-12b';
    await byId.setupUse.click();
    assert.deepEqual(switched, [{provider: 'lmstudio', model: 'gemma-3-12b'}]);
    assert.equal(calls.filter(call => call.url === '/api/setup/check').length, 2);
});

test('a provider that is not chosen yet starts on its own default model when its server lists it', async () => {
    const {doc, byId} = page();
    const {fetchImpl} = server();
    const providers = {providers: [{key: 'lmstudio', name: 'LM Studio', base_url: 'http://127.0.0.1:1234/v1', is_active: true},
        {key: 'cloud', name: 'Cloud', base_url: 'https://api.example.com/v1', default_model: 'house-model'},
        {key: 'other', name: 'Other', base_url: 'https://api.example.org/v1', default_model: 'gone-model'}], active_model: ''};
    const models = {lmstudio: [], cloud: ['alpha-model', 'house-model'], other: ['first-model']};
    const setup = load().start({doc, fetchImpl: withModels(fetchImpl, {providers, models}), navigate() {}, search: () => ''});
    await setup.loaded;
    byId.setupProvider.value = 'cloud'; await byId.setupProvider.fire('change');
    assert.equal(byId.setupModel.value, 'house-model');
    byId.setupProvider.value = 'other'; await byId.setupProvider.fire('change');
    assert.equal(byId.setupModel.value, 'first-model', 'a default the server does not list is not offered');
});

test('a provider off this PC says the chats are sent there', async () => {
    const {doc, byId} = page();
    const {fetchImpl} = server();
    const providers = {providers: [{key: 'cloud', name: 'Cloud', base_url: 'https://api.example.com/v1', is_active: true}],
        active_model: 'big-model'};
    const setup = load().start({doc, fetchImpl: withModels(fetchImpl, {providers, models: {cloud: []}}), navigate() {}, search: () => ''});
    await setup.loaded;
    assert.match(byId.setupWhere.textContent, /not on this PC: your chats are sent to api\.example\.com/);
    assert.equal(byId.setupModel.value, 'big-model');
    assert.doesNotMatch(template, /Nothing here sends anything off this PC/);
});
