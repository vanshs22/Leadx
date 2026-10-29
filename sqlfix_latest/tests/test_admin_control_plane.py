import asyncio
from types import SimpleNamespace
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials

import app.middleware.platform_auth as platform_auth
import app.middleware.service_auth as service_auth


class _Req:
    def __init__(self, path, cookies=None):
        self.url = SimpleNamespace(path=path)
        self.cookies = cookies or {}
        self.state = SimpleNamespace()


def test_service_key_is_rejected_on_admin_routes(monkeypatch):
    async def fake_platform(request, creds):
        if creds and creds.credentials == "admin-jwt":
            return SimpleNamespace(user_id="u1", role="admin")
        raise HTTPException(401, "bad jwt")

    async def fake_service(request, creds):
        if creds and creds.credentials == "service-key":
            return None
        raise HTTPException(401, "bad service")

    monkeypatch.setattr(platform_auth, "require_platform_admin", fake_platform)
    monkeypatch.setattr(service_auth, "require_service_key", fake_service)

    async def run():
        await platform_auth.require_control_plane(
            _Req("/leads/v2/admin/jobs"),
            HTTPAuthorizationCredentials(scheme="Bearer", credentials="admin-jwt"),
        )
        try:
            await platform_auth.require_control_plane(
                _Req("/leads/v2/admin/jobs"),
                HTTPAuthorizationCredentials(scheme="Bearer", credentials="service-key"),
            )
        except HTTPException as exc:
            assert exc.status_code == 401
        else:
            raise AssertionError("service key must not authorize browser admin routes")

    asyncio.run(run())


def test_session_cookie_authorizes_non_admin_control_plane_route(monkeypatch):
    async def fake_platform(request, creds):
        assert creds is None
        assert request.cookies.get("leadx_session") == "admin-cookie"
        return SimpleNamespace(user_id="u1", role="admin")

    async def fail_service(request, creds):
        raise AssertionError("session-authenticated browser call used service auth")

    monkeypatch.setattr(platform_auth, "require_platform_admin", fake_platform)
    monkeypatch.setattr(service_auth, "require_service_key", fail_service)

    async def run():
        request = _Req("/leads/v2/pipeline/run", {"leadx_session": "admin-cookie"})
        await platform_auth.require_control_plane(request, None)
        assert request.state.platform_admin.user_id == "u1"

    asyncio.run(run())
