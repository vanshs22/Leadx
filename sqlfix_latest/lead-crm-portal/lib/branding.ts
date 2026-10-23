/**
 * White-label branding — load via CRM API (tenant-scoped).
 * NEVER uses operator service key in the browser.
 */
import { useEffect, useState } from "react";
import { getApiBases } from "./config";


export type Branding = {
  id: string;
  name: string;
  brand_name?: string;
  brand_logo_url?: string;
  brand_primary_color?: string;
  brand_accent_color?: string;
  brand_favicon_url?: string;
  custom_domain?: string;
  support_email?: string;
};

function getBrandingBases(): string[] {
  return getApiBases();
}

function toRgb(value: string): string | null {
  const v = value.trim();
  const m = v.match(/^#([0-9a-f]{6})$/i);
  if (!m) return null;
  const n = parseInt(m[1], 16);
  return `${(n >> 16) & 255} ${(n >> 8) & 255} ${n & 255}`;
}

export async function fetchBranding(tenantId: string): Promise<Branding | null> {
  try {
    for (const base of getBrandingBases()) {
      try {
        const res = await fetch(`${base}/crm/${tenantId}/branding`, {
          credentials: "include", cache: "no-store",
        });
        if (res.ok) return res.json();
        if (![404, 502, 503].includes(res.status)) return null;
      } catch {
        // Try the next API base (e.g. Codespaces forwarded port).
      }
    }
    return null;
  } catch {
    return null;
  }
}

export function applyBranding(b: Branding) {
  if (typeof document === "undefined") return;
  const root = document.documentElement;
  if (b.brand_primary_color) {
    root.style.setProperty("--brand-primary", b.brand_primary_color);
    const rgb = toRgb(b.brand_primary_color);
    if (rgb) root.style.setProperty("--brand", rgb);
  }
  if (b.brand_accent_color) {
    root.style.setProperty("--brand-accent", b.brand_accent_color);
    const rgb = toRgb(b.brand_accent_color);
    if (rgb) root.style.setProperty("--accent", rgb);
  }
  if (b.brand_favicon_url) {
    let link = document.querySelector("link[rel*='icon']") as HTMLLinkElement | null;
    if (!link) {
      link = document.createElement("link");
      link.rel = "icon";
      document.head.appendChild(link);
    }
    link.href = b.brand_favicon_url;
  }
  if (b.brand_name || b.name) {
    document.title = `${b.brand_name || b.name} — Leads`;
  }
}

export function useBranding(tenantId: string) {
  const [brand, setBrand] = useState<Branding | null>(null);
  useEffect(() => {
    if (!tenantId || tenantId === "YOUR_TENANT_UUID") return;
    fetchBranding(tenantId).then((b) => {
      if (b) {
        setBrand(b);
        applyBranding(b);
      }
    });
  }, [tenantId]);
  return brand;
}
