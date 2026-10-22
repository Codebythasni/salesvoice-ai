/**
 * Tests for what the dashboard does after a page reload.
 *
 * The script is loaded into a small DOM stub and driven with a fake server,
 * so the real static/js/dashboard.js is what gets tested.
 */

import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import vm from 'node:vm';
import test from 'node:test';

import { createSandbox, elementText, settle, submitForm, transcriptText } from './dom_stub.mjs';

const SOURCE = readFileSync(
  fileURLToPath(new URL('../../static/js/dashboard.js', import.meta.url)),
  'utf8'
);

const CONFIG = {
  sales_statuses: ['Converted', 'Interested', 'Follow-up Required', 'Not Interested', 'Demo Requested', 'No Response'],
  default_sales_status: 'No Response',
  limits: { chat_timeout_seconds: 90 }
};

const TURN = (role, text, source, clientMessageId) => ({
  role,
  text,
  at: '2026-01-01T10:00:00+00:00',
  source,
  client_message_id: clientMessageId || null
});

function activeCall(overrides = {}) {
  return {
    id: 'call-active-1',
    product: 'taskflow-crm',
    status: 'active',
    sales_status: 'Interested',
    sales_status_source: 'manual',
    started_at: '2026-01-01T10:00:00+00:00',
    ended_at: null,
    duration_seconds: 42,
    turn_count: 2,
    summary: null,
    summary_error: null,
    summary_available: false,
    summary_fields: {},
    recording_consent: 'granted',
    recording_count: 1,
    recording_available: true,
    persisted: true,
    turns: [
      TURN('assistant', 'Hello, this is Ava.', 'server'),
      TURN('customer', 'What does it cost?', 'voice', 'm-1')
    ],
    recordings: [
      {
        id: 7,
        call_id: 'call-active-1',
        filename: 'recording.webm',
        mime_type: 'audio/webm',
        size_bytes: 2048,
        duration_seconds: 3.2,
        created_at: '2026-01-01T10:00:30+00:00',
        available: true,
        valid_audio: true,
        playable: true,
        problem: null,
        playback_url: '/api/recording/7'
      }
    ],
    ...overrides
  };
}

function endedCall(overrides = {}) {
  return {
    ...activeCall(),
    id: 'call-ended-1',
    status: 'ended',
    ended_at: '2026-01-01T10:05:00+00:00',
    duration_seconds: 300,
    summary: 'Customer requirements: A shared pipeline\nObjections raised: Price\nNext steps: Send a sheet\nOutcome: Demo booked',
    summary_fields: {
      requirements: 'A shared pipeline',
      objections: 'Price',
      next_steps: 'Send a sheet',
      outcome: 'Demo booked'
    },
    ...overrides
  };
}

async function boot(routes, options = {}) {
  const { sandbox, document, requests, recognition, sessionStorage } = createSandbox({ routes });
  if (options.session) {
    Object.entries(options.session).forEach(([key, value]) => sessionStorage.setItem(key, value));
  }
  vm.createContext(sandbox);
  vm.runInContext(SOURCE, sandbox, { filename: 'dashboard.js' });
  await settle();
  return { sandbox, document, requests, recognition, sessionStorage };
}

function baseRoutes(extra = {}) {
  return {
    'GET /api/config': CONFIG,
    'GET /api/calls?limit=50': { ok: true, count: 0, calls: [] },
    'GET /api/calls?limit=1': { ok: true, count: 0, calls: [] },
    'GET /api/call/active': { ok: true, active: false, call: null },
    ...extra
  };
}

test('a reload restores the call that was still open without opening a second one', async () => {
  const call = activeCall();
  const { document, requests } = await boot(baseRoutes({
    'GET /api/call/active': { ok: true, active: true, call },
    'GET /api/calls?limit=50': { ok: true, count: 1, calls: [call] }
  }));

  assert.equal(requests.filter((r) => r.path === '/api/call/start').length, 0, 'no new call was started');
  assert.equal(document.getElementById('call-id').textContent, 'call-active-1');
  assert.equal(document.getElementById('turn-count').textContent, '2');
  assert.deepEqual(transcriptText(document), ['Hello, this is Ava.', 'What does it cost?']);
  assert.equal(document.getElementById('sales-status').value, 'Interested');
  assert.equal(document.getElementById('resume-banner').hidden, false);
  assert.match(document.getElementById('resume-banner-text').textContent, /did not keep running in the background/);
});

test('the restored call keeps its summary, its recording links and its consent', async () => {
  const call = endedCall();
  const { document } = await boot(baseRoutes({
    'GET /api/calls?limit=50': { ok: true, count: 1, calls: [call] },
    'GET /api/calls?limit=1': { ok: true, count: 1, calls: [call] },
    'GET /api/calls/call-ended-1': { ok: true, call }
  }));

  const summary = document.getElementById('summary-grid');
  assert.equal(summary.hidden, false);
  assert.match(elementText(summary), /A shared pipeline/);
  assert.match(elementText(summary), /Demo booked/);

  const list = document.getElementById('recording-list');
  assert.equal(list.querySelectorAll('.recording-item').length, 1);
  assert.match(elementText(list), /recording\.webm/);
  assert.match(document.getElementById('recording-consent-state').textContent, /Consent granted/);
});

test('with no active call the last saved call is shown instead of an empty panel', async () => {
  const call = endedCall();
  const { document, requests } = await boot(baseRoutes({
    'GET /api/calls?limit=50': { ok: true, count: 1, calls: [call] },
    'GET /api/calls?limit=1': { ok: true, count: 1, calls: [call] },
    'GET /api/calls/call-ended-1': { ok: true, call }
  }));

  assert.equal(requests.filter((r) => r.path === '/api/call/start').length, 0);
  assert.equal(document.getElementById('call-id').textContent, 'call-ended-1');
  assert.deepEqual(transcriptText(document), ['Hello, this is Ava.', 'What does it cost?']);
  assert.match(document.getElementById('mic-hint').textContent, /last saved call/);
});

test('continuing a restored call does not start another one', async () => {
  const call = activeCall();
  const { document, requests, recognition } = await boot(baseRoutes({
    'GET /api/call/active': { ok: true, active: true, call }
  }));

  document.getElementById('resume-continue').click();
  await settle();

  assert.equal(requests.filter((r) => r.path === '/api/call/start').length, 0);
  assert.equal(document.getElementById('resume-banner').hidden, true);
  assert.equal(recognition.started, 1, 'the microphone was asked for again');
  assert.equal(document.getElementById('call-id').textContent, 'call-active-1');
});

test('a message typed after the reload goes to the restored call', async () => {
  const call = activeCall();
  const { document, requests } = await boot(baseRoutes({
    'GET /api/call/active': { ok: true, active: true, call },
    'POST /api/chat': { ok: true, reply: 'It is $39 per user.', call }
  }));

  document.getElementById('message-input').value = 'And the price?';
  submitForm(document.getElementById('composer'));
  await settle();

  const chat = requests.find((r) => r.path === '/api/chat');
  assert.ok(chat, 'the message was sent');
  assert.equal(chat.body.call_id, 'call-active-1');
  assert.equal(requests.filter((r) => r.path === '/api/call/start').length, 0);
});

test('a message that was in flight during the reload is not stored twice', async () => {
  // The customer turn reached the server but the reply did not.
  const call = activeCall({
    turn_count: 2,
    turns: [
      TURN('assistant', 'Hello, this is Ava.', 'server'),
      TURN('customer', 'What does it cost?', 'voice', 'm-77')
    ]
  });

  const { document, requests } = await boot(baseRoutes({
    'GET /api/call/active': { ok: true, active: true, call },
    'POST /api/chat': { ok: true, reply: 'It is $39 per user.', call }
  }), {
    session: {
      'salesvoice.pending-message': JSON.stringify({
        callId: 'call-active-1',
        messageId: 'm-77',
        text: 'What does it cost?',
        source: 'voice'
      })
    }
  });

  const chat = requests.find((r) => r.path === '/api/chat');
  assert.ok(chat, 'the pending message was sent again');
  assert.equal(chat.body.message_id, 'm-77', 'the same message id was used');
  assert.equal(chat.body.call_id, 'call-active-1');
  assert.deepEqual(
    transcriptText(document),
    ['Hello, this is Ava.', 'What does it cost?', 'It is $39 per user.'],
    'the customer message was not shown or stored twice'
  );
});

test('a message that already has a reply is not sent again after the reload', async () => {
  const call = activeCall({
    turn_count: 3,
    turns: [
      TURN('assistant', 'Hello, this is Ava.', 'server'),
      TURN('customer', 'What does it cost?', 'voice', 'm-1'),
      TURN('assistant', 'It is $39 per user.', 'server')
    ]
  });
  const { requests } = await boot(baseRoutes({
    'GET /api/call/active': { ok: true, active: true, call },
    'POST /api/chat': { ok: true, reply: 'It is $39 per user.', call }
  }), {
    session: {
      'salesvoice.pending-message': JSON.stringify({
        callId: 'call-active-1',
        messageId: 'm-1',
        text: 'What does it cost?',
        source: 'voice'
      })
    }
  });

  assert.equal(requests.filter((r) => r.path === '/api/chat').length, 0);
});
