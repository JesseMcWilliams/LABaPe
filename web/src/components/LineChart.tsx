// A small line chart over time: axes, a line with a point per reading (with a tooltip), and an optional
// warning line. Used for a drive's history (design §64); plain SVG, no library.
import { Box, Typography } from "@mui/material";

export interface ChartPoint { t: number; v: number | null }

const W = 640, H = 200, L = 52, R = 12, T = 12, B = 28;
const day = (t: number) => new Date(t).toLocaleDateString(undefined, { month: "short", day: "numeric" });
const niceMax = (m: number) => {
  if (m <= 1) return 1;
  const p = 10 ** Math.floor(Math.log10(m));
  const n = m / p;
  return (n <= 1 ? 1 : n <= 2 ? 2 : n <= 5 ? 5 : 10) * p;
};

export default function LineChart({ title, points, from, to, unit = "", threshold, color = "#1976d2", format }: {
  title: string; points: ChartPoint[]; from: number; to: number; unit?: string; threshold?: number; color?: string;
  format?: (v: number) => string;
}) {
  const pts = points.filter((p) => p.v !== null) as { t: number; v: number }[];
  const fmt = format ?? ((v: number) => `${Math.round(v * 10) / 10}${unit}`);
  if (pts.length === 0) {
    return <Box><Typography variant="subtitle2">{title}</Typography><Typography variant="body2" color="text.secondary">No readings in this range yet.</Typography></Box>;
  }
  const max = niceMax(Math.max(threshold ?? 0, ...pts.map((p) => p.v)));
  const x = (t: number) => L + ((t - from) / Math.max(1, to - from)) * (W - L - R);
  const y = (v: number) => T + (1 - v / max) * (H - T - B);
  const ticks = [0, 0.25, 0.5, 0.75, 1].map((f) => f * max);
  const path = pts.map((p, i) => `${i ? "L" : "M"}${x(p.t).toFixed(1)},${y(p.v).toFixed(1)}`).join(" ");
  const last = pts[pts.length - 1];
  return (
    <Box>
      <Typography variant="subtitle2">{title} <Typography component="span" variant="body2" color="text.secondary">now {fmt(last.v)}</Typography></Typography>
      <svg viewBox={`0 0 ${W} ${H}`} width="100%" style={{ maxWidth: W, display: "block" }} role="img" aria-label={`${title}, ${pts.length} readings`}>
        {ticks.map((v) => (
          <g key={v}>
            <line x1={L} x2={W - R} y1={y(v)} y2={y(v)} stroke="currentColor" opacity={0.12} />
            <text x={L - 6} y={y(v) + 4} textAnchor="end" fontSize={11} fill="currentColor" opacity={0.7}>{fmt(v)}</text>
          </g>
        ))}
        {[from, (from + to) / 2, to].map((t, i) => (
          <text key={i} x={x(t)} y={H - 8} textAnchor={i === 0 ? "start" : i === 2 ? "end" : "middle"} fontSize={11} fill="currentColor" opacity={0.7}>{day(t)}</text>
        ))}
        {threshold !== undefined && (
          <g>
            <line x1={L} x2={W - R} y1={y(threshold)} y2={y(threshold)} stroke="#ed6c02" strokeDasharray="5 4" />
            <text x={W - R} y={y(threshold) - 4} textAnchor="end" fontSize={11} fill="#ed6c02">limit {fmt(threshold)}</text>
          </g>
        )}
        <path d={path} fill="none" stroke={color} strokeWidth={2} />
        {pts.map((p) => (
          <circle key={p.t} cx={x(p.t)} cy={y(p.v)} r={3} fill={color}>
            <title>{`${new Date(p.t).toLocaleString()}: ${fmt(p.v)}`}</title>
          </circle>
        ))}
      </svg>
    </Box>
  );
}
