"""Offline stand-ins for third-party packages that cannot be installed in the sandbox.
httpx: every request fails fast with ConnectError (== no network). Others: permissive modules."""
import sys, types, importlib.abc, importlib.machinery

class _Any:
    def __init__(self, *a, **k): self.__dict__.update(k)
    def __call__(self, *a, **k):
        if len(a) == 1 and callable(a[0]) and not k: return a[0]
        return _Any()
    def __getattr__(self, n): return _Any()
    def __iter__(self): return iter(())
    def __bool__(self): return True
    def __enter__(self): return self
    def __exit__(self, *a): return False
    def __await__(self):
        async def _c(): return self
        return _c().__await__()

class _Router(_Any):
    def _dec(self, *a, **k): return lambda f: f
    get = post = put = patch = delete = websocket = on_event = middleware = exception_handler = api_route = _dec
    def include_router(self, *a, **k): pass

def _mod(name, **attrs):
    m = types.ModuleType(name); m.__dict__.update(attrs); m.__path__ = []; sys.modules[name] = m; return m

class HTTPException(Exception):
    def __init__(self, status_code=500, detail=None, headers=None): super().__init__(detail); self.status_code, self.detail = status_code, detail

class BaseModel:
    def __init__(self, **kw):
        for k in getattr(type(self), "__annotations__", {}):
            setattr(self, k, kw.get(k, getattr(type(self), k, None)))
        for k, v in kw.items(): setattr(self, k, v)
    def model_dump(self, **k): return dict(self.__dict__)
    dict = model_dump
def Field(default=None, **k): return k.get("default_factory", lambda: default)() if "default_factory" in k else default

class ConnectError(Exception): pass
class HTTPError(Exception): pass
class TimeoutException(Exception): pass
class _Resp:
    status_code = 599; text = ""; content = b""; headers = {}
    def json(self): return {}
    def raise_for_status(self): raise HTTPError("offline")
class AsyncClient:
    def __init__(self, *a, **k): pass
    async def __aenter__(self): return self
    async def __aexit__(self, *a): return False
    async def aclose(self): pass
    async def _go(self, *a, **k): raise ConnectError("offline sandbox")
    get = post = put = patch = delete = head = request = send = _go
class Client(AsyncClient):
    def __enter__(self): return self
    def __exit__(self, *a): return False
    def _go(self, *a, **k): raise ConnectError("offline sandbox")
    get = post = put = patch = delete = head = request = _go

class _Finder(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    ROOTS = {"fastapi", "starlette", "pydantic", "httpx", "supabase", "anyio", "jose", "anthropic", "uvicorn"}
    def find_spec(self, name, path, target=None):
        if name.split(".")[0] in self.ROOTS: return importlib.machinery.ModuleSpec(name, self, is_package=True)
    def create_module(self, spec):
        m = types.ModuleType(spec.name); m.__path__ = []
        root = spec.name.split(".")[0]
        class _G(dict): pass
        def __getattr__(n):
            if n.startswith("__"): raise AttributeError(n)
            if root == "fastapi" and n in ("APIRouter", "FastAPI"): return _Router
            if n == "HTTPException": return HTTPException
            if root == "pydantic" and n == "BaseModel": return BaseModel
            if root == "pydantic" and n == "Field": return Field
            if root == "httpx":
                return {"AsyncClient": AsyncClient, "Client": Client, "ConnectError": ConnectError, "HTTPError": HTTPError,
                        "TimeoutException": TimeoutException, "HTTPStatusError": HTTPError, "RequestError": HTTPError,
                        "ConnectTimeout": TimeoutException, "ReadTimeout": TimeoutException}.get(n, _Any)
            if n[:1].isupper() and n.endswith(("Error", "Exception")): return type(n, (Exception,), {})
            return _Any
        m.__getattr__ = __getattr__; return m
    def exec_module(self, m): pass

def install():
    if not any(isinstance(f, _Finder) for f in sys.meta_path): sys.meta_path.append(_Finder())
