/**
 * Altera — Service Worker (PWA Offline Support)
 * 
 * Cache static assets only. Personal pages and API responses always come from Altera.
 */

// Bumped to v2 with the avatar / pictures / adult-mode files below: static files are cache-first, so a new cache name is
// what makes an already-installed browser fetch the new app.js. Pictures are never cached here: they are served from
// /api/ (network-first, never stored) with Cache-Control: no-store.
// v5: Altera speaks in clips while she is still writing (app.js and style.css changed).
// v6: companions (one avatar, one chat), her picture beside the chat, her first meeting, her notes (new companions.js).
// v7: check-ins stop when switched off; studio pictures are fetched when the page comes back to the front.
// v8: the companion redesign (her stage, the adult-mode moods, discreet mode).
// v9: her living portrait (new living.js).
// v10: root scope for phone installs; preserve API errors instead of replaying them as offline requests.
// v12: reachability indicator, phone controls and page-owned offline queue.
// v13: durable replies, reconnect, and per-message conversation controls.
// v14: backup/restore, data epochs and drafts that require review after restore.
// v15: never cache personalized HTML: its dataset identifier must change after restore.
// v16: companion memory sources and remember/correct/forget controls.
// v24: microphone interruption and scoped saved-context reports.
// v30: encrypted archive/recovery controls and credential-clearing dialogs.
// v31: versioned backup mutations refuse older running servers safely.
// v34: key-owning browser admissions and masked acknowledged closure.
// v36: load the portable encrypted browser-data codec with private record adapters.
// v37: reviewed recovery copies, atomic insertion and closure fencing.
// v38: page-owned panel work, delayed-result fencing and retained edit drafts.
// v39: synchronous owned events, companion edit context and visible photo polling.
// v40: owned message editors/history and original-conversation edit retention.
// v41: owned main initialization/settings/tools and acknowledged unfinished edits.
// v42: warn for unacknowledged main-page edits and pending private closure.
// v43: owned Our story work and encrypted retention before replacing drafts.
// v44: owned read-only search/timeline and text-only historical rendering.
// v45: encrypted companion-link context and guarded private navigation.
// v46: freeze companion identity in presence writes.
// v49: reviewed shared-picture controls and recipient confirmation.
// v50: explain the private companion-switching gate and avoid automatic first meetings.
// v51: private per-tab companion selection is encrypted and reviewed.
// v53: own and generation-bind private living-portrait frames.
// v54: deliver the saved-picture gallery and private living setting controls.
// v55: private saved-backup download controls and generation binding.
// v56: preserve browser drafts and report unreadable draft storage during backup.
// v57: include the current tab's saved unfinished text in backup snapshots.
// v58: advertise private backup creation separately from restore.
// v59: permit saved private backup preview while restore stays gated.
// v60: refresh reviewed memory controls and save-attempt handling.
// v61: refresh interrupted-restore settings review.
// v62: report unavailable local transcription assets in voice settings.
// v66: explicitly transcribe private voice notes with owned local jobs.
// v67: allow reviewed, retryable deletion of private voice notes.
// v68: report damaged saved voice-note recordings while preserving browser originals.
// v69: refresh the corrected knowledge-report count labels.
// v70: refresh microphone setup failure handling.
// v71: refresh microphone transcription failure guidance.
// v72: distinguish busy-state and transcription-service failures.
// v73: offer to save a failed chat recording for review.
// v74: rename Her to Altera in page titles, the manifest and messages.
// v75: add the start screen (home.css, home.js) and the sidebar Home link.
// v76: make pals on Home (name, vibe, voice), pal settings, a pal's hello and its own voice.
// v78: import character cards on Home, character settings, a character's first message.
// v80: kids mode on Home (set it up once, then a switch per pal), its badge, and the pal line in the chat.
// v81: portraits from NPC Forge for pals and characters on Home, and a sample of a pal's or character's voice.
// v82: choose NPC Forge's picture and redraw engines for a portrait and a living portrait on Home.
// v83: a character's lorebook switch and entries in its settings on Home.
// v84: a portrait's later age check is shown in a pal's or character's settings on Home.
// v85: a finished living portrait says its frames are still being age-checked and copied.
// v86: guide recovered voice uploads and hide picture requests in kids mode.
// v87: refresh the ordinary release assets.
// v88: the Memory page says when the phrase matcher wrote a memory and what Rebuild does now.
// v89: a first run that ends in a working chat (setup.css, setup.js) and the Setup check in Settings.
// v90: one interface — Home in the phone bar, a sidebar named for the people in it, no "she" for a man or a pal.
// v91: a parent PIN and a kids view a child cannot click out of (lock.css, lock.js, kids-view.css, kids-view.js).
// v92: a companion written in Altera without NPC Forge: the write dialog, an uploaded portrait, and settings for companions.
// v93: alternative replies: keep the answer a regeneration replaces and move between them (message-versions controls).
// v94: Rebuild on Memory runs in the background; the setup page chooses the chat model (knowledge.js, setup.js, setup.css).
// v95: reply settings in Settings and a person's own temperature and reply length on Home (reply-settings.js, home.js).
// v96: change who a pal is in its settings on Home (home.js, home.css).
// v97: opt-in browser notifications for check-ins (checkin-notices.js, and the notification click below).
// v98: Altera's version at the bottom of Settings (style.css).
// v99: page actions without inline handlers, for the Content-Security-Policy (actions.js, app.js, offline-queue.js).
// v100: app.js's appearance and welcome moved to appearance.js and welcome.js; living.js asks currentMood().
// v101: the rename to Altera (every script and style sheet).
// v102: the backup dialog's vault mode and the vault styles removed.
// v103: the browser's vault mode removed (browser-state.js and the page scripts); companion messages are .message.companion.
// v104: the setup page starts a provider on its own default model (setup.js).
// v105: the setup page says how to install the local voice and explains the optional NPC Forge tool (setup.js).
// v106: the setup page shows NPC Forge as simply "Not running." and says pictures in the chat need ComfyUI (setup.js).
// v107: the typing dots show the companion's own initial (not the old "H") and stay below your message (app.js); the
// Voice switch in Settings turns the local voice on (app.js).
// v108: About and its π (about.js); adult mode's settings move out of Settings into the box the π opens. Home: a voice
// sample says why it stayed silent, and a portrait can come from NPC Forge's library or an upload (home.js).
// v110: the setup page links to NPC Forge, and About links to all four Fel's Studio apps.
// v111: suite GPU status, reply waiting, and NPC Forge connection details.
// v112: Meet someone new uses Forge's Modern life setting and offers a gender choice.
// v113: Voice Lab discovery, per-pal voice licences and speech availability.
// v114: character card inbox, card voice review, and Forge card portraits.
// v115: optional add-on panels and core-only picture controls.
const CACHE_VERSION = 'v115';
const STATIC_CACHE = `altera-static-${CACHE_VERSION}`;
const DYNAMIC_CACHE = `altera-dynamic-${CACHE_VERSION}`;

// Assets to cache immediately on install
const STATIC_ASSETS = [
  '/static/css/style.css',
  '/static/css/backup.css',
  '/static/css/knowledge.css',
  '/static/css/story.css',
  '/static/css/home.css',
  '/static/css/setup.css',
  '/static/css/lock.css',
  '/static/css/kids-view.css',
  '/static/css/companion.css',
  '/static/js/browser-state.js',
  '/static/js/app.js',
  '/static/js/voice-activity.js',
  '/static/js/voice-capture-worklet.js',
  '/static/js/voice-notes.js',
  '/static/js/presence.js',
  '/static/js/story.js',
  '/static/js/home.js',
  '/static/js/setup.js',
  '/static/js/lock.js',
  '/static/js/kids-view.js',
  '/static/js/auxiliary.js',
  '/static/js/accessibility.js',
  '/static/js/diagnostics.js',
  '/static/js/reply-settings.js',
  '/static/js/checkin-notices.js',
  '/static/js/api.js',
  '/static/js/actions.js',
  '/static/js/appearance.js',
  '/static/js/welcome.js',
  '/static/js/backups.js',
  '/static/js/knowledge.js',
  '/static/js/chat-controls.js',
  '/static/js/companion.js',
  '/static/js/about.js',
  '/static/js/companions.js',
  '/static/js/shared-scene.js',
  '/static/js/living.js',
  '/static/js/offline-queue.js',
];

// ============================================================
// Install — precache static assets (resilient: individual failures don't block)
// ============================================================
self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(STATIC_CACHE).then(async (cache) => {
      console.log('[SW] Precaching static assets');
      // Fetch each asset individually so one failure doesn't block the rest
      for (const url of STATIC_ASSETS) {
        try {
          const resp = await fetch(url);
          if (resp.ok) {
            await cache.put(url, resp);
            console.log(`[SW] Cached: ${url}`);
          } else {
            console.warn(`[SW] Failed to cache ${url}: HTTP ${resp.status}`);
          }
        } catch (err) {
          console.warn(`[SW] Could not fetch ${url}:`, err.message);
        }
      }
    })
  );
  // Force immediate activation
  self.skipWaiting();
});

// ============================================================
// Activate — clean old caches
// ============================================================
self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((cacheNames) => {
      return Promise.all(
        cacheNames
          .filter((name) => /^(?:altera|her)-(?:static|dynamic)-v[0-9]+$/.test(name) && name !== STATIC_CACHE && name !== DYNAMIC_CACHE)
          .map((name) => {
            console.log('[SW] Deleting old cache:', name);
            return caches.delete(name);
          })
      );
    }).then(() => {
      // Take control of all pages immediately
      return self.clients.claim();
    })
  );
});

// ============================================================
// Fetch — serve from cache if available, fall back to network
// ============================================================
self.addEventListener('fetch', (event) => {
  const { request } = event;
  
  // Skip non-GET requests
  if (request.method !== 'GET') return;

  if (request.mode === 'navigate') {
    event.respondWith(fetch(new Request(request, {cache: 'no-store'})).catch(() => new Response(
      '<!doctype html><html lang="en"><meta name="viewport" content="width=device-width"><title>Altera is offline</title><body><h1>Altera is offline</h1><p>Reconnect to the PC running Altera, then reload. Your unsent drafts remain on this device.</p><a href="/">Reconnect</a></body></html>',
      {status: 503, headers: {'Content-Type': 'text/html; charset=utf-8', 'Cache-Control': 'no-store'}}
    )));
    return;
  }
  
  // API calls: network-first with offline queue fallback
  if (request.url.includes('/api/')) {
    event.respondWith(networkFirst(request));
    return;
  }
  
  const url = new URL(request.url);
  if (url.origin === self.location.origin && url.pathname.startsWith('/static/audio/')) {
    event.respondWith(fetch(new Request(request, {cache: 'no-store'})));
    return;
  }
  if (url.origin === self.location.origin && url.pathname.startsWith('/static/')) {
    event.respondWith(cacheFirst(request));
  }
});

// ============================================================
// Cache-first strategy for static assets
// ============================================================
async function cacheFirst(request) {
  const cached = await caches.match(request);
  if (cached) return cached;
  
  try {
    const response = await fetch(request);
    // Only cache successful responses
    if (response.ok && !/no-store|private/i.test(response.headers.get('Cache-Control') || '')) {
      const clone = response.clone();
      caches.open(STATIC_CACHE).then((cache) => cache.put(request, clone));
    }
    return response;
  } catch (err) {
    console.warn('[SW] Cache-first failed for:', request.url);
    // For other requests, return a minimal response
    return new Response('Offline', { status: 503, statusText: 'Service Unavailable' });
  }
}

// ============================================================
// Network-first strategy for API calls
// ============================================================
async function networkFirst(request) {
  try {
    const response = await fetch(request);
    return response;
  } catch (err) {
    // These are GET requests. Only the page's explicit message queue may replay chat writes.
    console.warn('[SW] Network down:', request.url);
    
    // For chat API, return a friendly offline message
    if (request.url.includes('/api/chat')) {
      return new Response(JSON.stringify({ 
        error: 'Connection lost. Reconnect to load this conversation.',
        offline: true 
      }), {
        status: 503,
        headers: { 'Content-Type': 'application/json' }
      });
    }
    
    return new Response(JSON.stringify({ offline: true }), {
      status: 503,
      headers: { 'Content-Type': 'application/json' }
    });
  }
}

// Message retries belong to offline-queue.js in the page. The worker never replays API requests.

// ============================================================
// A check-in notification was clicked (checkin-notices.js): bring Altera to the front and let the page open that person.
// The notification carries only who it is about; the page asks Altera for everything else.
// ============================================================
self.addEventListener('notificationclick', (event) => {
  event.notification.close();
  const data = event.notification.data || {};
  if (!String(event.notification.tag || '').startsWith('altera-checkin-')) return;
  event.waitUntil((async () => {
    const windows = await self.clients.matchAll({ type: 'window', includeUncontrolled: true });
    const open = windows.find((client) => new URL(client.url).origin === self.location.origin);
    if (open) {
      open.postMessage({ type: 'altera-checkin', avatar_id: data.avatar_id || '', session_id: data.session_id || null });
      return open.focus();
    }
    return self.clients.openWindow('/');
  })());
});
