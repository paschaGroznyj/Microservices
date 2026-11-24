"""User-mediated device relay; no commands are executed by the gateway.

Integration::

    router = create_router(get_current_user, async_redis_client)
    app.include_router(router)
    # During application shutdown, before closing the externally owned Redis:
    await router.device_bridge_service.close()

The factory returns an APIRouter, not a tuple. The injected get_current_user
is called with the HTTP Request; it must return a mapping with
``authenticated is True`` and a nonempty user_id. It is NEVER called for WS.
Use a shared, non-cluster Redis supporting EVAL (redis.asyncio.Redis with either
bytes or decoded responses). This module does not own/close that client.
Redis failures fail closed (HTTP 503 / WS 1011). No database is used.

HTTP (JSON bodies reject extra fields and coercions):
* POST /api/devices {name, platform} -> 201 {device_id, token, expires_in}.
  The random bearer token is returned once; only its SHA-256 digest is stored,
  with a 30-day TTL. Owner comes exclusively from get_current_user.
* GET /api/devices -> {devices: [{device_id,name,platform,status,created_at}]}.
  Status is online/offline/revoked; only the authenticated owner's records show.
* DELETE /api/devices/{id} -> {device_id, revoked:true}. Invalidates the token
  and lease atomically; the socket notices in <=1s (plus network scheduling).
* POST /api/devices/{id}/commands {operation:"ping", timeout:1..30,text?} waits for the result. ping and plain-text notify only; no execution.
  -> {job_id,status:"ok"|"error",output,exit_code?}; offline/disconnected 409,
  full queue 429, deadline 504. HTTP cancellation removes its outstanding job.

WS /api/devices/connect accepts ONLY Authorization: Bearer <device token>.
No query-string authentication, cookies, or SSO authentication is consulted.
Authentication and an atomic exclusive session claim precede accept; duplicate
connections are rejected, not allowed to replace the existing session.
Gateway -> {type:"hello",device_id,session_id,heartbeat_interval:5}.
Gateway -> {type:"ping",nonce}; reply {type:"pong",nonce} within 15s.
Client -> {type:"hello",protocol:1,capabilities:["ping","notify"]} (optional for legacy ping).
Gateway -> {type:"command",job_id,session_id,deadline,operation,timeout,text?}; deadline is Unix seconds (Redis clock).
Device -> {type:"result",job_id,session_id,status:"ok"|"error",output:str,
           exit_code?:int}. Unknown/late/duplicate results receive
{type:"error",error:"result_not_pending"}; malformed frames close 1008/1009.
Output is at most 64KiB UTF-8; frames at most 512KiB, including JSON escaping.
Configure the ASGI server's websocket frame limit too: application validation
cannot prevent the ASGI server from initially buffering a frame.

All authorization, session leases, bounded queues (100 outstanding jobs per
session), job issuance and result correlation live in shared Redis. Lua makes
revocation, lease renewal/cleanup, enqueue, dispatch and completion atomic.
Jobs are tied to the current session and deadline, dispatched once, never
replayed after reconnect. Abruptly killed workers lose their lease after 5s;
keys expire and HTTP waiters detect the lost session. No client-supplied ID can
create a result key. The only per-socket state is transport/heartbeat state.
No background tasks survive the websocket handler; close() signals shutdown.
"""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import os
import secrets
import time
import uuid
import unicodedata
from collections.abc import Mapping
from typing import Any, Callable, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.routing import APIRoute
from urllib.parse import urlsplit
from pydantic import BaseModel, Field, StrictStr, conint, root_validator

TOKEN_TTL = 30 * 86400
LEASE_TTL = 5
QUEUE_LIMIT = 100
MAX_OUTPUT = 64 * 1024
MAX_MESSAGE = 512 * 1024
PREFIX = "gw:device-bridge:"
MAX_OWNER_ID = 2**63 - 1


class _StrictModel(BaseModel):
    class Config:
        extra = "forbid"


class DeviceCreate(_StrictModel):
    name: StrictStr = Field(..., min_length=1, max_length=128)
    platform: StrictStr = Field(..., min_length=1, max_length=64)


class CommandCreate(_StrictModel):
    operation: Literal["ping", "notify"]
    timeout: conint(strict=True, ge=1, le=30) = 10
    text: Optional[StrictStr] = Field(None, max_length=2000)

    @root_validator(pre=True)
    def validate_text(cls, values):
        if not isinstance(values, dict):
            return values
        if values.get('operation') == 'notify':
            text = values.get('text')
            # Plain display text; LF/CR/tab are allowed. Reject other controls,
            # bidi formatting and surrogates. Never trim the payload.
            if (not isinstance(text, str) or not text.strip() or len(text) > 2000
                    or any(unicodedata.category(c) in {'Cc', 'Cf', 'Cs'} and c not in '\n\r\t' for c in text)):
                raise ValueError('notify requires 1..2000 nonblank safe text characters')
        elif 'text' in values:
            raise ValueError('text is only allowed for notify')
        return values


class InternalCommandCreate(CommandCreate):
    user_id: conint(strict=True, gt=0, le=MAX_OWNER_ID)


# Scripts deliberately have no non-atomic fallback. Redis TIME defines all job
# deadlines so separate gateway clocks cannot extend or prematurely expire jobs.
_NOW = "local t=redis.call('TIME'); local now=tonumber(t[1])+tonumber(t[2])/1000000; "
_LIVE = """
local live = redis.call('GET', KEYS[2]) == ARGV[1]
    and redis.call('HGET', KEYS[1], 'session_id') == ARGV[1]
    and redis.call('HGET', KEYS[1], 'revoked') == '0'
    and redis.call('HGET', KEYS[1], 'token_hash') == ARGV[2]
    and redis.call('GET', KEYS[3]) == ARGV[3]
"""
_CREATE = """
redis.call('HSET',KEYS[1],'device_id',ARGV[1],'owner_id',ARGV[2],
    'name',ARGV[3],'platform',ARGV[4],'token_hash',ARGV[5],'revoked','0',
    'created_at',ARGV[6])
redis.call('EXPIRE',KEYS[1],ARGV[7])
redis.call('SET',KEYS[2],ARGV[1],'EX',ARGV[7])
redis.call('SADD',KEYS[3],ARGV[1]); redis.call('EXPIRE',KEYS[3],ARGV[7])
return 1
"""
_CLAIM = """
if redis.call('HGET',KEYS[1],'revoked') ~= '0'
    or redis.call('HGET',KEYS[1],'token_hash') ~= ARGV[2]
    or not redis.call('HGET',KEYS[1],'owner_id')
    or redis.call('GET',KEYS[3]) ~= ARGV[3] then return 0 end
if redis.call('EXISTS',KEYS[2]) == 1 then return 2 end
redis.call('SET',KEYS[2],ARGV[1],'EX',ARGV[4])
redis.call('HSET',KEYS[1],'session_id',ARGV[1])
redis.call('HDEL',KEYS[1],'notify_session')
return 1
"""
_RENEW = _LIVE + """
if not live then return 0 end
redis.call('EXPIRE',KEYS[2],ARGV[4]); return 1
"""
_ADVERTISE = _LIVE + """
if not live then return 0 end
if ARGV[4] == '1' then redis.call('HSET',KEYS[1],'notify_session',ARGV[1])
else redis.call('HDEL',KEYS[1],'notify_session') end
return 1
"""
_REVOKE = """
if redis.call('HGET',KEYS[1],'owner_id') ~= ARGV[1] then return 0 end
redis.call('HSET',KEYS[1],'revoked','1')
redis.call('DEL',KEYS[2],KEYS[3]); redis.call('HDEL',KEYS[1],'session_id')
return 1
"""
_ENQUEUE = _NOW + _LIVE + """
if not live or redis.call('HGET',KEYS[1],'owner_id') ~= ARGV[4] then return {0} end
if ARGV[11] == 'notify' and redis.call('HGET',KEYS[1],'notify_session') ~= ARGV[1] then return {3} end
if redis.call('SCARD',KEYS[5]) >= tonumber(ARGV[10])
    or redis.call('LLEN',KEYS[4]) >= tonumber(ARGV[10]) then return {2} end
local deadline = now + tonumber(ARGV[6])
redis.call('HSET',KEYS[6],'job_id',ARGV[5],'owner_id',ARGV[4],
    'device_id',ARGV[3],'session_id',ARGV[1],'state','queued',
    'deadline',tostring(deadline),'payload',ARGV[7],'operation',ARGV[11])
redis.call('EXPIRE',KEYS[6],ARGV[8])
redis.call('SADD',KEYS[5],ARGV[5]); redis.call('EXPIRE',KEYS[5],ARGV[9])
redis.call('RPUSH',KEYS[4],ARGV[5]); redis.call('EXPIRE',KEYS[4],ARGV[9])
return {1,tostring(deadline)}
"""
# Pop and validate/mark dispatched in one operation. Keys under the constant
# prefix can only originate in the server-generated queue, never from a frame.
_DISPATCH = _NOW + _LIVE + """
if not live then return {0} end
local id=redis.call('LPOP',KEYS[4]); if not id then return {1} end
local job=ARGV[4] .. id
if redis.call('SISMEMBER',KEYS[5],id) == 0 then return {1} end
local state=redis.call('HGET',job,'state')
local deadline=tonumber(redis.call('HGET',job,'deadline') or '0')
if state ~= 'queued' or deadline <= now
    or redis.call('HGET',job,'session_id') ~= ARGV[1]
    or redis.call('HGET',job,'device_id') ~= ARGV[3] then return {1} end
redis.call('HSET',job,'state','sent')
return {2,id,tostring(deadline),redis.call('HGET',job,'payload')}
"""
_COMPLETE = _NOW + _LIVE + """
if not live or redis.call('SISMEMBER',KEYS[4],ARGV[4]) == 0 then return 0 end
if redis.call('HGET',KEYS[5],'state') ~= 'sent'
    or redis.call('HGET',KEYS[5],'session_id') ~= ARGV[1]
    or redis.call('HGET',KEYS[5],'device_id') ~= ARGV[3]
    or redis.call('HGET',KEYS[5],'owner_id') ~= redis.call('HGET',KEYS[1],'owner_id')
    or tonumber(redis.call('HGET',KEYS[5],'deadline') or '0') <= now then return 0 end
redis.call('HSET',KEYS[5],'state','done','result',ARGV[5]); return 1
"""
_READ = _NOW + _LIVE + """
if redis.call('HGET',KEYS[4],'owner_id') ~= ARGV[4]
    or redis.call('HGET',KEYS[4],'session_id') ~= ARGV[1]
    or redis.call('HGET',KEYS[4],'device_id') ~= ARGV[3] then return {0} end
-- A result committed while live and before its deadline remains deliverable
-- even if the device disconnected immediately afterwards.
if redis.call('HGET',KEYS[4],'state') == 'done' then
    return {1,redis.call('HGET',KEYS[4],'result')} end
if not live then return {0} end
if tonumber(redis.call('HGET',KEYS[4],'deadline') or '0') <= now then return {2} end
return {3}
"""
_CLEAN_JOB = """
redis.call('DEL',KEYS[1]); redis.call('SREM',KEYS[2],ARGV[1])
redis.call('LREM',KEYS[3],0,ARGV[1]); return 1
"""
_DISCONNECT = """
-- Never erase a replacement connection's lease or session field.
if redis.call('GET',KEYS[2]) == ARGV[1] then redis.call('DEL',KEYS[2]) end
if redis.call('HGET',KEYS[1],'session_id') == ARGV[1] then
    redis.call('HDEL',KEYS[1],'session_id','notify_session') end
local ids=redis.call('SMEMBERS',KEYS[3])
for _,id in ipairs(ids) do
    local job=ARGV[2] .. id
    local state=redis.call('HGET',job,'state')
    if state == 'queued' or state == 'sent' then
        redis.call('HSET',job,'state','cancelled') end
end
redis.call('DEL',KEYS[3],KEYS[4]); return 1
"""


class InternalDeviceRoute(APIRoute):
    """No cookie/CSRF checks; bearer-only routes must not be cached either."""

    def get_route_handler(self):
        handler = super().get_route_handler()

        async def guarded(request: Request):
            response = await handler(request)
            response.headers['Cache-Control'] = 'no-store'
            response.headers['Pragma'] = 'no-cache'
            return response
        return guarded


class DeviceRoute(APIRoute):
    """CSRF protection for cookie/SSO HTTP callers; never applies to device WS."""

    def get_route_handler(self):
        handler = super().get_route_handler()

        async def guarded(request: Request):
            if request.method in {"POST", "DELETE"} and request.headers.get("x-device-bridge") != "1":
                raise HTTPException(403, "X-Device-Bridge: 1 required")
            if request.headers.get("sec-fetch-site") == "cross-site":
                raise HTTPException(403, "Cross-site request rejected")
            origin = request.headers.get("origin")
            if origin:
                parsed = urlsplit(origin)
                expected = urlsplit(str(request.base_url))
                if (parsed.scheme, parsed.netloc) != (expected.scheme, expected.netloc) or parsed.path or parsed.query or parsed.fragment:
                    raise HTTPException(403, "Origin rejected")
            response = await handler(request)
            response.headers["Cache-Control"] = "no-store"
            response.headers["Pragma"] = "no-cache"
            return response
        return guarded


class DeviceBridgeService:
    """Factory-local lifecycle only; the injected Redis client stays external."""

    def __init__(self, get_current_user: Callable[..., Any], redis_client: Any):
        self.get_current_user = get_current_user
        self.redis = redis_client
        self._closing = asyncio.Event()
        # Dedicated trust boundary: no JWT/editor/device token or cookie fallback.
        token = os.getenv('DEVICE_BRIDGE_SERVICE_TOKEN', '')
        self._service_digest = (hashlib.sha256(token.encode()).digest()
                                if 32 <= len(token) <= 512 and token.isascii()
                                and all(33 <= ord(c) <= 126 for c in token) else None)

    async def service_auth(self, request: Request) -> None:
        if self._service_digest is None:
            raise HTTPException(503, 'Device service authentication is not configured')
        headers = request.headers.getlist('authorization')
        parts = headers[0].split(' ') if len(headers) == 1 else []
        if (len(parts) != 2 or parts[0].lower() != 'bearer'
                or not 32 <= len(parts[1]) <= 512
                or not secrets.compare_digest(hashlib.sha256(parts[1].encode()).digest(), self._service_digest)):
            raise HTTPException(401, 'Invalid service credential', headers={'WWW-Authenticate': 'Bearer'})

    async def internal_list(self, user_id: str = Query(..., min_length=1, max_length=19,
                                                     regex=r'^[1-9][0-9]*$')) -> dict[str, Any]:
        if int(user_id) > MAX_OWNER_ID:
            raise HTTPException(422, 'user_id must be a positive signed 64-bit integer')
        return await self._list_devices(user_id)

    async def internal_command(self, device_id: str, body: InternalCommandCreate,
                               request: Request) -> dict[str, Any]:
        return await self._command(device_id, body, request, str(body.user_id))

    async def close(self) -> None:
        """Signal active handlers to stop on their next <=1s lease check."""
        self._closing.set()

    @staticmethod
    def _text(value: Any) -> str:
        return value.decode('utf-8') if isinstance(value, bytes) else str(value)

    @classmethod
    def _mapping(cls, value: Any) -> dict[str, str]:
        return {cls._text(k): cls._text(v) for k, v in value.items()}

    @staticmethod
    def _key(kind: str, identifier: str) -> str:
        return PREFIX + kind + ':' + identifier

    @staticmethod
    def _valid_id(value: Any) -> bool:
        if not isinstance(value, str) or len(value) != 36:
            return False
        try:
            return str(uuid.UUID(value)) == value
        except ValueError:
            return False

    async def _call(self, method: str, *args: Any, **kwargs: Any) -> Any:
        try:
            return await asyncio.wait_for(getattr(self.redis, method)(*args, **kwargs), 2)
        except Exception:
            # Do not interpolate exceptions: client exceptions can contain
            # connection credentials, Lua arguments, commands or bearer tokens.
            raise HTTPException(503, 'Device store unavailable') from None

    async def _eval(self, script: str, keys: list[str], *args: Any) -> Any:
        return await self._call('eval', script, len(keys), *keys, *args)

    def _live_keys(self, device_id: str, digest: str) -> list[str]:
        return [self._key('device', device_id), self._key('lease', device_id),
                self._key('token', digest)]

    def _session_keys(self, session: str) -> list[str]:
        return [self._key('queue', session), self._key('pending', session)]

    async def _user(self, request: Request) -> str:
        user = self.get_current_user(request)
        if inspect.isawaitable(user):
            user = await user
        if not isinstance(user, Mapping) or user.get('authenticated') is not True:
            raise HTTPException(401, 'Not authenticated')
        uid = user.get('user_id')
        if isinstance(uid, bool) or not isinstance(uid, (str, int)) or not str(uid).strip() or len(str(uid)) > 256:
            raise HTTPException(401, 'Not authenticated')
        return str(uid)

    def _owner_key(self, owner: str) -> str:
        return self._key('owner', hashlib.sha256(owner.encode()).hexdigest())

    async def _owned(self, device_id: str, owner: str) -> dict[str, str]:
        if not self._valid_id(device_id):
            raise HTTPException(404, 'Device not found')
        device = self._mapping(await self._call('hgetall', self._key('device', device_id)))
        if device.get('owner_id') != owner or device.get('device_id') != device_id:
            raise HTTPException(404, 'Device not found')
        return device

    async def create_device(self, body: DeviceCreate, request: Request) -> dict[str, Any]:
        owner = await self._user(request)
        if not body.name.strip() or not body.platform.strip() or '\x00' in body.name + body.platform:
            raise HTTPException(422, 'name and platform must be nonblank text without NUL')
        device_id = str(uuid.uuid4())
        token = secrets.token_urlsafe(48)
        digest = hashlib.sha256(token.encode()).hexdigest()
        await self._eval(_CREATE, [self._key('device', device_id), self._key('token', digest),
                                  self._owner_key(owner)], device_id, owner, body.name.strip(),
                         body.platform.strip(), digest, str(int(time.time())), TOKEN_TTL)
        return {'device_id': device_id, 'token': token, 'expires_in': TOKEN_TTL}

    async def list_devices(self, request: Request) -> dict[str, Any]:
        return await self._list_devices(await self._user(request))

    async def _list_devices(self, owner: str) -> dict[str, Any]:
        owner_key = self._owner_key(owner)
        devices = []
        # SSCAN avoids a single unbounded Redis SMEMBERS response. Expired
        # records are pruned without extending the index's lifetime.
        cursor = 0
        seen: set[str] = set()
        while True:
            cursor, ids = await self._call('sscan', owner_key, cursor=cursor, count=100)
            for raw_id in ids:
                device_id = self._text(raw_id)
                if device_id in seen:
                    continue
                seen.add(device_id)
                d = self._mapping(await self._call('hgetall', self._key('device', device_id)))
                if d.get('owner_id') != owner:
                    await self._call('srem', owner_key, device_id)
                    continue
                lease = await self._call('get', self._key('lease', device_id))
                online = bool(lease and self._text(lease) == d.get('session_id'))
                status = 'revoked' if d.get('revoked') == '1' else ('online' if online else 'offline')
                devices.append({'device_id': device_id, 'name': d['name'], 'platform': d['platform'],
                                'status': status, 'created_at': int(d['created_at'])})
            if int(cursor) == 0:
                break
        return {'devices': devices}

    async def revoke_device(self, device_id: str, request: Request) -> dict[str, Any]:
        owner = await self._user(request)
        d = await self._owned(device_id, owner)
        if not await self._eval(_REVOKE, self._live_keys(device_id, d['token_hash']), owner):
            raise HTTPException(404, 'Device not found')
        return {'device_id': device_id, 'revoked': True}

    async def command(self, device_id: str, body: CommandCreate, request: Request) -> dict[str, Any]:
        return await self._command(device_id, body, request, await self._user(request))

    async def _command(self, device_id: str, body: CommandCreate, request: Request,
                       owner: str) -> dict[str, Any]:
        d = await self._owned(device_id, owner)
        session = d.get('session_id', '')
        if not session or d.get('revoked') != '0' or self._closing.is_set():
            raise HTTPException(409, 'Device is offline')
        live_keys = self._live_keys(device_id, d['token_hash'])
        queue_key, pending_key = self._session_keys(session)
        job_id = str(uuid.uuid4())
        job_key = self._key('job', job_id)
        payload = {'operation': body.operation, 'timeout': body.timeout}
        if body.operation == 'notify':
            payload['text'] = body.text
        # Cleanup is attempted even if an enqueue response is lost after Redis
        # has applied it. The fallback safety net is bounded key TTLs.
        try:
            result = await self._eval(_ENQUEUE, live_keys + [queue_key, pending_key, job_key],
                                      session, d['token_hash'], device_id, owner, job_id,
                                      body.timeout, json.dumps(payload), body.timeout + 30,
                                      150, QUEUE_LIMIT, body.operation)
            code = int(result[0])
            if code == 0:
                raise HTTPException(409, 'Device is offline')
            if code == 3:
                raise HTTPException(409, 'Device does not support notify in this session')
            if code == 2:
                raise HTTPException(429, 'Device command queue is full')
            local_deadline = time.monotonic() + body.timeout
            while True:
                result = await self._eval(_READ, live_keys + [job_key], session,
                                          d['token_hash'], device_id, owner)
                code = int(result[0])
                if code == 1:
                    return {'job_id': job_id, **json.loads(self._text(result[1]))}
                if code == 0 or self._closing.is_set():
                    raise HTTPException(409, 'Device disconnected or command cancelled')
                if code == 2 or time.monotonic() >= local_deadline:
                    raise HTTPException(504, 'Device command timed out')
                if await request.is_disconnected():
                    raise HTTPException(499, 'Request disconnected')
                await asyncio.sleep(0.1)
        finally:
            # Cancellation must not orphan the job. Shield a finite operation;
            # the helper owns no perpetual cleanup tasks.
            cleanup = asyncio.create_task(self._eval(_CLEAN_JOB, [job_key, pending_key, queue_key], job_id))
            try:
                await asyncio.shield(cleanup)
            except asyncio.CancelledError:
                await asyncio.gather(cleanup, return_exceptions=True)
                raise
            except HTTPException:
                pass

    async def _authenticate(self, ws: WebSocket) -> tuple[str, str]:
        headers = ws.headers.getlist('authorization')
        # A query is not an authentication fallback, nor is get_current_user.
        if len(headers) != 1:
            raise ValueError('invalid credential')
        parts = headers[0].split(' ')
        if len(parts) != 2 or parts[0].lower() != 'bearer' or len(parts[1]) != 64:
            raise ValueError('invalid credential')
        if any(c not in 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_' for c in parts[1]):
            raise ValueError('invalid credential')
        digest = hashlib.sha256(parts[1].encode()).hexdigest()
        raw_id = await self._call('get', self._key('token', digest))
        device_id = self._text(raw_id) if raw_id else ''
        if not self._valid_id(device_id):
            raise ValueError('invalid credential')
        return device_id, digest

    async def websocket(self, ws: WebSocket) -> None:
        session = str(uuid.uuid4())
        tasks: list[asyncio.Task] = []
        close_code = 1000
        live_keys: list[str] = []
        queue_key, pending_key = self._session_keys(session)
        try:
            if self._closing.is_set():
                await ws.close(code=1011)
                return
            device_id, digest = await self._authenticate(ws)
            live_keys = self._live_keys(device_id, digest)
            code = int(await self._eval(_CLAIM, live_keys, session, digest, device_id, LEASE_TTL))
            if code != 1:
                raise ValueError('invalid credential or duplicate session')
            # Revalidate after claiming and immediately before accept.
            if not await self._eval(_RENEW, live_keys, session, digest, device_id, LEASE_TTL):
                raise ValueError('revoked')
            await ws.accept()
            send_lock = asyncio.Lock()
            last_pong = time.monotonic()
            nonce: Optional[str] = None

            async def send(value: dict[str, Any]) -> None:
                async with send_lock:
                    await asyncio.wait_for(ws.send_json(value), 0.5)

            await send({'type': 'hello', 'device_id': device_id, 'session_id': session,
                        'heartbeat_interval': 5})

            async def receive() -> int:
                nonlocal last_pong, nonce
                hello_seen = False
                while True:
                    packet = await ws.receive()
                    if packet['type'] == 'websocket.disconnect':
                        return 1000
                    text = packet.get('text')
                    if text is None:
                        return 1008
                    if len(text.encode('utf-8')) > MAX_MESSAGE:
                        return 1009
                    try:
                        msg = json.loads(text)
                    except (ValueError, RecursionError):
                        return 1008
                    if not isinstance(msg, dict):
                        return 1008
                    if msg.get('type') == 'hello':
                        capabilities = msg.get('capabilities')
                        if set(msg) == {'type', 'protocol', 'capabilities'}:
                            if (hello_seen or type(msg.get('protocol')) is not int or msg['protocol'] != 1
                                    or not isinstance(capabilities, list) or len(capabilities) > 16
                                    or any(not isinstance(c, str) or not 1 <= len(c) <= 64 for c in capabilities)):
                                return 1008
                            hello_seen = True
                            if not await self._eval(_ADVERTISE, live_keys, session, digest, device_id,
                                                    int('notify' in capabilities)):
                                return 1008
                            continue
                        return 1008
                    if msg.get('type') == 'pong':
                        if set(msg) != {'type', 'nonce'} or nonce is None or msg['nonce'] != nonce:
                            return 1008
                        nonce = None
                        last_pong = time.monotonic()
                        continue
                    if msg.get('type') != 'result' or set(msg) - {'type', 'job_id', 'session_id', 'status', 'output', 'exit_code'}:
                        return 1008
                    if msg.get('session_id') != session:
                        return 1008
                    job_id = msg.get('job_id')
                    if (not self._valid_id(job_id)
                            or msg.get('session_id') != session
                            or msg.get('status') not in ('ok', 'error')
                            or not isinstance(msg.get('output'), str)):
                        return 1008
                    if len(msg['output'].encode('utf-8')) > MAX_OUTPUT:
                        return 1009
                    if 'exit_code' in msg and (type(msg['exit_code']) is not int
                                               or not -(2**31) <= msg['exit_code'] < 2**31):
                        return 1008
                    result = {k: msg[k] for k in ('status', 'output', 'exit_code') if k in msg}
                    accepted = await self._eval(_COMPLETE,
                                                live_keys + [pending_key, self._key('job', job_id)],
                                                session, digest, device_id, job_id, json.dumps(result))
                    if not accepted:
                        await send({'type': 'error', 'error': 'result_not_pending'})

            async def relay() -> int:
                nonlocal nonce
                next_renew = 0.0
                next_ping = 0.0
                while True:
                    now = time.monotonic()
                    if self._closing.is_set():
                        return 1001
                    if now - last_pong >= 15:
                        return 1008
                    if now >= next_renew:
                        if not await self._eval(_RENEW, live_keys, session, digest, device_id, LEASE_TTL):
                            return 1008
                        next_renew = now + 0.5
                    if now >= next_ping and nonce is None:
                        nonce = secrets.token_urlsafe(16)
                        await send({'type': 'ping', 'nonce': nonce})
                        next_ping = now + 5
                    item = await self._eval(_DISPATCH, live_keys + [queue_key, pending_key],
                                            session, digest, device_id, self._key('job', ''))
                    code = int(item[0])
                    if code == 0:
                        return 1008
                    if code == 2:
                        payload = json.loads(self._text(item[3]))
                        await send({'type': 'command', 'job_id': self._text(item[1]),
                                    'session_id': session, 'deadline': float(self._text(item[2])),
                                    **payload})
                    await asyncio.sleep(0.1)

            tasks = [asyncio.create_task(receive()), asyncio.create_task(relay())]
            done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                close_code = max(close_code, task.result())
        except ValueError:
            close_code = 1008
        except WebSocketDisconnect:
            close_code = 1000
        except Exception:
            close_code = 1011
        finally:
            for task in tasks:
                task.cancel()
            # Close transport before Redis cleanup: a failed store must never
            # keep a revoked/malformed connection visibly open.
            try:
                await asyncio.wait_for(ws.close(code=close_code), 1)
            except Exception:
                pass
            await asyncio.gather(*tasks, return_exceptions=True)
            # Even a lost claim response might have applied. Cleaning with our
            # random session ID cannot damage the incumbent/newer connection.
            if live_keys:
                try:
                    await self._eval(_DISCONNECT, live_keys[:2] + [pending_key, queue_key],
                                     session, self._key('job', ''))
                except HTTPException:
                    pass


def create_router(get_current_user: Callable[..., Any], redis_client: Any) -> APIRouter:
    """Bind one independent service; never mutates global authentication state."""
    service = DeviceBridgeService(get_current_user, redis_client)
    router = APIRouter(route_class=DeviceRoute)
    router.device_bridge_service = service  # type: ignore[attr-defined]
    # Mount separately, never through DeviceRoute's cookie/CSRF guard. A router
    # dependency authenticates before handlers and never calls get_current_user.
    internal = APIRouter(route_class=InternalDeviceRoute, dependencies=[Depends(service.service_auth)])
    internal.add_api_route('/internal/devices', service.internal_list, methods=['GET'])
    internal.add_api_route('/internal/devices/{device_id}/commands', service.internal_command, methods=['POST'])
    router.internal_router = internal  # type: ignore[attr-defined]
    router.add_api_route('/api/devices', service.create_device, methods=['POST'], status_code=201)
    router.add_api_route('/api/devices', service.list_devices, methods=['GET'])
    router.add_api_route('/api/devices/{device_id}', service.revoke_device, methods=['DELETE'])
    router.add_api_route('/api/devices/{device_id}/commands', service.command, methods=['POST'])
    router.add_api_websocket_route('/api/devices/connect', service.websocket)
    return router
