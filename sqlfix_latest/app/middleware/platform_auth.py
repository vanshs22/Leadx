"""Platform-admin JWT authentication.

Service-key auth remains available for internal workers/backward compatibility,
but browser-facing admin sessions can use Supabase Auth + platform_admins.
"""
from __future__ import annotations
from app.services.aio import aexec
from dataclasses import dataclass
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from app.middleware.auth import _verify_supabase_jwt, local_authentication_enabled
from app.memory.supabase_client import get_supabase

bearer=HTTPBearer(auto_error=False)
@dataclass
class PlatformAdmin:
    user_id:str
    email:str|None
    role:str

def _sb(): return get_supabase()

async def require_platform_admin(request:Request, creds:HTTPAuthorizationCredentials|None=Depends(bearer))->PlatformAdmin:
    token = creds.credentials if creds and creds.credentials else request.cookies.get("leadx_session")
    if not token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED,'Missing admin session',headers={'WWW-Authenticate':'Bearer'})
    payload=await _verify_supabase_jwt(token)
    uid=payload.get('sub')
    if not uid: raise HTTPException(status.HTTP_401_UNAUTHORIZED,'Invalid admin token')
    if payload.get('iss') == 'leadx-local' and local_authentication_enabled():
        admin = PlatformAdmin(user_id=str(uid), email=payload.get('email'), role='owner')
        request.state.platform_admin = admin
        return admin
    try:
        rows=(await aexec(_sb().table('platform_admins').select('user_id,email,role,active').eq('user_id',uid).eq('active',True).limit(1))).data or []
    except Exception:
        rows=[]
    if not rows:
        from app.integrations.prod.platform_control import security_event
        security_event('platform_admin_denied','warning',actor_id=str(uid),ip=request.client.host if request.client else None)
        raise HTTPException(status.HTTP_403_FORBIDDEN,'Platform admin access required')
    a=rows[0]; request.state.platform_admin=a
    return PlatformAdmin(user_id=str(a['user_id']),email=a.get('email') or payload.get('email'),role=a.get('role') or 'admin')


async def require_control_plane(request: Request, creds: HTTPAuthorizationCredentials|None=Depends(bearer)) -> None:
    """Authorize browser control-plane calls with a platform-admin JWT.

    Service keys remain valid only for non-admin machine endpoints. Any path
    under /admin is deliberately rejected when the credential is a service key.
    """
    path=request.url.path
    admin_path="/admin" in path
    if admin_path:
        # Browser admin calls authenticate through the HttpOnly session cookie;
        # machine callers may still supply a bearer credential.
        admin=await require_platform_admin(request, creds)
        request.state.platform_admin=admin
        return
    if request.cookies.get("leadx_session"):
        admin=await require_platform_admin(request, None)
        request.state.platform_admin=admin
        return
    if creds and creds.credentials:
        try:
            admin=await require_platform_admin(request, creds)
            request.state.platform_admin=admin
            return
        except HTTPException:
            pass
    from app.middleware.service_auth import require_service_key
    await require_service_key(request, creds)
