// Drive Pools (design §53.4), split out of HostSections.tsx: mdadm arrays, ZFS pools and LVM volume groups.
import { ReactNode, useState } from "react";
import {
  Alert, Box, Button, Checkbox, Chip, Dialog, DialogActions, DialogContent, DialogTitle, FormControlLabel, LinearProgress, MenuItem, Paper, Stack, Table, TableBody, TableCell, TableHead, TableRow, Typography,
} from "@mui/material";
import { alpha } from "@mui/material/styles";
import { api } from "../api";
import { Field } from "../forms";
import { useConfirm } from "./Confirm";
import { ReplaceDialog } from "./PoolReplace";
import { Facts } from "./PageParts";
import { AdminView, FormerMember, gb, Pool, PoolMember } from "./HostSections";

const POOL_COLOR: Record<string, "success" | "info" | "warning" | "error" | "default"> = {
  healthy: "success", scrubbing: "info", rebuilding: "warning", degraded: "error", failed: "error", unknown: "default",
};
const POOL_KIND: Record<string, string> = { mdadm: "Linux RAID (mdadm)", zfs: "ZFS", lvm: "LVM volume group" };

/** Drive Pools (design §53.4): mdadm arrays, ZFS pools and LVM volume groups the host has, with the safe actions. */
export function PoolsSection({ view, hostId, reload }: { view: AdminView; hostId: number; reload: () => void }) {
  const confirm = useConfirm();
  const [error, setError] = useState("");
  const [done, setDone] = useState("");
  const [busy, setBusy] = useState(false);
  const [adding, setAdding] = useState<Pool | null>(null);
  const [spare, setSpare] = useState("");
  const [replacing, setReplacing] = useState<Pool | null>(null);
  const [readding, setReadding] = useState<{ pool: Pool; drive: FormerMember } | null>(null);
  const [typed, setTyped] = useState("");
  const [full, setFull] = useState(false);
  const pools = view.pools ?? [];
  const free = view.pool_candidates ?? [];
  const run = async (pool: Pool, op: string, device: string | undefined, title: string, body: ReactNode, label: string, danger = false) => {
    if (!(await confirm({ title, body, confirmLabel: label, danger }))) return;
    await act(pool, op, device, label);
  };
  const act = async (pool: Pool, op: string, device: string | undefined, label: string, extra: Record<string, unknown> = {}) => {
    setBusy(true);
    setError("");
    setDone("");
    try {
      await api.post(`/api/hosts/${hostId}/pools/action`, { pool: pool.id, op, device, ...extra });
      setDone(`${label}: done on ${pool.name}.`);
      reload();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  const check = (p: Pool) => run(p, p.type === "zfs" ? "scrub" : "check", undefined, `${p.type === "zfs" ? "Scrub" : "Check"} ${p.name}?`,
    "Reads every drive of the pool and verifies it. It slows the pool down while it runs, and you can stop it.", p.type === "zfs" ? "Scrub" : "Check");
  const stop = (p: Pool) => run(p, "stop-sync", undefined, `Stop the running ${p.sync.action} on ${p.name}?`,
    p.state === "rebuilding"
      ? "This is a rebuild. Stopping it leaves the pool without its redundancy until it is started again."
      : "The check or scrub stops where it is.", "Stop", p.state === "rebuilding");
  const pull = (p: Pool, m: PoolMember) => run(p, "fail-remove", m.name, `Take ${m.name} out of ${p.name}?`,
    `${m.name} is marked faulty and removed from ${p.name}. The pool runs without it (degraded) until a replacement is added. Only do this to a drive that has failed or that you are replacing.`, "Take out", true);
  const offline = (p: Pool, m: PoolMember) => run(p, m.state === "OFFLINE" ? "online" : "offline", m.name,
    `${m.state === "OFFLINE" ? "Bring online" : "Take offline"} ${m.name}?`,
    m.state === "OFFLINE" ? "The drive rejoins the pool and is brought up to date." : `${p.name} runs without ${m.name} until it is brought back online.`,
    m.state === "OFFLINE" ? "Bring online" : "Take offline", m.state !== "OFFLINE");
  return (
    <Stack spacing={2}>
      <Typography variant="body2" color="text.secondary">
        The RAID arrays, ZFS pools and LVM volume groups on this host, read with the rest of its facts (every 5 minutes; Refresh above reads now).
        Degraded, failed and rebuilding pools also raise an alert on the dashboard. LVM groups are shown only; the other actions ask to confirm first.
      </Typography>
      {error && <Alert severity="error" onClose={() => setError("")}>{error}</Alert>}
      {done && <Alert severity="success" onClose={() => setDone("")}>{done}</Alert>}
      {pools.map((p) => {
        const pct = p.size && p.used != null ? Math.min(100, (100 * p.used) / p.size) : null;
        const working = p.sync.action !== "idle";
        return (
          <Paper key={p.id} variant="outlined" sx={{ p: 2 }}>
            <Stack spacing={1.5}>
              <Stack direction="row" spacing={1} useFlexGap sx={{ alignItems: "center", flexWrap: "wrap" }}>
                <Typography variant="h6" sx={{ fontFamily: "monospace" }}>{p.name}</Typography>
                <Chip size="small" label={POOL_KIND[p.type]} />
                {p.level && <Chip size="small" variant="outlined" label={p.level} />}
                <Chip size="small" color={POOL_COLOR[p.state] ?? "default"} label={p.state} />
                {p.errors > 0 && <Chip size="small" color="warning" label={`${p.errors} drive error${p.errors === 1 ? "" : "s"}`} />}
              </Stack>
              <Facts rows={[
                ["Size", p.size ? `${gb(p.size)}${p.used != null ? ` · ${gb(p.used)} used` : ""}${p.free != null ? ` · ${gb(p.free)} free` : ""}` : "—"],
                ...(p.mounts.length ? [["Mounted at", p.mounts.join(", ")] as [string, ReactNode]] : []),
                ...(p.uuid ? [["UUID", <code key="u">{p.uuid}</code>] as [string, ReactNode]] : []),
                ...(p.type === "zfs" && p.data_errors ? [["Data errors", p.data_errors] as [string, ReactNode]] : []),
              ]} />
              {pct != null && <LinearProgress variant="determinate" value={pct} color={pct >= 90 ? "error" : pct >= 80 ? "warning" : "primary"} />}
              {working && (
                <Box>
                  <Typography variant="body2">
                    {p.sync.action === "check" || p.sync.action === "scrub" ? "Checking" : p.sync.action === "repair" ? "Repairing" : "Rebuilding"}
                    {p.sync.percent != null ? `: ${p.sync.percent.toFixed(1)}% done` : ""}{p.sync.speed ? ` · ${(p.sync.speed / 1024).toFixed(0)} MB/s` : ""}
                  </Typography>
                  {p.sync.percent != null && <LinearProgress variant="determinate" value={p.sync.percent} />}
                  {p.sync.text && <Typography variant="caption" color="text.secondary">{p.sync.text}</Typography>}
                </Box>
              )}
              <Table size="small">
                <TableHead><TableRow><TableCell>Drive</TableCell><TableCell>Serial</TableCell><TableCell>Role</TableCell><TableCell>State</TableCell><TableCell>Errors</TableCell><TableCell /></TableRow></TableHead>
                <TableBody>
                  {p.members.map((m) => (
                    <TableRow key={m.name}>
                      <TableCell sx={{ fontFamily: "monospace", pl: 2 + (m.depth ?? 0) * 2 }}>{m.name}</TableCell>
                      <TableCell title={m.model ?? ""}>{m.serial || "—"}</TableCell>
                      <TableCell>{m.role}{m.slot && m.slot !== "none" ? ` (slot ${m.slot})` : ""}</TableCell>
                      <TableCell><Chip size="small" label={m.state} color={/faulty|FAULT|UNAVAIL|missing|REMOVED/.test(m.state) ? "error" : /OFFLINE|DEGRADED/.test(m.state) ? "warning" : "default"} /></TableCell>
                      <TableCell>{m.errors || "—"}</TableCell>
                      <TableCell align="right">
                        {p.type === "mdadm" && p.actions.includes("fail-remove") && (
                          <Button size="small" color="warning" disabled={busy} onClick={() => pull(p, m)}
                            title={m.role === "faulty" ? "Remove this failed drive from the pool" : "Detach this drive from the pool (asks first)"}>{m.role === "faulty" ? "Remove" : "Take out"}</Button>
                        )}
                        {p.type === "zfs" && m.name.startsWith("/") && (
                          <Button size="small" color="warning" disabled={busy} onClick={() => offline(p, m)}
                            title={m.state === "OFFLINE" ? "Put this drive back in service" : "Stop using this drive without removing it"}>{m.state === "OFFLINE" ? "Bring online" : "Take offline"}</Button>
                        )}
                      </TableCell>
                    </TableRow>
                  ))}
                  {(p.missing ?? []).map((s) => (
                    <TableRow key={`slot${s.slot}`} sx={{ bgcolor: (th) => alpha(th.palette.error.main, 0.08) }}>
                      <TableCell sx={{ fontFamily: "monospace", pl: 2 }}>(empty)</TableCell>
                      <TableCell>—</TableCell>
                      <TableCell>slot {s.slot}</TableCell>
                      <TableCell><Chip size="small" color="error" label="missing" /></TableCell>
                      <TableCell>—</TableCell><TableCell />
                    </TableRow>
                  ))}
                  {(p.former_members ?? []).map((f) => (
                    <TableRow key={f.name} sx={{ bgcolor: (th) => alpha(th.palette.warning.main, 0.1) }}>
                      <TableCell sx={{ fontFamily: "monospace", pl: 2 }}>{f.name}</TableCell>
                      <TableCell title={f.model}>{f.serial || "—"}</TableCell>
                      <TableCell>was in this array</TableCell>
                      <TableCell><Chip size="small" color="warning" label="out of the array" /></TableCell>
                      <TableCell>—</TableCell><TableCell />
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
              {(p.missing?.length || p.former_members?.length) ? (
                <Alert severity="error">
                  {p.missing?.length ? `${p.missing.length} drive${p.missing.length === 1 ? " is" : "s are"} missing (slot ${p.missing.map((s) => s.slot).join(", ")}). ` : ""}
                  {p.former_members?.length ? `${p.former_members.map((f) => `${f.name}${f.serial ? ` (serial ${f.serial}${f.model ? `, ${f.model}` : ""})` : ""}`).join(", ")} still carr${p.former_members.length === 1 ? "ies" : "y"} this array's label but ${p.former_members.length === 1 ? "is" : "are"} not in it: ${p.former_members.length === 1 ? "that is" : "those are"} the drive${p.former_members.length === 1 ? "" : "s"} that ${p.former_members.length === 1 ? "was" : "were"} failed out; match the serial to the label on the drive before pulling it. ` : "A drive that is gone entirely can only be told by the serial numbers of the others. "}
                  {p.at_risk?.length ? `Until it is replaced and rebuilt, ${p.at_risk.join(", ")} holds the only copy of what that slot mirrored.` : ""}
                </Alert>
              ) : null}
              {p.logical_volumes && p.logical_volumes.length > 0 && (
                <Typography variant="body2" color="text.secondary">Logical volumes: {p.logical_volumes.map((l) => `${l.name} (${gb(l.size)})`).join(", ")}</Typography>
              )}
              {p.actions.length === 0 ? <Typography variant="body2" color="text.secondary">Shown only: LABaPe doesn&apos;t change LVM groups.</Typography> : (
                <Stack direction="row" spacing={1} useFlexGap sx={{ flexWrap: "wrap" }}>
                  {working
                    ? <Button size="small" variant="outlined" disabled={busy} onClick={() => stop(p)} title="Stop the check or scrub that is running">Stop</Button>
                    : <Button size="small" variant="outlined" disabled={busy} onClick={() => check(p)}
                      title={p.type === "zfs" ? "Start a scrub: read every block and repair what is wrong" : "Start a check of this array"}>{p.type === "zfs" ? "Scrub" : "Check"}</Button>}
                  {p.type === "mdadm" && !working && p.actions.includes("resync") && (
                    <Button size="small" variant="outlined" disabled={busy} title="Rewrite mismatched blocks from the other drives"
                      onClick={() => run(p, "resync", undefined, `Repair ${p.name}?`, "Starts a repair pass: blocks that differ between the drives are rewritten. It reads every drive and slows the array while it runs.", "Repair")}>Repair</Button>
                  )}
                  {p.actions.includes("re-add") && (p.former_members ?? []).map((f) => (
                    <Button key={f.name} size="small" variant="outlined" color={f.assessment.ok ? "primary" : "warning"} disabled={busy}
                      onClick={() => { setReadding({ pool: p, drive: f }); setTyped(""); setFull(false); }}
                      title={f.assessment.ok ? "Put this drive back into the array (checks its health first)" : `Put this drive back anyway: ${f.assessment.reasons.join("; ")}`}>
                      Re-add {f.name}…
                    </Button>
                  ))}
                  {p.replace && (
                    <Button size="small" variant="contained" color="warning" disabled={busy} onClick={() => setReplacing(p)}
                      title="Guided: which drive to pull, prepare the new one, add it and rebuild">Replace a drive…</Button>
                  )}
                  {p.actions.includes("add-spare") && (
                    <Button size="small" variant="outlined" disabled={busy || free.length === 0} onClick={() => { setAdding(p); setSpare(free[0]?.path ?? ""); }}
                      title={free.length === 0 ? "No free drive: every disk is mounted, has a filesystem or is in a pool" : "Add a free drive as a spare or replacement"}>Add a drive…</Button>
                  )}
                </Stack>
              )}
            </Stack>
          </Paper>
        );
      })}
      {replacing && <ReplaceDialog pool={replacing} hostId={hostId} onClose={() => setReplacing(null)}
        onDone={(m) => { setReplacing(null); setDone(m); reload(); }} />}
      <Dialog open={Boolean(readding)} onClose={() => setReadding(null)} maxWidth="sm" fullWidth>
        <DialogTitle>Re-add {readding?.drive.name} to {readding?.pool.name}</DialogTitle>
        <DialogContent>
          {readding && (
            <Stack spacing={2} sx={{ pt: 1 }}>
              <Typography variant="body2">
                {readding.drive.name}{readding.drive.serial ? ` (serial ${readding.drive.serial}${readding.drive.model ? `, ${readding.drive.model}` : ""})` : ""} was
                failed out of {readding.pool.name}. Re-adding it copies back only what changed while it was out (the array&apos;s write-intent bitmap),
                then the array has its redundancy again. If the array has no bitmap or mdadm refuses, choose the full rebuild.
              </Typography>
              {readding.drive.assessment.ok
                ? <Alert severity="success">The drive&apos;s own health reading is clean: it passed, with no reallocated, pending or uncorrectable sectors.</Alert>
                : (
                  <Alert severity="error">
                    The drive didn&apos;t pass the check: {readding.drive.assessment.reasons.join("; ")}. A drive like this is likely to fail again, and the rebuild reads every
                    sector of {readding.pool.at_risk?.length ? `${readding.pool.at_risk.join(", ")}, which holds the only copy` : "the drives left in the array"}.
                    Replacing it is safer.
                  </Alert>
                )}
              <FormControlLabel control={<Checkbox checked={full} onChange={(e) => setFull(e.target.checked)} />}
                label="Full rebuild: wipe the drive's old array label and copy everything back" />
              {!readding.drive.assessment.ok && (
                <Field label={`Type ${readding.drive.name} to re-add it anyway`} value={typed} onChange={(e) => setTyped(e.target.value)} />
              )}
            </Stack>
          )}
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setReadding(null)}>Cancel</Button>
          <Button variant="contained" color={readding?.drive.assessment.ok ? "primary" : "error"}
            disabled={!readding || (!readding.drive.assessment.ok && typed !== readding.drive.name)} onClick={() => {
              const r = readding;
              setReadding(null);
              if (r) act(r.pool, "re-add", r.drive.name, `Re-add ${r.drive.name}`, { full, confirm: typed || undefined });
            }}>Re-add</Button>
        </DialogActions>
      </Dialog>
      <Dialog open={Boolean(adding)} onClose={() => setAdding(null)} maxWidth="sm" fullWidth>
        <DialogTitle>Add a drive to {adding?.name}</DialogTitle>
        <DialogContent>
          <Stack spacing={2} sx={{ pt: 1 }}>
            <Typography variant="body2">Only drives that are not mounted, have no filesystem and are in no pool are offered. Whatever is on the drive is overwritten. In a degraded pool it starts rebuilding onto the drive.</Typography>
            <Field select label="Drive" value={spare} onChange={(e) => setSpare(e.target.value)}>
              {free.map((d) => <MenuItem key={d.path} value={d.path}>{d.path} · {gb(d.size)}{d.model ? ` · ${d.model}` : ""}</MenuItem>)}
            </Field>
          </Stack>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setAdding(null)}>Cancel</Button>
          <Button variant="contained" color="warning" disabled={!spare} onClick={() => {
            const p = adding;
            setAdding(null);
            if (p) run(p, "add-spare", spare, `Add ${spare} to ${p.name}?`, `${spare} becomes part of ${p.name}; anything on it is overwritten.`, "Add the drive", true);
          }}>Add…</Button>
        </DialogActions>
      </Dialog>
    </Stack>
  );
}
