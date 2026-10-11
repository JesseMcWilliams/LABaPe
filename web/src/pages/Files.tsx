import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  Alert, Box, Button, Checkbox, Chip, Dialog, DialogActions, DialogContent, DialogTitle, FormControlLabel,
  LinearProgress, MenuItem, Stack, Tab, Table, TableBody, TableCell, TableHead, TableRow, Tabs, Tooltip,
  Typography,
} from "@mui/material";
import UploadIcon from "@mui/icons-material/Upload";
import NotesIcon from "@mui/icons-material/StickyNote2Outlined";
import * as tus from "tus-js-client";
import { api, fmtTime, KvmHost, Template } from "../api";
import { hasRole, useAuth } from "../App";
import { ErrorSummary, Field, FieldErrors, fromApiError } from "../forms";

// File manager (design §9, §21.3): ISOs, installers, other files, and the
// template library. Admins and file managers upload; template editors
// build, refresh and edit templates; everyone can browse.
interface StoredFile {
  id: number; category: string; name: string; path: string; size: number; mtime: string | null; sha256: string;
  os_key: string; is_default: boolean; description: string; notes: string; uploaded_by: string;
  uploaded_at: string | null;
}

const OS_KEYS = ["rocky9", "ubuntu_lts", "ubuntu_26", "debian_latest", "windows_server_2019", "windows_server_2022",
  "windows_server_2025", "windows_11", "windows_10"];
const CATS = [{ key: "isos", label: "ISOs" }, { key: "installers", label: "Installers" }, { key: "files", label: "Other files" }];

const size = (n: number) => n >= 2 ** 30 ? `${(n / 2 ** 30).toFixed(1)} GB` : n >= 2 ** 20 ? `${(n / 2 ** 20).toFixed(1)} MB`
  : `${Math.ceil(n / 1024)} KB`;

function Uploader({ category, onDone }: { category: string; onDone: () => void }) {
  const input = useRef<HTMLInputElement>(null);
  const [progress, setProgress] = useState<{ name: string; pct: number } | null>(null);
  const [error, setError] = useState("");
  const [upload, setUpload] = useState<tus.Upload | null>(null);

  const start = async (file: File) => {
    setError("");
    const up = new tus.Upload(file, {
      endpoint: "/api/files/tus",
      chunkSize: 32 * 2 ** 20,
      retryDelays: [0, 3000, 10000, 30000],
      removeFingerprintOnSuccess: true,
      metadata: { filename: file.name, category },
      onProgress: (sent, total) => setProgress({ name: file.name, pct: total ? (sent / total) * 100 : 0 }),
      onSuccess: () => { setProgress(null); setUpload(null); onDone(); },
      onError: (err) => {
        setProgress(null);
        setUpload(null);
        // The server's JSON explains refusals (duplicate, name taken, bad name).
        const body = (err as tus.DetailedError).originalResponse?.getBody();
        let msg = err.message;
        try { if (body) msg = JSON.parse(body).detail ?? msg; } catch { /* not JSON */ }
        setError(msg);
      },
    });
    // Resume an earlier attempt at the same file, if the browser remembers one.
    const previous = await up.findPreviousUploads();
    if (previous.length) up.resumeFromPreviousUpload(previous[0]);
    setUpload(up);
    up.start();
  };

  return (
    <Stack spacing={1}>
      <Stack direction="row" spacing={2} sx={{ alignItems: "center" }}>
        <input ref={input} type="file" hidden onChange={(e) => { const f = e.target.files?.[0]; if (f) start(f); e.target.value = ""; }} />
        <Button variant="contained" startIcon={<UploadIcon />} disabled={Boolean(progress)} onClick={() => input.current?.click()}>
          Upload
        </Button>
        {progress && (
          <Box sx={{ flexGrow: 1 }}>
            <Typography variant="caption">{progress.name}: {progress.pct.toFixed(0)}% (resumes if interrupted)</Typography>
            <LinearProgress variant="determinate" value={progress.pct} />
          </Box>
        )}
        {upload && <Button color="error" onClick={() => { upload.abort(true); setProgress(null); setUpload(null); }}>Cancel</Button>}
      </Stack>
      {error && <Alert severity="error" onClose={() => setError("")}>{error}</Alert>}
    </Stack>
  );
}

function FileEditor({ file, onClose }: { file: StoredFile; onClose: (changed: boolean) => void }) {
  const [form, setForm] = useState({ description: file.description, notes: file.notes, os_key: file.os_key, is_default: file.is_default });
  const [errs, setErrs] = useState<{ summary: string; fields: FieldErrors }>({ summary: "", fields: {} });
  const save = async () => {
    try {
      await api.put(`/api/files/${file.id}`, form);
      onClose(true);
    } catch (e) {
      setErrs(fromApiError(e));
    }
  };
  return (
    <Dialog open onClose={() => onClose(false)} maxWidth="sm" fullWidth>
      <DialogTitle>{file.name}</DialogTitle>
      <DialogContent>
        <Stack spacing={1} sx={{ mt: 1 }}>
          <ErrorSummary summary={errs.summary} fields={errs.fields} />
          {file.category === "isos" && (
            <Stack direction="row" spacing={2} sx={{ alignItems: "flex-start" }}>
              <Field select label="Installs OS" value={form.os_key} sx={{ minWidth: 220 }} error={errs.fields.os_key}
                onChange={(e) => setForm({ ...form, os_key: e.target.value })} help="Which OS key builds from this ISO">
                <MenuItem value="">(none)</MenuItem>
                {OS_KEYS.map((k) => <MenuItem key={k} value={k}>{k}</MenuItem>)}
              </Field>
              <FormControlLabel label="Default for this OS" control={<Checkbox checked={form.is_default}
                onChange={(e) => setForm({ ...form, is_default: e.target.checked })} />} />
            </Stack>
          )}
          <Field label="Description" value={form.description} fullWidth onChange={(e) => setForm({ ...form, description: e.target.value })} />
          <Field label="Notes" value={form.notes} fullWidth multiline minRows={3} help="Quirks, after-tasks, licensing"
            onChange={(e) => setForm({ ...form, notes: e.target.value })} />
          <Typography variant="caption" color="text.secondary">
            {file.path} · {size(file.size)} · SHA-256 {file.sha256 || "computing…"}
          </Typography>
        </Stack>
      </DialogContent>
      <DialogActions>
        <Button onClick={() => onClose(false)}>Cancel</Button>
        <Button variant="contained" onClick={save}>Save</Button>
      </DialogActions>
    </Dialog>
  );
}

function FileTable({ category, canManage }: { category: string; canManage: boolean }) {
  const [files, setFiles] = useState<StoredFile[]>([]);
  const [editing, setEditing] = useState<StoredFile | null>(null);
  const [error, setError] = useState("");
  const load = () => api.get<StoredFile[]>(`/api/files?category=${category}`).then(setFiles, (e) => setError(e.message));
  useEffect(() => { load(); }, [category]); // eslint-disable-line react-hooks/exhaustive-deps
  const remove = async (f: StoredFile) => {
    if (!confirm(`Delete ${f.name}? The file is removed from disk.`)) return;
    try { await api.del(`/api/files/${f.id}`); load(); } catch (e) { setError((e as Error).message); }
  };
  return (
    <Stack spacing={2}>
      {canManage && <Uploader category={category} onDone={load} />}
      {error && <Alert severity="error">{error}</Alert>}
      <Table size="small">
        <TableHead>
          <TableRow>
            <TableCell>Name</TableCell>
            {category === "isos" && <TableCell>OS</TableCell>}
            <TableCell>Size</TableCell><TableCell>SHA-256</TableCell><TableCell>Description</TableCell>
            <TableCell>Added</TableCell><TableCell />
          </TableRow>
        </TableHead>
        <TableBody>
          {files.map((f) => (
            <TableRow key={f.id}>
              <TableCell>
                {f.name}
                {f.notes && <Tooltip title={<Box sx={{ whiteSpace: "pre-wrap" }}>{f.notes}</Box>}><NotesIcon fontSize="small" sx={{ ml: 0.5, verticalAlign: "middle" }} /></Tooltip>}
              </TableCell>
              {category === "isos" && (
                <TableCell>{f.os_key || "—"}{f.is_default && <Chip size="small" label="default" color="primary" sx={{ ml: 1 }} />}</TableCell>
              )}
              <TableCell sx={{ whiteSpace: "nowrap" }}>{size(f.size)}</TableCell>
              <TableCell sx={{ fontFamily: "monospace", fontSize: 12 }}>
                {f.sha256 ? <Tooltip title={f.sha256}><span>{f.sha256.slice(0, 12)}…</span></Tooltip> : "computing…"}
              </TableCell>
              <TableCell>{f.description}</TableCell>
              <TableCell sx={{ whiteSpace: "nowrap" }}>{f.uploaded_by ? `${f.uploaded_by}, ${fmtTime(f.uploaded_at)}` : `found ${fmtTime(f.mtime)}`}</TableCell>
              <TableCell sx={{ whiteSpace: "nowrap" }}>
                {canManage && <Button size="small" onClick={() => setEditing(f)}>Edit</Button>}
                {canManage && <Button size="small" color="error" onClick={() => remove(f)}>Delete</Button>}
              </TableCell>
            </TableRow>
          ))}
          {files.length === 0 && <TableRow><TableCell colSpan={7}>No files here yet.</TableCell></TableRow>}
        </TableBody>
      </Table>
      {editing && <FileEditor file={editing} onClose={(changed) => { setEditing(null); if (changed) load(); }} />}
    </Stack>
  );
}

function TemplateEditor({ t, onClose }: { t: Template; onClose: (changed: boolean) => void }) {
  const [form, setForm] = useState({ description: t.description, notes: t.notes, os_key: t.os_key, edition: t.edition,
    min_cpu: t.min_cpu, min_memory_mb: t.min_memory_mb });
  const [errs, setErrs] = useState<{ summary: string; fields: FieldErrors }>({ summary: "", fields: {} });
  const save = async () => {
    try { await api.put(`/api/templates/${t.id}`, form); onClose(true); } catch (e) { setErrs(fromApiError(e)); }
  };
  return (
    <Dialog open onClose={() => onClose(false)} maxWidth="sm" fullWidth>
      <DialogTitle>{t.name}</DialogTitle>
      <DialogContent>
        <Stack spacing={1} sx={{ mt: 1 }}>
          <ErrorSummary summary={errs.summary} fields={errs.fields} />
          <Stack direction="row" spacing={2} useFlexGap sx={{ alignItems: "flex-start", flexWrap: "wrap" }}>
            <Field select label="OS" value={form.os_key} sx={{ minWidth: 200 }} error={errs.fields.os_key}
              onChange={(e) => setForm({ ...form, os_key: e.target.value })}>
              {OS_KEYS.map((k) => <MenuItem key={k} value={k}>{k}</MenuItem>)}
            </Field>
            <Field select label="Edition" value={form.edition} sx={{ minWidth: 140 }} onChange={(e) => setForm({ ...form, edition: e.target.value })}>
              <MenuItem value="">(n/a)</MenuItem><MenuItem value="desktop">Desktop</MenuItem><MenuItem value="core">Core</MenuItem>
            </Field>
            <Field label="Min vCPUs" type="number" value={form.min_cpu} sx={{ width: 110 }} error={errs.fields.min_cpu}
              onChange={(e) => setForm({ ...form, min_cpu: Number(e.target.value) })} />
            <Field label="Min memory (MB)" type="number" value={form.min_memory_mb} sx={{ width: 150 }} error={errs.fields.min_memory_mb}
              onChange={(e) => setForm({ ...form, min_memory_mb: Number(e.target.value) })} />
          </Stack>
          <Field label="Description" value={form.description} fullWidth onChange={(e) => setForm({ ...form, description: e.target.value })} />
          <Field label="Notes" value={form.notes} fullWidth multiline minRows={4} help="Quirks and after-tasks for anyone using this template"
            onChange={(e) => setForm({ ...form, notes: e.target.value })} />
        </Stack>
      </DialogContent>
      <DialogActions>
        <Button onClick={() => onClose(false)}>Cancel</Button>
        <Button variant="contained" onClick={save}>Save</Button>
      </DialogActions>
    </Dialog>
  );
}

function JobDialog({ title, fields, submit, onClose }: {
  title: string; fields: (errs: FieldErrors) => React.ReactNode; submit: () => Promise<{ job_id: number }>;
  onClose: () => void;
}) {
  const navigate = useNavigate();
  const [errs, setErrs] = useState<{ summary: string; fields: FieldErrors }>({ summary: "", fields: {} });
  const go = async () => {
    try { const r = await submit(); navigate(`/jobs/${r.job_id}`); } catch (e) { setErrs(fromApiError(e)); }
  };
  return (
    <Dialog open onClose={onClose} maxWidth="sm" fullWidth>
      <DialogTitle>{title}</DialogTitle>
      <DialogContent>
        <Stack spacing={1} sx={{ mt: 1 }}>
          <ErrorSummary summary={errs.summary} fields={errs.fields} />
          {fields(errs.fields)}
        </Stack>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>Cancel</Button>
        <Button variant="contained" onClick={go}>Start job</Button>
      </DialogActions>
    </Dialog>
  );
}

function Templates({ canEdit }: { canEdit: boolean }) {
  const [templates, setTemplates] = useState<Template[]>([]);
  const [hosts, setHosts] = useState<KvmHost[]>([]);
  const [editing, setEditing] = useState<Template | null>(null);
  const [refreshing, setRefreshing] = useState<Template | null>(null);
  const [newName, setNewName] = useState("");
  const [building, setBuilding] = useState(false);
  const [build, setBuild] = useState({ kvm_host_id: 0, os_key: "rocky9", name: "", core: false });
  const load = () => api.get<Template[]>("/api/templates").then(setTemplates);
  useEffect(() => {
    load();
    api.get<KvmHost[]>("/api/hosts").then((h) => { setHosts(h); if (h.length) setBuild((b) => ({ ...b, kvm_host_id: h[0].id })); });
  }, []);
  const hostName = (id: number) => hosts.find((h) => h.id === id)?.name ?? id;
  return (
    <Stack spacing={2}>
      {canEdit && (
        <Box>
          <Button variant="contained" onClick={() => setBuilding(true)}>Build a template</Button>
        </Box>
      )}
      <Table size="small">
        <TableHead>
          <TableRow>
            <TableCell>Template</TableCell><TableCell>Host</TableCell><TableCell>OS</TableCell><TableCell>Disk</TableCell>
            <TableCell>Minimums</TableCell><TableCell>Description</TableCell><TableCell />
          </TableRow>
        </TableHead>
        <TableBody>
          {templates.map((t) => (
            <TableRow key={t.id}>
              <TableCell>
                {t.name}
                {t.notes && <Tooltip title={<Box sx={{ whiteSpace: "pre-wrap" }}>{t.notes}</Box>}><NotesIcon fontSize="small" sx={{ ml: 0.5, verticalAlign: "middle" }} /></Tooltip>}
                {t.source === "inferred" && <Chip size="small" label="inferred" sx={{ ml: 1 }} />}
              </TableCell>
              <TableCell>{hostName(t.kvm_host_id)}</TableCell>
              <TableCell>{t.os_key}{t.edition ? ` (${t.edition})` : ""}</TableCell>
              <TableCell>{t.disk_gb} GB</TableCell>
              <TableCell sx={{ whiteSpace: "nowrap" }}>{t.min_cpu} vCPU · {t.min_memory_mb} MB</TableCell>
              <TableCell>{t.description}</TableCell>
              <TableCell sx={{ whiteSpace: "nowrap" }}>
                {canEdit && <Button size="small" onClick={() => setEditing(t)}>Edit</Button>}
                {canEdit && <Button size="small" onClick={() => { setRefreshing(t); setNewName(`${t.name}.1`); }}>Refresh</Button>}
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
      {editing && <TemplateEditor t={editing} onClose={(c) => { setEditing(null); if (c) load(); }} />}
      {refreshing && (
        <JobDialog title={`Refresh ${refreshing.name}`} onClose={() => setRefreshing(null)}
          submit={() => api.post(`/api/templates/${refreshing.id}/refresh`, { new_name: newName, addressing: "dhcp" })}
          fields={(errs) => (
            <>
              <Typography variant="body2">Clones the template, applies every OS update (rebooting as needed), and saves it under a new name. The original is left as it is.</Typography>
              <Field label="New template name" value={newName} onChange={(e) => setNewName(e.target.value)} error={errs.new_name} fullWidth />
            </>
          )} />
      )}
      {building && (
        <JobDialog title="Build a template" onClose={() => setBuilding(false)}
          submit={() => api.post("/api/templates/build", build)}
          fields={(errs) => (
            <Stack spacing={1}>
              <Typography variant="body2">Installs the OS from its ISO with Packer and finalizes it. Linux takes about 15 minutes, Windows up to an hour or more.</Typography>
              <Stack direction="row" spacing={2} useFlexGap sx={{ alignItems: "flex-start", flexWrap: "wrap" }}>
                <Field select label="KVM host" value={build.kvm_host_id} sx={{ minWidth: 160 }} error={errs.kvm_host_id}
                  onChange={(e) => setBuild({ ...build, kvm_host_id: Number(e.target.value) })}>
                  {hosts.map((h) => <MenuItem key={h.id} value={h.id}>{h.name}</MenuItem>)}
                </Field>
                <Field select label="OS" value={build.os_key} sx={{ minWidth: 200 }} error={errs.os_key}
                  onChange={(e) => setBuild({ ...build, os_key: e.target.value, core: false })}>
                  {OS_KEYS.map((k) => <MenuItem key={k} value={k}>{k}</MenuItem>)}
                </Field>
              </Stack>
              <Field label="Template name" value={build.name} placeholder={`${build.os_key.replace(/_/g, "")}-base-2026.10`}
                error={errs.name} fullWidth help="Letters, digits, . _ -; never reused" onChange={(e) => setBuild({ ...build, name: e.target.value })} />
              {build.os_key.startsWith("windows_server") && (
                <FormControlLabel label="Server Core (no desktop)" control={<Checkbox checked={build.core}
                  onChange={(e) => setBuild({ ...build, core: e.target.checked })} />} />
              )}
            </Stack>
          )} />
      )}
    </Stack>
  );
}

export default function Files() {
  const { me } = useAuth();
  const [tab, setTab] = useState("isos");
  const canManage = hasRole(me, "file_manager");
  const canEditTemplates = hasRole(me, "template_editor");
  return (
    <Stack spacing={2}>
      <Typography variant="h5">Files</Typography>
      <Tabs value={tab} onChange={(_, v) => setTab(v)}>
        {CATS.map((c) => <Tab key={c.key} value={c.key} label={c.label} />)}
        <Tab value="templates" label="Templates" />
      </Tabs>
      {tab === "templates" ? <Templates canEdit={canEditTemplates} />
        : <FileTable key={tab} category={tab} canManage={canManage} />}
      {tab !== "templates" && !canManage && (
        <Typography variant="body2" color="text.secondary">Admins and file managers can upload and edit files.</Typography>
      )}
    </Stack>
  );
}
