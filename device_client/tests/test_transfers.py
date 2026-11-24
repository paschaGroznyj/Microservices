"""Actual loopback HTTP byte transport; no external services or GUI needed."""
import hashlib
import json
import os
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from device_client.core import BridgeClient
from device_client.executor import ExecConfig, Executor
from device_client.transfers import FileTransport, MAX_BYTES


@pytest.fixture
def env(tmp_path):
    cfg = ExecConfig(tmp_path/'config'/'config.json')
    work = tmp_path/'workspace'
    work.mkdir()
    cfg.data.update(exec_enabled=True, workspace_dir=str(work), blocked_paths=[str(tmp_path/'blocked')])
    return cfg, Executor(cfg), work


@pytest.fixture
def server():
    state = {'body': b'\xff\x00raw bytes', 'requests':[], 'status':200, 'header_sha':None}
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args): pass
        def do_GET(self):
            state['requests'].append((self.command,self.path,dict(self.headers)))
            self.send_response(state['status'])
            self.send_header('Content-Length',str(state.get('length',len(state['body']))))
            if state['header_sha']:
                self.send_header('X-Content-SHA256',state['header_sha'])
            if state['status']==302:
                self.send_header('Location','http://example.invalid/stolen')
            self.end_headers()
            self.wfile.write(state['body'])
        def do_PUT(self):
            self.do_GET = None
            state['requests'].append((self.command,self.path,dict(self.headers)))
            state['uploaded'] = self.rfile.read(int(self.headers['Content-Length']))
            self.send_response(state['status'])
            self.send_header('Content-Length','2')
            self.end_headers()
            self.wfile.write(b'{}')
    server = ThreadingHTTPServer(('127.0.0.1',0),Handler)
    worker = threading.Thread(target=server.serve_forever,daemon=True)
    worker.start()
    try:
        yield 'http://127.0.0.1:%s' % server.server_port, state
    finally:
        server.shutdown(); server.server_close(); worker.join(2)


def command(operation='file_download', **kwargs):
    return dict(operation=operation,transfer_id=str(uuid.uuid4()),transfer_token='s'*64,timeout=3,**kwargs)


def test_download_same_origin_metadata_and_no_overwrite(env,server):
    cfg,exe,work = env
    url,state = server
    transport = FileTransport(url,exe)
    msg = command(path='result.dat',expected_size=len(state['body']),sha256=hashlib.sha256(state['body']).hexdigest())
    result = transport.run({**msg,'url':'https://evil.invalid'})
    assert result['status']=='ok',result
    metadata=json.loads(result['output'])
    assert metadata['size']==len(state['body'])
    assert metadata['sha256']==msg['sha256']
    assert metadata['path']==str(work/'result.dat')
    assert (work/'result.dat').read_bytes()==state['body']
    method,path,headers=state['requests'][0]
    assert (method,path)==('GET','/api/device-transfers/'+msg['transfer_id'])
    assert headers['Authorization']=='Bearer '+msg['transfer_token']
    assert transport.run(msg)['status']=='error'
    assert len(state['requests'])==1
    assert not list(work.glob('.bridge-*'))
    # Omitted path or a directory uses a UUID name, never a server filename.
    msg=command()
    assert transport.run(msg)['status']=='ok'
    assert (work/(msg['transfer_id']+'.bin')).read_bytes()==state['body']


def test_upload_raw_regular_and_workspace_cwd(env,server):
    cfg,exe,work=env;url,state=server
    (work/'sub').mkdir();(work/'sub'/'input.txt').write_bytes(b'hello\x00\xff')
    msg=command('file_upload',path='input.txt',cwd='sub')
    result=FileTransport(url,exe).run(msg)
    assert result['status']=='ok',result
    assert state['uploaded']==b'hello\x00\xff'
    assert state['requests'][0][0]=='PUT'
    assert json.loads(result['output'])['mime']=='text/plain'
    assert FileTransport(url,exe).run(command('file_upload',path='sub'))['status']=='error'


@pytest.mark.parametrize('path',['../outside','/etc/passwd','../blocked/secret'])
def test_workspace_blocked_and_traversal(env,server,path):
    cfg,exe,work=env;url,state=server
    for op in ('file_download','file_upload'):
        result=FileTransport(url,exe).run(command(op,path=path))
        assert result['status']=='error'
    assert not state['requests']


def test_symlink_escape_and_full_mode_blocked_paths(env,server,tmp_path):
    cfg,exe,work=env;url,state=server
    outside=tmp_path/'outside';outside.mkdir();(outside/'secret').write_bytes(b'secret')
    (work/'escape').symlink_to(outside,target_is_directory=True)
    t=FileTransport(url,exe)
    assert t.run(command('file_upload',path='escape/secret'))['status']=='error'
    cfg.data['mode']='full'
    cfg.data['blocked_paths']=[str(outside)]
    assert t.run(command('file_upload',path='escape/secret'))['status']=='error'
    assert t.run(command('file_download',path='escape/new'))['status']=='error'
    assert not state['requests']
    cfg.data['blocked_paths']=[str(tmp_path/'blocked')]
    assert t.run(command('file_upload',path=str(outside/'secret')))['status']=='ok'
    assert state['uploaded']==b'secret'


def test_integrity_redirect_oversize_and_disabled(env,server):
    cfg,exe,work=env;url,state=server;t=FileTransport(url,exe)
    for extra in ({'expected_size':100},{'sha256':'0'*64}):
        assert t.run(command(path='fail',**extra))['status']=='error'
        assert not (work/'fail').exists()
    state['header_sha']='0'*64
    assert t.run(command(path='fail'))['status']=='error'
    state['header_sha']=None;state['status']=302
    result=t.run(command(path='fail'))
    assert result['status']=='error'
    assert '302' in result['output']
    state['status']=200;state['length']=MAX_BYTES+1
    assert t.run(command(path='fail'))['status']=='error'
    cfg.data['exec_enabled']=False
    before=len(state['requests'])
    assert t.run(command(path='fail'))['status']=='error'
    assert len(state['requests'])==before
    assert 'files' not in BridgeClient(url,'x'*64,executor=exe).capabilities()


def test_upload_max_nonregular_and_screenshot_policy(env,server,monkeypatch):
    cfg,exe,work=env;url,state=server;t=FileTransport(url,exe)
    with (work/'big').open('wb') as f:f.truncate(MAX_BYTES+1)
    assert t.run(command('file_upload',path='big'))['status']=='error'
    if os.name=='posix':
        os.mkfifo(work/'fifo')
        assert t.run(command('file_upload',path='fifo'))['status']=='error'
    assert not state['requests']
    result=t.run(command('screenshot'))
    assert result['status']=='error' and 'full mode' in result['output']
    cfg.data['mode']='full'
    monkeypatch.setattr('device_client.transfers.screenshot_available',lambda:False)
    result=t.run(command('screenshot'))
    assert result['status']=='error' and 'graphical desktop' in result['output']
    # Capture replacement lets us test upload without a physical desktop.
    monkeypatch.setattr(t,'capture',lambda:b'PNG bytes')
    result=t.run(command('screenshot'))
    assert result['status']=='ok'
    metadata=json.loads(result['output'])
    assert metadata['path'] is None and metadata['mime']=='image/png'
    assert state['uploaded']==b'PNG bytes'
    assert not list(work.glob('*.png'))


@pytest.mark.parametrize('field,value',[('transfer_id','../../escape'),('transfer_token','x\r\nHeader: bad'),
                                       ('expected_size',True),('sha256','f'*64+'\n'),('timeout',301)])
def test_invalid_ticket_and_untrusted_origin(env,server,field,value):
    cfg,exe,work=env;url,state=server
    assert FileTransport(url,exe).run({**command(),field:value})['status']=='error'
    assert not state['requests']
    with pytest.raises(ValueError):FileTransport('http://example.com',exe)
    with pytest.raises(ValueError):FileTransport('https://evil.com/path',exe)
