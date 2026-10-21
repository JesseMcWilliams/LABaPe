// One physical drive of a host (design §64, batch H3): its identity and layout, SMART attributes, the history of the
// numbers worth watching as charts, and when they changed. Reached from Drives and volumes and from a drive's alert.
import { useEffect, useRef, useState } from "react";
import { useParams, useSearchParams } from "react-router-dom";
import {
  Alert, Button, Chip, Paper, Stack, Table, TableBody, TableCell, TableHead, TableRow, ToggleButton, ToggleButtonGroup, Typography,
} from "@mui/material";
import { api, fmtTime } from "../api";
import { DetailHeader, Facts } from "../components/PageParts";
import LineChart, { ChartPoint } from "../components/LineChart";

/* eslint-disable @typescript-eslint/no-explicit-any */
type Any = any;
const gb = (b?: number) => (b ? `${(b / 1e9).toFixed(b >= 1e12 ? 0 : 1)} GB` : "—");

interface Detail {
  host: { id: number; name: string }; disk: Any; block: Any; smart_at: string | null; days: number; retention_days: number;
  samples: { at: string; data: Record<string, number | null> }[];
  changes: { at: string; attribute: string; from: number; to: number }[];
  alerts: { level: string; message: string; started_at: string; acked: boolean }[];
  warnings: { level: "error" | "warning" | "info"; message: string }[];
}
const RANGES = [{ days: 7, label: "7 days" }, { days: 30, label: "30 days" }, { days: 90, label: "90 days" }, { days: 365, label: "1 year" }];
const CHARTS: { key: string; title: string; unit?: string; threshold?: number; color: string }[] = [
  { key: "pending", title: "Pending sectors", color: "#d32f2f" },
  { key: "reallocated", title: "Reallocated sectors", color: "#ed6c02" },
  { key: "uncorrectable", title: "Uncorrectable sectors", color: "#9c27b0" },
  { key: "temperature", title: "Temperature", unit: " °C", threshold: 60, color: "#1976d2" },
  { key: "wear_pct", title: "Wear used", unit: "%", threshold: 80, color: "#2e7d32" },
  { key: "media_errors", title: "Media errors", color: "#795548" },
];

function Tree({ n, depth = 0 }: { n: Any; depth?: number }) {
  return (
    <>
      <TableRow>
        <TableCell sx={{ pl: 2 + depth * 3, fontFamily: "monospace" }}>{n.name}</TableCell><TableCell>{n.type}</TableCell>
        <TableCell>{gb(n.size)}</TableCell><TableCell>{n.fstype ?? ""}</TableCell><TableCell>{(n.mountpoints ?? []).join(", ")}</TableCell>
      </TableRow>
      {(n.children ?? []).map((c: Any) => <Tree key={c.path ?? c.name} n={c} depth={depth + 1} />)}
    </>
  );
}

export default function DiskDetail() {
  const { id, disk } = useParams();
  const [params, setParams] = useSearchParams();
  const days = Number(params.get("days") ?? 30);
  const metric = params.get("metric") ?? "";
  const [d, setD] = useState<Detail | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const focus = useRef<HTMLDivElement | null>(null);

  const load = () => api.get<Detail>(`/api/hosts/${id}/disks/${encodeURIComponent(disk ?? "")}?days=${days}`).then((r) => { setD(r); setError(""); },
    (e) => setError(e.message));
  useEffect(() => { load(); }, [id, disk, days]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { if (d && metric) focus.current?.scrollIntoView({ block: "center" }); }, [d, metric]);
  const readNow = async () => { setBusy(true); try { await api.post(`/api/hosts/${id}/admin/refresh`); await load(); } catch (e) { setError((e as Error).message); } finally { setBusy(false); } };

  const back = `/hosts/${id}?section=drives`;
  if (!d) return <Stack spacing={2}><DetailHeader back={back} backLabel="Drives and volumes" title="Drive" />{error ? <Alert severity="error">{error}</Alert> : null}</Stack>;
  const s = d.disk;
  const to = Date.now(), from = to - d.days * 86400000;
  const series = (k: string): ChartPoint[] => d.samples.map((p) => ({ t: new Date(p.at).getTime(), v: (p.data[k] as number | null | undefined) ?? null }));
  const shown = CHARTS.filter((c) => d.samples.some((p) => p.data[c.key] != null) || c.key === metric);
  return (
    <Stack spacing={2}>
      <DetailHeader back={back} backLabel="Drives and volumes"
        title={<>{d.host.name}: {s.device} <Typography component="span" color="text.secondary">{s.model}</Typography>{" "}
          <Chip size="small" label={s.passed === false ? "FAILED" : s.passed ? "SMART passed" : "SMART unknown"} color={s.passed === false ? "error" : s.passed ? "success" : "default"} /></>}
        actions={<Button variant="outlined" disabled={busy} onClick={readNow} title="Read this host's drives now and add a point to the history">Read now</Button>} />
      {error && <Alert severity="error">{error}</Alert>}
      {d.alerts.map((a, i) => <Alert key={i} severity={a.level === "error" ? "error" : "warning"}>{a.message} (open since {fmtTime(a.started_at)}{a.acked ? ", acknowledged" : ""})</Alert>)}
      {d.alerts.length === 0 && d.warnings.map((w, i) => <Alert key={i} severity={w.level}>{w.message}</Alert>)}
      <Facts rows={[
        ["Serial number", s.serial || "—"], ["Size", gb(s.size_bytes ?? d.block?.size)],
        ["Interface", [s.interface, s.rpm ? `${s.rpm} rpm` : s.rpm === 0 ? "SSD" : ""].filter(Boolean).join(" · ") || "—"],
        ["Power-on time", s.power_on_hours != null ? `${Math.round(s.power_on_hours / 24)} days (${s.power_on_hours} h)` : "—"],
        ["Temperature", s.temperature != null ? `${s.temperature} °C` : "—"],
        ["Sectors", `${s.reallocated ?? "—"} reallocated · ${s.pending ?? "—"} pending · ${s.uncorrectable ?? "—"} uncorrectable`],
        ["Last SMART reading", fmtTime(d.smart_at)],
      ]} />
      {d.block && (
        <>
          <Typography variant="subtitle1">Layout</Typography>
          <Table size="small"><TableHead><TableRow><TableCell>Device</TableCell><TableCell>Type</TableCell><TableCell>Size</TableCell><TableCell>FS</TableCell><TableCell>Mounted</TableCell></TableRow></TableHead>
            <TableBody><Tree n={d.block} /></TableBody></Table>
        </>
      )}
      <Stack direction="row" spacing={2} sx={{ alignItems: "center", flexWrap: "wrap" }}>
        <Typography variant="subtitle1">History</Typography>
        <ToggleButtonGroup size="small" exclusive value={days} onChange={(_, v) => v && setParams({ ...(metric ? { metric } : {}), days: String(v) }, { replace: true })}>
          {RANGES.map((r) => <ToggleButton key={r.days} value={r.days}>{r.label}</ToggleButton>)}
        </ToggleButtonGroup>
        <Typography variant="caption" color="text.secondary">
          {d.samples.length} reading{d.samples.length === 1 ? "" : "s"}; read every 6 hours and kept {d.retention_days} days (Settings → History)
        </Typography>
      </Stack>
      {d.samples.length < 2 && <Alert severity="info">A trend needs at least two readings. History starts with the first reading after this version was installed; press <b>Read now</b> to add one.</Alert>}
      <Stack spacing={2} useFlexGap sx={{ flexWrap: "wrap" }} direction="row">
        {shown.map((c) => (
          <Paper key={c.key} variant="outlined" sx={{ p: 1.5, flex: "1 1 420px", outline: metric === c.key ? "2px solid" : "none", outlineColor: "warning.main" }}
            ref={metric === c.key ? focus : undefined}>
            <LineChart title={c.title} points={series(c.key)} from={from} to={to} unit={c.unit} threshold={c.threshold} color={c.color} />
          </Paper>
        ))}
      </Stack>
      {d.changes.length > 0 && (
        <>
          <Typography variant="subtitle1">When the numbers moved</Typography>
          <Table size="small"><TableHead><TableRow><TableCell>When</TableCell><TableCell>Attribute</TableCell><TableCell>From</TableCell><TableCell>To</TableCell><TableCell>Change</TableCell></TableRow></TableHead>
            <TableBody>
              {d.changes.map((c, i) => (
                <TableRow key={i}><TableCell>{fmtTime(c.at)}</TableCell><TableCell>{c.attribute}</TableCell><TableCell>{c.from}</TableCell><TableCell>{c.to}</TableCell>
                  <TableCell>{c.to > c.from ? `+${c.to - c.from}` : c.to - c.from}</TableCell></TableRow>
              ))}
            </TableBody></Table>
        </>
      )}
      {(s.attributes ?? []).length > 0 && (
        <>
          <Typography variant="subtitle1">SMART attributes (latest reading)</Typography>
          <Table size="small"><TableHead><TableRow><TableCell>ID</TableCell><TableCell>Attribute</TableCell><TableCell>Value</TableCell><TableCell>Worst</TableCell><TableCell>Threshold</TableCell><TableCell>Raw</TableCell><TableCell>Failed</TableCell></TableRow></TableHead>
            <TableBody>
              {(s.attributes as Any[]).map((a) => (
                <TableRow key={a.id}><TableCell>{a.id}</TableCell><TableCell>{a.name}</TableCell><TableCell>{a.value}</TableCell><TableCell>{a.worst}</TableCell><TableCell>{a.thresh}</TableCell><TableCell>{String(a.raw)}</TableCell>
                  <TableCell>{a.failed ? <Chip size="small" color="error" label={a.failed} /> : ""}</TableCell></TableRow>
              ))}
            </TableBody></Table>
        </>
      )}
    </Stack>
  );
}
