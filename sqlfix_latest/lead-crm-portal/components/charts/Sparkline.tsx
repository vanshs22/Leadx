"use client";
import { LineChart, Line, ResponsiveContainer, YAxis } from "recharts";

export type SparklineTone = "brand" | "accent" | "success" | "warn" | "danger";

/** Stroke follows `currentColor`, so token classes drive light and dark mode. */
const TONE_TEXT: Record<SparklineTone, string> = {
  brand: "text-brand-600 dark:text-brand-300",
  accent: "text-signal-500 dark:text-signal-300",
  success: "text-emerald-600 dark:text-emerald-400",
  warn: "text-amber-600 dark:text-amber-400",
  danger: "text-rust-600 dark:text-rust-400",
};

export function Sparkline({
  data,
  color,
  tone = "brand",
  className = "h-9 w-20",
  label = "Trend",
}: {
  data: { value: number }[];
  /** Explicit stroke override (legacy callers). Prefer `tone`. */
  color?: string;
  tone?: SparklineTone;
  className?: string;
  label?: string;
}) {
  if (!data || data.length < 2) return null;
  return (
    <div className={`${className} ${TONE_TEXT[tone]}`} role="img" aria-label={label}>
      <ResponsiveContainer width="100%" height="100%">
        <LineChart data={data} margin={{ top: 4, right: 2, bottom: 2, left: 2 }}>
          <YAxis hide domain={["dataMin - 1", "dataMax + 1"]} />
          <Line
            type="monotone"
            dataKey="value"
            stroke={color || "currentColor"}
            strokeWidth={1.75}
            dot={false}
            isAnimationActive={false}
          />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}
