import os
os.environ['OPENBLAS_NUM_THREADS'] = '1'

from pathlib import Path
import shutil
import subprocess

import pytest


JS = r'''
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const root = process.argv[1];
const variant = process.argv[2];
const counts = { settings: 0, flow: 0, attach: [], history: [], around: [] };
const classes = () => {
    const values = new Set();
    return {
        add: (...names) => names.forEach(n => values.add(n)),
        remove: (...names) => names.forEach(n => values.delete(n)),
        toggle: (name, on) => on ? values.add(name) : values.delete(name),
        contains: name => values.has(name),
    };
};
const element = () => ({
    innerHTML: '', textContent: '', classList: classes(), dataset: {},
    querySelector: () => null, querySelectorAll: () => [],
});
const field = {
    tagName: 'DIV', innerHTML: '', innerText: '', value: '', style: {}, events: [],
    dispatchEvent(event) { this.events.push(event.type); },
};
const attach = element();
const activeInput = element();
const input = element();
input.dataset.renderedChat = '42';
input.querySelector = selector => ({
    '#message-input': field, '#attach-btn': attach, '.chat-input-area': activeInput,
})[selector] || null;
const messages = element();
const main = element();
const title = element();
const inner = element();
const agentView = element();
agentView.querySelector = selector => selector === '.message-input-container' ? input : null;
const doc = {
    querySelector: selector => ({ '.chat-main': main, '.chat-main-inner': inner })[selector] || null,
    querySelectorAll: () => [],
    getElementById: id => ({ 'view-agent': agentView, 'main-title': title })[id] || null,
    createRange: () => ({ selectNodeContents() {}, collapse() {} }),
};
const win = {
    __catchpmApp: {},
    getSelection: () => ({ removeAllRanges() {}, addRange() {} }),
    FileAttach: { init: (...args) => counts.attach.push(args) },
    ToolsPanel: { saveUserSettings: () => counts.settings++ },
    FlowViz: { reset: () => counts.flow++ },
};
const context = vm.createContext({
    window: win, document: doc, console, Event, setTimeout, clearTimeout,
    fetch: () => { throw Error('Unexpected network request'); },
});
const source = fs.readFileSync(path.join(root, variant), 'utf8');
assert.ok(source.includes('export default app;'));
vm.runInContext(source.replace('export default app;', 'globalThis.ChatApp = ChatApp;'), context);
vm.runInContext(fs.readFileSync(path.join(root, 'input-drafts.js'), 'utf8'), context);
const drafts = win.InputDrafts;
// Run the real state-only constructor, including main-app stream/ws proxies.
const app = new context.ChatApp();
app.currentChat = 42;
app.currentTab = 'agent';
app._syncBotPlaceholder = () => {};
app.getMessagesContainer = () => messages;
app._syncInputPreviewBar = () => {};
app._insertInputChip = (target, text, options) => {
    assert.strictEqual(target, field);
    app._pendingChips.set(options.restoreId, text);
    if (options.asFile) app._fileChips.add(options.restoreId);
};
const fail = name => () => { throw Error(`Unexpected ${name}`); };
for (const method of [
    'loadMessages', 'loadMessagesAround', '_restoreRunningUI', 'connectWebSocket',
    '_getStream', '_setSendButtonMode', '_resetNoToolsWarn', '_reattachBackgroundStream',
]) app[method] = fail(method);

async function sameChat() {
    const html = '  live&nbsp;text<div><br></div><div><b>format</b> ' +
        '<span class="skill-mention" contenteditable="false">skill</span></div>' +
        '<span class="input-chip-anchor" data-chip-id="chip-1"></span><div><br></div>';
    field.innerHTML = html;
    messages.innerHTML = '<article>existing history and streaming answer</article>';
    app._pendingChips = new Map([['chip-1', 'long pasted text']]);
    app._fileChips = new Set(['chip-1']);
    app._inputDrafts = { '42': { html: '<div>obsolete draft</div>', chips: [] } };
    app.subagentStates = { worker: { steps: ['working'] } };
    app._runState = 'running';
    app._activeRequestId = 'request-live';
    app._pagination = { loaded: 150, hasMore: true };
    app._streams = { '42': { ...app._newStreamState(), streamingActive: true } };
    app._sockets = {}; // main ws getter/setter proxies this per-chat socket bank
    app.ws = { readyState: 1 };
    app._sources = { '42': { readyState: 1 } };
    app._activePreviewChipId = 'chip-1';
    const references = Object.fromEntries([
        '_pendingChips', '_fileChips', '_inputDrafts', 'subagentStates',
        '_pagination', '_streams', '_sockets', 'ws', '_sources',
    ].map(key => [key, app[key]]));
    const save = app._saveInputDraft;
    const restore = app._restoreInputDraft;
    app._saveInputDraft = fail('saving same-chat draft');
    app._restoreInputDraft = fail('restoring stale same-chat draft');
    for (const tab of ['agent', 'plugins', 'skills', 'graph', 'group']) {
        app.currentTab = tab;
        const before = counts.attach.length;
        await app.openChat('42');
        assert.equal(app.currentTab, 'agent');
        assert.strictEqual(app.currentChat, 42);
        assert.equal(field.innerHTML, html);
        assert.equal(messages.innerHTML, '<article>existing history and streaming answer</article>');
        assert.equal(app._runState, 'running');
        assert.equal(app._activeRequestId, 'request-live');
        assert.equal(app._activePreviewChipId, 'chip-1');
        for (const [key, value] of Object.entries(references)) assert.strictEqual(app[key], value);
        assert.equal(app._pendingChips.get('chip-1'), 'long pasted text');
        assert.ok(app._fileChips.has('chip-1'));
        assert.equal(counts.attach.length, before + (tab === 'agent' ? 0 : 1));
        if (tab !== 'agent') {
            const args = counts.attach.at(-1);
            assert.strictEqual(args[0], attach);
            assert.strictEqual(args[1], input);
            assert.equal(args[2], 'a:42');
        }
    }
    assert.equal(counts.settings, 0);
    assert.equal(counts.flow, 0);
    app._saveInputDraft = save;
    app._restoreInputDraft = restore;
}

async function targetsAndSwitching() {
    app.currentChat = '42';
    app._noToolsWarnChatId = '42';
    app._setSendButtonMode = () => {};
    app._getStream = () => null;
    app._resetNoToolsWarn = () => {};
    app._reattachBackgroundStream = () => {};
    app._restoreRunningUI = async () => {};
    app.connectWebSocket = () => {};
    app.loadMessages = async id => counts.history.push(id);
    app.loadMessagesAround = async (...args) => counts.around.push(args);
    const html = field.innerHTML;
    for (const opts of [
        { targetMessageId: 'message-8', radius: 7 },
        { targetDbId: 'db-9' },
    ]) {
        await app.openChat('42', opts);
        const call = counts.around.at(-1);
        assert.equal(call[0], '42');
        assert.equal(call[1], opts.targetMessageId || opts.targetDbId);
        assert.equal(call[2], opts.radius || 10);
        assert.equal(call[3].dbId, opts.targetDbId);
        assert.equal(field.innerHTML, html, 'target navigation must not restore an obsolete draft');
        assert.equal(app._pendingChips.get('chip-1'), 'long pasted text');
        assert.ok(app._fileChips.has('chip-1'));
    }
    assert.equal(counts.around.length, 2);
    assert.equal(counts.history.length, 0);
    app.renderMessageInput = async () => {
        field.innerHTML = '';
        input.dataset.renderedChat = String(app.currentChat);
    };
    app._activePreviewChipId = null;
    await app.openChat('43');
    assert.equal(counts.history.at(-1), '43');
    assert.equal(field.innerHTML, '');
    assert.equal(app._pendingChips.size, 0);
    assert.equal(app._fileChips.size, 0);
    field.innerHTML = '  other chat<div><br></div>';
    await app.openChat('42');
    assert.equal(counts.history.at(-1), '42');
    assert.equal(field.innerHTML, html);
    assert.equal(app._pendingChips.get('chip-1'), 'long pasted text');
    assert.ok(app._fileChips.has('chip-1'));
    assert.equal(app._inputDrafts['43'].html, '  other chat<div><br></div>');
}

function draftRoundTrips() {
    app.getInputText = fail('trimmed send-text read during draft save');
    app._pendingChips = new Map();
    app._fileChips = new Set();
    for (const html of [
        '  leading and trailing  ',
        '<div>first</div><div><br></div><div><b>second</b></div><div><br></div>',
        'before&nbsp;<span class="skill-mention" contenteditable="false" data-skill-id="7">@skill</span> after ',
        '<div><br></div>',
        '',
    ]) {
        field.innerHTML = html;
        drafts.save(app, null);
        assert.equal(app._inputDrafts.__start__.html, html);
        assert.equal(app._inputDrafts.__start__.chips.length, 0);
        field.innerHTML = 'wrong';
        drafts.restore(app, null);
        assert.equal(field.innerHTML, html);
        assert.equal(field.events.at(-1), 'input');
    }
    field.tagName = 'TEXTAREA';
    for (const value of ['  original\n\ntext\n  ', ' \n\t ']) {
        field.value = value;
        drafts.save(app, 'textarea');
        assert.equal(app._inputDrafts.textarea, value);
        field.value = '';
        drafts.restore(app, 'textarea');
        assert.equal(field.value, value);
    }
    field.value = '';
    drafts.save(app, 'textarea');
    assert.ok(!Object.hasOwn(app._inputDrafts, 'textarea'));
    app._inputDrafts.legacy = '  old string draft\n';
    drafts.restore(app, 'legacy');
    assert.equal(field.value, '  old string draft\n');
    field.tagName = 'DIV';
    drafts.restore(app, 'legacy');
    assert.equal(field.innerText, '  old string draft\n');
    app._inputDrafts.oldObject = { html: '<i>old chip draft</i>', chips: [['old', 'text']] };
    drafts.restore(app, 'oldObject');
    assert.equal(field.innerHTML, '<i>old chip draft</i>');
    assert.equal(app._pendingChips.get('old'), 'text');
    assert.equal(app._fileChips.size, 0);
    app._inputDrafts.htmlOnly = { html: '<p>html without chip metadata</p>' };
    drafts.restore(app, 'htmlOnly');
    assert.equal(field.innerHTML, '<p>html without chip metadata</p>');
    assert.equal(app._pendingChips.size, 0);
}

(async () => {
    await sameChat();
    await targetsAndSwitching();
    draftRoundTrips();
    console.log(`${variant}: same-chat, targets, chat round-trip, HTML, textarea, legacy drafts OK`);
})().catch(error => { console.error(error); process.exitCode = 1; });
'''


@pytest.mark.parametrize('variant', ['app.js', 'app_sigma.js'])
def test_chat_navigation_and_drafts(variant):
    node = shutil.which('node')
    if not node:
        pytest.skip('Node.js is required for executable navigation checks')
    static_js = Path(__file__).resolve().parents[1] / 'static' / 'js'
    result = subprocess.run(
        [node, '-e', JS, str(static_js), variant],
        capture_output=True, text=True, timeout=20,
    )
    assert result.returncode == 0, result.stdout + result.stderr
