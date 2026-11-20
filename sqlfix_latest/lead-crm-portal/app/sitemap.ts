import type { MetadataRoute } from "next";

export default function sitemap(): MetadataRoute.Sitemap {
  const base = (process.env.NEXT_PUBLIC_APP_URL || "http://localhost:3000").replace(/\/$/, "");
  return ["", "/pricing", "/sample", "/contact", "/privacy", "/terms", "/dpa", "/security", "/cookies", "/acceptable-use", "/subprocessors", "/login", "/signup"]
    .map((p) => ({ url: `${base}${p}`, changeFrequency: "monthly" as const, priority: p === "" ? 1 : 0.5 }));
}
