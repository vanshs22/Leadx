import { NextResponse } from "next/server";
import { createPortalRequestId, getServerApiUpstream } from "@/lib/config";

export const dynamic = "force-dynamic";

function headers(requestId: string): HeadersInit {
  return {
    "Cache-Control": "no-store",
    "X-Request-ID": requestId,
  };
}

async function probe(url: string): Promise<{ ok: boolean; status: number }> {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 2_000);
  try {
    const response = await fetch(url, {
      cache: "no-store",
      signal: controller.signal,
    });
    return { ok: response.ok, status: response.status };
  } catch {
    return { ok: false, status: 0 };
  } finally {
    clearTimeout(timeout);
  }
}

/**
 * Portal readiness proves the running production bundle, the local root route,
 * and the server-side API service-DNS/readiness path. It never returns the
 * upstream URL or response body, so internal names and backend diagnostics do
 * not become health-response secrets.
 */
export async function GET(request: Request) {
  const requestId = createPortalRequestId(request.headers.get("x-request-id"));
  try {
    const upstream = getServerApiUpstream();
    const root = await probe(new URL("/", request.url).toString());
    const api = await probe(`${upstream}/health/ready`);
    const components = {
      process: { ok: true, status: "ready" },
      bundle: {
        ok: root.ok,
        status: root.ok ? "ready" : "unavailable",
        reason: root.ok ? "ok" : "bundle_unavailable",
      },
      upstream: {
        ok: api.ok,
        status: api.ok ? "ready" : "unavailable",
        reason: api.ok ? "ok" : api.status ? "upstream_unready" : "upstream_unavailable",
      },
    };
    const ready = Object.values(components).every((component) => component.ok);
    return NextResponse.json(
      {
        ok: ready,
        service: "portal",
        status: ready ? "ready" : "unready",
        request_id: requestId,
        components,
      },
      { status: ready ? 200 : 503, headers: headers(requestId) }
    );
  } catch {
    // Do not echo API_UPSTREAM or configuration exceptions: the value can
    // contain internal hostnames and must never become a health-response leak.
    return NextResponse.json(
      {
        ok: false,
        service: "portal",
        status: "unready",
        reason_code: "invalid_upstream_configuration",
        request_id: requestId,
        components: {
          process: { ok: true, status: "ready" },
          bundle: { ok: true, status: "ready" },
          upstream: { ok: false, status: "unavailable", reason: "invalid_configuration" },
        },
      },
      { status: 503, headers: headers(requestId) }
    );
  }
}
