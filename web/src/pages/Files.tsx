import { useEffect, useRef, useState } from "react";
import { Link as RouterLink, useNavigate, useSearchParams } from "react-router-dom";
import {
  Alert, Box, Button, Checkbox, Chip, Dialog, DialogActions, DialogContent, DialogTitle, FormControlLabel,
  LinearProgress, Link, MenuItem, Stack, Tab, Table, TableBody, TableCell, TableHead, TableRow, Tabs, Tooltip,
  Typography,
} from "@mui/material";
import UploadIcon from "@mui/icons-material/Upload";
import LinkIcon from "@mui/icons-material/Link";
import NotesIcon from "@mui/icons-material/StickyNote2Outlined";
import * as tus from "tus-js-client";
import { api, fmtTime, KvmHost, Template } from "../api";
import { hasRole, useAuth } from "../App";
import { ErrorSummary, Field, FieldErrors, fromApiError } from "../forms";
import { ActionButton, useConfirm } from "../components/Confirm";

// File manager (design §9, §21.3): ISOs, installers, other files, and the
// template library. Admins and file managers upload; template editors
// build, refresh and edit templates; everyone can browse.
export interface StoredFile {
  id: number; category: string; name: string; path: string; size: number; mtime: string | null; sha256: string;
  os_key: string; is_default: boolean; description: string; notes: string; uploaded_by: string;
  uploaded_at: string | null;
}

export const OS_KEYS = ["rocky9", "ubuntu_lts", "ubuntu_26", "debian_latest", "windows_server_2019", "windows_server_2022",
  "windows_server_2025", "windows_11", "windows_10"];
const CATS = [{ key: "isos", label: "ISOs" }, { key: "installers", label: "Installers" }, { key: "files", label: "Other files" }];

export const size = (n: number) => n >= 2 ** 30 ? `${(n / 2 ** 30).toFixed(1)} GB` : n >= 2 ** 20 ? `${(n / 2 ** 20).toFixed(1)} MB`
  : `${Math.ceil(n / 1024)} KB`;

// LABaPe always stores a SHA-256; a vendor's published value may use another type (question 72).
const HASH_TYPES = [{ key: "sha256", label: "SHA-256" }, { key: "sha1", label: "SHA-1" },
  { key: "sha512", label: "SHA-512" }, { key: "md5", label: "MD5" }];

function HashFields({ hashType, expected, onChange, errs }: {
  hashType: string; expected: string; onChange: (hashType: string, expected: string) => void; errs?: FieldErrors;
}) {
  return (
    <Stack direction="row" spacing={2} useFlexGap sx={{ alignItems: "flex-start", flexWrap: "wrap" }}>
      <Field select label="Hash type" value={hashType} sx={{ width: 130 }} error={errs?.hash_type}
        onChange={(e) => onChange(e.target.value, expected)}>
        {HASH_TYPES.map((h) => <MenuItem key={h.key} value={h.key}>{h.label}</MenuItem>)}
      </Field>
      <Field label="Expected hash (optional)" value={expected} sx={{ flex: 1, minWidth: 320 }} error={errs?.expected_hash}
        help="Paste the vendor's value or a checksum-file line; a mismatch deletes the file"
        onChange={(e) => onChange(hashType, e.target.value)} />
    </Stack>
  );
}

function Uploader({ category, onDone }: { category: string; onDone: () => void }) {
  const input = useRef<HTMLInputElement>(null);
  const [progress, setProgress] = useState<{ name: string; pct: number } | null>(null);
  const [error, setError] = useState("");
  const [upload, setUpload] = useState<tus.Upload | null>(null);
  const [hash, setHash] = useState({ type: "sha256", expected: "" });
  const [showHash, setShowHash] = useState(false);
  const confirm = useConfirm();

  const start = async (file: File) => {
    setError("");
    if (!(await confirm({ title: `Upload ${file.name}?`, confirmLabel: "Upload",
      body: `${size(file.size)} goes into this location${hash.expected.trim() ? `, checked against the ${hash.type.toUpperCase()} you gave` : ""}. The upload resumes if the connection drops; a file with the same content as a stored one is refused.` }))) return;
    const up = new tus.Upload(file, {
      endpoint: "/api/files/tus",
      chunkSize: 32 * 2 ** 20,
      retryDelays: [0, 3000, 10000, 30000],
      removeFingerprintOnSuccess: true,
      metadata: { filename: file.name, category, ...(hash.expected.trim() ? { hash_type: hash.type, expected_hash: hash.expected.trim() } : {}) },
      onProgress: (sent, total) => setProgress({ name: file.name, pct: total ? (sent / total) * 100 : 0 }),
      onSuccess: () => { setProgress(null); setUpload(null); setHash({ type: "sha256", expected: "" }); onDone(); },
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
        <ActionButton variant="contained" startIcon={<UploadIcon />} disabled={Boolean(progress)} onClick={() => input.current?.click()}
          tip="Choose a file to upload into this location (asks to confirm after you pick it)">
          Upload
        </ActionButton>
        {progress && (
          <Box sx={{ flexGrow: 1 }}>
            <Typography variant="caption">{progress.name}: {progress.pct.toFixed(0)}% (resumes if interrupted)</Typography>
            <LinearProgress variant="determinate" value={progress.pct} />
          </Box>
        )}
        {upload && (
          <ActionButton color="error" tip="Stop the upload and discard what was sent"
            confirm={{ title: "Cancel the upload?", danger: true, confirmLabel: "Cancel upload", body: "The part already sent is discarded; uploading the file again starts from the beginning." }}
            onClick={() => { upload.abort(true); setProgress(null); setUpload(null); }}>Cancel</ActionButton>
        )}
        {!progress && !showHash && (
          <ActionButton size="small" onClick={() => setShowHash(true)} tip="Give the vendor's hash so the upload is checked against it">
            Verify against a hash…
          </ActionButton>
        )}
        <Box sx={{ flexGrow: 1 }} />
        <DownloadButton category={category} />
      </Stack>
      {showHash && !progress && (
        <HashFields hashType={hash.type} expected={hash.expected} onChange={(type, expected) => setHash({ type, expected })} />
      )}
      {error && <Alert severity="error" onClose={() => setError("")}>{error}</Alert>}
    </Stack>
  );
}

function DownloadButton({ category }: { category: string }) {
  const [open, setOpen] = useState(false);
  const [form, setForm] = useState({ url: "", filename: "", hash_type: "sha256", expected_hash: "", description: "" });
  return (
    <>
      <ActionButton variant="outlined" startIcon={<LinkIcon />} onClick={() => setOpen(true)}
        tip="Have the worker download a file from a URL into this location (opens a form)">Download from URL</ActionButton>
      {open && (
        <JobDialog title="Download a file from a URL" onClose={() => setOpen(false)}
          submit={() => api.post("/api/files/download", { ...form, category })}
          fields={(errs) => (
            <>
              <Typography variant="body2">
                The worker downloads the file into this location as a job (progress in the job log; a retry resumes).
                HTTPS only unless Settings &gt; Downloads allows HTTP.
              </Typography>
              <Field label="URL" value={form.url} fullWidth error={errs.url} placeholder="https://vendor.example/tool.msi"
                onChange={(e) => setForm({ ...form, url: e.target.value })} />
              <Field label="File name" value={form.filename} fullWidth error={errs.filename}
                help="Empty: the last part of the URL" onChange={(e) => setForm({ ...form, filename: e.target.value })} />
              <HashFields hashType={form.hash_type} expected={form.expected_hash} errs={errs}
                onChange={(hash_type, expected_hash) => setForm({ ...form, hash_type, expected_hash })} />
              <Field label="Description" value={form.description} fullWidth onChange={(e) => setForm({ ...form, description: e.target.value })} />
            </>
          )} />
      )}
    </>
  );
}

function FileTable({ category, canManage }: { category: string; canManage: boolean }) {
  const [files, setFiles] = useState<StoredFile[]>([]);
  const [error, setError] = useState("");
  const load = () => api.get<StoredFile[]>(`/api/files?category=${category}`).then(setFiles, (e) => setError(e.message));
  useEffect(() => { load(); }, [category]); // eslint-disable-line react-hooks/exhaustive-deps
  const remove = async (f: StoredFile) => {
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
                <Link component={RouterLink} to={`/files/${f.id}`}>{f.name}</Link>
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
                {canManage && (
                  <ActionButton size="small" color="error" onClick={() => remove(f)} tip="Delete this file from disk"
                    confirm={{ title: `Delete ${f.name}?`, danger: true, confirmLabel: "Delete",
                      body: `The file is removed from ${f.path}. Templates already built from it aren't affected. This can't be undone.` }}>
                    Delete
                  </ActionButton>
                )}
              </TableCell>
            </TableRow>
          ))}
          {files.length === 0 && <TableRow><TableCell colSpan={7}>No files here yet.</TableCell></TableRow>}
        </TableBody>
      </Table>
    </Stack>
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
        <Button onClick={onClose} title="Close without starting anything">Cancel</Button>
        <Button variant="contained" onClick={go} title="Start the job and open its live log">Start job</Button>
      </DialogActions>
    </Dialog>
  );
}

function Templates({ canEdit }: { canEdit: boolean }) {
  const [templates, setTemplates] = useState<Template[]>([]);
  const [hosts, setHosts] = useState<KvmHost[]>([]);
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
        <Stack direction="row">
          <Box sx={{ flexGrow: 1 }} />
          <ActionButton variant="contained" onClick={() => setBuilding(true)}
            tip="Install an OS from its ISO with Packer and save it as a template (opens a form)">Build a template</ActionButton>
        </Stack>
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
                <Link component={RouterLink} to={`/templates/${t.id}`}>{t.name}</Link>
                {t.notes && <Tooltip title={<Box sx={{ whiteSpace: "pre-wrap" }}>{t.notes}</Box>}><NotesIcon fontSize="small" sx={{ ml: 0.5, verticalAlign: "middle" }} /></Tooltip>}
                {t.source === "inferred" && <Chip size="small" label="inferred" sx={{ ml: 1 }} />}
              </TableCell>
              <TableCell>{hostName(t.kvm_host_id)}</TableCell>
              <TableCell>{t.os_key}{t.edition ? ` (${t.edition})` : ""}</TableCell>
              <TableCell>{t.disk_gb} GB</TableCell>
              <TableCell sx={{ whiteSpace: "nowrap" }}>{t.min_cpu} vCPU · {t.min_memory_mb} MB</TableCell>
              <TableCell>{t.description}</TableCell>
              <TableCell sx={{ whiteSpace: "nowrap" }}>
                {canEdit && (
                  <ActionButton size="small" onClick={() => { setRefreshing(t); setNewName(`${t.name}.1`); }}
                    tip="Make an updated copy of this template with every OS update applied (opens a form)">Refresh</ActionButton>
                )}
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
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
  const [params, setParams] = useSearchParams();
  const tab = params.get("tab") ?? "isos";
  const setTab = (t: string) => setParams({ tab: t }, { replace: true });
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
