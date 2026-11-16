import { getServerApiUpstream } from "@/lib/config";
import { createAuthProxyRequest, createAuthProxyResponse } from "@/lib/authProxy";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

export async function POST(request: Request): Promise<Response> {
  try {
    const upstream = await fetch(
      `${getServerApiUpstream()}/auth/session/refresh`,
      createAuthProxyRequest(request),
    );
    return createAuthProxyResponse(upstream);
  } catch {
    return new Response(null, {
      status: 502,
      headers: { "cache-control": "no-store" },
    });
  }
}
