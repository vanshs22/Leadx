import type { SVGProps } from "react";

export type IconName =
  | "home" | "clock" | "brain" | "spark" | "check" | "search" | "leads"
  | "pipeline" | "sequence" | "automation" | "compose" | "reply" | "activity"
  | "report" | "revenue" | "deal" | "team" | "integration" | "billing" | "settings"
  | "clients" | "health" | "territory" | "icp" | "growth" | "run" | "job"
  | "bell" | "support" | "key" | "usage" | "suppression" | "scale" | "security"
  | "menu" | "arrow";

const PATHS: Record<IconName, string> = {
  home: "M3 10.5 12 3l9 7.5M5.5 9.5V21h13V9.5M9 21v-6h6v6",
  clock: "M12 7v5l3 2M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0",
  brain: "M9 4a3 3 0 0 0-3 3v1a3 3 0 0 0-2 3 3 3 0 0 0 2 3v2a3 3 0 0 0 3 3h1V4H9Zm6 0a3 3 0 0 1 3 3v1a3 3 0 0 1 2 3 3 3 0 0 1-2 3v2a3 3 0 0 1-3 3h-1V4h1Z",
  spark: "m12 3 1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8L12 3Zm7 13 .7 2.3L22 19l-2.3.7L19 22l-.7-2.3L16 19l2.3-.7L19 16Z",
  check: "m5 12 4 4L19 6",
  search: "m20 20-4.3-4.3M10.8 18a7.2 7.2 0 1 1 0-14.4 7.2 7.2 0 0 1 0 14.4Z",
  leads: "M5 5h14v14H5zM8 9h8M8 12h8M8 15h5",
  pipeline: "M5 5h5v5H5zM14 14h5v5h-5zM10 7.5h4M16.5 10v4M8 10v5h6",
  sequence: "M5 6h14M5 12h10M5 18h7M17 15l3 3-3 3",
  automation: "m13 2-9 12h7l-1 8 9-12h-7l1-8Z",
  compose: "m4 20 4.2-1 10.4-10.4a2.1 2.1 0 0 0-3-3L5.2 16 4 20Zm10.5-12.5 3 3",
  reply: "M20 11a7 7 0 0 0-7-7H9a7 7 0 0 0 0 14h2M4 18v3l3-3M16 17h5m-2.5-2.5V20",
  activity: "M4 12h4l2-7 4 14 2-7h4",
  report: "M5 20V10M12 20V4M19 20v-7",
  revenue: "M12 3v18M17 7.5C17 6.1 15 5 12.5 5S8 6.1 8 7.7c0 4.1 9 1.8 9 6.2 0 1.7-2 3.1-4.8 3.1S7 15.8 7 14",
  deal: "M4 6h16v13H4zM8 6V4h8v2M4 10h16M10 13h4",
  team: "M16 20v-1a4 4 0 0 0-4-4H7a4 4 0 0 0-4 4v1M9.5 11a4 4 0 1 0 0-8 4 4 0 0 0 0 8Zm6-7a4 4 0 0 1 0 7.7",
  integration: "M8 12h8M12 8v8M5 5h14v14H5z",
  billing: "M4 7h16v12H4zM4 10h16M8 15h3",
  settings: "M12 8a4 4 0 1 0 0 8 4 4 0 0 0 0-8Zm0-5v2m0 14v2m9-9h-2M5 12H3m15.4-6.4-1.4 1.4M7 17l-1.4 1.4m12.8 0L17 17M7 7 5.6 5.6",
  clients: "M4 5h16v14H4zM8 9h8M8 13h5",
  health: "M4 12h3l2-5 4 10 2-5h5",
  territory: "M12 21s7-5.2 7-11a7 7 0 1 0-14 0c0 5.8 7 11 7 11Zm0-8a3 3 0 1 0 0-6 3 3 0 0 0 0 6Z",
  icp: "M4 5h16M4 12h10M4 19h7M18 10v8m-3-3 3 3 3-3",
  growth: "M4 18 9 13l3 3 8-9M16 7h4v4",
  run: "M5 4v16l13-8L5 4Z",
  job: "M6 4h12v16H6zM9 8h6M9 12h6M9 16h4",
  bell: "M18 8a6 6 0 0 0-12 0c0 7-3 7-3 9h18c0-2-3-2-3-9M10 21h4",
  support: "M4 5h16v12H8l-4 4V5Zm4 5h8M8 13h5",
  key: "m14 10 7-7M17 7l2 2M13 11a5 5 0 1 1-7 7 5 5 0 0 1 7-7Z",
  usage: "M5 20V9M12 20V4M19 20v-7",
  suppression: "M6 6l12 12M5 5h14v14H5z",
  scale: "M12 3v18M5 7h14M7 7l-3 6a3 3 0 0 0 6 0L7 7Zm10 0-3 6a3 3 0 0 0 6 0l-3-6Z",
  security: "M12 3 20 6v6c0 5-3.5 8-8 9-4.5-1-8-4-8-9V6l8-3Zm-3 9 2 2 4-4",
  menu: "M4 7h16M4 12h16M4 17h16",
  arrow: "M5 12h13m-5-5 5 5-5 5",
};

export function Icon({ name, size = 18, strokeWidth = 1.8, ...props }: { name: IconName; size?: number; strokeWidth?: number } & SVGProps<SVGSVGElement>) {
  return <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={strokeWidth} strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" {...props}><path d={PATHS[name]} /></svg>;
}
