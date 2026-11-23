// Run from the repository root with jsdom@24.1.3 available via NODE_PATH.
// See agent_service/docs/streaming.md for isolated dependency installation.
// Uses complete production classes, skipping only module bootstrap/constructor UI setup.
const { JSDOM } = require('jsdom');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const assert = require('node:assert/strict');
let passed = 0;
async function main() {
for (const file of ['app.js', 'app_sigma.js']) {
  function fixture() {
    const dom = new JSDOM('<main id="messages"><div class="user-message-wrapper">question</div></main>', { url: 'https://test.local/', runScripts: 'outside-only' });
    const w = dom.window;
    const raf = new Map(); let nextId = 0;
    w.requestAnimationFrame = fn => { raf.set(++nextId, fn); return nextId; };
    w.cancelAnimationFrame = id => raf.delete(id);
    w.setInterval = () => ++nextId; w.clearInterval = () => {};
    const source = fs.readFileSync(path.join(__dirname, '../../static/js', file), 'utf8');
    vm.runInContext(source.slice(0, source.indexOf('/* ── Точка входа')) + '\nglobalThis.TestChatApp = ChatApp;', dom.getInternalVMContext(), { filename: file });
    const app = Object.create(w.TestChatApp.prototype);
    Object.assign(app, { currentChat: 'A', _streams: {}, _sources: {}, _sockets: {}, chats: [] });
    const box = w.document.getElementById('messages');
    app.getMessagesContainer = () => box;
    for (const method of ['_dedupeTurnReasoningAndActions', '_clearAllStatuses', '_removeImageGenSkeleton', '_applyCodeHeaders', '_onRunFinished', '_flattenWaveIndicator', '_removeBotPlaceholder']) app[method] = () => {};
    // Renderer is deliberately mocked: assertions here cover stream state/DOM ownership,
    // not markdown rendering (which requires other production plugins).
    app._renderBotStructuredContent = (el, text) => { el.textContent = text; };
    app.loadMessages = async () => {};
    app.createMessageActions = () => { const el = w.document.createElement('div'); el.className = 'message-actions'; return el; };
    const flush = () => {
      let turns = 0;
      while (raf.size) {
        assert.ok(++turns < 30, 'RAF must settle');
        const batch = [...raf]; raf.clear();
        for (const [, fn] of batch) fn(turns * 16);
      }
    };
    const streams = {};
    class FakeEventSource {
      constructor() { this.handlers = {}; }
      addEventListener(kind, fn) { (this.handlers[kind] ||= []).push(fn); }
      async emit(kind, data) { await Promise.all((this.handlers[kind] || []).map(fn => fn({ data: JSON.stringify(data) }))); }
      close() {}
    }
    w.EventSource = FakeEventSource;
    function send(chat, kind, data = {}) {
      if (file === 'app.js') app._onWsMessage(chat, { data: JSON.stringify({ ...data, kind, is_from_bot: true }) });
      else {
        if (!streams[chat]) { app._startStatusPolling(chat); streams[chat] = app._sources[chat]; }
        return streams[chat].emit(kind, data);
      }
    }
    return { app, box, send, flush, w, close: () => dom.window.close() };
  }
  async function test(name, fn) {
    const f = fixture();
    try { await fn(f); passed++; console.log(`PASS ${file}: ${name}`); }
    finally { f.close(); }
  }
  const literal = '**not bold** <b>literal</b>\n  indented & text';
  function assertPlain(el) {
    assert.equal(el.textContent, literal);
    assert.equal(el.style.whiteSpace, 'pre-wrap');
    assert.equal(el.children.length, 0, 'reasoning must not interpret HTML/markdown');
  }
  await test('running restoration is plain text, deduplicated, no ReferenceError', ({ app, box }) => {
    const state = { steps: [literal, literal], live_text: 'live summary', duration_sec: 5 };
    app._restoreReasoningFromServer('A', state, true);
    app._restoreReasoningFromServer('A', state, true);
    const steps = box.querySelectorAll('.reasoning-step');
    assert.equal(steps.length, 1); assertPlain(steps[0]);
  });
  await test('completed restoration matches live/plain representation', ({ app, box, w }) => {
    const bot = w.document.createElement('div'); bot.className = 'agent-message'; box.append(bot);
    app._restoreReasoningFromServer('A', { steps: [literal], is_final: true }, false);
    assertPlain(bot.querySelector('.reasoning-step'));
  });
  await test('live reasoning + candidate use independent RAF/buffers; reasoning_end preserves candidate', ({ app, box, send, flush }) => {
    send('A', 'live', { chunk: literal });
    send('A', 'answer_delta', { chunk: 'Candidate ' }); flush();
    const st = app._getStream('A');
    assert.equal(st.liveBlockEl.textContent, literal);
    assert.equal(st.answerCandidateEl.textContent, 'Candidate ');
    send('A', 'answer_delta', { chunk: 'answer' });
    send('A', 'reasoning_end');
    assertPlain(box.querySelector('.reasoning-step'));
    assert.equal(st.answerBuffer, 'answer');
    flush(); assert.equal(st.answerCandidateEl.textContent, 'Candidate answer');
    send('A', 'answer_end');
    send('A', 'final_commit'); flush();
    assert.equal(st.liveBlockEl.textContent, 'Candidate answer');
    assert.equal(st.liveBlockEl.className, 'live-block');
    assert.equal(st.liveBlockEl.style.whiteSpace, '');
    assert.equal(box.querySelectorAll('.reasoning-step').length, 1);
    assert.equal(box.querySelectorAll('.answer-candidate').length, 0);
  });
  await test('answer_end drains unpainted answer and reasoning; final_commit never swallows answer', ({ app, send, flush }) => {
    send('A', 'live', { chunk: literal });
    send('A', 'answer_delta', { chunk: 'unpainted' });
    send('A', 'answer_end'); send('A', 'reasoning_end'); send('A', 'final_commit'); flush();
    const st = app._getStream('A');
    assert.equal(st.streamShown, 'unpainted'); assert.equal(st.liveBlockEl.textContent, 'unpainted');
    assertPlain(st.pendingBotEl.querySelector('.reasoning-step'));
  });
  await test('reset cancels pending RAF, removes only rejected candidate, preserves reasoning', ({ app, send, flush }) => {
    send('A', 'live', { chunk: literal });
    send('A', 'answer_delta', { chunk: 'rejected' });
    const st = app._getStream('A'); const rejected = st.answerCandidateEl;
    send('A', 'answer_reset'); flush();
    assert.equal(rejected.isConnected, false); assert.equal(st.answerRaf, null);
    assert.equal(st.streamShown, literal);
    send('A', 'answer_delta', { chunk: 'accepted' });
    send('A', 'final_commit'); // marker ordering must not promote the reasoning as the answer
    send('A', 'answer_end', { chunk: 'Authoritative final' }); flush();
    assert.equal(st.liveBlockEl.textContent, 'Authoritative final');
    assertPlain(st.pendingBotEl.querySelector('.reasoning-step'));
    assert.ok(!st.pendingBotEl.textContent.includes('rejected'));
  });
  await test('authoritative empty end is not replaced by candidate text', ({ app, send }) => {
    send('A', 'answer_delta', { chunk: 'discard me' });
    send('A', 'answer_end', { chunk: '' });
    assert.equal(app._getStream('A').liveBlockEl.textContent, '');
  });
  await test('per-chat candidate and reasoning isolation, inactive DOM stays detached', ({ app, box, send, flush }) => {
    send('A', 'live', { chunk: 'thought A' }); send('B', 'live', { chunk: 'thought B' });
    send('A', 'answer_delta', { chunk: 'answer A' }); send('B', 'answer_delta', { chunk: 'answer B' }); flush();
    const a = app._getStream('A'), b = app._getStream('B');
    assert.equal(b.pendingBotEl.isConnected, false);
    send('B', 'answer_reset'); assert.equal(a.answerCandidateEl.textContent, 'answer A');
    send('B', 'answer_delta', { chunk: 'replacement B' });
    send('A', 'answer_end'); send('B', 'reasoning_end'); send('B', 'answer_end');
    send('A', 'final_commit'); send('B', 'final_commit'); flush();
    assert.equal(a.liveBlockEl.textContent, 'answer A'); assert.equal(b.liveBlockEl.textContent, 'replacement B');
    assert.equal(a.pendingBotEl.querySelector('.reasoning-step').textContent, 'thought A');
    assert.equal(b.pendingBotEl.querySelector('.reasoning-step').textContent, 'thought B');
    assert.ok(!box.textContent.includes('replacement B'));
    app._hardResetStreamState('A'); assert.equal(b.liveBlockEl.textContent, 'replacement B');
  });
  await test('finalization before answer_end preserves candidate, commits reasoning, cancels RAF', ({ app, send, flush }) => {
    send('A', 'live', { chunk: literal });
    send('A', 'answer_delta', { chunk: 'buffered candidate' });
    const st = app._getStream('A'), bot = st.pendingBotEl;
    app._finalizeStream('A'); flush();
    assert.equal(bot.querySelector('.live-block').textContent, 'buffered candidate');
    assertPlain(bot.querySelector('.reasoning-step'));
    assert.equal(bot.querySelectorAll('.answer-candidate').length, 0);
    assert.equal(st.answerRaf, null); assert.equal(st.streamRaf, null);
    assert.equal(st.pendingBotEl, null);
  });
  await test('transport done path completes without ReferenceError and preserves reasoning', async ({ app, send, flush }) => {
    send('A', 'live', { chunk: literal });
    send('A', 'answer_delta', { chunk: 'candidate before done' });
    const st = app._getStream('A'), bot = st.pendingBotEl;
    if (file === 'app.js') {
      app._onWsMessage('A', { data: JSON.stringify({ is_from_bot: true, done: true, message: 'DB answer', message_id: 'request-1' }) });
    } else {
      await send('A', 'done', { message: { message: 'DB answer' } });
    }
    flush();
    assertPlain(bot.querySelector('.reasoning-step'));
    assert.equal(bot.querySelectorAll('.answer-candidate').length, 0);
    assert.equal(st.pendingBotEl, null); assert.equal(st.answerRaf, null);
    if (file === 'app.js') assert.equal(bot.querySelector('.live-block').textContent, 'DB answer');
    else assert.equal(app._sources.A, undefined);
  });
  await test('legacy final_commit clears reasoning whitespace and unhides final', ({ app, send, flush }) => {
    send('A', 'live', { chunk: literal }); flush();
    const st = app._getStream('A'); assert.equal(st.liveBlockEl.style.whiteSpace, 'pre-wrap');
    send('A', 'final_commit');
    assert.equal(st.liveBlockEl.className, 'live-block'); assert.equal(st.liveBlockEl.style.whiteSpace, '');
    assert.equal(st.liveBlockEl.style.display, ''); assert.equal(st.liveBlockEl.textContent, literal);
  });
  await test('finalizer normalizes inline whitespace on legacy final without marker', ({ app, send, flush }) => {
    send('A', 'live', { chunk: literal }); flush();
    const st = app._getStream('A'), bot = st.pendingBotEl;
    st.streamFinalData = { fullText: 'Final content', message_id: 'msg-final' };
    app._finalizeStream('A');
    const final = bot.querySelector('.live-block');
    assert.equal(final.textContent, 'Final content'); assert.equal(final.style.whiteSpace, '');
    assert.equal(bot.querySelectorAll('.message-actions').length, 1);
  });
}
console.log(`\n${passed} integration tests passed.`);

}
main().catch(err => { console.error(err); process.exitCode = 1; });
