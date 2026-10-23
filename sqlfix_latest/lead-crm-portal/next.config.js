/** @type {import('next').NextConfig} */
const fs = require("fs");
const path = require("path");

/** Load KEY=VAL from a file into process.env (does not override existing). */
function loadEnvFile(filePath) {
  try {
    if (!fs.existsSync(filePath)) return;
    const text = fs.readFileSync(filePath, "utf8");
    for (const raw of text.split(/\r?\n/)) {
      const line = raw.trim();
      if (!line || line.startsWith("#")) continue;
      const eq = line.indexOf("=");
      if (eq <= 0) continue;
      const key = line.slice(0, eq).trim();
      let val = line.slice(eq + 1).trim();
      if (
        (val.startsWith('"') && val.endsWith('"')) ||
        (val.startsWith("'") && val.endsWith("'"))
      ) {
        val = val.slice(1, -1);
      }
      if (key && process.env[key] === undefined) {
        process.env[key] = val;
      }
    }
  } catch (e) {
    console.warn("[next.config] loadEnvFile failed:", filePath, e.message);
  }
}

// Load frontend env first. If the frontend public Supabase variables are
// absent, read ONLY those two public values from the backend .env file. Do not
// load the backend service-role/Firecrawl/etc secrets into the Next build.
loadEnvFile(path.join(__dirname, ".env.local"));
loadEnvFile(path.join(__dirname, ".env"));

function readEnvValue(filePath, wanted) {
  try {
    if (!fs.existsSync(filePath)) return;
    const lines = fs.readFileSync(filePath, "utf8").split(/\r?\n/);
    for (const raw of lines) {
      const line = raw.trim();
      if (!line || line.startsWith("#")) continue;
      const eq = line.indexOf("=");
      if (eq <= 0) continue;
      const key = line.slice(0, eq).trim();
      if (key !== wanted) continue;
      let val = line.slice(eq + 1).trim();
      if ((val.startsWith('"') && val.endsWith('"')) || (val.startsWith("'") && val.endsWith("'"))) val = val.slice(1, -1);
      return val;
    }
  } catch {}
  return undefined;
}

// Backend secrets/env are never loaded by Next. Public Supabase values must be
// supplied through the frontend .env.local/.env or deployment environment.

const SUPA_URL = (process.env.NEXT_PUBLIC_SUPABASE_URL || "").trim();
const SUPA_ANON = (process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY || "").trim();

const LOOPBACK_HOSTS = new Set(["localhost", "127.0.0.1", "::1"]);

function isHardenedPortalProfile() {
  const profile = String(
    process.env.LEADX_PORTAL_PROFILE ||
      process.env.PORTAL_PROFILE ||
      process.env.ENVIRONMENT ||
      ""
  ).toLowerCase();
  return process.env.NODE_ENV === "production" ||
    ["production", "staging", "container", "hardened"].includes(profile);
}

function resolveApiUpstream() {
  const hardened = isHardenedPortalProfile();
  const configured = String(process.env.API_UPSTREAM || "").trim();
  const upstream = configured || (hardened ? "http://api:8000" : "http://127.0.0.1:8000");

  let parsed;
  try {
    parsed = new URL(upstream);
  } catch {
    throw new Error("API_UPSTREAM must be an absolute HTTP(S) URL");
  }
  if (!/^https?:$/.test(parsed.protocol) || parsed.username || parsed.password || parsed.hash) {
    throw new Error("API_UPSTREAM must be an absolute HTTP(S) URL without credentials");
  }
  const hostname = parsed.hostname.replace(/^\[|\]$/g, "").toLowerCase();
  if (hardened && LOOPBACK_HOSTS.has(hostname)) {
    throw new Error("Loopback API_UPSTREAM is not allowed in the portal container profile");
  }
  return upstream.replace(/\/$/, "");
}

const API_UPSTREAM = resolveApiUpstream();
console.log(
  "[next.config] Supabase URL for client:",
  SUPA_URL ? SUPA_URL.slice(0, 40) + "…" : "(EMPTY — client /login will fail)"
);

const nextConfig = {
  reactStrictMode: true,
  poweredByHeader: false,
  compress: true,
  experimental: { optimizePackageImports: ["recharts", "@supabase/supabase-js"] },
  // Force-embed only public Supabase values into the browser bundle.
  env: {
    NEXT_PUBLIC_SUPABASE_URL: SUPA_URL,
    NEXT_PUBLIC_SUPABASE_ANON_KEY: SUPA_ANON,
  },
  async headers() {
    return [
      {
        source: "/(.*)",
        headers: [
          { key: "X-Content-Type-Options", value: "nosniff" },
          { key: "X-Frame-Options", value: "DENY" },
          { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
          { key: "Permissions-Policy", value: "camera=(), microphone=(), geolocation=()" },
        ],
      },
    ];
  },
  async rewrites() {
    return [
      {
        source: "/backend/:path*",
        destination: `${API_UPSTREAM.replace(/\/$/, "")}/:path*`,
      },
    ];
  },
};

module.exports = nextConfig;
