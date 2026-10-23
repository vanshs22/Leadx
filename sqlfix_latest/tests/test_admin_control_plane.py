import asyncio
from types import SimpleNamespace
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials

import app.middleware.platform_auth as platform_auth
import app.middleware.service_auth as service_auth


class _Req:
    def __init__(self, path):
        self.url = SimpleNamespace(path=path)
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
