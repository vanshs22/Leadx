import { NextResponse } from "next/server";
import { createPortalRequestId, getServerApiUpstream } from "@/lib/config";

export const dynamic = "force-dynamic";

function headers(requestId: string): HeadersInit {
  return {
    "Cache-Control": "no-store",
    "X-Request-ID": requestId,
  };
}

/**
 * Portal-process readiness, deliberately separate from API readiness.
 * This checks the server-side rewrite contract without calling the API or
 * bypassing its browser authentication boundary.
 */
export async function GET(request: Request) {
  const requestId = createPortalRequestId(request.headers.get("x-request-id"));
  try {
    const upstream = getServerApiUpstream();
    const protocol = new URL(upstream).protocol.replace(":", "");
    return NextResponse.json(
      {
        ok: true,
        service: "portal",
        status: "ready",
        request_id: requestId,
        components: {
          process: { ok: true },
          upstream: { ok: true, configured: true, protocol },
        },
      },
      { status: 200, headers: headers(requestId) }
    );
  } catch {
    // Do not echo the URL or configuration exception: API_UPSTREAM can contain
    // internal hostnames and must never become a health-response secret leak.
    return NextResponse.json(
      {
        ok: false,
        service: "portal",
        status: "unready",
        reason_code: "invalid_upstream_configuration",
        request_id: requestId,
        components: {
          process: { ok: true },
          upstream: { ok: false, configured: false },
        },
      },
      { status: 503, headers: headers(requestId) }
    );
  }
}
