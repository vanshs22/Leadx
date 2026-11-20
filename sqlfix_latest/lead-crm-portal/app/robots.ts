import type { MetadataRoute } from "next";

export default function robots(): MetadataRoute.Robots {
  const base = (process.env.NEXT_PUBLIC_APP_URL || "").replace(/\/$/, "");
  return {
    rules: [{ userAgent: "*", allow: ["/", "/pricing", "/privacy", "/terms", "/security", "/sample"], disallow: ["/admin", "/api", "/backend", "/dashboard", "/leads", "/audit/"] }],
    ...(base ? { sitemap: `${base}/sitemap.xml` } : {}),
  };
}
