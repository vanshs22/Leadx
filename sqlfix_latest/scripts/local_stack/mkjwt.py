"""Mint a HS256 JWT for the LOCAL test stack:  mkjwt.py <secret> key=value ...  (values with '=' split once)."""
import sys, time
from jose import jwt
secret, claims = sys.argv[1], {}
for kv in sys.argv[2:]:
    k, v = kv.split("=", 1)
    claims[k] = {"true": True, "false": False}.get(v, v)
claims.setdefault("aud", "authenticated"); claims.setdefault("iat", int(time.time())); claims.setdefault("exp", int(time.time()) + 86400)
print(jwt.encode(claims, secret, algorithm="HS256"))
