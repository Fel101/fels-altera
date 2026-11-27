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
    removeAttribute(name) { delete this.attributes[name]; }
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
    'setupProvider', 'setupModel', 'setupUse', 'setupWhere', 'setupLabStatus', 'setupLabUse',
    'setupAssist', 'setupAssistStatus', 'setupSuggestion', 'setupStart', 'setupDownload', 'setupStop',
    'setupModelLink', 'setupDownloadProgress', 'setupConfirmation', 'setupConfirmTitle',
    'setupConfirmDetail', 'setupConfirmAction'];

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
                 forge = {online: true, comfy_online: true}, comfy = {online: false}, complete = 200,
                 lab = {ready: false, message: 'Start Voice Lab to use its voices.'}} = {}) {
    const calls = [];
    const reply = (code, payload) => ({ok: code < 400, status: code, json: async () => payload});
    const fetchImpl = async (url, init = {}) => {
        calls.push({url, method: init.method || 'GET', body: init.body ? JSON.parse(init.body) : undefined});
        if (url === '/api/onboarding/status') return reply(200, status);
        if (url === '/api/setup/check') return reply(200, check);
        if (url === '/api/voice/status') return reply(200, voice);
        if (url === '/api/voice/lab') return reply(200, lab);
        if (url === '/api/voice/engine') return reply(200, {engine: 'voicelab', tts_enabled: true});
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

function localOllama({running = false, finishDownload = true} = {}) {
    const base = '/api/setup/local/ollama';
    const model = 'granite4:micro';
    const suggestion = {name: 'Granite 4.0 Micro', model, size_gb: 2.1, license: 'Apache 2.0', basis: '8 GB RAM'};
    const {fetchImpl, calls} = server({check: {ok: false}});
    const reply = data => ({ok: true, status: 200, json: async () => data});
    let job, installed = false, chosen = false;
    return {model, calls, fetchImpl: async (url, init = {}) => {
        if (url === '/api/llm/providers') return reply({providers: [
            {key: 'ollama', name: 'Ollama', base_url: 'http://localhost:11434/v1', is_active: true},
        ]});
        if (url === '/api/llm/switch') {
            calls.push({url, method: init.method, body: JSON.parse(init.body)});
            chosen = true;
            return reply({status: 'ok'});
        }
        if (url === '/api/setup/check' && chosen) return reply({ok: true, model, reply: 'OK'});
        if (!url.startsWith(base)) return fetchImpl(url, init);
        calls.push({url, method: init.method || 'GET', body: init.body ? JSON.parse(init.body) : undefined});
        if (url === base) return reply({
            supported: true, state: running ? 'running' : 'stopped', can_start: !running,
            models: installed ? [model] : [], suggestion: installed ? null : suggestion, job,
        });
        if (init.method === 'POST') {
            const action = url.endsWith('/start') ? 'start' : 'download';
            job = {id: action, action, model, state: 'working', message: 'Working', percent: 40};
        } else if (init.method === 'DELETE') {
            job = {...job, state: 'stopping', percent: null};
        } else if (job.state === 'stopping') {
            job = {...job, state: 'stopped', message: 'Download stopped.'};
        } else if (job.action === 'start' || finishDownload) {
            running = true;
            installed = job.action === 'download';
            job = {...job, state: 'ready'};
        }
        return reply(job);
    }};
}

test('a stopped local server and the suggested download each wait for confirmation', async () => {
    const {doc, byId} = page();
    const {fetchImpl, calls, model} = localOllama();
    const answers = [false, true, false, true], questions = [];
    const setup = load().start({doc, fetchImpl, navigate() {}, search: () => '', pause: async () => {},
        ask(message) { questions.push(message); return answers.shift(); }});
    await setup.loaded;
    assert.match(byId.setupAssistStatus.textContent, /Ollama is installed, but its server is off/);
    assert.equal(byId.setupStart.hidden, false);
    assert.equal(byId.setupDownload.hidden, true);
    await byId.setupStart.click();
    assert.equal(calls.filter(call => call.method === 'POST' && call.url.endsWith('/start')).length, 0);
    await byId.setupStart.click();
    assert.equal(byId.setupStart.hidden, true);
    assert.match(byId.setupDownload.textContent, /Download Granite 4\.0 Micro \(2\.1 GB\)/);
    await byId.setupDownload.click();
    assert.equal(calls.filter(call => call.method === 'POST' && call.url.endsWith('/download')).length, 0);
    await byId.setupDownload.click();
    assert.deepEqual(calls.find(call => call.url.endsWith('/download')).body, {confirmed: true, model});
    assert.deepEqual(calls.find(call => call.url.endsWith('/switch')).body, {provider: 'ollama', model});
    assert.equal(byId.setupModelState.textContent, `Ready — ${model} answered.`);
    assert.match(questions[3], /2\.1 GB of internet data and disk space/);
});

test('the active download shows file progress and Stop does not switch models', async () => {
    const {doc, byId} = page();
    const {fetchImpl, calls} = localOllama({running: true, finishDownload: false});
    let poll;
    const setup = load().start({doc, fetchImpl, navigate() {}, search: () => '', ask: () => true,
        pause: () => new Promise(resolve => { poll = resolve; })});
    await setup.loaded;
    const downloading = byId.setupDownload.click();
    await tick();
    assert.equal(byId.setupDownloadProgress.value, 40);
    assert.equal(byId.setupStop.hidden, false);
    await byId.setupStop.click();
    assert.equal(byId.setupStop.disabled, true);
    poll();
    await downloading;
    assert.match(byId.setupAssistStatus.textContent, /Download stopped/);
    assert.equal(byId.setupDownload.hidden, false);
    assert.equal(calls.filter(call => call.method === 'DELETE').length, 1);
    assert.equal(calls.some(call => call.url.endsWith('/switch')), false);
});

test('the Speech row says how this copy installs the local voice', async () => {
    for (const [voice, expected] of [
        [{tts_enabled: false, local_voice_installed: false,
          install_hint: "Run Altera's Setup again and tick the local Kokoro voice, or use Voice Lab for speech (Settings, Voice)."},
         /Optional\. Run Altera's Setup again and tick the local Kokoro voice.*Then switch Voice on in Settings\./],
        [{tts_enabled: false, local_voice_installed: true}, /Optional: switch Voice on in Settings to hear replies\./],
    ]) {
        const {doc, byId} = page();
        const {fetchImpl} = server({voice});
        const setup = load().start({doc, fetchImpl, navigate() {}, search: () => ''});
        await setup.loaded;
        assert.match(text(byId.setupOptional.children[0]), expected);
        assert.doesNotMatch(text(byId.setupOptional.children[0]), /setup\.bat/);
    }
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
    assert.match(template, /<a href="https:\/\/github\.com\/Fel101\/fels-voice-lab"[^>]*>Voice Lab<\/a> can give each pal a voice/);
    assert.equal(byId.setupLabUse.hidden, true);
    assert.equal(byId.setupDone.disabled, false);
});

test('Setup offers Voice Lab and switches speech only on a click', async () => {
    const {doc, byId} = page();
    const {fetchImpl, calls} = server({lab: {ready: true, count: 14, selected: false,
        message: 'Voice Lab is running with 14 voices. Use it for speech?'}});
    const setup = load().start({doc, fetchImpl, navigate() {}, search: () => ''});
    await setup.loaded;
    assert.equal(byId.setupLabStatus.textContent, 'Voice Lab is running with 14 voices. Use it for speech?');
    assert.equal(byId.setupLabUse.hidden, false);
    assert.equal(calls.some(call => call.url === '/api/voice/engine'), false);
    await byId.setupLabUse.click();
    assert.deepEqual(calls.find(call => call.url === '/api/voice/engine'),
        {url: '/api/voice/engine', method: 'POST', body: {engine: 'voicelab'}});
    assert.equal(byId.setupLabStatus.textContent, 'Voice Lab is selected for speech.');
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

test('a suggested model missing from LM Studio\'s catalog links to its Hugging Face page', async () => {
    for (const [link, words] of [['https://huggingface.co/LiquidAI/LFM3-1.7B-GGUF', 'Open LFM3 1.7B on Hugging Face'],
                                 ['https://lmstudio.ai/models/google/gemma-4-12b', 'Open LFM3 1.7B in LM Studio']]) {
        const {doc, byId} = page();
        const {fetchImpl} = server({check: {ok: false}});
        const reply = data => ({ok: true, status: 200, json: async () => data});
        const suggestion = {name: 'LFM3 1.7B', model: null, size_gb: null, license: 'LFM Open License v1.0', basis: '8 GB RAM', link};
        const setup = load().start({doc, navigate() {}, search: () => '', fetchImpl: async (url, init = {}) => {
            if (url === '/api/llm/providers') return reply({providers: [
                {key: 'lmstudio', name: 'LM Studio', base_url: 'http://127.0.0.1:1234/v1', is_active: true}]});
            if (url === '/api/setup/local/lmstudio') return reply({supported: true, state: 'running', can_start: false,
                models: [], suggestion, job: null});
            return fetchImpl(url, init);
        }});
        await setup.loaded;
        assert.equal(byId.setupModelLink.hidden, false);
        assert.equal(byId.setupModelLink.href, link);
        assert.equal(byId.setupModelLink.textContent, words);
    }
});
