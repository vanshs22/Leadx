/**
 * Mirrors the brand/signal/rust scales in tailwind.config.js. Recharts
 * renders SVG fill/stroke as literal attributes, which can't resolve
 * CSS custom properties the way Tailwind classes can — so chart color
 * gets its own small constant file instead of trying to read theme
 * variables at runtime. Keep in sync with tailwind.config.js by hand.
 */
export const chartColors = {
  brand: { 300: "#D6AF68", 400: "#C6994F", 500: "#B3853F", 600: "#966D31" },
  signal: { 300: "#75BCA7", 400: "#52A78D", 500: "#3D8E77", 600: "#2F715F" },
  rust: { 300: "#D3826A", 400: "#C2643F", 500: "#B0503F", 600: "#8F3F31" },
};

export function tierColor(tier: string, dark = false) {
  const t = (tier || "C").toUpperCase();
  if (t === "A") return dark ? chartColors.brand[400] : chartColors.brand[500];
  if (t === "B") return dark ? chartColors.signal[400] : chartColors.signal[500];
  return dark ? chartColors.rust[400] : chartColors.rust[500];
}

// Even-stepped brass shades for funnel stages — darker brass = further
// down the funnel, so "closer to won" visually reads as more saturated.
export const funnelShades = [
  chartColors.brand[300],
  chartColors.brand[400],
  chartColors.brand[500],
  chartColors.brand[600],
  chartColors.signal[500],
];
