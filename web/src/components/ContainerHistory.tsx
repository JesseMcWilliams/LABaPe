// A container's CPU and memory over the last hours or days (plan A1): sampled with every 5-minute read, kept 7 days.
import { useEffect, useState } from "react";
import { Alert, MenuItem, Stack, Typography } from "@mui/material";
import { api } from "../api";
import { Field } from "../forms";
import LineChart from "./LineChart";

interface Point { t: number; cpu: number | null; memory: number | null }

const RANGES: [number, string][] = [[6, "6 hours"], [24, "24 hours"], [72, "3 days"], [168, "7 days"]];
const mb = (b: number) => (b >= 2 ** 30 ? `${(b / 2 ** 30).toFixed(1)} GB` : `${Math.round(b / 2 ** 20)} MB`);

export default function ContainerHistory({ hostId, name }: { hostId: number; name: string }) {
  const [hours, setHours] = useState(24);
  const [points, setPoints] = useState<Point[] | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    setPoints(null);
    api.get<{ points: Point[] }>(`/api/hosts/${hostId}/containers/history?name=${encodeURIComponent(name)}&hours=${hours}`)
      .then((r) => setPoints(r.points), (e) => setError((e as Error).message));
  }, [hostId, name, hours]);
  const to = Date.now();
  const from = to - hours * 3600 * 1000;
  return (
    <Stack spacing={2}>
      <Field select label="Range" value={String(hours)} onChange={(e) => setHours(Number(e.target.value))} sx={{ width: 160 }}>
        {RANGES.map(([h, l]) => <MenuItem key={h} value={String(h)}>{l}</MenuItem>)}
      </Field>
      {error && <Alert severity="error">{error}</Alert>}
      {points && (
        <>
          <LineChart title="CPU" points={points.map((p) => ({ t: p.t, v: p.cpu }))} from={from} to={to} unit="%" />
          <LineChart title="Memory" points={points.map((p) => ({ t: p.t, v: p.memory }))} from={from} to={to} format={(v) => mb(v)} color="#9c27b0" />
        </>
      )}
      <Typography variant="caption" color="text.secondary">
        One reading every 5 minutes while the container runs, kept for 7 days. CPU is a share of one core as the runtime reports it, so a busy multi-threaded container can pass 100%.
      </Typography>
    </Stack>
  );
}
