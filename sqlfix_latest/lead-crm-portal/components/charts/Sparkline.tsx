"use client";

import { LineChart, Line, ResponsiveContainer, YAxis } from "recharts";
import { chartColors } from "@/lib/chartColors";

export function Sparkline({
  data,
  color = chartColors.brand[500],
}: {
  data: { value: number }[];
  color?: string;
}) {
  if (!data || data.length < 2) return null;
  return (
    <div className="h-9 w-20">
      <ResponsiveContainer width="100%" height="100%">
        <LineChart data={data} margin={{ top: 4, right: 2, bottom: 2, left: 2 }}>
          <YAxis hide domain={["dataMin - 1", "dataMax + 1"]} />
          <Line
            type="monotone"
            dataKey="value"
            stroke={color}
            strokeWidth={2}
            dot={false}
            isAnimationActive={false}
          />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}
