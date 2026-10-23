import type { Metadata } from "next";
import "./globals.css";
import { ThemeProvider } from "@/components/theme/ThemeProvider";

export const dynamic = "force-dynamic";

export const metadata: Metadata = {
  title: "LeadX — Revenue Intelligence",
  description: "High-signal lead intelligence and revenue workspace",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" suppressHydrationWarning>
      <body
        className="font-sans antialiased"
        style={{
          "--font-sans": 'Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif',
          "--font-display": '"Space Grotesk", Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif',
          "--font-mono": '"IBM Plex Mono", ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace',
        } as React.CSSProperties}
      >
        <ThemeProvider>{children}</ThemeProvider>
      </body>
    </html>
  );
}
