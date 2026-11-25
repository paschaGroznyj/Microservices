# Desktop core tests

From the repository root:

```bash
PYTHONDONTWRITEBYTECODE=1 ../.venvt/bin/python -m pytest device_client/tests \
  -q -o asyncio_default_fixture_loop_scope=function --tb=short -rxX
```

Requires pytest, websocket-client, httpx, redis, fastapi, uvicorn and websockets.
The real integration test needs `redis-server` on PATH or the existing extracted
binary at `../redis-test-runtime/usr/bin/redis-server` (its extracted libraries
are selected automatically). No pytest-asyncio fixture is required.

`test_core.py` uses deterministic fake sockets/clocks for validation, protocol,
security, lifecycle and retry behavior, including strict token validation and
terminal rejection of redirect handshake responses.

`test_integration.py` starts its own isolated Redis and two separate uvicorn
processes on dynamically allocated loopback ports. It uses the actual
`BridgeClient.run()` in threads, not a protocol stub. HTTP requests go to one
worker; device WebSocket connections go to the other. The worker setup follows
`output/network_bridge_smoke.py`, but is kept locally in `network_worker.py` so
these tests do not depend on a generated output script.

Integration covers registration, offline/online listing, duplicate session
rejection, HTTP ping/pong across workers, uninterrupted heartbeat for 6.3 seconds,
stop/offline, manual reconnection, automatic reconnection after forced socket
shutdown, revocation stopping the client, and terminal invalid/revoked token
authentication failures. Registration credentials remain in memory only. Child
stdout/stderr are discarded. The report contains checks/counts/timing only.

Every client thread is stopped/joined and each subprocess terminated/killed and
reaped in fixture teardown, including assertion failures. Redis persistence is
disabled and its directory is disposable. The integration report is written to
`output/device_client/integration.json`.

To run only the real smoke:

```bash
PYTHONDONTWRITEBYTECODE=1 ../.venvt/bin/python -m pytest \
  device_client/tests/test_integration.py -q \
  -o asyncio_default_fixture_loop_scope=function --tb=short
```

`gui_smoke.py` exercises actual Tk widgets and actual network bridge (not a UI
mock), including connect, disabled duplicate connect, ping in log, token clearing,
disconnect, clear log and close. Run from the repository root with a display:

```bash
python device_client/tests/gui_smoke.py
```

Linux CI needs Xvfb and Tcl/Tk. Screenshots and checks go to output/device_client/.
This is not validation of native Windows rendering or PowerShell execution.
