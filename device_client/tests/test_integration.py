import os
os.environ['OPENBLAS_NUM_THREADS'] = '1'

import contextlib
import json
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import threading
import time

import httpx
import pytest
import redis

from device_client.core import BridgeClient

ROOT = Path(__file__).resolve().parents[2]


def reserve_socket():
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(('127.0.0.1', 0))
    return listener


def eventually(predicate, timeout=5):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():
            return
        time.sleep(0.03)
    raise AssertionError('Timed out waiting for expected state')


def stop_processes(processes):
    for process in processes:
        if process.poll() is None:
            with contextlib.suppress(ProcessLookupError):
                process.terminate()
    end = time.monotonic() + 2
    for process in processes:
        with contextlib.suppress(subprocess.TimeoutExpired):
            process.wait(timeout=max(0, end - time.monotonic()))
    for process in processes:
        if process.poll() is None:
            process.kill()
        process.wait(timeout=2)


@pytest.fixture
def network(tmp_path):
    processes = []
    clients = []
    summary = {'transport': 'real TCP; two uvicorn workers; isolated Redis', 'checks': {}}
    report = ROOT / 'output/device_client/integration.json'
    started = time.monotonic()
    runtime = ROOT.parent / 'redis-test-runtime'
    binary = shutil.which('redis-server') or str(runtime / 'usr/bin/redis-server')
    env = dict(os.environ)
    if Path(binary).is_relative_to(runtime):
        env['LD_LIBRARY_PATH'] = str(runtime / 'usr/lib/x86_64-linux-gnu')
    try:
        with reserve_socket() as listener:
            port = listener.getsockname()[1]
        processes.append(subprocess.Popen(
            [binary, '--bind', '127.0.0.1', '--port', str(port), '--save', '',
             '--appendonly', 'no', '--protected-mode', 'yes', '--dir', str(tmp_path)],
            env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
        redis_url = f'redis://127.0.0.1:{port}/0'
        with redis.Redis.from_url(redis_url, socket_connect_timeout=0.15, socket_timeout=0.2) as probe:
            def redis_ready():
                assert processes[0].poll() is None, 'Disposable Redis exited'
                try:
                    return probe.ping()
                except redis.RedisError:
                    return False
            eventually(redis_ready)
        ports = []
        for _ in range(2):
            with reserve_socket() as listener:
                listener.listen(128)
                ports.append(listener.getsockname()[1])
                processes.append(subprocess.Popen(
                    [sys.executable, str(Path(__file__).with_name('network_worker.py')),
                     '--redis-url', redis_url, '--fd', str(listener.fileno())],
                    pass_fds=(listener.fileno(),), cwd=str(ROOT),
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
        with httpx.Client(timeout=1, trust_env=False, headers={'X-Device-Bridge': '1'}) as http:
            for port, process in zip(ports, processes[1:]):
                def ready():
                    assert process.poll() is None, 'Disposable uvicorn exited'
                    try:
                        response = http.get(f'http://127.0.0.1:{port}/_smoke/ready')
                        return response.status_code == 200 and response.json() == {'ready': True, 'pid': process.pid}
                    except httpx.HTTPError:
                        return False
                eventually(ready)
            summary['checks']['isolated_services_ready'] = True
            yield http, f'http://127.0.0.1:{ports[0]}', f'http://127.0.0.1:{ports[1]}', clients, summary['checks']
    finally:
        for bridge, thread, _events in clients:
            bridge.stop()
        for _bridge, thread, _events in clients:
            thread.join(7)
        stop_processes(processes)
        summary['checks']['all_threads_stopped'] = all(not thread.is_alive() for _, thread, _ in clients)
        summary['checks']['all_processes_reaped'] = all(process.poll() is not None for process in processes)
        summary['process_count'] = len(processes)
        summary['elapsed_seconds'] = round(time.monotonic() - started, 3)
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text(json.dumps(summary, indent=2) + '\n')
        assert summary['checks']['all_threads_stopped']
        assert summary['checks']['all_processes_reaped']


def start_client(url, token, clients):
    events = []
    bridge = BridgeClient(url, token, lambda *event: events.append(event))
    thread = threading.Thread(target=bridge.run, name='integration-BridgeClient', daemon=True)
    clients.append((bridge, thread, events))
    thread.start()
    return bridge, thread, events


def test_real_bridge_lifecycle(network):
    http, base, ws_base, clients, checks = network
    response = http.post(base + '/api/devices', json={'name': 'desktop-core-integration', 'platform': 'linux'})
    assert response.status_code == 201
    registration = response.json()
    device_id = registration['device_id']
    token = registration.pop('token')
    checks['http_register'] = True

    def status():
        response = http.get(base + '/api/devices')
        assert response.status_code == 200
        token_absent = token not in response.text
        assert token_absent, 'Device listing exposed a credential'
        devices = response.json()['devices']
        return next(device['status'] for device in devices if device['device_id'] == device_id)

    def ping():
        response = http.post(base + f'/api/devices/{device_id}/commands',
                             json={'operation': 'ping', 'timeout': 3}, timeout=5)
        assert response.status_code == 200
        result = response.json()
        assert result['status'] == 'ok' and result['output'] == 'pong'
        assert result.get('exit_code', 0) == 0

    assert status() == 'offline'
    bridge, thread, events = start_client(ws_base, token, clients)
    eventually(lambda: ('status', 'online') in events)
    assert status() == 'online'
    checks['actual_bridge_thread_online'] = True
    _, duplicate_thread, duplicate_events = start_client(ws_base, token, clients)
    duplicate_thread.join(5)
    assert not duplicate_thread.is_alive()
    assert ('status', 'unauthorized') in duplicate_events
    assert ('status', 'online') not in duplicate_events
    assert thread.is_alive() and status() == 'online'
    checks['duplicate_client_denied_without_displacing_incumbent'] = True
    ping()
    checks['cross_worker_ping_pong'] = True
    time.sleep(6.3)
    assert thread.is_alive()
    assert status() == 'online'
    assert events.count(('status', 'online')) == 1
    assert ('status', 'reconnecting') not in events
    ping()
    checks['heartbeat_survives_over_six_seconds_without_reconnect'] = True

    bridge.stop()
    thread.join(2)
    assert not thread.is_alive()
    eventually(lambda: status() == 'offline')
    checks['disconnect_offline'] = True

    bridge, thread, events = start_client(ws_base, token, clients)
    eventually(lambda: ('status', 'online') in events)
    ping()
    checks['manual_reconnect_same_token'] = True

    with bridge._lock:
        transport = bridge._socket
    assert transport is not None
    transport.shutdown()
    eventually(lambda: events.count(('status', 'online')) >= 2, timeout=6)
    assert thread.is_alive() and status() == 'online'
    ping()
    checks['automatic_reconnect_after_transport_loss'] = True

    response = http.delete(base + f'/api/devices/{device_id}')
    assert response.status_code == 200
    eventually(lambda: not thread.is_alive(), timeout=7)
    assert ('status', 'unauthorized') in events
    assert events[-1] == ('status', 'stopped')
    assert status() == 'revoked'
    checks['revocation_stops_client'] = True

    _, invalid_thread, invalid_events = start_client(ws_base, 'invalid-device-token-for-integration', clients)
    invalid_thread.join(5)
    assert not invalid_thread.is_alive()
    assert ('status', 'unauthorized') in invalid_events
    assert ('status', 'online') not in invalid_events
    assert ('status', 'reconnecting') not in invalid_events
    checks['invalid_token_terminal'] = True

    _, revoked_thread, revoked_events = start_client(ws_base, token, clients)
    revoked_thread.join(5)
    assert not revoked_thread.is_alive()
    assert ('status', 'unauthorized') in revoked_events
    assert ('status', 'online') not in revoked_events
    checks['revoked_token_cannot_reconnect'] = True
    events_sanitized = token not in repr([entry[2] for entry in clients])
    assert events_sanitized, 'Client events exposed a credential'
    checks['token_absent_from_events_and_list'] = True
