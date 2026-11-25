"""Real Redis, two independent gateway workers, bearer-only raw byte API."""
import asyncio
import hashlib
import time
import uuid

import pytest
from fastapi import HTTPException
from gw_shared.device_bridge import CommandCreate, PREFIX
from gw_shared.device_transfers import MAX_BYTES
from api_gateway.tests.test_device_bridge import (redis_url, gateways, create_device, SERVICE_HEADERS,
                                open_device_ws, close_ws, post_command, receive_until,
                                call_in_thread, thread_result)


def ticket(g, direction='to_device', **kwargs):
    device = create_device(g.alice)
    response = g.bob.post('/internal/devices/%s/transfers?user_id=101' % device['device_id'],
                          headers=SERVICE_HEADERS, json={'direction': direction, **kwargs})
    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == {'transfer_id', 'transfer_token', 'max_bytes'}
    assert body['max_bytes'] == MAX_BYTES
    assert len(body['transfer_token']) == 64
    identifier = body['transfer_id']
    uuid.UUID(identifier)
    return device, body, '/internal/device-transfers/' + identifier + '?user_id=101', '/api/device-transfers/' + identifier


def bearer(body):
    return {'Authorization': 'Bearer ' + body['transfer_token']}


def test_download_binary_cross_worker_single_use_and_headers(gateways):
    data = b'\x00\xff\xfe\x80raw\r\n' * 100
    sha = hashlib.sha256(data).hexdigest()
    d, t, internal, public = ticket(gateways, size=len(data), sha256=sha)
    assert gateways.alice.put(internal, content=data, headers=SERVICE_HEADERS).json() == {'size': len(data), 'sha256': sha}
    assert gateways.bob.put(internal, content=data, headers=SERVICE_HEADERS).status_code == 409
    # No CSRF/SSO dependency: exclusively transfer bearer.
    response = gateways.bob.get(public, headers=bearer(t))
    assert response.status_code == 200
    assert response.content == data
    assert response.headers['cache-control'] == 'no-store'
    assert response.headers['x-content-sha256'] == sha
    assert gateways.alice.get(public, headers=bearer(t)).status_code == 409
    assert gateways.alice.get(internal, headers=SERVICE_HEADERS).status_code == 409
    assert gateways.alice.delete(internal, headers=SERVICE_HEADERS).json() == {'deleted': True}
    assert gateways.bob.get(public, headers=bearer(t)).status_code == 404


def test_upload_once_claim_owner_and_forged_credentials(gateways):
    d, t, internal, public = ticket(gateways, 'from_device')
    assert gateways.bob.put(public, content=b'abc').status_code == 401
    assert gateways.alice.put(public, content=b'abc', headers={'Authorization': 'Bearer ' + 'a'*64}).status_code == 404
    assert gateways.bob.put(public, content=b'abc', headers={'Authorization': 'Bearer ' + d['token']}).status_code == 404
    assert gateways.bob.put(public + '?transfer_token=' + t['transfer_token'], content=b'a').status_code == 401
    assert gateways.bob.get(public, headers=bearer(t)).status_code == 409
    assert gateways.bob.put(internal, content=b'a', headers=SERVICE_HEADERS).status_code == 409
    assert gateways.bob.get(internal.replace('101','202'), headers=SERVICE_HEADERS).status_code == 404
    assert gateways.bob.get(internal).status_code == 401
    assert gateways.bob.get(internal, headers=bearer(t)).status_code == 401
    assert gateways.bob.put(public, content=b'abc', headers=bearer(t)).status_code == 200
    assert gateways.alice.put(public, content=b'abc', headers=bearer(t)).status_code == 409
    assert gateways.alice.get(internal, headers=SERVICE_HEADERS).content == b'abc'
    assert gateways.bob.get(internal, headers=SERVICE_HEADERS).status_code == 409


@pytest.mark.parametrize('fields', [{'size': 2}, {'sha256': '0'*64}])
def test_integrity_mismatch_burns_put(gateways, fields):
    d,t,internal,public = ticket(gateways, 'from_device', **fields)
    assert gateways.alice.put(public, content=b'abc', headers=bearer(t)).status_code == 422
    assert gateways.bob.put(public, content=b'abc', headers=bearer(t)).status_code == 409
    assert gateways.bob.get(internal, headers=SERVICE_HEADERS).status_code == 409


def test_max_body_stream_empty_and_strict_create(gateways):
    d,t,internal,public = ticket(gateways)
    url = '/internal/devices/' + d['device_id'] + '/transfers?user_id=101'
    for fields in ({'size': MAX_BYTES+1}, {'size': True}, {'size': -1}, {'sha256':'f'*64+'\n'},
                   {'direction':'other'}, {'url':'https://evil'}, {'size':'3'}):
        assert gateways.alice.post(url, headers=SERVICE_HEADERS,
                                  json={'direction':'to_device', **fields}).status_code == 422
    assert gateways.bob.post(url.replace('101','202'), json={'direction':'to_device'}, headers=SERVICE_HEADERS).status_code == 404
    for owner in ('0', '-1', '01', '9223372036854775808', 'x'):
        assert gateways.bob.post(url.replace('101',owner), json={'direction':'to_device'}, headers=SERVICE_HEADERS).status_code == 422
    assert gateways.alice.put(internal, content=b'x', headers={**SERVICE_HEADERS, 'Content-Length': str(MAX_BYTES+1)}).status_code == 413
    # No Content-Length: streamed, not request.body(); never store an oversized body.
    assert gateways.alice.put(internal, content=iter([b'x'*(MAX_BYTES//2), b'x'*(MAX_BYTES//2+1)]), headers=SERVICE_HEADERS).status_code == 413
    assert gateways.bob.get(public, headers=bearer(t)).status_code == 409
    d,t,internal,public = ticket(gateways, size=0)
    assert gateways.alice.put(internal, content=b'', headers=SERVICE_HEADERS).status_code == 200
    assert gateways.bob.get(public, headers=bearer(t)).content == b''


def test_ttl_hash_only_revocation_and_redis_failure(gateways):
    d,t,internal,public = ticket(gateways)
    service = gateways.alice_service
    async def inspect():
        key = PREFIX + 'transfer:' + t['transfer_id']
        data = await service.redis.hgetall(key)
        assert t['transfer_token'] not in str(data)
        assert data['token_hash'] == hashlib.sha256(t['transfer_token'].encode()).hexdigest()
        assert 0 < await service.redis.ttl(key) <= 600
        await service.redis.expire(key, 1)
    gateways.alice.portal.call(inspect)
    time.sleep(1.05)
    assert gateways.bob.get(public, headers=bearer(t)).status_code == 404
    d,t,internal,public = ticket(gateways)
    assert gateways.alice.delete('/api/devices/' + d['device_id']).status_code == 200
    assert gateways.bob.put(internal, content=b'a', headers=SERVICE_HEADERS).status_code == 404
    assert gateways.bob.get(public, headers=bearer(t)).status_code == 404
    # Simulate unavailable Redis through the shared fail-closed wrapper.
    original = service.redis
    class Failed:
        async def hget(self, *args):
            raise RuntimeError('private credentials')
    service.redis = Failed()
    try:
        r = gateways.alice.get('/api/device-transfers/'+str(uuid.uuid4()), headers=bearer(t))
        assert r.status_code == 503
        assert 'private' not in r.text
    finally:
        service.redis = original


def test_concurrent_claim_only_one_worker_wins(gateways):
    d,t,internal,public = ticket(gateways, 'from_device')
    first = call_in_thread(lambda: gateways.alice.put(public, content=b'a', headers=bearer(t)))
    second = gateways.bob.put(public, content=b'b', headers=bearer(t))
    assert sorted([thread_result(*first).status_code, second.status_code]) == [200,409]


def test_file_commands_capability_binding_and_validated_relay(gateways):
    d,t,internal,public = ticket(gateways)
    assert gateways.alice.put(internal, content=b'data', headers=SERVICE_HEADERS).status_code == 200
    context, ws, hello = open_device_ws(gateways.alice, d['token'])
    payload = {'operation':'file_download','transfer_id':t['transfer_id'], 'transfer_token':t['transfer_token'],
               'path':'output.bin', 'expected_size':4,'timeout':2}
    try:
        ws.send_json({'type':'hello','protocol':1,'capabilities':['exec']})
        time.sleep(.2)
        assert post_command(gateways.bob,d['device_id'],**payload).status_code == 404
        assert post_command(gateways.alice,d['device_id'],**payload).status_code == 409
        ws.send_json({'type':'capabilities','capabilities':['exec','files']})
        time.sleep(.2)
        pending = call_in_thread(lambda: post_command(gateways.alice,d['device_id'],**payload))
        command = receive_until(ws,'command')
        assert {k:command[k] for k in payload} == payload
        assert set(command) == set(payload) | {'type','job_id','session_id','deadline'}
        ws.send_json({'type':'result','job_id':command['job_id'],'session_id':hello['session_id'],
                      'status':'ok','output':'metadata'})
        assert thread_result(*pending).status_code == 200
        # Tickets are also bound to direction/device/owner, not just valid UUIDs.
        assert post_command(gateways.alice,d['device_id'],**{**payload,'operation':'file_upload'}).status_code == 409
        d2,t2,_,_ = ticket(gateways)
        assert post_command(gateways.alice,d['device_id'],**{**payload,'transfer_id':t2['transfer_id'],
                                                          'transfer_token':t2['transfer_token']}).status_code == 404
        ws.send_json({'type':'capabilities','capabilities':['files']})
        time.sleep(.2)
        assert post_command(gateways.alice,d['device_id'],**payload).status_code == 409
    finally:
        close_ws(context, ws)
    assert t['transfer_token'] not in repr(CommandCreate(**payload))
