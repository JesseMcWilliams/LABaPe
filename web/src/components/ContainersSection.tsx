// Containers (plan C1 and C2, Planning_Container-Management.md): what a host's Docker or Podman holds, and the guarded actions.
// The collector reads every 5 minutes; Refresh reads now; logs and live stats are fetched on request and never stored.
// Start, stop, restart and recreate follow the host's level (off, known, new, all); the Stack's own containers are never changed here.
import { useCallback, useEffect, useState } from "react";
import {
  Alert, Box, Button, Checkbox, Chip, Dialog, DialogActions, DialogContent, DialogTitle, FormControlLabel, MenuItem, Stack, Switch, Tab, Table, TableBody,
  TableCell, TableHead, TableRow, Tabs, Typography,
} from "@mui/material";
import { api, fmtTime } from "../api";
import { Field } from "../forms";
import { ActionButton } from "./Confirm";
import { CleanupDialog } from "./ContainersCleanup";

interface Mount { type: string; source: string; target: string; rw: boolean }
interface Container {
  name: string; id: string; image: string; project: string; service: string; state: string; health: string; exit_code: number | null; oom: boolean;
  error: string; started_at: string; finished_at: string; restarts: number; restart_policy: string; unit: string; compose_dir: string; ports: string[]; mounts: Mount[]; networks: string[];
  stats: { cpu: string; memory: string; memory_pct: string } | null; attention: string; stack_own: boolean; quadlet: boolean;
  actionable: boolean; why_not: string; ops: string[];
}
interface Image { id: string; repository: string; tag: string; size: string; created: string; dangling: boolean; in_use: boolean }
interface Volume { name: string; driver: string; size: string | null; used_by: string[] }
interface Page {
  runtime: string; version: string; collected_at: string | null; error: string; alerts: boolean; level: string; known: string[]; containers: Container[]; images: Image[]; volumes: Volume[];
  summary: { total: number; running: number; attention: number };
}
interface Live { cpu: string; memory: string; memory_pct: string; pids: string; net: string; block: string }

const ACTION_LABEL: Record<string, string> = { start: "Start", stop: "Stop", restart: "Restart", recreate: "Pull and recreate" };
const ACTION_TIP: Record<string, string> = {
  start: "Start the stopped container", stop: "Stop the container (it stays until started again)", restart: "Restart the container",
  recreate: "Pull the compose service's image; the container is recreated only if a newer image came down",
};
const ACTION_BODY: Record<string, (c: Container) => string> = {
  start: (c) => `${c.name} is started. Whatever depends on it can use it again.`,
  stop: (c) => `${c.name} is stopped${c.restart_policy && c.restart_policy !== "no" ? ` (its restart policy is ${c.restart_policy}, so it stays down only until the next host or runtime restart)` : ""}. Anything that uses it stops working until it is started.`,
  restart: (c) => c.quadlet ? `${c.name} belongs to the systemd unit ${c.unit}, which is restarted. It is briefly unavailable.` : `${c.name} is stopped and started again. It is briefly unavailable.`,
  recreate: (c) => `Pulls the newest image of ${c.project}/${c.service} and recreates ${c.name} from the compose file in ${c.compose_dir}. If no newer image was published, nothing is replaced and it keeps running. If one was, it is unavailable while it is replaced.`,
};

const LEVEL_LABEL: Record<string, string> = { off: "Nothing (read-only)", known: "Known containers", new: "Known and new containers", all: "All containers" };
const LEVEL_HELP: Record<string, string> = {
  off: "This page only reads.",
  known: "Only the containers ticked in the Known column.",
  new: "Known containers, and any created after this was set.",
  all: "Every container except the stack's own.",
};

const stateColor = (c: Container) => (c.attention ? "error" : c.state === "running" ? "success" : "default");

function Detail({ hostId, c, onClose, onChanged }: { hostId: number; c: Container; onClose: () => void; onChanged: () => void }) {
  const [tab, setTab] = useState(0);
  const [tail, setTail] = useState(200);
  const [since, setSince] = useState("");
  const [needle, setNeedle] = useState("");
  const [log, setLog] = useState<{ text: string; lines: number; truncated: boolean } | null>(null);
  const [live, setLive] = useState<Live | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState("");
  const act = async (op: string) => {
    setBusy(true);
    setError("");
    setDone("");
    try {
      await api.post(`/api/hosts/${hostId}/containers/action`, { name: c.name, op });
      setDone(`${op}: done on ${c.name}.`);
      onChanged();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  const call = async (what: "logs" | "stats") => {
    setBusy(true);
    setError("");
    try {
      if (what === "logs") setLog(await api.post(`/api/hosts/${hostId}/containers/logs`, { name: c.name, tail, since, filter: needle }));
      else setLive(await api.post(`/api/hosts/${hostId}/containers/stats`, { name: c.name }));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <Dialog open onClose={onClose} maxWidth="md" fullWidth>
      <DialogTitle sx={{ fontFamily: "monospace" }}>{c.name}</DialogTitle>
      <DialogContent>
        <Tabs value={tab} onChange={(_, v) => setTab(v)} sx={{ mb: 2 }}>
          <Tab label="Details" /><Tab label="Logs" />
        </Tabs>
        {error && <Alert severity="error" sx={{ mb: 1 }}>{error}</Alert>}
        {done && <Alert severity="success" sx={{ mb: 1 }}>{done}</Alert>}
        {tab === 0 && (
          <Stack spacing={1}>
            {c.attention && <Alert severity="error">This container {c.attention}{c.error ? `: ${c.error}` : ""}.</Alert>}
            {c.stack_own && <Alert severity="info">Part of this LABaPe stack: shown here, never changed from this page.</Alert>}
            <Table size="small">
              <TableBody>
                {([
                  ["Image", c.image], ["State", `${c.state}${c.health ? ` (${c.health})` : ""}`], ["Started", c.started_at && !c.started_at.startsWith("0001") ? fmtTime(c.started_at) : "—"],
                  ["Finished", c.finished_at && !c.finished_at.startsWith("0001") ? `${fmtTime(c.finished_at)}, exit code ${c.exit_code}` : "—"],
                  ["Restarts", `${c.restarts} (policy ${c.restart_policy || "none"})`], ["Compose", c.project ? `${c.project} / ${c.service}` : "—"],
                  ["Ports", c.ports.join(", ") || "—"], ["Networks", c.networks.join(", ") || "—"],
                  ["Mounts", c.mounts.map((m) => `${m.source} → ${m.target}${m.rw ? "" : " (read-only)"}`).join("\n") || "—"],
                ] as [string, string][]).map(([k, v]) => (
                  <TableRow key={k}><TableCell sx={{ width: 140, color: "text.secondary" }}>{k}</TableCell><TableCell sx={{ whiteSpace: "pre-line", wordBreak: "break-all" }}>{v}</TableCell></TableRow>
                ))}
              </TableBody>
            </Table>
            <Stack direction="row" spacing={1} useFlexGap sx={{ alignItems: "center", flexWrap: "wrap" }}>
              {(["start", "stop", "restart", "recreate"] as const).map((op) => (
                <ActionButton key={op} size="small" variant="outlined" color={op === "stop" || op === "recreate" ? "warning" : "primary"}
                  disabled={busy || !c.ops.includes(op)}
                  tip={!c.actionable ? `Not available: ${c.why_not}` : !c.ops.includes(op) ? `${op} doesn't apply while it is ${c.state}` : ACTION_TIP[op]}
                  confirm={{ title: `${ACTION_LABEL[op]} ${c.name}?`, confirmLabel: ACTION_LABEL[op], danger: op !== "start", body: ACTION_BODY[op](c) }}
                  onClick={() => act(op)}>{ACTION_LABEL[op]}</ActionButton>
              ))}
              {!c.actionable && <Typography variant="body2" color="text.secondary">{c.why_not}.</Typography>}
            </Stack>
            <Stack direction="row" spacing={2} sx={{ alignItems: "center" }}>
              <Button size="small" variant="outlined" disabled={busy || c.state !== "running"} onClick={() => call("stats")}
                title={c.state === "running" ? "Read the container's CPU and memory now" : "Only a running container has a reading"}>Read now</Button>
              <Typography variant="body2" color="text.secondary">
                {live ? `CPU ${live.cpu} · memory ${live.memory} (${live.memory_pct}) · ${live.pids} processes · network ${live.net} · disk ${live.block}`
                  : c.stats ? `CPU ${c.stats.cpu} · memory ${c.stats.memory} (as of the last collection)` : "No reading yet"}
              </Typography>
            </Stack>
          </Stack>
        )}
        {tab === 1 && (
          <Stack spacing={1}>
            <Stack direction="row" spacing={1} useFlexGap sx={{ flexWrap: "wrap", alignItems: "flex-start" }}>
              <Field select label="Lines" value={String(tail)} onChange={(e) => setTail(Number(e.target.value))} sx={{ width: 110 }}>
                {[50, 200, 500, 1000, 2000].map((n) => <MenuItem key={n} value={String(n)}>{n}</MenuItem>)}
              </Field>
              <Field select label="Since" value={since} onChange={(e) => setSince(e.target.value)} sx={{ width: 130 }}>
                {[["", "any time"], ["10m", "10 minutes"], ["1h", "1 hour"], ["6h", "6 hours"], ["1d", "1 day"], ["7d", "7 days"]].map(([v, l]) => <MenuItem key={v} value={v}>{l}</MenuItem>)}
              </Field>
              <Field label="Only lines containing" value={needle} onChange={(e) => setNeedle(e.target.value)} sx={{ flex: 1, minWidth: 180 }} />
              <Button variant="contained" disabled={busy} onClick={() => call("logs")} sx={{ mt: 1 }}>Load</Button>
            </Stack>
            <Typography variant="caption" color="text.secondary">
              Read on request, never stored. Values after PASSWORD, TOKEN, SECRET or KEY are hidden; a log can still hold other private data.
            </Typography>
            {log && (
              <>
                {log.truncated && <Alert severity="warning">Only the last megabyte is shown.</Alert>}
                <Box component="pre" sx={{ m: 0, p: 1, maxHeight: 420, overflow: "auto", fontSize: 12, bgcolor: "action.hover", borderRadius: 1, whiteSpace: "pre-wrap", wordBreak: "break-all" }}>
                  {log.text || "(no lines)"}
                </Box>
                <Typography variant="caption" color="text.secondary">{log.lines} line{log.lines === 1 ? "" : "s"}</Typography>
              </>
            )}
          </Stack>
        )}
      </DialogContent>
      <DialogActions><Button onClick={onClose}>Close</Button></DialogActions>
    </Dialog>
  );
}

export function ContainersSection({ hostId, reload }: { hostId: number; reload: () => void }) {
  const [page, setPage] = useState<Page | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [openName, setOpenName] = useState("");
  const [cleaning, setCleaning] = useState(false);
  const load = useCallback(async () => {
    try {
      setPage(await api.get<Page>(`/api/hosts/${hostId}/containers`));
      setError("");
    } catch (e) {
      setError((e as Error).message);
    }
  }, [hostId]);
  useEffect(() => { void load(); }, [load]);
  const refresh = async () => {
    setBusy(true);
    try {
      setPage(await api.post<Page>(`/api/hosts/${hostId}/containers/refresh`, {}));
      setError("");
      reload();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  const post = async (path: string, body: unknown) => {
    try {
      await api.post(`/api/hosts/${hostId}/containers/${path}`, body);
      await load();
    } catch (e) {
      setError((e as Error).message);
    }
  };
  const setAlerts = async (enabled: boolean) => {
    try {
      await api.post(`/api/hosts/${hostId}/containers/alerts`, { enabled });
      await load();
    } catch (e) {
      setError((e as Error).message);
    }
  };
  if (!page) return error ? <Alert severity="error">{error}</Alert> : <Typography color="text.secondary">Loading…</Typography>;
  if (!page.runtime) return <Alert severity="info">This host has neither Docker nor Podman, or its containers haven&apos;t been read yet (they are read every 5 minutes once SSH access is set up).</Alert>;
  const s = page.summary;
  const open = page.containers.find((x) => x.name === openName) ?? null;
  return (
    <Stack spacing={2}>
      <Typography variant="body2" color="text.secondary">
        {page.runtime === "podman" ? "Podman" : "Docker"} {page.version} · {s.total} container{s.total === 1 ? "" : "s"}, {s.running} running
        {s.attention ? `, ${s.attention} need a look` : ""}. Read every 5 minutes{page.collected_at ? ` (last ${fmtTime(page.collected_at)})` : ""}; it changes a container only when you ask, as far as the setting below allows.
      </Typography>
      {(page.error || error) && <Alert severity="warning">{page.error ? `The last read failed: ${page.error}` : error}</Alert>}
      <Stack direction="row" spacing={2} sx={{ alignItems: "center", flexWrap: "wrap" }}>
        <Button size="small" variant="outlined" disabled={busy} onClick={refresh} title="Read the host's containers now">Refresh</Button>
        <Button size="small" variant="outlined" onClick={() => setCleaning(true)}
          title="See what could be removed (stopped containers, unused images and volumes) and what it frees; nothing is removed until you confirm">Clean up…</Button>
        <FormControlLabel control={<Switch checked={page.alerts} onChange={(e) => setAlerts(e.target.checked)} />}
          label="Alert when a container of this host is down, unhealthy or restarting" />
      </Stack>
      <Stack direction="row" spacing={2} useFlexGap sx={{ alignItems: "flex-start", flexWrap: "wrap" }}>
        <Field select label="Changes from this page" value={page.level} onChange={(e) => post("level", { level: e.target.value })} sx={{ width: 240 }}
          help={LEVEL_HELP[page.level]}>
          {Object.entries(LEVEL_LABEL).map(([v, l]) => <MenuItem key={v} value={v}>{l}</MenuItem>)}
        </Field>
        <Typography variant="body2" color="text.secondary" sx={{ flex: 1, minWidth: 260, pt: 1 }}>
          The LABaPe stack&apos;s own containers (marked <em>stack</em>) are never changed from here, whatever this says.
        </Typography>
      </Stack>
      <Table size="small">
        <TableHead>
          <TableRow>{(page.level === "known" || page.level === "new") && <TableCell>Known</TableCell>}<TableCell>Container</TableCell><TableCell>State</TableCell><TableCell>Image</TableCell><TableCell>Compose</TableCell><TableCell>Ports</TableCell><TableCell>CPU</TableCell><TableCell>Memory</TableCell><TableCell>Restarts</TableCell></TableRow>
        </TableHead>
        <TableBody>
          {page.containers.map((c) => (
            <TableRow key={c.name} hover sx={{ cursor: "pointer" }} onClick={() => setOpenName(c.name)}>
              {(page.level === "known" || page.level === "new") && (
                <TableCell onClick={(e) => e.stopPropagation()} padding="checkbox">
                  <Checkbox size="small" disabled={c.stack_own} checked={page.known.includes(c.name)}
                    onChange={(e) => post("known", { name: c.name, known: e.target.checked })}
                    title={c.stack_own ? "The stack's own containers are never changed from here" : "A known container may be started, stopped, restarted and recreated from this page"} />
                </TableCell>
              )}
              <TableCell sx={{ fontFamily: "monospace" }}>{c.name}{c.stack_own && <Chip size="small" label="stack" sx={{ ml: 1 }} title="Part of this LABaPe stack" />}</TableCell>
              <TableCell><Chip size="small" color={stateColor(c)} label={c.health ? `${c.state} · ${c.health}` : c.state} title={c.attention ? `This container ${c.attention}` : undefined} /></TableCell>
              <TableCell sx={{ fontSize: 12 }}>{c.image}</TableCell>
              <TableCell>{c.project ? `${c.project}/${c.service}` : "—"}</TableCell>
              <TableCell sx={{ fontSize: 12 }}>{c.ports.join(", ") || "—"}</TableCell>
              <TableCell>{c.stats?.cpu ?? "—"}</TableCell><TableCell>{c.stats?.memory?.split(" / ")[0] ?? "—"}</TableCell>
              <TableCell>{c.restarts}</TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
      <Typography variant="subtitle2">Images</Typography>
      <Table size="small">
        <TableHead><TableRow><TableCell>Image</TableCell><TableCell>ID</TableCell><TableCell>Size</TableCell><TableCell>Created</TableCell><TableCell>Used</TableCell></TableRow></TableHead>
        <TableBody>
          {page.images.map((i) => (
            <TableRow key={`${i.id}${i.repository}${i.tag}`}>
              <TableCell sx={{ fontFamily: "monospace", fontSize: 12 }}>{i.dangling ? "(untagged)" : `${i.repository}:${i.tag}`}</TableCell>
              <TableCell sx={{ fontFamily: "monospace", fontSize: 12 }}>{i.id}</TableCell><TableCell>{i.size}</TableCell><TableCell>{i.created}</TableCell>
              <TableCell>{i.in_use ? <Chip size="small" label="in use" /> : i.dangling ? <Chip size="small" color="warning" label="dangling" title="Untagged and unused" /> : "—"}</TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
      <Typography variant="subtitle2">Volumes</Typography>
      <Table size="small">
        <TableHead><TableRow><TableCell>Volume</TableCell><TableCell>Driver</TableCell><TableCell>Size</TableCell><TableCell>Used by</TableCell></TableRow></TableHead>
        <TableBody>
          {page.volumes.map((v) => (
            <TableRow key={v.name}>
              <TableCell sx={{ fontFamily: "monospace", fontSize: 12, wordBreak: "break-all" }}>{v.name}</TableCell><TableCell>{v.driver}</TableCell>
              <TableCell>{v.size ?? "—"}</TableCell>
              <TableCell>{v.used_by.length ? v.used_by.join(", ") : <Chip size="small" color="warning" label="unused" />}</TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
      {cleaning && <CleanupDialog hostId={hostId} onClose={() => setCleaning(false)} onChanged={() => void load()} />}
      {open && <Detail hostId={hostId} c={open} onClose={() => setOpenName("")} onChanged={() => void load()} />}
    </Stack>
  );
}
