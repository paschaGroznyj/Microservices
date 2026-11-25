import os
os.environ['OPENBLAS_NUM_THREADS'] = '1'

import argparse
import asyncio
import contextlib
from pathlib import Path
import socket
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


def worker(redis_url, fd):
    from fastapi import FastAPI
    from redis.asyncio import Redis
    import uvicorn
    from gw_shared.device_bridge import create_router

    store = Redis.from_url(redis_url, decode_responses=True,
                           socket_connect_timeout=0.3, socket_timeout=0.5)

    async def current_user(_request):
        return {'authenticated': True, 'user_id': 56}

    router = create_router(current_user, store)

    @contextlib.asynccontextmanager
    async def lifespan(_app):
        try:
            yield
        finally:
            await router.device_bridge_service.close()
            await store.aclose()

    app = FastAPI(lifespan=lifespan)
    app.include_router(router)
    app.include_router(router.internal_router)

    @app.get('/_smoke/ready')
    async def ready():
        return {'ready': bool(await store.ping()), 'pid': os.getpid()}

    config = uvicorn.Config(app, host='127.0.0.1', log_level='critical',
                            access_log=False, ws='websockets', ws_max_size=512 * 1024,
                            timeout_graceful_shutdown=1, lifespan='on')
    with socket.socket(fileno=fd) as listener:
        asyncio.run(uvicorn.Server(config).serve(sockets=[listener]))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--redis-url', required=True)
    parser.add_argument('--fd', type=int, required=True)
    args = parser.parse_args()
    worker(args.redis_url, args.fd)
