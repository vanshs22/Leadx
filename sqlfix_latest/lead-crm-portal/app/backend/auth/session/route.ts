import { getServerApiUpstream } from "@/lib/config";
import { createAuthProxyRequest, createAuthProxyResponse } from "@/lib/authProxy";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

async function proxySession(request: Request, path: string): Promise<Response> {
  try {
    const upstream = await fetch(`${getServerApiUpstream()}${path}`, createAuthProxyRequest(request));
    return createAuthProxyResponse(upstream);
  } catch {
    return new Response(null, {
      status: 502,
      headers: { "cache-control": "no-store" },
    });
  }
}

export function GET(request: Request) {
  return proxySession(request, "/auth/session");
}

export function POST(request: Request) {
  return proxySession(request, "/auth/session");
}

export function DELETE(request: Request) {
  return proxySession(request, "/auth/session");
}
