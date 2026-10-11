import { Box, Tooltip } from "@mui/material";

/**
 * A tiny 24-hour trend line (design §22.1). Gaps (null) break the line.
 * `max` fixes the top of the scale (e.g. 100 for percentages).
 */
export default function Sparkline({ values, times, max, width = 120, height = 28, label, format }: {
  values: (number | null)[];
  times?: string[];
  max?: number;
  width?: number;
  height?: number;
  label: string;
  format?: (v: number) => string;
}) {
  const nums = values.filter((v): v is number => v !== null);
  if (nums.length < 2) {
    return <Box component="span" sx={{ width, display: "inline-block", color: "text.disabled", fontSize: 12 }}>no trend yet</Box>;
  }
  const top = Math.max(max ?? 0, ...nums) || 1;
  const step = width / Math.max(values.length - 1, 1);
  const segments: string[][] = [[]];
  values.forEach((v, i) => {
    if (v === null) {
      if (segments[segments.length - 1].length) segments.push([]);
      return;
    }
    segments[segments.length - 1].push(`${(i * step).toFixed(1)},${(height - 2 - (v / top) * (height - 4)).toFixed(1)}`);
  });
  const fmt = format ?? ((v: number) => v.toFixed(1));
  const lo = Math.min(...nums), hi = Math.max(...nums);
  const span = times && times.length ? `${new Date(times[0]).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}–now` : "24 h";
  return (
    <Tooltip title={`${label} (${span}): low ${fmt(lo)}, high ${fmt(hi)}, now ${fmt(nums[nums.length - 1])}`}>
      <svg width={width} height={height} role="img" aria-label={`${label} trend`} style={{ display: "block" }}>
        {segments.filter((s) => s.length > 1).map((s, k) => (
          <polyline key={k} points={s.join(" ")} fill="none" stroke="currentColor" strokeWidth={1.5} opacity={0.8} />
        ))}
      </svg>
    </Tooltip>
  );
}
