"""Real TCP: service-authenticated sender -> worker A -> Redis -> worker B -> desktop core."""
import json
from pathlib import Path
import time

import pytest

from test_integration import network, start_client, eventually

SERVICE_TOKEN = 'disposable-integration-service-credential-' + 'x' * 32

@pytest.fixture
def service_network(tmp_path, monkeypatch):
    monkeypatch.setenv('DEVICE_BRIDGE_SERVICE_TOKEN', SERVICE_TOKEN)
    gen = network.__wrapped__(tmp_path)
    try:
        yield next(gen)
    finally:
        gen.close()


def test_internal_notify_cross_worker_and_owner(service_network):
    http, sender, receiver, clients, checks = service_network
    registration = http.post(sender + '/api/devices', json={'name':'Notify fixture', 'platform':'windows'}).json()
    device = registration['device_id']
    bridge, thread, events = start_client(receiver, registration['token'], clients)
    eventually(lambda: ('status','online') in events)
    headers = {'Authorization': 'Bearer ' + SERVICE_TOKEN}
    response = http.get(sender + '/internal/devices', params={'user_id':56}, headers=headers)
    assert response.status_code == 200
    assert registration['token'] not in response.text
    assert any(d['device_id'] == device for d in response.json()['devices'])
    url = sender + f'/internal/devices/{device}/commands'
    body = {'user_id':56, 'operation':'notify', 'text':'Server → desktop\nПривет, Павел!\tPlain text.', 'timeout':5}
    assert http.post(url,json=body).status_code == 401
    assert http.post(url,json=body,headers={'Authorization':'Bearer '+'z'*64}).status_code == 401
    assert http.post(url,json={**body,'user_id':57},headers=headers).status_code == 404
    assert not any(kind == 'notification' for kind,_ in events)
    response = http.post(url,json=body,headers=headers)
    assert response.status_code == 200, response.text
    assert response.json()['output'] == 'displayed'
    assert response.json()['exit_code'] == 0
    eventually(lambda: ('notification',body['text']) in events)
    assert [e for e in events if e[0]=='notification'] == [('notification',body['text'])]
    assert http.post(url,json={'user_id':56,'operation':'ping'},headers=headers).json()['output'] == 'pong'
    checks['internal_service_notify_cross_worker'] = True
    checks['internal_service_owner_enforced'] = True
    checks['internal_service_auth_enforced'] = True
    bridge.stop(); thread.join(5)
    assert not thread.is_alive()
    second, thread2, events2 = start_client(receiver,registration['token'],clients)
    eventually(lambda: ('status','online') in events2)
    assert http.post(url,json={'user_id':56,'operation':'ping'},headers=headers).json()['output']=='pong'
    assert not any(k=='notification' for k,_ in events2)
    checks['notify_not_replayed_after_reconnect'] = True
    report=Path('output/device_notify/network-notify.json'); report.parent.mkdir(parents=True,exist_ok=True)
    report.write_text(json.dumps({'checks':checks,'result':response.json(),'payload_chars':len(body['text'])},indent=2)+'\n')
