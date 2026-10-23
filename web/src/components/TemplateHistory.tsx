// A template's history (design §73): its versions, what changed between two of them, and going back to one (which makes
// that content a new version: nothing is rewritten).
import { useCallback, useEffect, useState } from "react";
import {
  Alert, Button, Chip, Dialog, DialogActions, DialogContent, DialogTitle, MenuItem, Stack, Table, TableBody, TableCell, TableHead,
  TableRow, TextField, Typography,
} from "@mui/material";
import { api, fmtTime } from "../api";
import { ActionButton } from "./Confirm";
import type { EnvTemplateInfo } from "./TemplatePicker";

interface Version { version: number; created_at: string; created_by: string; current: boolean; summary: string; params: number; parts: number }
interface Versions { current: number; can_restore: boolean; versions: Version[] }
interface Part { template: string; version: number | null; label: string; overrides: Record<string, Record<string, unknown>> }
interface Diff {
  from: number; to: number; same: boolean;
  groups: { added: string[]; removed: string[]; changed: { name: string; fields: { field: string; from: unknown; to: unknown }[] }[] };
  params: { added: string[]; removed: string[]; changed: { key: string; from: { label: string; default: unknown }; to: { label: string; default: unknown } }[] };
  parts: { added: string[]; removed: string[]; changed: { prefix: string; from: Part; to: Part }[] };
  timezone: { from: string | null; to: string | null } | null;
}

const show = (v: unknown) => (v === null || v === undefined || v === "" ? "none" : typeof v === "object" ? JSON.stringify(v) : String(v));
const partText = (p: Part) => `${p.template}, ${p.version === null ? "latest" : `v${p.version}`}${p.label ? `, label ${p.label}` : ""}${Object.keys(p.overrides).length ? `, values ${JSON.stringify(p.overrides)}` : ""}`;

function DiffView({ d }: { d: Diff }) {
  if (d.same) return <Typography variant="body2" color="text.secondary">Versions {d.from} and {d.to} hold the same content.</Typography>;
  const lines: string[] = [
    ...d.groups.added.map((n) => `Host group ${n} was added`),
    ...d.groups.removed.map((n) => `Host group ${n} was removed`),
    ...d.groups.changed.map((c) => `${c.name}: ${c.fields.map((f) => `${f.field} ${show(f.from)} → ${show(f.to)}`).join(", ")}`),
    ...d.params.added.map((k) => `Value ${k} is asked for now`),
    ...d.params.removed.map((k) => `Value ${k} is no longer asked for`),
    ...d.params.changed.map((c) => `Value ${c.key}: label "${c.from.label}" → "${c.to.label}", default ${show(c.from.default)} → ${show(c.to.default)}`),
    ...d.parts.added.map((p) => `Part ${p} was added`),
    ...d.parts.removed.map((p) => `Part ${p} was removed`),
    ...d.parts.changed.map((c) => `Part ${c.prefix}: ${partText(c.from)} → ${partText(c.to)}`),
    ...(d.timezone ? [`Time zone ${show(d.timezone.from)} → ${show(d.timezone.to)}`] : []),
  ];
  return <Stack component="ul" spacing={0.5} sx={{ m: 0, pl: 3 }}>{lines.map((l, i) => <Typography key={i} component="li" variant="body2">{l}</Typography>)}</Stack>;
}

export default function TemplateHistory({ t, onClose, onChanged }: { t: EnvTemplateInfo; onClose: () => void; onChanged: () => void }) {
  const [data, setData] = useState<Versions | null>(null);
  const [from, setFrom] = useState<number>(0);
  const [to, setTo] = useState<number>(0);
  const [diff, setDiff] = useState<Diff | null>(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const load = useCallback(() => {
    api.get<Versions>(`/api/env-templates/${t.id}/versions`).then((v) => {
      setData(v);
      setTo(v.current);
      setFrom(Math.max(1, v.current - 1));
    }, (e) => setError((e as Error).message));
  }, [t.id]);
  useEffect(load, [load]);
  useEffect(() => {
    if (!from || !to) return;
    api.get<Diff>(`/api/env-templates/${t.id}/diff?from=${from}&to=${to}`).then(setDiff, (e) => setError((e as Error).message));
  }, [t.id, from, to]);
  const restore = async (version: number) => {
    setError("");
    try {
      await api.post(`/api/env-templates/${t.id}/restore`, { version });
      setNotice(`Restored version ${version} as a new version.`);
      load();
      onChanged();
    } catch (e) { setError((e as Error).message); }
  };
  return (
    <Dialog open onClose={onClose} maxWidth="md" fullWidth>
      <DialogTitle>History of {t.name}</DialogTitle>
      <DialogContent>
        <Stack spacing={2} sx={{ pt: 1 }}>
          {error && <Alert severity="error" onClose={() => setError("")}>{error}</Alert>}
          {notice && <Alert severity="success" onClose={() => setNotice("")}>{notice}</Alert>}
          {data && (
            <>
              <Table size="small">
                <TableHead><TableRow><TableCell>Version</TableCell><TableCell>Saved</TableCell><TableCell>By</TableCell><TableCell>What it held</TableCell><TableCell /></TableRow></TableHead>
                <TableBody>
                  {data.versions.map((v) => (
                    <TableRow key={v.version}>
                      <TableCell>v{v.version} {v.current && <Chip size="small" label="current" color="primary" sx={{ ml: 0.5 }} />}</TableCell>
                      <TableCell>{fmtTime(v.created_at)}</TableCell>
                      <TableCell>{v.created_by || "–"}</TableCell>
                      <TableCell>{v.summary}</TableCell>
                      <TableCell align="right" sx={{ whiteSpace: "nowrap" }}>
                        {data.can_restore && !v.current && (
                          <ActionButton size="small" tip="Make this version's groups, values and parts the template's new version (the history stays)"
                            confirm={{ title: `Restore version ${v.version}?`, body: `${t.name} takes version ${v.version}'s groups, values and parts as a new version. Its name, description and sharing stay, and no version is deleted.`, confirmLabel: "Restore" }}
                            onClick={() => restore(v.version)}>
                            Restore
                          </ActionButton>
                        )}
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
              {data.versions.length > 1 && (
                <>
                  <Stack direction="row" spacing={2} sx={{ alignItems: "center" }}>
                    <Typography variant="subtitle2">What changed</Typography>
                    <TextField select size="small" label="From" value={from} sx={{ width: 110 }} onChange={(e) => setFrom(Number(e.target.value))}>
                      {data.versions.map((v) => <MenuItem key={v.version} value={v.version}>v{v.version}</MenuItem>)}
                    </TextField>
                    <TextField select size="small" label="To" value={to} sx={{ width: 110 }} onChange={(e) => setTo(Number(e.target.value))}>
                      {data.versions.map((v) => <MenuItem key={v.version} value={v.version}>v{v.version}</MenuItem>)}
                    </TextField>
                  </Stack>
                  {diff && <DiffView d={diff} />}
                </>
              )}
            </>
          )}
        </Stack>
      </DialogContent>
      <DialogActions><Button onClick={onClose}>Close</Button></DialogActions>
    </Dialog>
  );
}
