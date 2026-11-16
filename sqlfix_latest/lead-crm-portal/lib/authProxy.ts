const HOP_BY_HOP_HEADERS = new Set([
  "connection",
  "content-length",
  "host",
  "keep-alive",
  "proxy-authenticate",
  "proxy-authorization",
  "te",
  "trailer",
  "transfer-encoding",
  "upgrade",
]);

type HeadersWithSetCookie = Headers & {
  getSetCookie?: () => string[];
};

/** Preserve multiple Set-Cookie values when the runtime does not expose getSetCookie(). */
export function splitSetCookieHeader(value: string): string[] {
  return value.split(/,\s*(?=[^;,=\s]+=[^;,]*)/).map((part) => part.trim()).filter(Boolean);
}

export function getSetCookieValues(headers: Headers): string[] {
  const withSetCookie = headers as HeadersWithSetCookie;
  if (typeof withSetCookie.getSetCookie === "function") {
    return withSetCookie.getSetCookie();
  }
  const combined = headers.get("set-cookie");
  return combined ? splitSetCookieHeader(combined) : [];
}

/** Forward browser cookies and CSRF/auth headers without forwarding hop-by-hop transport headers. */
export function createUpstreamRequestHeaders(request: Request): Headers {
  const headers = new Headers();
  request.headers.forEach((value, key) => {
    if (!HOP_BY_HOP_HEADERS.has(key.toLowerCase())) headers.set(key, value);
  });
  return headers;
}

/** Copy the upstream response unchanged, including each authentication cookie. */
export function createAuthProxyResponse(upstream: Response): Response {
  const headers = new Headers();
  upstream.headers.forEach((value, key) => {
    const normalized = key.toLowerCase();
    if (normalized !== "set-cookie" && !HOP_BY_HOP_HEADERS.has(normalized)) {
      headers.set(key, value);
    }
  });
  for (const cookie of getSetCookieValues(upstream.headers)) {
    headers.append("set-cookie", cookie);
  }
  return new Response(upstream.body, {
    status: upstream.status,
    statusText: upstream.statusText,
    headers,
  });
}

export function createAuthProxyRequest(request: Request): RequestInit & { duplex?: "half" } {
  const init: RequestInit & { duplex?: "half" } = {
    method: request.method,
    headers: createUpstreamRequestHeaders(request),
    cache: "no-store",
    redirect: "manual",
  };
  if (request.body) {
    init.body = request.body;
    init.duplex = "half";
  }
  return init;
}
