"""gw_shared/secrets_gw: user_id только из сессии, ключ не логируется, ответ no-store."""
import logging
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from gw_shared import secrets_gw  # noqa: E402

KEY = "sk-" + "k3Y" * 10


@pytest.fixture
def env():
    upstream = MagicMock()
    upstream.post = AsyncMock(return_value=httpx.Response(200, json={"last4": KEY[-4:]}))
    user = {"user_id": 5}
    secrets_gw.bind(get_client=lambda: upstream, auth_url="http://auth/",
                    get_user=AsyncMock(side_effect=lambda r: user["value"] if "value" in user else {"user_id": 5}))
    app = FastAPI(); app.include_router(secrets_gw.router)
    return TestClient(app), upstream, user


def test_put_uses_session_user(env, caplog):
    c, up, _ = env
    caplog.set_level(logging.DEBUG)
    r = c.put("/api/profile/secrets", json={"provider": "routerai", "secret": KEY, "user_id": 999})
    assert r.status_code == 200 and r.headers["cache-control"] == "no-store"
    url, kw = up.post.await_args.args[0], up.post.await_args.kwargs
    assert url == "http://auth/profile/secrets/set"
    assert kw["json"] == {"user_id": 5, "provider": "routerai", "name": "default", "secret": KEY}
    assert KEY not in caplog.text


def test_unauthenticated(env):
    c, up, user = env
    user["value"] = None
    assert c.get("/api/profile/secrets").status_code == 401
    up.post.assert_not_called()


@pytest.mark.parametrize("body", [{"provider": "routerai"}, {"provider": "routerai", "secret": "  "},
                                  {"provider": "routerai", "secret": 123}])
def test_empty_secret(env, body):
    c, up, _ = env
    assert c.put("/api/profile/secrets", json=body).status_code == 400
    up.post.assert_not_called()


def test_body_limit(env):
    c, _, _ = env
    r = c.put("/api/profile/secrets", content=b'{"secret":"' + b"a" * 5000 + b'"}',
              headers={"content-type": "application/json"})
    assert r.status_code == 413


def test_delete_and_list(env):
    c, up, _ = env
    c.request("DELETE", "/api/profile/secrets", json={"provider": "routerai"})
    assert up.post.await_args.kwargs["json"] == {"user_id": 5, "provider": "routerai", "name": "default"}
    c.get("/api/profile/secrets")
    assert up.post.await_args.args[0].endswith("/list")


def test_upstream_down(env):
    c, up, _ = env
    up.post.side_effect = httpx.ConnectError("x")
    assert c.get("/api/profile/secrets").status_code == 502
