# Agent Device Bridge files

The `content` and `plugins` categories expose `list_devices`, `run_on_device`,
`send_file_to_device`, `get_file_from_device`, and `capture_screenshot`.
All are bound to the web request owner. Messenger/trigger contexts remain disabled.

* Send: provide `device_id`, `destination_path`, and exactly one source:
  `local_path` previously returned by these bridge tools for the same owner (arbitrary shared files are refused), `workspace_id` + relative
  `workspace_path`, or a generated `image_key` owned by the request user.
  Factory `/tmp/exec/workspaces/<id>/<relative-path>` local paths are bridged
  through the existing sandbox endpoint, never opened as agent-local files. Workspace access first requires `owns: true` from the authoritative chats_service /editor/owns registry, fail-closed.
  No arbitrary URL source or cwd override is exposed.
* Get: provide `device_id` and device source `path`.
* Screenshot: provide `device_id` only. Ask only when the user requests it:
  captures may include private windows and credentials. Client **full mode**,
  command permission, graphical desktop, and OS capture permission are required.

Files are limited to **10 MiB**, SHA-256 and size checked, and downloaded to
`/app/generated_files/device_bridge/<owner>/<uuid>_<sanitized-filename>` with private
file permissions and a private SQLite ownership/hash registry. MIME is inferred locally, not accepted from the remote device.
Results contain metadata and local paths, not binary/base64 or automatic attachments.
Use `send_file_to_user(path=...)` to deliver files or `analyze_image(local_path=...)`
to inspect images. Relative device paths resolve from its configured workspace;
client policy is authoritative. Sending cannot overwrite an existing device file.

Gateway requests use `DEVICE_BRIDGE_URL` and `DEVICE_BRIDGE_SERVICE_TOKEN`.
Ticket creation and PUT/GET/DELETE include the server-bound `user_id`; only
commands receive the short-lived transfer credential. HTTP responses are streamed
with bounded reads and redirects disabled. Error bodies, remote failure output,
and exceptions are not echoed to the model. Every acquired valid ticket is
best-effort deleted in `finally`, with the gateway TTL as fallback.

Test in an environment with langchain, httpx, pytest and pytest-asyncio:

```sh
cd agent_service
pytest -q tests/test_device_file_tools.py
```

## Deployment and ingress

Update both gateways and agent_service together, then restart the updated desktop client. Existing device tokens and config remain valid. `DEVICE_BRIDGE_SERVICE_TOKEN` must match in agent and gateways. Workspace source authorization also requires access to `CHATS_SERVICE_URL` (default `http://chats_service:8001`).

Nginx must proxy `/api/device-transfers/` to the same gateway that handles `/api/devices/`, pass Authorization, allow GET/PUT bodies up to **10m**, and allow a 300-second file request. Set `client_max_body_size 10m` on this location (default nginx 1m otherwise rejects larger files). No SSO redirect/interstitial may replace the binary response. Never disable token authorization on the gateway.

Block **both** `/internal/devices` (exact and prefix) and `/internal/device-transfers/` on every public vhost. Agent uses the trusted Docker origin directly; do not expose service API through ingress. Public `/api/device-transfers/` does not use browser cookies: each request requires its single-use transfer Bearer token, bound to one device and direction.

Files are kept base64-encoded in Redis for a maximum 600 seconds (10 MiB binary is about 13.4 MiB in Redis per ticket). Deploy with Redis memory headroom and appropriate per-user rate limiting at ingress. Successful agent operations delete tickets immediately; TTL cleans up interrupted transfers.
