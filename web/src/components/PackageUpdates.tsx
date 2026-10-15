// Catalog → Updates (U1, question 170): download sites checked weekly; new versions go into Files →
// Installers and the newest N are kept per source. Admins add, change, check and remove sources.
import { useEffect, useState } from "react";
import {
  Alert, Button, Checkbox, Dialog, DialogActions, DialogContent, DialogTitle, FormControlLabel, IconButton, MenuItem,
  Stack, Table, TableBody, TableCell, TableHead, TableRow, TextField, Tooltip, Typography,
} from "@mui/material";
import DeleteIcon from "@mui/icons-material/Delete";
import EditIcon from "@mui/icons-material/Edit";
import RefreshIcon from "@mui/icons-material/Refresh";
import { api } from "../api";

interface Source {
  id?: number; name: string; display_name: string; kind: string; params: Record<string, string>; targets: string[];
  keep: number; enabled: boolean; last_checked_at?: string | null; last_version?: string; last_error?: string;
  history?: { version: string; name: string; at: string }[];
}
interface Schedule { enabled: boolean; day: string; hour: number }
interface Listing { sources: Source[]; schedule: Schedule; kinds: string[]; running_job: number | null }

const FIELDS: Record<string, { key: string; label: string; help: string }[]> = {
  dotnet: [{ key: "channel", label: "Channel", help: "8.0, 10.0, ..." },
           { key: "product", label: "Product", help: "runtime, hosting, aspnetcore or sdk" },
           { key: "rid", label: "Runtime ID", help: "win-x64, linux-x64, ..." }],
  github: [{ key: "repo", label: "Repository", help: "owner/name" },
           { key: "asset", label: "Asset", help: "File name pattern, e.g. App-*-Win64.msi" }],
  url: [{ key: "url", label: "Address", help: "https://...; a fixed download link" },
        { key: "version_regex", label: "Version pattern", help: "Optional: a regex whose group 1 is the version, in the address it redirects to" }],
  page: [{ key: "page", label: "Page", help: "https://... that links the newest version" },
         { key: "link_regex", label: "Link pattern", help: "A regex: group 1 the link, group 2 the version" }],
  chocolatey: [{ key: "package", label: "Package", help: "Its Chocolatey id; the newest .nupkg" }],
};
const DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"];
const blank: Source = { name: "", display_name: "", kind: "github", params: {}, targets: ["windows"], keep: 3, enabled: true };

export default function PackageUpdates({ isAdmin }: { isAdmin: boolean }) {
  const [data, setData] = useState<Listing | null>(null);
  const [draft, setDraft] = useState<Source | null>(null);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const [err, setErr] = useState("");
  const load = () => api.get<Listing>("/api/package-updates").then(setData, (e) => setMsg({ ok: false, text: e.message }));
  useEffect(() => { load(); }, []);
  if (!data) return null;

  const check = (source_id?: number) => api.post<{ job_id: number }>("/api/package-updates/check", { source_id })
    .then((r) => { setMsg({ ok: true, text: `Checking now (job ${r.job_id}); the list updates when it's done.` }); load(); },
      (e) => setMsg({ ok: false, text: e.message }));
  const save = () => (draft!.id ? api.put(`/api/package-updates/${draft!.id}`, draft) : api.post("/api/package-updates", draft))
    .then(() => { setDraft(null); load(); }, (e) => setErr(e.message));
  const remove = (s: Source) => {
    if (!window.confirm(`Remove ${s.name}? The files it downloaded stay in Files → Installers.`)) return;
    api.del(`/api/package-updates/${s.id}`).then(load, (e) => setMsg({ ok: false, text: e.message }));
  };
  const saveSchedule = (sch: Schedule) => api.put("/api/admin/settings/package_updates", sch)
    .then(() => { setMsg({ ok: true, text: "Schedule saved." }); load(); }, (e) => setMsg({ ok: false, text: e.message }));
  const sch = data.schedule;

  return (
    <Stack spacing={2}>
      <Typography variant="body2" color="text.secondary">
        Download sites checked {sch.enabled ? `every ${sch.day} at ${String(sch.hour).padStart(2, "0")}:00 UTC` : "only when asked"}.
        A new version goes into Files → Installers (its hash checked where the site publishes one), and only the newest few of
        each are kept.
      </Typography>
      {msg && <Alert severity={msg.ok ? "success" : "error"} onClose={() => setMsg(null)}>{msg.text}</Alert>}
      {isAdmin && (
        <Stack direction="row" spacing={2} sx={{ alignItems: "center", flexWrap: "wrap" }}>
          <FormControlLabel label="Weekly" control={<Checkbox checked={sch.enabled} onChange={(e) => saveSchedule({ ...sch, enabled: e.target.checked })} />} />
          <TextField select size="small" label="Day" value={sch.day} sx={{ width: 110 }} onChange={(e) => saveSchedule({ ...sch, day: e.target.value })}>
            {DAYS.map((d) => <MenuItem key={d} value={d}>{d}</MenuItem>)}
          </TextField>
          <TextField select size="small" label="Hour (UTC)" value={sch.hour} sx={{ width: 120 }} onChange={(e) => saveSchedule({ ...sch, hour: Number(e.target.value) })}>
            {Array.from({ length: 24 }, (_, h) => <MenuItem key={h} value={h}>{String(h).padStart(2, "0")}:00</MenuItem>)}
          </TextField>
          <Button variant="outlined" size="small" disabled={Boolean(data.running_job)} onClick={() => check()}
            title="Check every enabled source now">{data.running_job ? `Checking (job ${data.running_job})` : "Check all now"}</Button>
          <Button size="small" onClick={() => { setErr(""); setDraft({ ...blank }); }} title="Add a download site">Add a source</Button>
        </Stack>
      )}
      <Table size="small">
        <TableHead>
          <TableRow>
            <TableCell>Source</TableCell><TableCell>Kind</TableCell><TableCell>Newest</TableCell><TableCell>Kept</TableCell>
            <TableCell>Last check</TableCell><TableCell>Problem</TableCell><TableCell />
          </TableRow>
        </TableHead>
        <TableBody>
          {data.sources.map((s) => (
            <TableRow key={s.id} sx={{ opacity: s.enabled ? 1 : 0.6 }}>
              <TableCell title={JSON.stringify(s.params)}>{s.display_name || s.name}<Typography variant="caption" component="div" color="text.secondary">{s.name}{s.enabled ? "" : " (off)"}</Typography></TableCell>
              <TableCell>{s.kind}</TableCell>
              <TableCell>{s.last_version || "—"}</TableCell>
              <TableCell title={(s.history ?? []).map((h) => `${h.version}: ${h.name}`).join("\n")}>{(s.history ?? []).length} of {s.keep}</TableCell>
              <TableCell>{s.last_checked_at ? new Date(s.last_checked_at).toLocaleString() : "never"}</TableCell>
              <TableCell sx={{ maxWidth: 320 }}><Typography variant="caption" color="error">{s.last_error}</Typography></TableCell>
              <TableCell sx={{ whiteSpace: "nowrap" }}>
                {isAdmin && <>
                  <Tooltip title="Check this source now"><span><IconButton size="small" disabled={Boolean(data.running_job)} onClick={() => check(s.id)}><RefreshIcon fontSize="small" /></IconButton></span></Tooltip>
                  <Tooltip title="Change it"><IconButton size="small" onClick={() => { setErr(""); setDraft({ ...s }); }}><EditIcon fontSize="small" /></IconButton></Tooltip>
                  <Tooltip title="Remove it (its files stay)"><IconButton size="small" onClick={() => remove(s)}><DeleteIcon fontSize="small" /></IconButton></Tooltip>
                </>}
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
      <Dialog open={!!draft} onClose={() => setDraft(null)} maxWidth="sm" fullWidth>
        <DialogTitle>{draft?.id ? `Change ${draft.name}` : "Add a source"}</DialogTitle>
        {draft && <DialogContent>
          <Stack spacing={2} sx={{ mt: 1 }}>
            {err && <Alert severity="error">{err}</Alert>}
            <TextField size="small" label="Name" value={draft.name} helperText="lowercase, digits and dashes"
              onChange={(e) => setDraft({ ...draft, name: e.target.value })} />
            <TextField size="small" label="Display name" value={draft.display_name} onChange={(e) => setDraft({ ...draft, display_name: e.target.value })} />
            <TextField select size="small" label="Kind" value={draft.kind} onChange={(e) => setDraft({ ...draft, kind: e.target.value, params: {} })}>
              {data.kinds.map((k) => <MenuItem key={k} value={k}>{k}</MenuItem>)}
            </TextField>
            {(FIELDS[draft.kind] ?? []).map((f) => (
              <TextField key={f.key} size="small" label={f.label} helperText={f.help} value={draft.params[f.key] ?? ""}
                onChange={(e) => setDraft({ ...draft, params: { ...draft.params, [f.key]: e.target.value } })} />
            ))}
            <TextField size="small" label="OS targets" value={draft.targets.join(", ")} helperText="Where the installers may be used: windows, linux, or finer (Files' targets)"
              onChange={(e) => setDraft({ ...draft, targets: e.target.value.split(/[\s,]+/).filter(Boolean) })} />
            <TextField size="small" type="number" label="Versions to keep" value={draft.keep} sx={{ width: 160 }}
              onChange={(e) => setDraft({ ...draft, keep: Number(e.target.value) })} />
            <FormControlLabel label="Checked weekly" control={<Checkbox checked={draft.enabled} onChange={(e) => setDraft({ ...draft, enabled: e.target.checked })} />} />
          </Stack>
        </DialogContent>}
        <DialogActions>
          <Button onClick={() => setDraft(null)}>Cancel</Button>
          <Button variant="contained" onClick={save}>{draft?.id ? "Save" : "Add"}</Button>
        </DialogActions>
      </Dialog>
    </Stack>
  );
}
