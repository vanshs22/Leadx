"use client";

import { PieChart, Pie, Cell, ResponsiveContainer } from "recharts";
import { chartColors } from "@/lib/chartColors";

export function TierDonut({ a, b, c }: { a: number; b: number; c: number }) {
  const total = a + b + c;
  const data = [
    { name: "Tier A", value: a, color: chartColors.brand[500] },
    { name: "Tier B", value: b, color: chartColors.signal[500] },
    { name: "Tier C", value: c, color: chartColors.rust[400] },
  ].filter((d) => d.value > 0);

  return (
    <div className="relative h-40 w-40 shrink-0">
      <ResponsiveContainer width="100%" height="100%">
        <PieChart>
          <Pie
            data={data.length ? data : [{ name: "empty", value: 1, color: chartColors.rust[300] }]}
            dataKey="value"
            innerRadius="68%"
            outerRadius="100%"
            paddingAngle={data.length > 1 ? 3 : 0}
            isAnimationActive={false}
            stroke="none"
          >
            {(data.length ? data : [{ color: "#e5e5e5" }]).map((d, i) => (
              <Cell key={i} fill={d.color} />
            ))}
          </Pie>
        </PieChart>
      </ResponsiveContainer>
      <div className="pointer-events-none absolute inset-0 flex flex-col items-center justify-center">
        <span className="font-mono text-2xl font-semibold tabular-nums text-ink">{total}</span>
        <span className="text-[10px] uppercase tracking-wide text-ink-faint">leads</span>
      </div>
    </div>
  );
}
