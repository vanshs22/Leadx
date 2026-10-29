import type { Metadata, Viewport } from "next";
// Self-hosted variable fonts: no runtime request to a font CDN, works offline in Docker builds.
import "@fontsource-variable/inter/opsz.css";
import "@fontsource-variable/jetbrains-mono/index.css";
import "./globals.css";
import { ThemeProvider } from "@/components/theme/ThemeProvider";

export const dynamic = "force-dynamic";

export const metadata: Metadata = {
  title: "LeadX — Revenue Intelligence",
  description: "High-signal lead intelligence and revenue workspace",
};

export const viewport: Viewport = {
  themeColor: [
    { media: "(prefers-color-scheme: light)", color: "#F6F6F3" },
    { media: "(prefers-color-scheme: dark)", color: "#0A0F19" },
  ],
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" suppressHydrationWarning>
      <body
        className="font-sans antialiased"
        style={{
          "--font-sans": '"Inter Variable", Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif',
          "--font-display": '"Inter Variable", Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif',
          "--font-mono": '"JetBrains Mono Variable", "JetBrains Mono", ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace',
        } as React.CSSProperties}
      >
        <ThemeProvider>{children}</ThemeProvider>
      </body>
    </html>
  );
}
