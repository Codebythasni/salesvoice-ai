(function () {
  'use strict';

  var el = {
    banner: document.getElementById('capability-banner'),
    bannerTitle: document.getElementById('capability-banner-title'),
    bannerText: document.getElementById('capability-banner-text'),
    dismissBanner: document.getElementById('dismiss-banner'),
    resumeBanner: document.getElementById('resume-banner'),
    resumeBannerTitle: document.getElementById('resume-banner-title'),
    resumeBannerText: document.getElementById('resume-banner-text'),
    resumeContinue: document.getElementById('resume-continue'),
    resumeNewCall: document.getElementById('resume-new-call'),
    resumeDismiss: document.getElementById('resume-dismiss'),
    startCall: document.getElementById('start-call'),
    endCall: document.getElementById('end-call'),
    toggleMic: document.getElementById('toggle-mic'),
    toggleVoice: document.getElementById('toggle-voice'),
    toggleMicLabel: document.getElementById('toggle-mic-label'),
    toggleVoiceLabel: document.getElementById('toggle-voice-label'),
    timer: document.getElementById('call-timer'),
    status: document.getElementById('call-status'),
    statusText: document.getElementById('call-status-text'),
    listeningState: document.getElementById('listening-state'),
    callId: document.getElementById('call-id'),
    turnCount: document.getElementById('turn-count'),
    micHint: document.getElementById('mic-hint'),
    salesStatus: document.getElementById('sales-status'),
    salesStatusHint: document.getElementById('sales-status-hint'),
    summaryPanel: document.getElementById('summary-panel'),
    summaryGrid: document.getElementById('summary-grid'),
    summaryError: document.getElementById('summary-error'),
    retrySummary: document.getElementById('retry-summary'),
    transcript: document.getElementById('transcript'),
    transcriptEmpty: document.getElementById('transcript-empty'),
    interimBadge: document.getElementById('interim-badge'),
    clearTranscript: document.getElementById('clear-transcript'),
    composer: document.getElementById('composer'),
    input: document.getElementById('message-input'),
    send: document.getElementById('send-message'),
    askConsent: document.getElementById('ask-consent'),
    toggleRecord: document.getElementById('toggle-record'),
    toggleRecordLabel: document.getElementById('toggle-record-label'),
    recordingConsentState: document.getElementById('recording-consent-state'),
    recordingList: document.getElementById('recording-list'),
    recordingListNote: document.getElementById('recording-list-note'),
    recordingPlayer: document.getElementById('recording-player'),
    recordingPlayerError: document.getElementById('recording-player-error'),
    storedCalls: document.getElementById('stored-calls'),
    historyBody: document.getElementById('history-body'),
    historyCount: document.getElementById('history-count'),
    refreshHistory: document.getElementById('refresh-history'),
    consentModal: document.getElementById('consent-modal'),
    consentGrant: document.getElementById('consent-grant'),
    consentDeny: document.getElementById('consent-deny'),
    callDetailModal: document.getElementById('call-detail-modal'),
    callDetailId: document.getElementById('call-detail-id'),
    callDetailMeta: document.getElementById('call-detail-meta'),
    callDetailStatus: document.getElementById('call-detail-status'),
    callDetailStatusHint: document.getElementById('call-detail-status-hint'),
    callDetailSummary: document.getElementById('call-detail-summary'),
    callDetailSummaryError: document.getElementById('call-detail-summary-error'),
    callDetailSummaryEmpty: document.getElementById('call-detail-summary-empty'),
    callDetailRetry: document.getElementById('call-detail-retry'),
    callDetailRecordings: document.getElementById('call-detail-recordings'),
    callDetailRecordingNote: document.getElementById('call-detail-recording-note'),
    callDetailPlayer: document.getElementById('call-detail-player'),
    callDetailPlayerError: document.getElementById('call-detail-player-error'),
    callDetailTranscript: document.getElementById('call-detail-transcript'),
    closeCallDetail: document.getElementById('close-call-detail')
  };

  var SpeechRecognitionCtor = window.SpeechRecognition || window.webkitSpeechRecognition || null;

  // Used when the server does not tell us how long to wait. The server
  // always answers before this fires, so the browser only gives up on a
  // request that is genuinely stuck.
  var DEFAULT_CHAT_TIMEOUT_SECONDS = 90;

  var capabilities = {
    speechRecognition: Boolean(SpeechRecognitionCtor),
    speechSynthesis: typeof window.speechSynthesis !== 'undefined' &&
      typeof window.SpeechSynthesisUtterance !== 'undefined',
    mediaDevices: Boolean(navigator.mediaDevices && navigator.mediaDevices.getUserMedia),
    mediaRecorder: typeof window.MediaRecorder !== 'undefined',
    secureContext: window.isSecureContext !== false
  };

  var state = {
    callId: null,
    callActive: false,
    callEnded: false,
    recognition: null,
    recognitionRunning: false,
    wantRecognition: false,
    micMuted: true,
    voiceEnabled: true,
    sending: false,
    queue: [],
    timerId: null,
    startedAt: 0,
    elapsedMs: 0,
    lastFinalText: '',
    lastFinalAt: 0,
    interimNode: null,
    pendingNode: null,
    voice: null,
    consent: 'pending',
    recorder: null,
    recorderStartedAt: 0,
    recordingChunks: [],
    recordingStream: null,
    recordingCount: 0,
    salesStatuses: [],
    viewedCallId: null,
    chatTimeoutSeconds: 0,
    restoredCallId: null,
    showingEndedCall: false,
    micPausedByReload: false,
    activeCallId: null
  };

  // Where the message that is currently being sent is kept, so a page reload
  // can pick it up again with the same id instead of sending it twice.
  var PENDING_MESSAGE_KEY = 'salesvoice.pending-message';

  function uid() {
    return 'm-' + Date.now().toString(36) + '-' + Math.random().toString(36).slice(2, 10);
  }

  function readPendingMessage() {
    try {
      var raw = window.sessionStorage.getItem(PENDING_MESSAGE_KEY);
      if (!raw) { return null; }
      var data = JSON.parse(raw);
      if (!data || typeof data.messageId !== 'string' || typeof data.text !== 'string') {
        return null;
      }
      return data;
    } catch (error) {
      return null;
    }
  }

  function writePendingMessage(data) {
    try {
      window.sessionStorage.setItem(PENDING_MESSAGE_KEY, JSON.stringify(data));
    } catch (error) {
      /* storage is not available - the message is still sent */
    }
  }

  function clearPendingMessage() {
    try {
      window.sessionStorage.removeItem(PENDING_MESSAGE_KEY);
    } catch (error) {
      /* nothing to clean up */
    }
  }

  function api(path, options) {
    var settings = options || {};
    var init = {
      method: settings.method || 'GET',
      headers: settings.headers || {},
      cache: 'no-store'
    };
    if (settings.body !== undefined) {
      init.body = settings.body;
    }
    if (settings.signal) {
      init.signal = settings.signal;
    }
    return fetch(path, init).then(function (response) {
      var isJson = (response.headers.get('content-type') || '').indexOf('json') !== -1;
      return (isJson ? response.json() : response.text().then(function (t) { return { raw: t }; }))
        .catch(function () { return {}; })
        .then(function (body) {
          if (!response.ok) {
            var message = body && body.error && body.error.message
              ? body.error.message
              : 'The server returned an unexpected response.';
            var error = new Error(message);
            error.status = response.status;
            error.body = body;
            throw error;
          }
          return body;
        });
    });
  }

  function postJson(path, payload, options) {
    var settings = options || {};
    return api(path, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload || {}),
      signal: settings.signal
    });
  }

  function formatTime(ms) {
    var total = Math.floor(ms / 1000);
    var minutes = Math.floor(total / 60);
    var seconds = total % 60;
    return String(minutes).padStart(2, '0') + ':' + String(seconds).padStart(2, '0');
  }

  function setStatus(text, mode) {
    if (el.statusText) { el.statusText.textContent = text; }
    el.status.className = 'status-pill status-pill--' + mode;
    var dot = el.status.querySelector('.dot');
    if (dot) { dot.className = mode === 'live' ? 'dot dot--live' : 'dot'; }
  }

  function setListening(text) {
    if (el.listeningState) { el.listeningState.textContent = text; }
  }

  function setTimer(ms) {
    el.timer.textContent = formatTime(ms);
  }

  function showBanner(title, text) {
    el.bannerTitle.textContent = title;
    el.bannerText.textContent = text;
    el.banner.hidden = false;
  }

  function clearTranscriptEmpty() {
    if (el.transcriptEmpty && el.transcriptEmpty.parentNode) {
      el.transcriptEmpty.parentNode.removeChild(el.transcriptEmpty);
      el.transcriptEmpty = null;
    }
  }

  function appendMessage(role, text, options) {
    var opts = options || {};
    clearTranscriptEmpty();

    var wrapper = document.createElement('div');
    wrapper.className = 'msg msg--' + role;

    var avatar = document.createElement('div');
    avatar.className = 'msg__avatar';
    avatar.textContent = role === 'bot' ? 'Ava' : (role === 'user' ? 'You' : 'i');

    var body = document.createElement('div');
    body.className = 'msg__body';

    var meta = document.createElement('div');
    meta.className = 'msg__meta';
    meta.textContent = opts.label || (role === 'bot' ? 'Assistant' : (role === 'user' ? 'Customer' : 'System'));
    body.appendChild(meta);

    var textNode = document.createElement('p');
    textNode.className = 'msg__text';
    textNode.textContent = text;
    body.appendChild(textNode);

    wrapper.appendChild(avatar);
    wrapper.appendChild(body);
    el.transcript.appendChild(wrapper);
    el.transcript.scrollTop = el.transcript.scrollHeight;

    if (role === 'user' || role === 'bot') { updateTurnCount(); }
    return wrapper;
  }

  function updateTurnCount() {
    var count = el.transcript.querySelectorAll('.msg--user, .msg--bot').length;
    el.turnCount.textContent = String(count);
  }

  function showPending() {
    var node = appendMessage('system', 'The assistant is thinking...', { label: 'Working' });
    node.classList.add('msg--pending');
    state.pendingNode = node;
    return node;
  }

  function clearPending() {
    if (state.pendingNode && state.pendingNode.parentNode) {
      state.pendingNode.parentNode.removeChild(state.pendingNode);
    }
    state.pendingNode = null;
  }

  // ------------------------------------------------------------------
  // capabilities
  // ------------------------------------------------------------------

  function checkCapabilities() {
    if (!capabilities.speechRecognition) {
      showBanner(
        'Browser speech recognition is not supported in this browser',
        'Speech input is unavailable here, so the assistant cannot hear the customer. ' +
        'Use the text chat below to continue the conversation - it works the same way and ' +
        'is saved in the call history. For voice calls, open this page in Chrome or Edge ' +
        'on https:// or on localhost.'
      );
      el.micHint.textContent = 'Voice input is unavailable. Use the text chat below to talk to the assistant.';
      setListening('Unsupported');
      return false;
    }
    if (!capabilities.secureContext) {
      showBanner(
        'Microphone access needs a secure connection',
        'Browsers only allow microphone access on https:// pages or on localhost. ' +
        'Reopen the dashboard using the http://127.0.0.1 address printed by the server.'
      );
      el.micHint.textContent = 'Open the dashboard via http://127.0.0.1 to enable the microphone.';
      setListening('Blocked');
      return false;
    }
    if (!capabilities.mediaDevices) {
      showBanner(
        'Microphone access is unavailable in this browser',
        'This browser does not expose navigator.mediaDevices.getUserMedia. ' +
        'Use the text chat below, or switch to Chrome or Edge.'
      );
      el.micHint.textContent = 'Microphone API is unavailable. Use the text chat below.';
      setListening('Unavailable');
      return false;
    }
    setListening('Ready');
    return true;
  }

  function pickVoice() {
    if (!capabilities.speechSynthesis) { return null; }
    var voices = window.speechSynthesis.getVoices() || [];
    var preferred = voices.filter(function (voice) {
      return /^en(-|_|$)/i.test(voice.lang || '');
    });
    state.voice = preferred[0] || voices[0] || null;
    return state.voice;
  }

  function speak(text) {
    if (!capabilities.speechSynthesis || !state.voiceEnabled || !text) { return; }
    try {
      window.speechSynthesis.cancel();
      var utterance = new window.SpeechSynthesisUtterance(text);
      utterance.voice = state.voice;
      utterance.lang = (state.voice && state.voice.lang) || 'en-US';
      utterance.rate = 1.02;
      utterance.pitch = 1;
      window.speechSynthesis.speak(utterance);
    } catch (error) {
      setStatus('Voice output failed - text only', 'error');
    }
  }

  // ------------------------------------------------------------------
  // speech recognition
  // ------------------------------------------------------------------

  function createRecognition() {
    if (!SpeechRecognitionCtor) { return null; }
    var recognition = new SpeechRecognitionCtor();
    recognition.lang = 'en-US';
    recognition.continuous = true;
    recognition.interimResults = true;
    recognition.maxAlternatives = 1;

    recognition.onstart = function () {
      state.recognitionRunning = true;
      el.interimBadge.hidden = false;
      setListening('Listening');
    };

    recognition.onresult = function (event) {
      var interim = '';
      var finalText = '';
      for (var i = event.resultIndex; i < event.results.length; i += 1) {
        var result = event.results[i];
        var chunk = result[0] ? result[0].transcript : '';
        if (result.isFinal) { finalText += chunk; } else { interim += chunk; }
      }

      if (interim) { showInterim(interim); } else { clearInterim(); }

      if (finalText) {
        clearInterim();
        var trimmed = finalText.trim();
        var now = Date.now();
        if (!trimmed) { return; }
        if (trimmed === state.lastFinalText && now - state.lastFinalAt < 4000) { return; }
        state.lastFinalText = trimmed;
        state.lastFinalAt = now;
        handleCustomerUtterance(trimmed, 'voice');
      }
    };

    recognition.onerror = function (event) {
      var reason = event && event.error ? event.error : 'unknown';
      if (reason === 'no-speech' || reason === 'aborted') { return; }
      if (reason === 'not-allowed' || reason === 'service-not-allowed') {
        showBanner(
          'Microphone permission was denied',
          'The browser blocked microphone access. Allow it in the site permissions ' +
          '(padlock icon) and start the call again, or continue with the text chat below.'
        );
        el.micHint.textContent = 'Microphone blocked. Allow access in the browser, or use the text chat.';
        state.wantRecognition = false;
        state.micMuted = true;
        updateMicButton();
        setListening('Blocked');
        setStatus('Microphone blocked', 'error');
        return;
      }
      if (reason === 'audio-capture') {
        showBanner(
          'No microphone was found',
          'The browser could not find a microphone device. Connect a microphone or use the text chat below.'
        );
        state.wantRecognition = false;
        state.micMuted = true;
        updateMicButton();
        setListening('Unavailable');
        setStatus('No microphone found', 'error');
        return;
      }
      setStatus('Speech recognition error: ' + reason, 'error');
      setListening('Error: ' + reason);
    };

    recognition.onend = function () {
      state.recognitionRunning = false;
      el.interimBadge.hidden = true;
      clearInterim();
      if (state.wantRecognition && state.callActive) {
        window.setTimeout(function () {
          if (state.wantRecognition && state.callActive && !state.recognitionRunning) {
            try { recognition.start(); } catch (error) { /* already started */ }
          }
        }, 350);
      } else {
        setListening(state.micMuted ? 'Paused' : 'Off');
      }
    };

    return recognition;
  }

  function showInterim(text) {
    if (!state.interimNode) {
      state.interimNode = appendMessage('interim', text, { label: 'Listening' });
      state.interimNode.classList.add('msg--interim');
    } else {
      var textNode = state.interimNode.querySelector('.msg__text');
      if (textNode) { textNode.textContent = text; }
      el.transcript.scrollTop = el.transcript.scrollHeight;
    }
  }

  function clearInterim() {
    if (state.interimNode && state.interimNode.parentNode) {
      state.interimNode.parentNode.removeChild(state.interimNode);
    }
    state.interimNode = null;
  }

  function startRecognition() {
    if (!capabilities.speechRecognition || state.micMuted || !state.callActive) { return; }
    if (!state.recognition) { state.recognition = createRecognition(); }
    if (!state.recognition || state.recognitionRunning) { return; }
    state.wantRecognition = true;
    try {
      state.recognition.start();
    } catch (error) {
      setStatus('Could not start the microphone', 'error');
      setListening('Error');
    }
  }

  function stopRecognition() {
    state.wantRecognition = false;
    if (state.recognition && state.recognitionRunning) {
      try { state.recognition.stop(); } catch (error) { /* not running */ }
    }
    el.interimBadge.hidden = true;
    clearInterim();
    if (!state.micMuted) { setListening('Paused'); }
  }

  function updateMicButton() {
    el.toggleMicLabel.textContent = state.micMuted ? 'Unmute mic' : 'Mute mic';
  }

  // ------------------------------------------------------------------
  // messaging
  // ------------------------------------------------------------------

  function handleCustomerUtterance(text, source, options) {
    var opts = options || {};
    if (state.showingEndedCall && state.callId) {
      // The panel is showing a call that has already ended - the last saved
      // call after a reload, for example. Typing starts a fresh call, so the
      // old transcript is cleared first and the two calls are never mixed up.
      resetForNewCall();
    }
    if (state.callEnded && state.callId) {
      appendMessage('error', 'This call has ended. Start a new call to continue.');
      return;
    }
    if (state.sending) {
      if (state.queue.length < 5) {
        state.queue.push({ text: text, source: source, options: opts });
      }
      return;
    }
    sendMessage(text, source, opts);
  }

  function friendlyChatError(error) {
    var body = error && error.body;
    var code = body && body.error ? body.error.code : '';
    var status = error ? error.status : 0;
    if (code === 'gemini_timeout' || status === 504) {
      return 'The assistant took too long to respond. Your message is saved - press Retry.';
    }
    if (code === 'gemini_rate_limited' || status === 429) {
      return 'The assistant hit a usage limit, so it could not answer. ' +
        'Your message is saved - wait a moment and press Retry.';
    }
    if (code === 'gemini_auth') {
      return 'The server\'s Gemini API key was rejected, so no reply could be generated. ' +
        'An administrator needs to fix the key in the server .env file.';
    }
    if (code === 'gemini_model_not_found') {
      return 'The configured Gemini model was not found on the server. ' +
        'An administrator needs to update GEMINI_MODEL in the server .env file.';
    }
    if (code === 'assistant_not_configured') {
      return 'The assistant is not configured on the server yet. No reply could be generated.';
    }
    if (code === 'gemini_bad_request' || status === 400) {
      return 'Gemini rejected the request, so no reply could be generated. This is not a ' +
        'temporary outage - the model name or the server settings need a look. ' +
        'Your message is saved.';
    }
    if (code === 'gemini_network') {
      return 'The server could not reach Gemini, so no reply could be generated. ' +
        'Check the internet connection and press Retry.';
    }
    if (code === 'gemini_unavailable' || status === 503) {
      return 'Gemini is busy with this model right now, so no reply could be generated. ' +
        'This is a temporary problem on Google\'s side, not with your call. ' +
        'Your message is saved - press Retry in a moment.';
    }
    if (code === 'gemini_client_error') {
      return 'The assistant could not be started on the server. ' +
        'An administrator needs to check the server .env file.';
    }
    if (error && error.message) {
      return error.message + ' Your message is saved - press Retry.';
    }
    return 'The assistant could not reply. Your message is saved - press Retry.';
  }

  function canRetryChatError(error) {
    var body = error && error.body;
    if (body && body.retryable === false) { return false; }
    var code = body && body.error ? body.error.code : '';
    return code !== 'gemini_auth' && code !== 'gemini_model_not_found' &&
      code !== 'assistant_not_configured' && code !== 'gemini_bad_request' &&
      code !== 'gemini_client_error';
  }

  function abortedChatError() {
    var error = new Error('The assistant did not answer in time.');
    error.name = 'AbortError';
    error.status = 504;
    error.body = {
      error: {
        code: 'gemini_timeout',
        message: 'The assistant did not answer in time.'
      },
      retryable: true
    };
    return error;
  }

  function chatTimeoutMs() {
    var seconds = state.chatTimeoutSeconds;
    if (!seconds || seconds <= 0) { seconds = DEFAULT_CHAT_TIMEOUT_SECONDS; }
    return seconds * 1000;
  }

  function appendChatError(error, request) {
    var node = appendMessage('error', friendlyChatError(error), { label: 'No reply yet' });
    if (!canRetryChatError(error)) { return node; }
    var body = node.querySelector('.msg__body');
    if (!body) { return node; }

    var actions = document.createElement('div');
    actions.className = 'msg__actions';
    var retry = document.createElement('button');
    retry.type = 'button';
    retry.className = 'btn btn--small btn--ghost chat-error__retry';
    retry.textContent = 'Retry';
    retry.addEventListener('click', function () {
      retry.disabled = true;
      handleCustomerUtterance(request.text, request.source, {
        retry: true,
        messageId: request.messageId,
        retryNode: node
      });
    });
    actions.appendChild(retry);
    body.appendChild(actions);
    return node;
  }

  function sendMessage(text, source, options) {
    var opts = options || {};
    var isRetry = Boolean(opts.retry);
    var messageId = opts.messageId || uid();

    state.sending = true;
    el.send.disabled = true;
    el.input.disabled = true;

    if (opts.retryNode && opts.retryNode.parentNode) {
      opts.retryNode.parentNode.removeChild(opts.retryNode);
    }

    var userNode = isRetry ? null : appendMessage('user', text, {
      label: source === 'voice' ? 'Customer (voice)' : 'Customer (text)'
    });

    showPending();

    var payload = { message: text, source: source || 'text', message_id: messageId };
    if (state.callId) { payload.call_id = state.callId; }

    var request = { text: text, source: source, messageId: messageId };

    // Remember the message before it is sent. If the page is reloaded while
    // the reply is still on its way, the same id is used again so the server
    // answers the saved message instead of storing a second copy of it.
    writePendingMessage({
      callId: state.callId,
      messageId: messageId,
      text: text,
      source: source || 'text'
    });

    // A chat request must never hang forever. The server has its own timeout
    // and answers before this one fires, so the abort only catches a request
    // that is genuinely stuck.
    var controller = null;
    var abortTimer = null;
    var timeoutMs = chatTimeoutMs();
    var chatRequest;

    if (typeof window.AbortController === 'function') {
      controller = new window.AbortController();
      abortTimer = window.setTimeout(function () {
        try { controller.abort(); } catch (error) { /* nothing to abort */ }
      }, timeoutMs);
      chatRequest = postJson('/api/chat', payload, { signal: controller.signal });
    } else {
      // No AbortController in this browser: race the request against a timer
      // so the chat still ends in an error state instead of waiting forever.
      chatRequest = Promise.race([
        postJson('/api/chat', payload),
        new Promise(function (resolve, reject) {
          abortTimer = window.setTimeout(function () {
            reject(abortedChatError());
          }, timeoutMs);
        })
      ]);
    }

    chatRequest
      .then(function (body) {
        clearPendingMessage();
        if (body.greeting) {
          var greetingNode = appendMessage('bot', body.greeting, { label: 'Assistant (greeting)' });
          if (userNode && userNode.parentNode === el.transcript) {
            el.transcript.insertBefore(greetingNode, userNode);
          }
          startTimer();
        }
        if (body.call) { applyCallState(body.call); }
        if (body.call_created) {
          state.showingEndedCall = false;
          el.micHint.textContent =
            'A new call was started. The earlier call is still in the history below.';
        }
        if (body.normalized) {
          appendMessage('system', 'The product name in the message was corrected for the assistant.', { label: 'Note' });
        }
        if (body.reply) {
          appendMessage('bot', body.reply);
          speak(body.reply);
        } else {
          throw new Error('The assistant returned an empty reply.');
        }
      }).catch(function (error) {
        if (error && error.name === 'AbortError') {
          error = abortedChatError();
        }
        if (error.status === 409 && error.body && error.body.error &&
            error.body.error.code === 'call_ended') {
          state.callEnded = true;
          state.callActive = false;
          clearPendingMessage();
          setStatus('Call ended', 'idle');
          el.micHint.textContent = 'This call has ended. Start a new call to continue selling.';
          updateControls();
        }
        appendChatError(error, request);
      }).finally(function () {
        if (abortTimer) { window.clearTimeout(abortTimer); }
        clearPending();
        state.sending = false;
        el.send.disabled = false;
        el.input.disabled = false;
        el.input.focus();
        if (state.queue.length) {
          var next = state.queue.shift();
          window.setTimeout(function () {
            handleCustomerUtterance(next.text, next.source, next.options);
          }, 60);
        }
      });
  }

  // ------------------------------------------------------------------
  // call lifecycle
  // ------------------------------------------------------------------

  function applyCallState(call) {
    if (!call) { return; }
    state.callId = call.id;
    state.callActive = call.status === 'active';
    state.callEnded = !state.callActive;
    el.callId.textContent = call.id;
    if (call.sales_status) {
      el.salesStatus.value = call.sales_status;
    }
    if (call.recording_consent) {
      state.consent = call.recording_consent;
    }
    updateControls();
  }

  function updateControls() {
    var active = state.callActive;
    el.startCall.disabled = active;
    el.endCall.disabled = !active;
    el.toggleMic.disabled = !active;
    el.toggleVoice.disabled = !active;
    el.askConsent.disabled = !state.callId;
    el.toggleRecord.disabled = !state.callId || state.consent !== 'granted';
    el.salesStatus.disabled = !state.callId;
    el.input.disabled = false;
    el.send.disabled = false;

    if (!state.callId) {
      el.salesStatusHint.textContent = 'Start a call to set the sales status.';
    } else if (active) {
      el.salesStatusHint.textContent = 'Updates the saved status of this call.';
    } else {
      el.salesStatusHint.textContent = 'This call has ended. You can still correct the saved status.';
    }
  }

  function startTimer() {
    stopTimer();
    state.startedAt = Date.now() - state.elapsedMs;
    state.timerId = window.setInterval(function () {
      state.elapsedMs = Date.now() - state.startedAt;
      setTimer(state.elapsedMs);
    }, 500);
  }

  function stopTimer() {
    if (state.timerId) { window.clearInterval(state.timerId); }
    state.timerId = null;
  }

  function startCall() {
    el.startCall.disabled = true;
    setStatus('Connecting...', 'connecting');

    postJson('/api/call/start', {}).then(function (body) {
      state.elapsedMs = 0;
      setTimer(0);
      state.consent = 'pending';
      state.callEnded = false;
      applyCallState(body.call);
      setStatus('Connected', 'live');
      el.micHint.textContent = 'Listening for the customer. Speak naturally - pause when you finish a sentence.';
      appendMessage('bot', body.greeting, { label: 'Assistant (greeting)' });
      speak(body.greeting);
      startTimer();
      resetRecordingPanel();
      if (capabilities.speechRecognition) {
        state.micMuted = false;
        updateMicButton();
        startRecognition();
      } else {
        state.micMuted = true;
        updateMicButton();
      }
      setStatus('Connected', 'live');
      loadHistory();
    }).catch(function (error) {
      el.startCall.disabled = false;
      if (error.status === 409 && error.body && error.body.call) {
        var existing = error.body.call;
        setStatus('Call already active', 'error');
        el.startCall.disabled = false;
        appendMessage('error',
          'A call was already open on the server (' + existing.id + ', started ' +
          formatDate(existing.started_at) + '). It was not started a second time.');
        // The server still has that call open, so the dashboard shows it
        // instead of opening a second call.
        restoreActiveCall().then(function (call) {
          if (!call) { return; }
          showActiveCallBanner(call);
        });
        return;
      }
      setStatus('Could not start the call', 'error');
      appendMessage('error', error.message || 'The call could not be started.');
    });
  }

  function endCall() {
    if (!state.callId) { return; }
    el.endCall.disabled = true;
    stopRecognition();
    if (capabilities.speechSynthesis) {
      try { window.speechSynthesis.cancel(); } catch (error) { /* ignore */ }
    }
    stopTimer();
    if (state.recorder) { stopRecording(); }

    var callId = state.callId;
    state.callActive = false;
    state.callEnded = true;
    updateControls();
    setStatus('Ending call...', 'connecting');
    el.micHint.textContent = 'Call ended. Start a new call to continue selling.';

    postJson('/api/call/end', { call_id: callId }).then(function (body) {
      setStatus('Call ended', 'idle');
      if (body.call) { applyCallState(body.call); }
      renderSummary(body.summary, body.summary_error);
      var call = body.call || {};
      appendMessage('system',
        'Call ended after ' + formatTime((call.duration_seconds || 0) * 1000) +
        ' with ' + (call.turn_count || 0) + ' transcript entries. The call was saved to the ' +
        'server database and will still be here after a restart.',
        { label: 'System' });
      loadHistory();
    }).catch(function (error) {
      setStatus('Call ended', 'idle');
      appendMessage('error', error.message || 'The call could not be closed cleanly.');
      loadHistory();
    });
  }

  function resetForNewCall() {
    state.callId = null;
    state.callActive = false;
    state.callEnded = false;
    state.elapsedMs = 0;
    state.consent = 'pending';
    state.recordingCount = 0;
    state.restoredCallId = null;
    state.showingEndedCall = false;
    state.micPausedByReload = false;
    state.lastFinalText = '';
    state.lastFinalAt = 0;
    hideResumeBanner();
    setTimer(0);
    el.callId.textContent = 'not started';
    el.salesStatus.value = state.salesStatuses[0] || '';
    el.salesStatusHint.textContent = 'Start a call to set the sales status.';
    el.summaryPanel.hidden = true;
    el.summaryGrid.innerHTML = '';
    el.summaryError.hidden = true;
    el.retrySummary.hidden = true;
    resetRecordingPanel();
    clearTranscript();
    setStatus('Idle - no active call', 'idle');
    setListening(capabilities.speechRecognition ? 'Ready' : 'Unsupported');
    el.micHint.textContent =
      'Start the call, allow microphone access, then speak to the customer. ' +
      'You can also type straight away - a call is opened automatically.';
    updateControls();
  }

  function clearTranscript() {
    var messages = el.transcript.querySelectorAll('.msg');
    Array.prototype.forEach.call(messages, function (node) {
      node.parentNode.removeChild(node);
    });
    state.interimNode = null;
    state.pendingNode = null;
    el.transcriptEmpty = null;
    var empty = document.createElement('div');
    empty.className = 'transcript__empty';
    empty.innerHTML = '<span class="transcript__empty-icon">&#128172;</span>' +
      '<p><strong>No conversation yet.</strong></p>' +
      '<p>Start the call to greet the customer, or type a message below to begin in text mode.</p>';
    el.transcript.appendChild(empty);
    el.transcriptEmpty = empty;
    updateTurnCount();
  }

  // ------------------------------------------------------------------
  // summary
  // ------------------------------------------------------------------

  function renderSummaryFields(fields, rawText) {
    el.summaryPanel.hidden = false;
    el.summaryError.hidden = true;
    el.retrySummary.hidden = true;
    el.summaryGrid.innerHTML = '';

    if (rawText) {
      var rawLabel = document.createElement('dt');
      rawLabel.textContent = 'Call summary';
      var rawValue = document.createElement('dd');
      rawValue.textContent = rawText;
      el.summaryGrid.appendChild(rawLabel);
      el.summaryGrid.appendChild(rawValue);
      return;
    }

    var rows = [
      ['Customer requirements', fields.requirements],
      ['Objections raised', fields.objections],
      ['Next steps', fields.next_steps],
      ['Outcome', fields.outcome]
    ];
    rows.forEach(function (pair) {
      var dt = document.createElement('dt');
      dt.textContent = pair[0];
      var dd = document.createElement('dd');
      dd.textContent = pair[1] || 'Not mentioned';
      el.summaryGrid.appendChild(dt);
      el.summaryGrid.appendChild(dd);
    });
  }

  function renderSummary(summary, error) {
    if (summary) {
      renderSummaryFields(summary, '');
      return;
    }
    if (error) {
      el.summaryPanel.hidden = false;
      el.summaryGrid.innerHTML = '';
      el.summaryError.hidden = false;
      el.summaryError.textContent = error + ' The call itself was saved and is safe in the history.';
      el.retrySummary.hidden = false;
    }
  }

  function retrySummary() {
    if (!state.callId) { return; }
    el.retrySummary.disabled = true;
    postJson('/api/call/summary', { call_id: state.callId }).then(function (body) {
      renderSummary(body.summary, body.summary_error);
      if (body.call) { applyCallState(body.call); }
      loadHistory();
    }).catch(function (error) {
      renderSummary(null, error.message || 'The summary could not be generated.');
    }).then(function () {
      el.retrySummary.disabled = false;
    });
  }

  // ------------------------------------------------------------------
  // sales status
  // ------------------------------------------------------------------

  function saveSalesStatus(callId, status, onDone) {
    if (!callId || !status) { return; }
    postJson('/api/call/status', { call_id: callId, sales_status: status })
      .then(function (body) {
        if (body.call && state.callId === body.call.id) {
          el.salesStatus.value = body.call.sales_status;
        }
        loadHistory();
        if (onDone) { onDone(null); }
      })
      .catch(function (error) {
        if (onDone) { onDone(error); }
      });
  }

  // ------------------------------------------------------------------
  // recording
  // ------------------------------------------------------------------

  function openConsentModal() {
    el.consentModal.hidden = false;
    el.consentGrant.focus();
  }

  function closeConsentModal() {
    el.consentModal.hidden = true;
  }

  function submitConsent(decision) {
    if (!state.callId) { closeConsentModal(); return; }
    var callId = state.callId;
    postJson('/api/call/consent', { call_id: callId, decision: decision })
      .then(function (body) {
        state.consent = decision;
        if (body.call) { applyCallState(body.call); }
        el.recordingConsentState.textContent = decision === 'granted'
          ? 'Consent granted for this call. Recording is allowed.'
          : 'Consent denied. Nothing is recorded for this call.';
        updateControls();
        if (decision === 'granted') { startRecording(); }
      })
      .catch(function (error) {
        el.recordingConsentState.textContent = error.message || 'The consent decision could not be saved.';
      })
      .then(closeConsentModal);
  }

  function pickRecorderMime() {
    if (!window.MediaRecorder || !MediaRecorder.isTypeSupported) { return ''; }
    var candidates = ['audio/webm;codecs=opus', 'audio/webm', 'audio/ogg;codecs=opus', 'audio/mp4'];
    for (var i = 0; i < candidates.length; i += 1) {
      if (MediaRecorder.isTypeSupported(candidates[i])) { return candidates[i]; }
    }
    return '';
  }

  function extensionFor(mime) {
    if (!mime) { return '.webm'; }
    if (mime.indexOf('ogg') !== -1) { return '.ogg'; }
    if (mime.indexOf('mp4') !== -1) { return '.mp4'; }
    if (mime.indexOf('wav') !== -1) { return '.wav'; }
    if (mime.indexOf('mpeg') !== -1 || mime.indexOf('mp3') !== -1) { return '.mp3'; }
    return '.webm';
  }

  function startRecording() {
    if (!state.callId) { return; }
    if (state.consent !== 'granted') {
      showBanner(
        'Recording needs the customer to agree first',
        'Ask for consent before recording. Nothing is captured until the customer agrees.'
      );
      openConsentModal();
      return;
    }
    if (!capabilities.mediaRecorder) {
      showBanner(
        'Recording is not supported in this browser',
        'This browser does not support MediaRecorder, so no audio can be captured. ' +
        'The call transcript is still saved normally.'
      );
      return;
    }
    if (!capabilities.mediaDevices) {
      showBanner(
        'Microphone access is unavailable in this browser',
        'Recording needs navigator.mediaDevices.getUserMedia, which this browser does not provide.'
      );
      return;
    }

    navigator.mediaDevices.getUserMedia({ audio: true }).then(function (stream) {
      var mime = pickRecorderMime();
      var recorder = mime ? new MediaRecorder(stream, { mimeType: mime }) : new MediaRecorder(stream);
      state.recordingChunks = [];
      state.recordingStream = stream;
      state.recorder = recorder;
      state.recorderStartedAt = Date.now();

      recorder.ondataavailable = function (event) {
        if (event.data && event.data.size) { state.recordingChunks.push(event.data); }
      };
      recorder.onstop = function () {
        var duration = (Date.now() - state.recorderStartedAt) / 1000;
        var type = recorder.mimeType || 'audio/webm';
        var blob = new Blob(state.recordingChunks, { type: type });
        state.recordingChunks = [];
        state.recorder = null;
        state.recordingStream = null;
        el.toggleRecordLabel.textContent = 'Start recording';
        el.toggleRecord.disabled = false;
        uploadRecording(blob, duration, type);
      };

      recorder.start(1000);
      el.toggleRecordLabel.textContent = 'Stop recording';
      setStatus('Recording', 'live');
    }).catch(function () {
      showBanner(
        'The microphone could not be opened for recording',
        'Recording needs microphone access. The call transcript is still saved normally.'
      );
    });
  }

  function stopRecording() {
    if (!state.recorder) { return; }
    try { state.recorder.stop(); } catch (error) { /* ignore */ }
    if (state.recordingStream) {
      state.recordingStream.getTracks().forEach(function (track) { track.stop(); });
    }
  }

  function uploadRecording(blob, duration, mimeType) {
    var callId = state.callId;
    if (!callId) { return; }
    var extension = extensionFor(mimeType);
    var form = new FormData();
    form.append('call_id', callId);
    form.append('duration_seconds', String(Math.round(duration * 100) / 100));
    form.append('recording', blob, 'recording' + extension);

    api('/api/call/recording', { method: 'POST', body: form })
      .then(function (body) {
        el.recordingConsentState.textContent = 'Consent granted for this call. Recording is allowed.';
        loadCallRecordings(callId);
        if (body.call) { applyCallState(body.call); }
        if (body.recording && body.recording.playable) {
          appendMessage('system', 'A customer-microphone recording was saved locally and can be played back.', { label: 'System' });
        } else {
          appendMessage('error',
            'The server did not confirm a playable recording, so nothing is shown as saved.', { label: 'System' });
        }
      })
      .catch(function (error) {
        appendMessage('error', error.message || 'The recording could not be saved.');
      });
  }

  function loadCallRecordings(callId) {
    if (!callId) { return; }
    api('/api/call/' + callId + '/recordings').then(function (body) {
      renderRecordings(body.recordings || []);
    }).catch(function () { /* the list stays as it is */ });
  }

  function renderRecordings(recordings) {
    renderRecordingList({
      container: el.recordingList,
      player: el.recordingPlayer,
      errorEl: el.recordingPlayerError,
      noteEl: el.recordingListNote,
      recordings: recordings,
      consent: state.consent
    });
  }

  function noRecordingMessage(consent) {
    if (consent === 'denied') {
      return 'Recording was declined for this call, so no audio was captured.';
    }
    if (consent === 'granted') {
      return 'Consent was granted, but no audio file was uploaded for this call.';
    }
    return 'No recording was made for this call because consent was not granted.';
  }

  function renderRecordingList(options) {
    var container = options.container;
    var player = options.player;
    var errorEl = options.errorEl;
    var noteEl = options.noteEl;
    var recordings = options.recordings || [];
    var consent = options.consent || 'pending';

    if (!container) { return; }
    container.innerHTML = '';
    if (player) {
      player.hidden = true;
      player.removeAttribute('src');
    }
    if (errorEl) {
      errorEl.hidden = true;
      errorEl.textContent = '';
    }

    if (!recordings.length) {
      if (noteEl) {
        noteEl.textContent = noRecordingMessage(consent);
        noteEl.hidden = false;
      }
      return;
    }

    var playableCount = 0;
    recordings.forEach(function (recording) {
      var item = document.createElement('li');
      item.className = 'recording-item';
      if (recording.playable) { playableCount += 1; }

      var play = document.createElement('button');
      play.type = 'button';
      play.className = 'btn btn--small btn--ghost';
      play.textContent = 'Play';
      play.disabled = !recording.playable;
      if (!recording.playable) {
        play.title = recording.problem || 'This recording cannot be played.';
      }
      play.addEventListener('click', function () {
        if (!recording.playable) {
          if (noteEl) {
            noteEl.textContent = recording.problem || 'This recording cannot be played.';
            noteEl.hidden = false;
          }
          return;
        }
        if (!player) { return; }
        if (errorEl) { errorEl.hidden = true; }
        player.src = recording.playback_url;
        player.hidden = false;
        player.load();
        var started = player.play();
        if (started && started.catch) {
          started.catch(function () {
            if (errorEl) {
              errorEl.textContent =
                'The browser blocked playback or could not decode this file. ' +
                'Use the player controls below to press play.';
              errorEl.hidden = false;
            }
          });
        }
      });

      var remove = document.createElement('button');
      remove.type = 'button';
      remove.className = 'btn btn--small btn--ghost recording-item__delete';
      remove.textContent = 'Delete';
      remove.addEventListener('click', function () {
        api('/api/recording/' + recording.id, { method: 'DELETE' }).then(function () {
          item.parentNode.removeChild(item);
          if (player && player.getAttribute('src') === recording.playback_url) {
            player.pause();
            player.removeAttribute('src');
            player.hidden = true;
          }
          if (options.onChanged) { options.onChanged(); }
        }).catch(function (error) {
          if (noteEl) {
            noteEl.textContent = error.message || 'The recording could not be deleted.';
            noteEl.hidden = false;
          }
        });
      });

      var label = document.createElement('span');
      label.className = 'recording-item__label';
      label.textContent = (recording.filename || 'recording') + ' - ' +
        Math.round((recording.size_bytes || 0) / 1024) + ' KB';

      item.appendChild(play);
      item.appendChild(label);
      item.appendChild(remove);
      container.appendChild(item);

      if (!recording.playable) {
        var problem = document.createElement('p');
        problem.className = 'recording-item__problem';
        problem.textContent = recording.problem || 'This recording cannot be played.';
        item.appendChild(problem);
      }
    });

    if (noteEl) {
      if (!playableCount) {
        noteEl.textContent =
          'A recording entry was saved, but no playable audio file exists for this call.';
        noteEl.hidden = false;
      } else {
        noteEl.hidden = true;
      }
    }
  }

  function resetRecordingPanel() {
    state.consent = 'pending';
    el.recordingConsentState.textContent = 'Consent has not been requested for this call.';
    el.recordingList.innerHTML = '';
    el.recordingListNote.textContent = 'No recording has been made for this call yet.';
    el.recordingListNote.hidden = false;
    el.recordingPlayer.hidden = true;
    el.recordingPlayer.removeAttribute('src');
    el.recordingPlayerError.hidden = true;
    el.recordingPlayerError.textContent = '';
    el.toggleRecordLabel.textContent = 'Start recording';
    updateControls();
  }

  // ------------------------------------------------------------------
  // history
  // ------------------------------------------------------------------

  function formatDate(value) {
    if (!value) { return '-'; }
    var date = new Date(value);
    if (isNaN(date.getTime())) { return value; }
    return date.toLocaleString();
  }

  function loadHistory() {
    return api('/api/calls?limit=50').then(function (body) {
      var calls = body.calls || [];
      state.activeCallId = body.active_call_id || null;
      if (el.storedCalls) { el.storedCalls.textContent = String(body.count || calls.length); }
      if (el.historyCount) {
        el.historyCount.textContent = (body.count || calls.length) + ' call(s) saved';
      }
      renderHistory(calls);
    }).catch(function () {
      if (el.historyCount) { el.historyCount.textContent = 'History could not be loaded'; }
    });
  }

  function renderHistory(calls) {
    el.historyBody.innerHTML = '';
    if (!calls.length) {
      var emptyRow = document.createElement('tr');
      emptyRow.className = 'history-empty';
      var emptyCell = document.createElement('td');
      emptyCell.colSpan = 7;
      emptyCell.textContent = 'No calls saved yet. Start a call to build the history.';
      emptyRow.appendChild(emptyCell);
      el.historyBody.appendChild(emptyRow);
      return;
    }

    calls.forEach(function (call) {
      var row = document.createElement('tr');

      var idCell = document.createElement('td');
      idCell.className = 'mono history-id';
      idCell.textContent = call.id.slice(0, 8);
      idCell.title = call.id;
      if (call.id === state.activeCallId) {
        var liveBadge = document.createElement('span');
        liveBadge.className = 'badge badge--live history-id__live';
        liveBadge.textContent = 'live';
        idCell.appendChild(liveBadge);
      }

      var startedCell = document.createElement('td');
      startedCell.textContent = formatDate(call.started_at);

      var durationCell = document.createElement('td');
      durationCell.textContent = formatTime((call.duration_seconds || 0) * 1000);

      var turnsCell = document.createElement('td');
      turnsCell.textContent = String(call.turn_count || 0);

      var summaryCell = document.createElement('td');
      summaryCell.className = 'history-summary';
      if (call.summary) {
        summaryCell.textContent = call.summary;
        summaryCell.title = call.summary;
      } else if (call.summary_error) {
        summaryCell.textContent = 'Summary failed: ' + call.summary_error;
        summaryCell.classList.add('is-error');
      } else {
        summaryCell.textContent = call.status === 'active' ? 'Call in progress' : 'No summary yet';
      }

      var statusCell = document.createElement('td');
      var select = document.createElement('select');
      select.className = 'select select--compact';
      state.salesStatuses.forEach(function (option) {
        var node = document.createElement('option');
        node.value = option;
        node.textContent = option;
        if (option === call.sales_status) { node.selected = true; }
        select.appendChild(node);
      });
      select.addEventListener('change', function () {
        saveSalesStatus(call.id, select.value, function (error) {
          if (error) { select.value = call.sales_status; }
        });
      });
      statusCell.appendChild(select);

      var actionCell = document.createElement('td');
      var viewButton = document.createElement('button');
      viewButton.type = 'button';
      viewButton.className = 'btn btn--small btn--ghost';
      viewButton.textContent = 'Open';
      viewButton.addEventListener('click', function () { openCall(call.id); });
      actionCell.appendChild(viewButton);

      row.appendChild(idCell);
      row.appendChild(startedCell);
      row.appendChild(durationCell);
      row.appendChild(turnsCell);
      row.appendChild(summaryCell);
      row.appendChild(statusCell);
      row.appendChild(actionCell);
      el.historyBody.appendChild(row);
    });
  }

  // ------------------------------------------------------------------
  // saved call detail
  // ------------------------------------------------------------------

  function consentLabel(consent) {
    if (consent === 'granted') { return 'Granted - customer agreed to recording'; }
    if (consent === 'denied') { return 'Declined - nothing was recorded'; }
    return 'Not requested - nothing was recorded';
  }

  function appendMeta(list, label, value) {
    var dt = document.createElement('dt');
    dt.textContent = label;
    var dd = document.createElement('dd');
    dd.textContent = value;
    list.appendChild(dt);
    list.appendChild(dd);
  }

  function roleLabel(role) {
    if (role === 'assistant') { return 'Assistant'; }
    if (role === 'customer') { return 'Customer'; }
    return 'System';
  }

  function renderDetailTranscript(turns) {
    el.callDetailTranscript.innerHTML = '';
    if (!turns || !turns.length) {
      var empty = document.createElement('p');
      empty.className = 'muted small';
      empty.textContent = 'This call has no saved transcript entries.';
      el.callDetailTranscript.appendChild(empty);
      return;
    }
    turns.forEach(function (turn) {
      var row = document.createElement('div');
      row.className = 'detail__turn detail__turn--' + (turn.role || 'system');

      var head = document.createElement('div');
      head.className = 'detail__turn-head';
      head.textContent = roleLabel(turn.role) + ' - ' + formatDate(turn.at);

      var text = document.createElement('p');
      text.className = 'detail__turn-text';
      text.textContent = turn.text || '';

      row.appendChild(head);
      row.appendChild(text);
      el.callDetailTranscript.appendChild(row);
    });
  }

  function renderCallDetail(call) {
    state.viewedCallId = call.id;
    el.callDetailId.textContent = call.id;

    el.callDetailMeta.innerHTML = '';
    appendMeta(el.callDetailMeta, 'Started', formatDate(call.started_at));
    appendMeta(el.callDetailMeta, 'Ended', call.ended_at ? formatDate(call.ended_at) : 'Still active');
    appendMeta(el.callDetailMeta, 'Duration', formatTime((call.duration_seconds || 0) * 1000));
    appendMeta(el.callDetailMeta, 'Transcript turns', String(call.turn_count || 0));
    appendMeta(el.callDetailMeta, 'Status', call.status === 'active' ? 'Active' : 'Ended');
    appendMeta(el.callDetailMeta, 'Recording consent', consentLabel(call.recording_consent));

    var statuses = (state.salesStatuses && state.salesStatuses.length)
      ? state.salesStatuses.slice()
      : [];
    if (call.sales_status && statuses.indexOf(call.sales_status) === -1) {
      statuses.push(call.sales_status);
    }
    el.callDetailStatus.innerHTML = '';
    statuses.forEach(function (option) {
      var node = document.createElement('option');
      node.value = option;
      node.textContent = option;
      if (option === call.sales_status) { node.selected = true; }
      el.callDetailStatus.appendChild(node);
    });
    el.callDetailStatus.value = call.sales_status || '';
    el.callDetailStatusHint.textContent = 'Changing this updates the saved call.';

    el.callDetailSummary.innerHTML = '';
    el.callDetailSummaryError.hidden = true;
    el.callDetailSummaryError.textContent = '';
    el.callDetailSummaryEmpty.hidden = true;
    el.callDetailRetry.disabled = false;
    el.callDetailRetry.textContent = 'Retry summary';

    if (call.summary) {
      var fields = call.summary_fields || {};
      if (hasSummaryFields(fields)) {
        [
          ['Customer requirements', fields.requirements],
          ['Objections raised', fields.objections],
          ['Next steps', fields.next_steps],
          ['Outcome', fields.outcome]
        ].forEach(function (pair) {
          appendMeta(el.callDetailSummary, pair[0], pair[1] || 'Not mentioned');
        });
      } else {
        appendMeta(el.callDetailSummary, 'Call summary', call.summary);
      }
      el.callDetailRetry.hidden = true;
    } else if (call.summary_error) {
      el.callDetailSummaryError.textContent =
        call.summary_error + ' The transcript and sales status were kept.';
      el.callDetailSummaryError.hidden = false;
      el.callDetailRetry.hidden = false;
    } else {
      el.callDetailSummaryEmpty.hidden = false;
      el.callDetailRetry.hidden = false;
    }

    renderRecordingList({
      container: el.callDetailRecordings,
      player: el.callDetailPlayer,
      errorEl: el.callDetailPlayerError,
      noteEl: el.callDetailRecordingNote,
      recordings: call.recordings || [],
      consent: call.recording_consent,
      onChanged: function () {
        refreshCallDetail();
        loadHistory();
      }
    });

    renderDetailTranscript(call.turns || []);
  }

  function refreshCallDetail() {
    if (!state.viewedCallId) { return Promise.resolve(); }
    var viewed = state.viewedCallId;
    return api('/api/calls/' + encodeURIComponent(viewed)).then(function (body) {
      if (body.call && state.viewedCallId === viewed) {
        renderCallDetail(body.call);
      }
    }).catch(function (error) {
      el.callDetailSummaryError.textContent =
        error.message || 'The saved call could not be refreshed.';
      el.callDetailSummaryError.hidden = false;
    });
  }

  function openCall(callId) {
    api('/api/calls/' + encodeURIComponent(callId)).then(function (body) {
      if (!body.call) { throw new Error('That call could not be opened.'); }
      renderCallDetail(body.call);
      el.callDetailModal.hidden = false;
      el.closeCallDetail.focus();
    }).catch(function (error) {
      var message = error.message || 'That call could not be opened.';
      showBanner('Could not open the saved call', message);
      if (el.historyCount) { el.historyCount.textContent = message; }
    });
  }

  function closeCallDetail() {
    el.callDetailModal.hidden = true;
    el.callDetailPlayer.pause();
    el.callDetailPlayer.removeAttribute('src');
    state.viewedCallId = null;
  }

  function retryViewedSummary() {
    var callId = state.viewedCallId;
    if (!callId) { return; }
    el.callDetailRetry.disabled = true;
    el.callDetailRetry.textContent = 'Retrying...';
    el.callDetailSummaryError.hidden = true;

    postJson('/api/call/summary', { call_id: callId }).then(function (body) {
      if (body.summary_error) {
        el.callDetailSummaryError.textContent =
          body.summary_error + ' The transcript and sales status were kept.';
        el.callDetailSummaryError.hidden = false;
      }
      return refreshCallDetail();
    }).catch(function (error) {
      el.callDetailSummaryError.textContent =
        (error.message || 'The summary could not be generated.') +
        ' The transcript and sales status were kept.';
      el.callDetailSummaryError.hidden = false;
    }).then(function () {
      el.callDetailRetry.disabled = false;
      el.callDetailRetry.textContent = 'Retry summary';
      loadHistory();
    });
  }

  function saveViewedStatus() {
    var callId = state.viewedCallId;
    if (!callId) { return; }
    var status = el.callDetailStatus.value;
    el.callDetailStatusHint.textContent = 'Saving...';
    saveSalesStatus(callId, status, function (error) {
      if (error) {
        el.callDetailStatusHint.textContent =
          error.message || 'The sales status could not be saved.';
        return;
      }
      el.callDetailStatusHint.textContent = 'Saved.';
      refreshCallDetail();
    });
  }

  // ------------------------------------------------------------------
  // restoring a call after a page reload
  // ------------------------------------------------------------------

  function roleToUi(role) {
    if (role === 'assistant') { return 'bot'; }
    if (role === 'customer') { return 'user'; }
    return 'system';
  }

  function turnLabel(turn, index) {
    if (turn.role === 'assistant') {
      return index === 0 ? 'Assistant (greeting)' : 'Assistant';
    }
    if (turn.role === 'customer') {
      if (turn.source === 'voice') { return 'Customer (voice)'; }
      if (turn.source === 'text') { return 'Customer (text)'; }
      return 'Customer';
    }
    return 'System';
  }

  function renderTranscriptFromTurns(turns) {
    clearTranscript();
    (turns || []).forEach(function (turn, index) {
      appendMessage(roleToUi(turn.role), turn.text, { label: turnLabel(turn, index) });
    });
    updateTurnCount();
  }

  function restoreTimer(call) {
    var started = call.started_at ? new Date(call.started_at).getTime() : NaN;
    state.elapsedMs = isNaN(started) ? 0 : Math.max(0, Date.now() - started);
    setTimer(state.elapsedMs);
    if (call.status === 'active') {
      startTimer();
    } else {
      stopTimer();
    }
  }

  function hasSummaryFields(fields) {
    return Object.keys(fields || {}).some(function (key) {
      return fields[key] && String(fields[key]).trim();
    });
  }

  function restoreStoredSummary(call) {
    var fields = call.summary_fields || {};
    if (call.summary) {
      if (hasSummaryFields(fields)) {
        renderSummaryFields(fields, '');
      } else {
        renderSummaryFields({}, call.summary);
      }
      return;
    }
    if (call.summary_error) {
      renderSummary(null, call.summary_error);
      return;
    }
    el.summaryPanel.hidden = true;
    el.summaryGrid.innerHTML = '';
    el.summaryError.hidden = true;
    el.retrySummary.hidden = true;
  }

  function consentStateText(consent) {
    if (consent === 'granted') {
      return 'Consent granted for this call. Recording is allowed.';
    }
    if (consent === 'denied') {
      return 'Consent denied. Nothing is recorded for this call.';
    }
    return 'Consent has not been requested for this call.';
  }

  function restoreRecordingPanel(call) {
    var recordings = call.recordings || [];
    state.consent = call.recording_consent || 'pending';
    state.recordingCount = recordings.length;
    el.recordingConsentState.textContent = consentStateText(state.consent);
    el.recordingList.innerHTML = '';
    el.recordingPlayer.hidden = true;
    el.recordingPlayer.removeAttribute('src');
    el.recordingPlayerError.hidden = true;
    el.recordingPlayerError.textContent = '';
    el.toggleRecordLabel.textContent = 'Start recording';
    renderRecordings(recordings);
  }

  function restoreCallIntoPanel(call) {
    state.restoredCallId = call.id;
    state.showingEndedCall = call.status !== 'active';
    renderTranscriptFromTurns(call.turns || []);
    applyCallState(call);
    restoreTimer(call);
    restoreStoredSummary(call);
    restoreRecordingPanel(call);
    updateControls();
  }

  function showResumeBanner(title, text) {
    el.resumeBannerTitle.textContent = title;
    el.resumeBannerText.textContent = text;
    el.resumeBanner.hidden = false;
  }

  function hideResumeBanner() {
    el.resumeBanner.hidden = true;
  }

  function describeCall(call) {
    return 'call ' + String(call.id || '').slice(0, 8) +
      ' (started ' + formatDate(call.started_at) + ')';
  }

  function showActiveCallBanner(call) {
    state.micPausedByReload = true;
    state.micMuted = true;
    updateMicButton();
    showResumeBanner(
      'This call was still open when the page reloaded',
      describeCall(call) + ' is still active on the server, and the transcript below is the one ' +
      'saved in the database. The microphone and any recording stopped when the page reloaded - ' +
      'they did not keep running in the background. Continue the call to pick it up where you ' +
      'left off, or end it and start a new one.'
    );
    setStatus('Call open - microphone paused', 'idle');
    setListening('Paused');
    el.micHint.textContent =
      'The microphone is off after the reload. Press "Continue this call" to switch it back on, ' +
      'or keep typing to carry on in this call.';
  }

  function showLastCallNote(call) {
    hideResumeBanner();
    setStatus('Idle - showing the last saved call', 'idle');
    el.micHint.textContent =
      'Showing the last saved call (' + describeCall(call) + ', ended ' +
      formatDate(call.ended_at) + '). Start a new call to continue selling, or open the call in ' +
      'the history below.';
  }

  function continueRestoredCall() {
    hideResumeBanner();
    state.micPausedByReload = false;

    if (capabilities.speechRecognition) {
      state.micMuted = false;
      updateMicButton();
      startRecognition();
      setStatus('Connected', 'live');
      setListening('Listening');
      el.micHint.textContent =
        'Listening for the customer. Speak naturally - pause when you finish a sentence.';
      appendMessage('system',
        'Microphone switched back on. The transcript above is the one saved in the database.',
        { label: 'System' });
    } else {
      state.micMuted = true;
      updateMicButton();
      setStatus('Connected - text mode', 'live');
      el.micHint.textContent =
        'Voice input is unavailable in this browser. Keep typing to carry on in this call.';
      appendMessage('system',
        'Continued in text mode - this browser cannot listen to the microphone.',
        { label: 'System' });
    }

    if (state.consent === 'granted') {
      appendMessage('system',
        'The recording did not continue in the background. Anything that was being recorded ' +
        'stopped with the page, so press Start recording to record again.',
        { label: 'System' });
    }
  }

  function endRestoredAndStartNew() {
    var callId = state.callId;
    hideResumeBanner();

    if (!callId) {
      resetForNewCall();
      startCall();
      return;
    }

    el.startCall.disabled = true;
    setStatus('Ending the earlier call...', 'connecting');
    postJson('/api/call/end', { call_id: callId }).then(function () {
      resetForNewCall();
      startCall();
    }).catch(function (error) {
      appendMessage('error', error.message || 'The earlier call could not be closed cleanly.');
      resetForNewCall();
      setStatus('Idle - no active call', 'idle');
    });
  }

  function restoreActiveCall() {
    return api('/api/call/active').then(function (body) {
      if (!body.call) { return null; }
      restoreCallIntoPanel(body.call);
      return body.call;
    }).catch(function () { return null; });
  }

  function restoreLastSavedCall() {
    return api('/api/calls?limit=1').then(function (body) {
      var calls = body.calls || [];
      if (!calls.length) { return null; }
      var latest = calls[0];
      return api('/api/calls/' + encodeURIComponent(latest.id)).then(function (detail) {
        if (!detail.call) { return null; }
        restoreCallIntoPanel(detail.call);
        showLastCallNote(detail.call);
        return detail.call;
      });
    }).catch(function () { return null; });
  }

  function resumePendingMessage(call) {
    var pending = readPendingMessage();
    if (!pending) { return; }

    if (!call || call.status !== 'active') {
      clearPendingMessage();
      return;
    }

    var turns = call.turns || [];
    var index = -1;
    for (var i = 0; i < turns.length; i += 1) {
      if (turns[i].client_message_id && turns[i].client_message_id === pending.messageId) {
        index = i;
        break;
      }
    }

    if (index === -1) {
      // The message never reached the server. It is only picked up again when
      // it can only belong to this call.
      if (pending.callId && pending.callId !== call.id) {
        clearPendingMessage();
        return;
      }
      if (!pending.callId && turns.length > 1) {
        clearPendingMessage();
        return;
      }
      sendMessage(pending.text, pending.source, { messageId: pending.messageId });
      return;
    }

    var next = turns[index + 1];
    if (next && next.role === 'assistant') {
      // The reply was saved as well, so there is nothing left to do.
      clearPendingMessage();
      return;
    }

    // The message was saved but the reply was not: ask for the reply again
    // with the same id, so the customer turn is never stored twice.
    sendMessage(pending.text, pending.source, { retry: true, messageId: pending.messageId });
  }

  function restoreAfterReload() {
    return api('/api/call/active').then(function (body) {
      if (body.call) {
        restoreCallIntoPanel(body.call);
        showActiveCallBanner(body.call);
        resumePendingMessage(body.call);
        return body.call;
      }
      return restoreLastSavedCall().then(function (call) {
        resumePendingMessage(call);
        return call;
      });
    }).catch(function () {
      return null;
    });
  }

  // ------------------------------------------------------------------
  // events
  // ------------------------------------------------------------------

  el.startCall.addEventListener('click', function () {
    if (state.callEnded || (!state.callId && transcriptHasContent())) {
      resetForNewCall();
    }
    startCall();
  });

  el.endCall.addEventListener('click', endCall);

  el.toggleMic.addEventListener('click', function () {
    state.micMuted = !state.micMuted;
    updateMicButton();
    if (state.micMuted) {
      stopRecognition();
      setListening('Paused');
      if (state.callActive) { setStatus('Connected', 'live'); }
    } else {
      startRecognition();
      setListening('Listening');
    }
  });

  el.toggleVoice.addEventListener('click', function () {
    state.voiceEnabled = !state.voiceEnabled;
    el.toggleVoiceLabel.textContent = state.voiceEnabled ? 'Voice on' : 'Voice off';
    if (!state.voiceEnabled && capabilities.speechSynthesis) {
      try { window.speechSynthesis.cancel(); } catch (error) { /* ignore */ }
    }
    if (state.voiceEnabled) { pickVoice(); }
  });

  el.composer.addEventListener('submit', function (event) {
    event.preventDefault();
    var text = el.input.value.trim();
    if (!text) { return; }
    el.input.value = '';
    handleCustomerUtterance(text, 'text');
  });

  el.clearTranscript.addEventListener('click', function () {
    clearTranscript();
  });

  el.dismissBanner.addEventListener('click', function () {
    el.banner.hidden = true;
  });

  el.resumeContinue.addEventListener('click', continueRestoredCall);
  el.resumeNewCall.addEventListener('click', endRestoredAndStartNew);
  el.resumeDismiss.addEventListener('click', function () {
    hideResumeBanner();
    state.micPausedByReload = false;
  });

  el.salesStatus.addEventListener('change', function () {
    if (!state.callId) { return; }
    saveSalesStatus(state.callId, el.salesStatus.value);
  });

  el.retrySummary.addEventListener('click', retrySummary);

  el.askConsent.addEventListener('click', openConsentModal);
  el.consentGrant.addEventListener('click', function () { submitConsent('granted'); });
  el.consentDeny.addEventListener('click', function () { submitConsent('denied'); });
  el.toggleRecord.addEventListener('click', function () {
    if (state.recorder) { stopRecording(); } else { startRecording(); }
  });

  el.refreshHistory.addEventListener('click', function () { loadHistory(); });

  el.closeCallDetail.addEventListener('click', closeCallDetail);
  el.callDetailRetry.addEventListener('click', retryViewedSummary);
  el.callDetailStatus.addEventListener('change', saveViewedStatus);
  el.callDetailModal.addEventListener('click', function (event) {
    if (event.target === el.callDetailModal) { closeCallDetail(); }
  });
  document.addEventListener('keydown', function (event) {
    if (event.key === 'Escape' && !el.callDetailModal.hidden) { closeCallDetail(); }
  });

  el.recordingPlayer.addEventListener('error', function () {
    el.recordingPlayerError.textContent =
      'This audio could not be played. The file may be missing, empty or in an ' +
      'unsupported format.';
    el.recordingPlayerError.hidden = false;
  });
  el.callDetailPlayer.addEventListener('error', function () {
    el.callDetailPlayerError.textContent =
      'This audio could not be played. The file may be missing, empty or in an ' +
      'unsupported format.';
    el.callDetailPlayerError.hidden = false;
  });

  window.addEventListener('beforeunload', function () {
    stopRecognition();
    stopTimer();
  });

  // ------------------------------------------------------------------
  // boot
  // ------------------------------------------------------------------

  function transcriptHasContent() {
    return el.transcript.querySelectorAll('.msg--user, .msg--bot').length > 0;
  }

  var configReady = api('/api/config').then(function (body) {
    state.salesStatuses = body.sales_statuses || [];
    if (state.salesStatuses.length && !state.callId) {
      el.salesStatus.value = state.salesStatuses[0];
    }
    if (body.limits && body.limits.chat_timeout_seconds) {
      state.chatTimeoutSeconds = Number(body.limits.chat_timeout_seconds) || 0;
    }
  }).catch(function () { /* the page still works without it */ });

  if (capabilities.speechSynthesis) {
    pickVoice();
    window.speechSynthesis.onvoiceschanged = pickVoice;
  }

  checkCapabilities();
  updateControls();
  setTimer(0);
  updateTurnCount();
  loadHistory();

  // Rebuild the panel from the database, so a reload brings back the call
  // that was already there instead of opening a second one.
  configReady.then(function () { return restoreAfterReload(); });
})();
