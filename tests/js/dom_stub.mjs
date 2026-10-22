/**
 * A very small DOM stub, just enough to run static/js/dashboard.js in Node.
 *
 * It is deliberately dumb: every element is a plain object with the handful of
 * properties the dashboard touches. The goal is to exercise the real
 * dashboard script - not to be a browser.
 */

export function createElement(tag) {
  const element = {
    tagName: String(tag || 'div').toUpperCase(),
    className: '',
    textContent: '',
    innerHTML: '',
    value: '',
    title: '',
    type: '',
    src: '',
    hidden: false,
    disabled: false,
    scrollTop: 0,
    scrollHeight: 0,
    children: [],
    parentNode: null,
    listeners: {},
    attributes: {},
    classList: {
      add(...names) {
        const current = element.className.split(/\s+/).filter(Boolean);
        names.forEach((name) => {
          if (!current.includes(name)) { current.push(name); }
        });
        element.className = current.join(' ');
      },
      remove(...names) {
        element.className = element.className
          .split(/\s+/)
          .filter((name) => name && !names.includes(name))
          .join(' ');
      },
      contains(name) {
        return element.className.split(/\s+/).includes(name);
      }
    },

    appendChild(child) {
      if (!child) { return child; }
      child.parentNode = element;
      element.children.push(child);
      return child;
    },
    removeChild(child) {
      const index = element.children.indexOf(child);
      if (index !== -1) { element.children.splice(index, 1); }
      child.parentNode = null;
      return child;
    },
    addEventListener(type, handler) {
      if (!element.listeners[type]) { element.listeners[type] = []; }
      element.listeners[type].push(handler);
    },
    removeEventListener() {},
    setAttribute(name, value) { element.attributes[name] = String(value); },
    getAttribute(name) {
      return Object.prototype.hasOwnProperty.call(element.attributes, name)
        ? element.attributes[name]
        : null;
    },
    removeAttribute(name) { delete element.attributes[name]; },
    focus() {},
    blur() {},
    load() {},
    play() { return Promise.resolve(); },
    pause() {},
    click() {
      (element.listeners.click || []).forEach((handler) => handler({ target: element }));
    },
    descendants() {
      const out = [];
      element.children.forEach((child) => {
        out.push(child);
        out.push(...child.descendants());
      });
      return out;
    },
    matches(selector) {
      return selector
        .split(',')
        .map((part) => part.trim())
        .filter(Boolean)
        .some((part) => {
          if (part.startsWith('.')) { return element.className.split(/\s+/).includes(part.slice(1)); }
          return element.tagName === part.toUpperCase();
        });
    },
    querySelectorAll(selector) {
      return element.descendants().filter((node) => node.matches(selector));
    },
    querySelector(selector) {
      return element.querySelectorAll(selector)[0] || null;
    }
  };
  return element;
}

export function createDocument(ids) {
  const registry = {};
  ids.forEach((id) => {
    registry[id] = createElement('div');
    registry[id].id = id;
  });
  return {
    getElementById(id) { return registry[id] || null; },
    createElement,
    addEventListener() {},
    removeEventListener() {},
    body: createElement('body')
  };
}

/** The element ids that static/js/dashboard.js looks up. */
export const ELEMENT_IDS = [
  'capability-banner', 'capability-banner-title', 'capability-banner-text', 'dismiss-banner',
  'resume-banner', 'resume-banner-title', 'resume-banner-text', 'resume-continue',
  'resume-new-call', 'resume-dismiss',
  'start-call', 'end-call', 'toggle-mic', 'toggle-voice', 'toggle-mic-label', 'toggle-voice-label',
  'call-timer', 'call-status', 'call-status-text', 'listening-state', 'call-id', 'turn-count',
  'mic-hint', 'sales-status', 'sales-status-hint', 'summary-panel', 'summary-grid',
  'summary-error', 'retry-summary', 'transcript', 'transcript-empty', 'interim-badge',
  'clear-transcript', 'composer', 'message-input', 'send-message', 'ask-consent',
  'toggle-record', 'toggle-record-label', 'recording-consent-state', 'recording-list',
  'recording-list-note', 'recording-player', 'recording-player-error', 'stored-calls',
  'history-body', 'history-count', 'refresh-history', 'consent-modal', 'consent-grant',
  'consent-deny', 'call-detail-modal', 'call-detail-id', 'call-detail-meta',
  'call-detail-status', 'call-detail-status-hint', 'call-detail-summary',
  'call-detail-summary-error', 'call-detail-summary-empty', 'call-detail-retry',
  'call-detail-recordings', 'call-detail-recording-note', 'call-detail-player',
  'call-detail-player-error', 'call-detail-transcript', 'close-call-detail'
];

/** Build the sandbox that dashboard.js runs inside. */
export function createSandbox(options = {}) {
  const document = createDocument(ELEMENT_IDS);
  const transcript = document.getElementById('transcript');
  const empty = document.getElementById('transcript-empty');
  empty.className = 'transcript__empty';
  transcript.appendChild(empty);

  const requests = [];
  const fetchImpl = async (path, init = {}) => {
    const method = (init.method || 'GET').toUpperCase();
    let body = null;
    if (init.body && typeof init.body === 'string') {
      try { body = JSON.parse(init.body); } catch (error) { body = init.body; }
    }
    requests.push({ path, method, body });
    const handler = options.routes[`${method} ${path}`] || options.routes[path];
    if (!handler) {
      return {
        ok: false,
        status: 404,
        headers: { get: () => 'application/json' },
        json: async () => ({ ok: false, error: { code: 'not_found', message: 'no route' } }),
        text: async () => ''
      };
    }
    const result = typeof handler === 'function' ? await handler(body, requests) : handler;
    const payload = result && result.__body !== undefined ? result.__body : result;
    const status = result && result.__status ? result.__status : 200;
    return {
      ok: status < 400,
      status,
      headers: { get: () => 'application/json' },
      json: async () => payload,
      text: async () => JSON.stringify(payload)
    };
  };

  const sessionStore = {};
  const sessionStorage = {
    getItem(key) { return Object.prototype.hasOwnProperty.call(sessionStore, key) ? sessionStore[key] : null; },
    setItem(key, value) { sessionStore[key] = String(value); },
    removeItem(key) { delete sessionStore[key]; }
  };

  const recognition = {
    started: 0,
    stopped: 0,
    start() { this.started += 1; },
    stop() { this.stopped += 1; }
  };

  const windowStub = {
    SpeechRecognition: function SpeechRecognition() { return recognition; },
    webkitSpeechRecognition: undefined,
    speechSynthesis: {
      getVoices: () => [{ lang: 'en-US', name: 'Test' }],
      cancel() {},
      speak() {},
      onvoiceschanged: null
    },
    SpeechSynthesisUtterance: function SpeechSynthesisUtterance(text) { this.text = text; },
    isSecureContext: true,
    addEventListener() {},
    removeEventListener() {},
    setTimeout: (...args) => setTimeout(...args),
    clearTimeout: (...args) => clearTimeout(...args),
    setInterval: (...args) => {
      const handle = setInterval(...args);
      // The call timer must not keep the test process alive.
      if (handle && typeof handle.unref === 'function') { handle.unref(); }
      return handle;
    },
    clearInterval: (...args) => clearInterval(...args),
    sessionStorage,
    AbortController: globalThis.AbortController,
    fetch: fetchImpl
  };
  windowStub.window = windowStub;

  const navigatorStub = {
    mediaDevices: { getUserMedia: async () => ({ getTracks: () => [] }) },
    userAgent: 'node'
  };

  const sandbox = {
    window: windowStub,
    document,
    navigator: navigatorStub,
    fetch: fetchImpl,
    sessionStorage,
    setTimeout: (...args) => setTimeout(...args),
    clearTimeout: (...args) => clearTimeout(...args),
    setInterval: (...args) => setInterval(...args),
    clearInterval: (...args) => clearInterval(...args),
    AbortController: globalThis.AbortController,
    console
  };
  sandbox.globalThis = sandbox;

  return { sandbox, document, requests, recognition, sessionStorage };
}

/** Let every pending promise in the sandbox settle. */
export async function settle(rounds = 6) {
  for (let i = 0; i < rounds; i += 1) {
    await new Promise((resolve) => setTimeout(resolve, 0));
  }
}

/** The text of every message currently shown in the transcript. */
export function transcriptText(document) {
  const transcript = document.getElementById('transcript');
  return transcript.querySelectorAll('.msg').map((node) => {
    const body = node.querySelector('.msg__text');
    return body ? body.textContent : '';
  });
}

/** All the text inside an element, including its children. */
export function elementText(element) {
  return [element.textContent || '']
    .concat(element.descendants().map((node) => node.textContent || ''))
    .join(' ');
}

/** Fire the submit handler of a form element. */
export function submitForm(element) {
  (element.listeners.submit || []).forEach((handler) => handler({ preventDefault() {} }));
}
