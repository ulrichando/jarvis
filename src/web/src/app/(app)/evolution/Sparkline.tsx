"use client";

// Tiny inline fitness sparkline for /evolution — an SVG line over the last N
// soak-fitness composite readings. Refined-minimal: one stroke + a soft area
// fade, dots colored by pass/fail, latest point emphasized. No axes, no library.

type Point = { ts: string; composite: number; passed: boolean };

const EMERALD = "#10b981";
const AMBER = "#f59e0b";

export function Sparkline({
  points,
  width = 176,
  height = 44,
  className,
}: {
  points: Point[];
  width?: number;
  height?: number;
  className?: string;
}) {
  if (!points || points.length === 0) {
    return <div className={className} style={{ width, height }} aria-hidden />;
  }

  const vals = points.map((p) => p.composite);
  const min = Math.min(...vals);
  const max = Math.max(...vals);
  const span = max - min || 1;
  const pad = 4;
  const w = width - pad * 2;
  const h = height - pad * 2;
  const x = (i: number) =>
    pad + (points.length === 1 ? w / 2 : (i / (points.length - 1)) * w);
  const y = (v: number) => pad + h - ((v - min) / span) * h;

  const line = points.map((p, i) => `${x(i)},${y(p.composite)}`).join(" ");
  const area = `${x(0)},${pad + h} ${line} ${x(points.length - 1)},${pad + h}`;
  const last = points[points.length - 1];
  const stroke = last.passed ? EMERALD : AMBER;
  const gid = `spark-${Math.round(min * 1000)}-${Math.round(max * 1000)}-${points.length}`;

  return (
    <svg
      width={width}
      height={height}
      viewBox={`0 0 ${width} ${height}`}
      className={className}
      role="img"
      aria-label={`Fitness trend, latest ${last.composite.toFixed(2)}`}
    >
      <defs>
        <linearGradient id={gid} x1="0" x2="0" y1="0" y2="1">
          <stop offset="0%" stopColor={stroke} stopOpacity="0.16" />
          <stop offset="100%" stopColor={stroke} stopOpacity="0" />
        </linearGradient>
      </defs>
      <polygon points={area} fill={`url(#${gid})`} />
      <polyline
        points={line}
        fill="none"
        stroke={stroke}
        strokeWidth={1.5}
        strokeLinejoin="round"
        strokeLinecap="round"
      />
      {points.map((p, i) => (
        <circle
          key={`${p.ts}-${i}`}
          cx={x(i)}
          cy={y(p.composite)}
          r={i === points.length - 1 ? 2.6 : 1.1}
          fill={p.passed ? EMERALD : AMBER}
          opacity={i === points.length - 1 ? 1 : 0.6}
        />
      ))}
    </svg>
  );
}
