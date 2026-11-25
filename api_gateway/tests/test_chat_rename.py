"""Rename starts with the live sidebar title, not a generic chat number."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest


@pytest.mark.parametrize('filename', ['app.js', 'app_sigma.js'])
def test_rename_initial_title(filename):
    if not shutil.which('node'):
        pytest.skip('node is required')
    source = (Path(__file__).parents[1] / 'static/js' / filename).read_text()
    method = source.split('    async _renameChat(chatId, chat) {', 1)[1].split('    _askChatRename(', 1)[0]
    script = r'''
const assert = require('node:assert/strict');
let rows = [], initial;
global.document = {querySelectorAll: () => rows};
global.fetch = () => { throw Error('Cancellation must not send a request'); };
const app = {
  _askChatRename: async title => { initial = title; return null; },
  _renameChat: new (Object.getPrototypeOf(async function(){}).constructor)('chatId','chat',METHOD)
};
(async () => {
 const cases = [
  {visible:'  Живое & <название>  ', chat:{renamed_title:'Старое'}, expected:'Живое & <название>'},
  {visible:'Автоматическое название', chat:{auto_title:'Устарело'}, expected:'Автоматическое название'},
  {visible:'Тема из сообщения', chat:{}, expected:'Тема из сообщения'},
  {chat:{renamed_title:'  Ручное  ',auto_title:'Авто'},expected:'Ручное'},
  {chat:{auto_title:'  Авто  '},expected:'Авто'},
  {chat:{},expected:'Новый чат'}
 ];
 for (const c of cases) {
  rows = [{dataset:{chatId:'99'},querySelector:()=>({textContent:'Не тот чат'})}];
  if ('visible' in c) rows.push({dataset:{chatId:'42'},querySelector:()=>({textContent:c.visible})});
  await app._renameChat(42,c.chat);
  assert.equal(initial,c.expected);
 }
})().catch(e => {console.error(e);process.exitCode=1;});
'''.replace('METHOD', json.dumps(method.rsplit('}', 1)[0]))
    result = subprocess.run(['node', '-e', script], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
