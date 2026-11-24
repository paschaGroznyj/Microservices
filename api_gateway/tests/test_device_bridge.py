"""Black-box integration coverage for :mod:`gw_shared.device_bridge`.

These tests deliberately use two real ASGI applications and two Redis clients.
The websocket side is driven through Starlette's TestClient (not a mocked
transport), while command HTTP requests run in a bounded worker so the test
thread can service the device socket.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import queue
import shutil
import socket
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass
from typing import Any, Callable

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from redis.asyncio import Redis
from starlette.websockets import WebSocketDisconnect

from gw_shared.device_bridge import MAX_MESSAGE, PREFIX, TOKEN_TTL, create_router

SERVICE_TOKEN = "test-only-service-credential-" + "x" * 32
SERVICE_HEADERS = {"Authorization": "Bearer " + SERVICE_TOKEN}


@dataclass
class Gateways:
    alice: TestClient
    bob: TestClient
    alice_service: Any
    bob_service: Any


@pytest.fixture(scope="session")
def redis_url():
    """Run an isolated, ephemeral Redis instance for the whole test module."""
    executable = shutil.which("redis-server")
    if executable is None:
        pytest.skip("redis-server is not installed")

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    process = subprocess.Popen(
        [executable, "--port", str(port), "--bind", "127.0.0.1",
         "--save", "", "--appendonly", "no", "--protected-mode", "no"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )
    url = f"redis://127.0.0.1:{port}/0"
    async def wait_ready():
        async with Redis.from_url(url, socket_timeout=0.25) as probe:
            deadline = time.monotonic() + 5
            while True:
                try:
                    await probe.ping()
                    return
                except Exception:
                    if process.poll() is not None:
                        error = process.stderr.read() if process.stderr else ""
                        raise RuntimeError(f"redis-server exited: {error}")
                    if time.monotonic() >= deadline:
                        raise RuntimeError("redis-server did not become ready")
                    await asyncio.sleep(0.02)

    try:
        asyncio.run(wait_ready())
        yield url
    finally:
        process.terminate()
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=3)
        if process.stderr:
            process.stderr.close()


@pytest.fixture
def gateways(redis_url, monkeypatch):
    """Separate persistent event loops, Redis pools, apps and auth callbacks."""
    monkeypatch.setenv('DEVICE_BRIDGE_SERVICE_TOKEN', SERVICE_TOKEN)

    def make(owner, *, decode_responses, flush=False):
        store = Redis.from_url(redis_url, decode_responses=decode_responses)

        # One sync and one async callback exercise both supported injection forms.
        def sync_user(_request):
            return {"authenticated": True, "user_id": owner}

        async def async_user(_request):
            return sync_user(_request)

        router = create_router(sync_user if owner == 101 else async_user, store)

        @contextlib.asynccontextmanager
        async def lifespan(_app):
            if flush:
                await store.flushdb()
            try:
                yield
            finally:
                await router.device_bridge_service.close()
                await store.aclose()

        app = FastAPI(lifespan=lifespan)
        app.include_router(router)
        app.include_router(router.internal_router)
        return TestClient(app, headers={"X-Device-Bridge": "1"}), router.device_bridge_service

    alice, alice_service = make(101, decode_responses=True, flush=True)
    bob, bob_service = make(202, decode_responses=False)
    with alice, bob:
        assert alice_service is not bob_service
        assert alice_service.redis is not bob_service.redis
        yield Gateways(alice, bob, alice_service, bob_service)


def create_device(client: TestClient, name: str = "laptop", platform: str = "linux"):
    response = client.post("/api/devices", json={"name": name, "platform": platform})
    assert response.status_code == 201, response.text
    data = response.json()
    assert set(data) == {"device_id", "token", "expires_in"}
    assert data["expires_in"] == TOKEN_TTL
    uuid.UUID(data["device_id"])
    assert isinstance(data["token"], str) and len(data["token"]) >= 40
    return data


def call_in_thread(function: Callable[[], Any]):
    """Call a blocking TestClient method without risking an unbounded join."""
    result: queue.Queue[tuple[str, Any]] = queue.Queue(1)

    def run():
        try:
            result.put(("value", function()))
        except BaseException as exc:  # re-raise in the test thread
            result.put(("error", exc))

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    return thread, result


def thread_result(thread, result, timeout=4):
    thread.join(timeout)
    assert not thread.is_alive(), "blocking HTTP operation exceeded its test deadline"
    kind, value = result.get_nowait()
    if kind == "error":
        raise value
    return value


def receive_bounded(ws, timeout=3):
    """A single reader owns each TestClient receive; tests consume its queue.

    A timeout never leaves an extra concurrent receive behind. On teardown the
    context cancellation releases the reader; it is daemonized as a final safety
    net, not joined indefinitely by ThreadPoolExecutor.__exit__.
    """
    try:
        kind, value = ws._test_messages.get(timeout=timeout)
    except queue.Empty:
        raise TimeoutError(f"websocket receive exceeded {timeout}s") from None
    if kind == "error":
        raise value
    return value


def receive_until(ws, message_type: str, timeout=3):
    message = receive_bounded(ws, timeout)
    assert message.get("type") == message_type, message
    return message


def open_device_ws(client: TestClient, token: str):
    """Connect over ASGI and let a dedicated device reader answer heartbeats."""
    context = client.websocket_connect(
        "/api/devices/connect", headers={"Authorization": f"Bearer {token}"}
    )
    ws = context.__enter__()
    ws._test_messages = queue.Queue()

    def reader():
        try:
            while True:
                message = ws.receive_json()
                if message.get("type") == "ping":
                    ws.send_json({"type": "pong", "nonce": message["nonce"]})
                else:
                    ws._test_messages.put(("value", message))
        except BaseException as error:
            ws._test_messages.put(("error", error))

    ws._test_reader = threading.Thread(target=reader, daemon=True)
    ws._test_reader.start()
    try:
        hello = receive_until(ws, "hello")
        assert set(hello) == {"type", "device_id", "session_id", "heartbeat_interval"}
        assert hello["heartbeat_interval"] == 5
        return context, ws, hello
    except BaseException:
        close_ws(context, ws)
        raise


def close_ws(context, ws):
    # Send a graceful disconnect and wait for the server close (after its Lua
    # cleanup) before cancelling TestClient's application task. This avoids
    # racing immediate reconnect against the previous lease's cleanup.
    try:
        if ws._test_reader.is_alive():
            ws.close()
            ws._test_reader.join(timeout=3)
    finally:
        context.__exit__(None, None, None)
        ws._test_reader.join(timeout=1)
    assert not ws._test_reader.is_alive(), "websocket reader survived teardown"


def post_command(client, device_id, **payload):
    return client.post(f"/api/devices/{device_id}/commands", json=payload, headers={"X-Device-Bridge": "1"})


def test_anonymous_auth_owner_binding_and_strict_bodies(gateways):
    anonymous = FastAPI()

    async def no_user(_request):
        return {"authenticated": False}

    # A third factory binding makes this an actual authentication test rather
    # than relying on either of the authenticated application fixtures.
    anonymous_router = create_router(no_user, Redis.from_url("redis://127.0.0.1:1"))
    anonymous.include_router(anonymous_router)
    anon = TestClient(anonymous, headers={"X-Device-Bridge": "1"})
    try:
        assert anon.get("/api/devices").status_code == 401
        assert anon.post("/api/devices", json={"name": "x", "platform": "linux"}).status_code == 401
    finally:
        anon.close()

    # The owner is injected, never accepted from a request body.  Pydantic's
    # forbid-extra setting also rejects a tempting owner/user_id override.
    response = gateways.alice.post(
        "/api/devices", json={"name": "x", "platform": "linux", "user_id": "bob"}
    )
    assert response.status_code == 422
    response = gateways.alice.post(
        "/api/devices", json={"name": "x", "platform": "linux", "owner_id": "bob"}
    )
    assert response.status_code == 422
    for body in (
        {"name": " ", "platform": "linux"},
        {"name": "x", "platform": "linux", "extra": 1},
        {"name": 4, "platform": "linux"},
    ):
        assert gateways.alice.post("/api/devices", json=body).status_code == 422


def test_owner_isolation_list_delete_commands_and_no_secret_listing(gateways):
    alice_device = create_device(gateways.alice, "alice laptop")
    bob_device = create_device(gateways.bob, "bob desktop", "windows")

    alice_list = gateways.alice.get("/api/devices")
    bob_list = gateways.bob.get("/api/devices")
    assert alice_list.status_code == bob_list.status_code == 200
    assert [d["device_id"] for d in alice_list.json()["devices"]] == [alice_device["device_id"]]
    assert [d["device_id"] for d in bob_list.json()["devices"]] == [bob_device["device_id"]]
    listed = alice_list.json()["devices"][0]
    assert set(listed) == {"device_id", "name", "platform", "status", "created_at"}
    assert alice_device["token"] not in alice_list.text
    assert "token" not in listed
    assert "token_hash" not in alice_list.text

    # Cross-owner access is indistinguishable from a missing device.
    assert gateways.bob.delete(f"/api/devices/{alice_device['device_id']}").status_code == 404
    assert post_command(
        gateways.bob, alice_device["device_id"], operation="ping", timeout=1
    ).status_code == 404
    assert gateways.alice.delete(f"/api/devices/{bob_device['device_id']}").status_code == 404

    # A device without a lease cannot receive a command.
    response = post_command(gateways.alice, alice_device["device_id"], operation="ping", timeout=1)
    assert response.status_code == 409


def test_bearer_only_ws_auth_and_duplicate_session_rejected(gateways):
    device = create_device(gateways.alice)
    token = device["token"]

    # Query credentials, cookies, and an absent Authorization header are not
    # accepted. The failure happens before the websocket is accepted.
    for path, headers in (
        (f"/api/devices/connect?token={token}", None),
        ("/api/devices/connect", None),
        ("/api/devices/connect", {"Authorization": "Basic " + token}),
        ("/api/devices/connect", {"Authorization": "Bearer not-a-token"}),
    ):
        with pytest.raises(WebSocketDisconnect) as error:
            with gateways.bob.websocket_connect(path, headers=headers) if headers else gateways.bob.websocket_connect(path):
                pass
        assert error.value.code == 1008

    context, ws, hello = open_device_ws(gateways.bob, token)
    try:
        with pytest.raises(WebSocketDisconnect) as error:
            with gateways.alice.websocket_connect(
                "/api/devices/connect", headers={"Authorization": f"Bearer {token}"}
            ):
                pass
        assert error.value.code == 1008
        assert hello["device_id"] == device["device_id"]
    finally:
        close_ws(context, ws)


def test_ping_cross_gateway_over_real_ws_and_shell_rejected(gateways):
    device = create_device(gateways.alice, "cross-app device")
    context, ws, hello = open_device_ws(gateways.bob, device["token"])
    try:
        request_thread, request_result = call_in_thread(
            lambda: post_command(gateways.alice, device["device_id"], operation="ping", timeout=3)
        )
        command = receive_until(ws, "command")
        assert command["operation"] == "ping"
        assert set(command) == {"type", "job_id", "session_id", "deadline", "operation", "timeout"}
        assert command["session_id"] == hello["session_id"]
        uuid.UUID(command["job_id"])
        ws.send_json({
            "type": "result", "job_id": command["job_id"],
            "session_id": hello["session_id"], "status": "ok", "output": "pong",
        })
        response = thread_result(request_thread, request_result)
        assert response.status_code == 200
        assert response.json() == {
            "job_id": command["job_id"], "status": "ok", "output": "pong"
        }

        rejected = post_command(gateways.alice, device["device_id"],
                                operation="shell", command="whoami", confirmed=True)
        assert rejected.status_code == 422
    finally:
        close_ws(context, ws)


def test_shell_is_disabled_and_ping_fields_are_enforced(gateways):
    device = create_device(gateways.alice)
    checks = (
        {"operation": "shell", "command": "echo x", "timeout": 1},
        {"operation": "shell", "command": "   ", "confirmed": True, "timeout": 1},
        {"operation": "shell", "command": "echo x", "confirmed": True, "timeout": 1, "cwd": ""},
        {"operation": "ping", "command": "echo x", "timeout": 1},
        {"operation": "ping", "cwd": "/tmp", "timeout": 1},
        {"operation": "ping", "timeout": "1"},
        {"operation": "ping", "timeout": 0},
        {"operation": "ping", "timeout": 121},
    )
    for body in checks:
        response = post_command(gateways.alice, device["device_id"], **body)
        assert response.status_code == 422, (body, response.text)


def test_spoofed_unknown_and_duplicate_results_are_not_correlated(gateways):
    device = create_device(gateways.alice)
    context, ws, hello = open_device_ws(gateways.bob, device["token"])
    try:
        thread, result = call_in_thread(
            lambda: post_command(gateways.alice, device["device_id"], operation="ping", timeout=3)
        )
        command = receive_until(ws, "command")
        fake_job = str(uuid.uuid4())
        ws.send_json({
            "type": "result", "job_id": fake_job, "session_id": hello["session_id"],
            "status": "ok", "output": "spoof",
        })
        assert receive_until(ws, "error")["error"] == "result_not_pending"

        actual = {
            "type": "result", "job_id": command["job_id"], "session_id": hello["session_id"],
            "status": "ok", "output": "real",
        }
        ws.send_json(actual)
        response = thread_result(thread, result)
        assert response.status_code == 200 and response.json()["output"] == "real"

        # Completion is one-shot, and the job key is cleaned after the waiter
        # returns; a replay cannot manufacture another HTTP result.
        ws.send_json(actual)
        assert receive_until(ws, "error")["error"] == "result_not_pending"
    finally:
        close_ws(context, ws)


def test_malformed_frames_close_without_being_interpreted_as_results(gateways):
    device = create_device(gateways.alice)
    context, ws, _ = open_device_ws(gateways.bob, device["token"])
    try:
        ws.send_text(json.dumps({"type": "result", "job_id": "not-a-uuid"}))
        with pytest.raises(WebSocketDisconnect) as error:
            receive_bounded(ws)
        assert error.value.code == 1008
    finally:
        close_ws(context, ws)

    context, ws, _ = open_device_ws(gateways.bob, device["token"])
    try:
        # This exceeds the application-level frame limit and is sent through
        # the real TestClient websocket transport, not directly to a handler.
        ws.send_text("x" * (MAX_MESSAGE + 1))
        with pytest.raises(WebSocketDisconnect) as error:
            receive_bounded(ws)
        assert error.value.code == 1009
    finally:
        close_ws(context, ws)


def test_revoke_invalidates_token_and_closes_socket_quickly(gateways):
    device = create_device(gateways.alice)
    context, ws, _ = open_device_ws(gateways.bob, device["token"])
    try:
        started = time.monotonic()
        response = gateways.alice.delete(f"/api/devices/{device['device_id']}")
        assert response.status_code == 200
        assert response.json() == {"device_id": device["device_id"], "revoked": True}
        with pytest.raises(WebSocketDisconnect):
            receive_bounded(ws, timeout=2)
        assert time.monotonic() - started < 2.5
    finally:
        close_ws(context, ws)

    listed = gateways.alice.get("/api/devices").json()["devices"]
    assert listed[0]["status"] == "revoked"
    assert post_command(gateways.alice, device["device_id"], operation="ping", timeout=1).status_code == 409
    with pytest.raises(WebSocketDisconnect):
        with gateways.bob.websocket_connect(
            "/api/devices/connect", headers={"Authorization": f"Bearer {device['token']}"}
        ):
            pass


def test_timeout_disconnect_and_reconnect_never_replays_a_command(gateways):
    device = create_device(gateways.alice)
    context, ws, _ = open_device_ws(gateways.bob, device["token"])
    try:
        thread, result = call_in_thread(
            lambda: post_command(gateways.alice, device["device_id"], operation="ping", timeout=1)
        )
        receive_until(ws, "command")
        response = thread_result(thread, result, timeout=4)
        assert response.status_code == 504
    finally:
        close_ws(context, ws)

    # A second command is dispatched, then the device disappears before a
    # result. Its cancellation is observed by the HTTP waiter and is not
    # replayed by the next lease/session.
    context, ws, _ = open_device_ws(gateways.bob, device["token"])
    thread, result = call_in_thread(
        lambda: post_command(gateways.alice, device["device_id"], operation="ping", timeout=3)
    )
    receive_until(ws, "command")
    close_ws(context, ws)
    response = thread_result(thread, result, timeout=3)
    assert response.status_code == 409

    context, ws, _ = open_device_ws(gateways.bob, device["token"])
    try:
        with pytest.raises(TimeoutError):
            receive_until(ws, "command", timeout=0.4)
    finally:
        close_ws(context, ws)


def test_csrf_origin_and_cache_headers(gateways):
    client = gateways.alice
    request = client.build_request("POST", "/api/devices", json={"name": "pc", "platform": "windows"})
    del request.headers["X-Device-Bridge"]
    assert client.send(request).status_code == 403
    for origin in ("https://evil.invalid", "null", "https://testserver"):
        assert client.post("/api/devices", json={"name": "pc", "platform": "windows"},
                           headers={"Origin": origin}).status_code == 403
    assert client.post("/api/devices", json={"name": "pc", "platform": "windows"},
                       headers={"Sec-Fetch-Site": "cross-site"}).status_code == 403
    result = client.post("/api/devices", json={"name": "pc", "platform": "windows"},
                         headers={"Origin": "http://testserver"})
    assert result.status_code == 201
    assert result.headers["cache-control"] == "no-store"
    assert client.get("/api/devices").headers["cache-control"] == "no-store"
    assert client.get("/api/devices", headers={"Origin": "https://evil.invalid"}).status_code == 403


def test_both_gateways_hookup_current_user_and_shutdown():
    import ast
    from pathlib import Path
    for gateway in ("api_gateway", "api_gateway_sigma"):
        tree = ast.parse(Path(gateway, "app.py").read_text())
        factory = [n for n in ast.walk(tree) if isinstance(n, ast.Call)
                   and isinstance(n.func, ast.Name) and n.func.id == "create_device_router"]
        assert len(factory) == 1
        assert [n.id for n in factory[0].args] == ["get_current_user", "device_redis"]
        source = Path(gateway, "app.py").read_text()
        assert "app.include_router(device_router)" in source
        assert "app.include_router(device_router.internal_router)" in source
        assert "await device_router.device_bridge_service.close()" in source
        assert "await device_redis.aclose()" in source


def test_redis_unavailable_fails_closed():
    async def current_user(_):
        return {"authenticated": True, "user_id": 56}
    app = FastAPI()
    client = Redis.from_url("redis://127.0.0.1:1", socket_connect_timeout=0.1)
    router = create_router(current_user, client)
    app.include_router(router)
    with TestClient(app, headers={"X-Device-Bridge": "1"}) as http:
        assert http.get("/api/devices").status_code == 503
        assert http.post("/api/devices", json={"name": "pc", "platform": "windows"}).status_code == 503
        with pytest.raises(WebSocketDisconnect):
            with http.websocket_connect("/api/devices/connect", headers={"Authorization": "Bearer " + "a" * 64}):
                pass
    asyncio.run(client.aclose())


def test_expired_device_token_rejected(gateways):
    device = create_device(gateways.alice)
    service = gateways.alice_service
    import hashlib
    digest = hashlib.sha256(device["token"].encode()).hexdigest()
    gateways.alice.portal.call(service.redis.delete, service._key("token", digest))
    with pytest.raises(WebSocketDisconnect):
        with gateways.bob.websocket_connect("/api/devices/connect", headers={"Authorization": "Bearer " + device["token"]}):
            pass


def test_result_output_limit(gateways):
    device = create_device(gateways.alice)
    context, ws, hello = open_device_ws(gateways.bob, device["token"])
    try:
        thread, result = call_in_thread(lambda: post_command(gateways.alice, device["device_id"], operation="ping", timeout=3))
        command = receive_until(ws, "command")
        ws.send_json({"type": "result", "job_id": command["job_id"], "session_id": hello["session_id"],
                      "status": "ok", "output": "x" * (65536 + 1)})
        with pytest.raises(WebSocketDisconnect) as error:
            receive_bounded(ws)
        assert error.value.code == 1009
        assert thread_result(thread, result).status_code == 409
    finally:
        close_ws(context, ws)


@pytest.mark.parametrize('configured', [None, '', 'too-short', ' ' * 40])
def test_internal_auth_disabled_fails_closed(monkeypatch, configured):
    if configured is None:
        monkeypatch.delenv('DEVICE_BRIDGE_SERVICE_TOKEN', raising=False)
    else:
        monkeypatch.setenv('DEVICE_BRIDGE_SERVICE_TOKEN', configured)
    def forbidden_user(_):
        pytest.fail('internal request consulted cookie/SSO authentication')
    # An unreachable store proves disabled auth never touches Redis.
    router = create_router(forbidden_user, None)
    app = FastAPI()
    app.include_router(router.internal_router)
    with TestClient(app) as client:
        assert client.get('/internal/devices?user_id=101', headers=SERVICE_HEADERS).status_code == 503
        assert client.post('/internal/devices/invalid/commands', headers=SERVICE_HEADERS,
                           json={'user_id': 101, 'operation': 'ping'}).status_code == 503


def test_service_bearer_only_auth_no_cookie_or_other_token_fallback(gateways):
    client = gateways.alice
    def forbidden_user(_):
        pytest.fail('internal request consulted cookie/SSO authentication')
    original = gateways.alice_service.get_current_user
    gateways.alice_service.get_current_user = forbidden_user
    try:
        for headers in ({}, {'Authorization': 'Basic ' + SERVICE_TOKEN},
                        {'Authorization': 'Bearer ' + 'z' * len(SERVICE_TOKEN)},
                        {'Authorization': 'Bearer short'},
                        {'X-Editor-Token': SERVICE_TOKEN},
                        {'Cookie': 'access_token=' + SERVICE_TOKEN,
                         'X-Authentik-Username': 'admin'},
                        [('Authorization', 'Bearer ' + SERVICE_TOKEN),
                         ('Authorization', 'Bearer ' + SERVICE_TOKEN)]):
            assert client.get('/internal/devices?user_id=101', headers=headers).status_code == 401
            assert client.post('/internal/devices/invalid/commands', headers=headers,
                               json={'user_id': 101, 'operation': 'ping'}).status_code == 401
        # Not subject to the public cookie Origin/CSRF route class.
        response = client.get('/internal/devices?user_id=101',
                              headers={**SERVICE_HEADERS, 'Origin': 'https://service.invalid',
                                       'Sec-Fetch-Site': 'cross-site'})
        assert response.status_code == 200
        assert response.headers['cache-control'] == 'no-store'
        assert response.json() == {'devices': []}
    finally:
        gateways.alice_service.get_current_user = original
    device = create_device(client)
    assert client.get('/internal/devices?user_id=101', headers={
        'Authorization': 'Bearer ' + device['token']}).status_code == 401
    assert client.get('/api/devices?user_id=202').json()['devices'][0]['device_id'] == device['device_id']


def test_internal_owner_validation_and_ownership(gateways):
    alice = create_device(gateways.alice)
    bob = create_device(gateways.bob)
    # Service on either gateway can delegate either owner, independent of its
    # cookie callback. Listing and command authorization use the same indexes.
    for client in (gateways.alice, gateways.bob):
        response = client.get('/internal/devices?user_id=101', headers=SERVICE_HEADERS)
        assert response.status_code == 200
        assert response.json() == gateways.alice.get('/api/devices').json()
        assert alice['token'] not in response.text and 'token_hash' not in response.text
        assert client.get('/internal/devices?user_id=202', headers=SERVICE_HEADERS).json() == gateways.bob.get('/api/devices').json()
        for uid in (None, '', '0', '-1', 'true', '1.5', '01', '+1', ' 1', '9223372036854775808'):
            params = {} if uid is None else {'user_id': uid}
            assert client.get('/internal/devices', params=params, headers=SERVICE_HEADERS).status_code == 422
        for uid in (None, 0, -1, True, 1.5, '101', 2**63):
            assert client.post(f"/internal/devices/{alice['device_id']}/commands", headers=SERVICE_HEADERS,
                               json={'user_id': uid, 'operation': 'ping'}).status_code == 422
        for device, wrong_owner in ((alice, 202), (bob, 101)):
            assert client.post(f"/internal/devices/{device['device_id']}/commands", headers=SERVICE_HEADERS,
                               json={'user_id': wrong_owner, 'operation': 'ping'}).status_code == 404
            assert client.post(f"/internal/devices/{device['device_id']}/commands", headers=SERVICE_HEADERS,
                               json={'user_id': wrong_owner, 'operation': 'notify', 'text': 'hello'}).status_code == 404
        assert client.post('/internal/devices', headers=SERVICE_HEADERS, json={}).status_code == 405
        assert client.delete(f"/internal/devices/{alice['device_id']}", headers=SERVICE_HEADERS).status_code == 404
        assert client.post(f"/internal/devices/{alice['device_id']}/commands", headers=SERVICE_HEADERS,
                           json={'user_id': 101, 'owner_id': 202, 'operation': 'ping'}).status_code == 422
    assert post_command(gateways.alice, alice['device_id'], user_id=202, operation='ping').status_code == 422
    assert post_command(gateways.alice, alice['device_id'], user_id=101, operation='notify', text='hi').status_code == 422


@pytest.mark.parametrize('text', [None, '', '  ', '\u2003', 12, True, ['hello'], 'x' * 2001,
                                 'bad\0text', 'bad\x1b[31m',
                                 'bad\x7f', 'bad\x85', 'bad\u202etext'])
def test_notify_rejects_invalid_text_public_and_internal(gateways, text):
    device_id = str(uuid.uuid4())
    body = {'operation': 'notify', 'text': text}
    assert post_command(gateways.alice, device_id, **body).status_code == 422
    assert gateways.bob.post(f'/internal/devices/{device_id}/commands', headers=SERVICE_HEADERS,
                             json={**body, 'user_id': 101}).status_code == 422


def test_command_schema_missing_text_and_ping_legacy_restrictions(gateways):
    device = create_device(gateways.alice)
    for body in ({'operation': 'notify'}, {'operation': 'ping', 'text': None},
                 {'operation': 'ping', 'text': 'hello'}, {'operation': 'exec'},
                 {'operation': 'notify', 'text': 'hi', 'shell': 'cmd'},
                 {'operation': 'notify', 'text': 'hi', 'timeout': True},
                 {'operation': 'ping', 'timeout': '10'}):
        assert post_command(gateways.alice, device['device_id'], **body).status_code == 422
        assert gateways.bob.post(f"/internal/devices/{device['device_id']}/commands", headers=SERVICE_HEADERS,
                                 json={**body, 'user_id': 101}).status_code == 422


def advertise_notify(gateways, ws, device_id):
    ws.send_json({'type': 'hello', 'protocol': 1, 'capabilities': ['ping', 'notify']})
    # Wait for advertisement to reach shared Redis rather than racing HTTP
    # against a websocket write; no artificial production handshake timeout.
    service = gateways.alice_service
    deadline = time.monotonic() + 2
    while not gateways.alice.portal.call(service.redis.hget, service._key('device', device_id), 'notify_session'):
        assert time.monotonic() < deadline
        time.sleep(0.01)


@pytest.mark.parametrize('internal', [False, True])
def test_notify_cross_gateway_dispatch_result_and_no_replay(gateways, internal):
    device = create_device(gateways.alice)
    device_id = device['device_id']
    context, ws, hello = open_device_ws(gateways.bob, device['token'])
    try:
        # Legacy clients (no optional hello) reject notify immediately, not 504.
        started = time.monotonic()
        assert post_command(gateways.alice, device_id, operation='notify', text='hello').status_code == 409
        assert time.monotonic() - started < 2
        advertise_notify(gateways, ws, device_id)
        # Shell-looking text remains inert display text and is preserved exactly.
        text = ' $(echo not-executed); Привет 🔔 ' + 'x' * (2000 - 39)
        text = text[:2000]
        body = {'operation': 'notify', 'text': text, 'timeout': 3}
        def issue():
            if internal:
                request = gateways.alice.build_request('POST', f'/internal/devices/{device_id}/commands',
                    headers={**SERVICE_HEADERS, 'Origin': 'https://service.invalid', 'Sec-Fetch-Site': 'cross-site'},
                    json={**body, 'user_id': 101})
                del request.headers['X-Device-Bridge']
                return gateways.alice.send(request)
            return post_command(gateways.alice, device_id, **body)
        thread, result = call_in_thread(issue)
        command = receive_until(ws, 'command')
        assert set(command) == {'type', 'job_id', 'session_id', 'deadline', 'operation', 'text', 'timeout'}
        assert command['operation'] == 'notify' and command['text'] == text
        assert command['session_id'] == hello['session_id']
        # Wrong schema cannot complete a notify job; operation is checked in Lua.
        ws.send_json({'type': 'result', 'job_id': str(uuid.uuid4()), 'session_id': hello['session_id'],
                      'status': 'ok', 'output': 'wrong correlation', 'exit_code': 0})
        assert receive_until(ws, 'error')['error'] == 'result_not_pending'
        ack = {'type': 'result', 'job_id': command['job_id'], 'session_id': hello['session_id'],
               'status': 'ok', 'output': 'displayed', 'exit_code': 0}
        ws.send_json(ack)
        response = thread_result(thread, result)
        assert response.status_code == 200
        assert response.json() == {'job_id': command['job_id'], 'status': 'ok', 'output': 'displayed', 'exit_code': 0}
        assert response.headers['cache-control'] == 'no-store'
        ws.send_json(ack)
        assert receive_until(ws, 'error')['error'] == 'result_not_pending'
    finally:
        close_ws(context, ws)
    context, ws, _ = open_device_ws(gateways.alice, device['token'])
    try:
        # Capability never persists across sessions/reconnects.
        assert post_command(gateways.alice, device_id, operation='notify', text='again').status_code == 409
        with pytest.raises(TimeoutError):
            receive_until(ws, 'command', timeout=0.3)
    finally:
        close_ws(context, ws)
    service = gateways.alice_service
    assert gateways.alice.portal.call(service.redis.keys, PREFIX + 'job:*') == []


def test_internal_ping_keeps_legacy_frames_and_results(gateways):
    device = create_device(gateways.alice)
    context, ws, hello = open_device_ws(gateways.bob, device['token'])
    try:
        thread, result = call_in_thread(lambda: gateways.alice.post(
            f"/internal/devices/{device['device_id']}/commands", headers=SERVICE_HEADERS,
            json={'user_id': 101, 'operation': 'ping', 'timeout': 3}))
        command = receive_until(ws, 'command')
        assert set(command) == {'type', 'job_id', 'session_id', 'deadline', 'operation', 'timeout'}
        ws.send_json({'type': 'result', 'job_id': command['job_id'], 'session_id': hello['session_id'],
                      'status': 'ok', 'output': 'pong', 'exit_code': 0})
        response = thread_result(thread, result)
        assert response.status_code == 200
        assert response.json() == {'job_id': command['job_id'], 'status': 'ok', 'output': 'pong', 'exit_code': 0}
    finally:
        close_ws(context, ws)


def _wait_field(gateways, device_id, field, present=True):
    service = gateways.alice_service
    deadline = time.monotonic() + 2
    while bool(gateways.alice.portal.call(service.redis.hget, service._key('device', device_id), field)) != present:
        assert time.monotonic() < deadline
        time.sleep(0.01)


def test_exec_requires_capability_and_relays_command_verbatim(gateways):
    device = create_device(gateways.alice)
    device_id = device['device_id']
    context, ws, hello = open_device_ws(gateways.bob, device['token'])
    try:
        # Without the exec capability: immediate 409, nothing dispatched.
        ws.send_json({'type': 'hello', 'protocol': 1, 'capabilities': ['ping', 'notify']})
        _wait_field(gateways, device_id, 'notify_session')
        r = post_command(gateways.alice, device_id, operation='exec', command='echo hi')
        assert r.status_code == 409
        listed = gateways.alice.get('/api/devices').json()['devices'][0]
        assert listed['capabilities'] == ['ping', 'notify']

        # Desktop toggle ON mid-session.
        ws.send_json({'type': 'capabilities', 'capabilities': ['ping', 'notify', 'exec']})
        _wait_field(gateways, device_id, 'exec_session')
        cmd_text = 'Get-ChildItem "C:\\Users" | Select -First 3; echo $(whoami) && ls ~'
        thread, result = call_in_thread(lambda: gateways.alice.post(
            f'/internal/devices/{device_id}/commands', headers=SERVICE_HEADERS,
            json={'user_id': 101, 'operation': 'exec', 'command': cmd_text, 'cwd': 'proj', 'timeout': 120}))
        command = receive_until(ws, 'command')
        assert command['operation'] == 'exec' and command['command'] == cmd_text
        assert command['cwd'] == 'proj' and command['timeout'] == 120
        ws.send_json({'type': 'result', 'job_id': command['job_id'], 'session_id': hello['session_id'],
                      'status': 'error', 'output': 'out', 'stderr': 'err', 'exit_code': 2,
                      'truncated': False, 'duration_ms': 15})
        response = thread_result(thread, result)
        assert response.status_code == 200
        assert response.json() == {'job_id': command['job_id'], 'status': 'error', 'output': 'out',
                                   'stderr': 'err', 'exit_code': 2, 'truncated': False, 'duration_ms': 15}

        # Toggle OFF: exec rejected again.
        ws.send_json({'type': 'capabilities', 'capabilities': ['ping', 'notify']})
        _wait_field(gateways, device_id, 'exec_session', present=False)
        assert post_command(gateways.alice, device_id, operation='exec', command='echo hi').status_code == 409
    finally:
        close_ws(context, ws)


def test_exec_schema_limits(gateways):
    device = create_device(gateways.alice)
    did = device['device_id']
    for body in ({'operation': 'exec'}, {'operation': 'exec', 'command': '   '},
                 {'operation': 'exec', 'command': 'a\x00b'},
                 {'operation': 'exec', 'command': 'x' * 16001},
                 {'operation': 'exec', 'command': 'ls', 'timeout': 301},
                 {'operation': 'ping', 'command': 'ls'}, {'operation': 'notify', 'text': 'hi', 'cwd': '/'},
                 {'operation': 'ping', 'timeout': 31}):
        assert post_command(gateways.alice, did, **body).status_code == 422, body


def test_capabilities_frame_before_hello_closes(gateways):
    device = create_device(gateways.alice)
    context, ws, _ = open_device_ws(gateways.bob, device['token'])
    try:
        ws.send_json({'type': 'capabilities', 'capabilities': ['exec']})
        with pytest.raises(WebSocketDisconnect) as error:
            receive_bounded(ws)
        assert error.value.code == 1008
    finally:
        close_ws(context, ws)
