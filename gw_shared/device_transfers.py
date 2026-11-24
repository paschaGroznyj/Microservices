"""Bounded, one-shot file tickets. Redis owns metadata and base64 bodies.

No filesystem or worker-local state. All authorization/state changes are Lua
transactions; TTL never extends. Base64 keeps binary data safe with either
redis decode_responses setting. Raw credentials never enter Redis or errors.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import re
import secrets
import uuid
from typing import Literal, Optional

from fastapi import HTTPException, Query, Request, Response
from pydantic import BaseModel, StrictStr, conint

MAX_BYTES = 10 * 1024 * 1024
TTL = 600
SHA_PATTERN = r'^[0-9a-f]{64}$'


class TransferCreate(BaseModel):
    direction: Literal['to_device', 'from_device']
    size: Optional[conint(strict=True, ge=0, le=MAX_BYTES)] = None
    sha256: Optional[StrictStr] = None

    class Config:
        extra = 'forbid'

    # Do not coerce hashes or permit partial matches/newlines.
    from pydantic import validator

    @validator('sha256')
    def digest(cls, value):
        if value is not None and not re.fullmatch(SHA_PATTERN, value):
            raise ValueError('sha256 must be lowercase hex SHA-256')
        return value


_CREATE = """
if redis.call('HGET',KEYS[2],'owner_id') ~= ARGV[1]
    or redis.call('HGET',KEYS[2],'revoked') ~= '0' then return 0 end
if redis.call('EXISTS',KEYS[1]) == 1 then return 0 end
redis.call('HSET',KEYS[1],'owner',ARGV[1],'device',ARGV[2],'direction',ARGV[3],
    'token_hash',ARGV[4],'expected_size',ARGV[5],'expected_sha',ARGV[6],'state','empty')
redis.call('EXPIRE',KEYS[1],600)
return 1
"""
_AUTH = """
if redis.call('EXISTS',KEYS[1]) == 0 then return {0} end
local owner=redis.call('HGET',KEYS[1],'owner')
if redis.call('HGET',KEYS[2],'owner_id') ~= owner
    or redis.call('HGET',KEYS[2],'revoked') ~= '0'
    or redis.call('HGET',KEYS[2],'device_id') ~= redis.call('HGET',KEYS[1],'device') then return {0} end
if ARGV[1] == 'service' then
    if owner ~= ARGV[2] then return {0} end
else
    if redis.call('HGET',KEYS[1],'token_hash') ~= ARGV[2] then return {0} end
end
"""
_ACTION = _AUTH + """
if ARGV[3] == 'delete' then redis.call('DEL',KEYS[1]); return {1} end
if redis.call('HGET',KEYS[1],'direction') ~= ARGV[4] then return {2} end
if ARGV[3] == 'check' then return {1} end
local state=redis.call('HGET',KEYS[1],'state')
if ARGV[3] == 'put' then
    if state ~= 'empty' then return {2} end
    redis.call('HSET',KEYS[1],'state','writing','claim',ARGV[5]); return {1}
end
if state ~= 'ready' then return {2} end
local body=redis.call('HGET',KEYS[1],'body')
if not body then return {2} end
redis.call('HSET',KEYS[1],'state','reading','claim',ARGV[5])
redis.call('HDEL',KEYS[1],'body')
return {1,body,redis.call('HGET',KEYS[1],'size'),redis.call('HGET',KEYS[1],'sha256')}
"""
_COMMIT = _AUTH + """
if redis.call('HGET',KEYS[1],'state') ~= 'writing'
    or redis.call('HGET',KEYS[1],'claim') ~= ARGV[3] then return {2} end
local size=redis.call('HGET',KEYS[1],'expected_size')
local sha=redis.call('HGET',KEYS[1],'expected_sha')
if (size ~= '' and size ~= ARGV[4]) or (sha ~= '' and sha ~= ARGV[5]) then
    redis.call('HSET',KEYS[1],'state','failed'); return {3} end
redis.call('HSET',KEYS[1],'state','ready','size',ARGV[4],'sha256',ARGV[5],'body',ARGV[6])
return {1}
"""
_FINISH = """
if redis.call('HGET',KEYS[1],'claim') == ARGV[1] then
    redis.call('HSET',KEYS[1],'state',ARGV[2]); redis.call('HDEL',KEYS[1],'body') end
return 1
"""


class _OneShotResponse(Response):
    """Claim before sending; consume even an ambiguous/disconnected delivery."""
    def __init__(self, content, finish, **kwargs):
        super().__init__(content, **kwargs)
        self.finish = finish

    async def __call__(self, scope, receive, send):
        try:
            await super().__call__(scope, receive, send)
        finally:
            await self.finish()


class DeviceTransfers:
    def __init__(self, bridge):
        self.bridge = bridge

    def key(self, identifier):
        return self.bridge._key('transfer', identifier)

    @staticmethod
    def owner(user_id):
        if not re.fullmatch(r'[1-9][0-9]{0,18}', user_id) or int(user_id) > 2**63 - 1:
            raise HTTPException(422, 'Invalid user_id')
        return user_id

    async def create(self, device_id: str, body: TransferCreate,
                     user_id: str = Query(..., min_length=1, max_length=19)):
        owner = self.owner(user_id)
        await self.bridge._owned(device_id, owner)
        identifier, token = str(uuid.uuid4()), secrets.token_urlsafe(48)
        result = await self.bridge._eval(_CREATE, [self.key(identifier), self.bridge._key('device', device_id)],
                                         owner, device_id, body.direction,
                                         hashlib.sha256(token.encode()).hexdigest(),
                                         '' if body.size is None else body.size, body.sha256 or '')
        if not result:
            raise HTTPException(409, 'Device is revoked or unavailable')
        return {'transfer_id': identifier, 'transfer_token': token, 'max_bytes': MAX_BYTES}

    async def keys(self, identifier):
        if not self.bridge._valid_id(identifier):
            raise HTTPException(404, 'Transfer not found')
        device = await self.bridge._call('hget', self.key(identifier), 'device')
        if not device or not self.bridge._valid_id(self.bridge._text(device)):
            raise HTTPException(404, 'Transfer not found')
        return [self.key(identifier), self.bridge._key('device', self.bridge._text(device))]

    @staticmethod
    def token(request):
        headers = request.headers.getlist('authorization')
        parts = headers[0].split(' ') if len(headers) == 1 else []
        if (len(parts) != 2 or parts[0].lower() != 'bearer'
                or not re.fullmatch(r'[A-Za-z0-9_-]{64}', parts[1])):
            raise HTTPException(401, 'Invalid transfer credential')
        return hashlib.sha256(parts[1].encode()).hexdigest()

    @staticmethod
    def checked(result):
        code = int(result[0])
        if code == 0:
            raise HTTPException(404, 'Transfer not found')
        if code == 2:
            raise HTTPException(409, 'Transfer direction or state conflict')
        if code == 3:
            raise HTTPException(422, 'Transfer size or sha256 mismatch')
        return result

    async def validate_command(self, identifier, token, device_id, owner, direction):
        keys = await self.keys(identifier)
        if keys[1] != self.bridge._key('device', device_id):
            raise HTTPException(404, 'Transfer not found')
        self.checked(await self.bridge._eval(_ACTION, keys, 'service', owner, 'check', direction, ''))
        self.checked(await self.bridge._eval(_ACTION, keys, 'device', hashlib.sha256(token.encode()).hexdigest(),
                                             'check', direction, ''))

    async def finish(self, keys, claim, state):
        try:
            await self.bridge._eval(_FINISH, keys[:1], claim, state)
        except HTTPException:
            pass  # Already reserved, cannot be retried; TTL is the safety net.

    async def put(self, identifier, request, role, credential, direction):
        length = request.headers.get('content-length')
        if length is not None and (not length.isdigit() or int(length) > MAX_BYTES):
            raise HTTPException(413, 'Transfer exceeds 10 MiB')
        keys, claim = await self.keys(identifier), secrets.token_hex(16)
        self.checked(await self.bridge._eval(_ACTION, keys, role, credential, 'put', direction, claim))
        async def collect():
            data = bytearray()
            async for chunk in request.stream():
                if len(data) + len(chunk) > MAX_BYTES:
                    raise HTTPException(413, 'Transfer exceeds 10 MiB')
                data.extend(chunk)
            return data
        try:
            try:
                data = await asyncio.wait_for(collect(), 300)
            except asyncio.TimeoutError:
                raise HTTPException(408, 'Transfer body timed out') from None
            digest = hashlib.sha256(data).hexdigest()
            self.checked(await self.bridge._eval(_COMMIT, keys, role, credential, claim, len(data), digest,
                                                 base64.b64encode(data).decode('ascii')))
            return {'size': len(data), 'sha256': digest}
        except BaseException:
            await self.finish(keys, claim, 'failed')
            raise

    async def get(self, identifier, role, credential, direction):
        keys, claim = await self.keys(identifier), secrets.token_hex(16)
        result = self.checked(await self.bridge._eval(_ACTION, keys, role, credential, 'get', direction, claim))
        try:
            body = base64.b64decode(result[1], validate=True)
            digest = self.bridge._text(result[3])
            if (len(body) > MAX_BYTES or len(body) != int(result[2])
                    or hashlib.sha256(body).hexdigest() != digest):
                raise ValueError
        except Exception:
            await self.finish(keys, claim, 'failed')
            raise HTTPException(503, 'Transfer data unavailable') from None
        return _OneShotResponse(body, lambda: self.finish(keys, claim, 'done'),
                                media_type='application/octet-stream',
                                headers={'X-Content-SHA256': digest, 'Cache-Control': 'no-store',
                                         'X-Content-Type-Options': 'nosniff'})

    async def internal_put(self, transfer_id: str, request: Request,
                           user_id: str = Query(..., min_length=1, max_length=19)):
        return await self.put(transfer_id, request, 'service', self.owner(user_id), 'to_device')

    async def internal_get(self, transfer_id: str,
                           user_id: str = Query(..., min_length=1, max_length=19)):
        return await self.get(transfer_id, 'service', self.owner(user_id), 'from_device')

    async def internal_delete(self, transfer_id: str,
                              user_id: str = Query(..., min_length=1, max_length=19)):
        keys = await self.keys(transfer_id)
        self.checked(await self.bridge._eval(_ACTION, keys, 'service', self.owner(user_id), 'delete', '', ''))
        return {'deleted': True}

    async def public_get(self, transfer_id: str, request: Request):
        return await self.get(transfer_id, 'device', self.token(request), 'to_device')

    async def public_put(self, transfer_id: str, request: Request):
        return await self.put(transfer_id, request, 'device', self.token(request), 'from_device')
