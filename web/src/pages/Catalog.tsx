import { useEffect, useRef, useState } from "react";
import { Link as RouterLink, useSearchParams } from "react-router-dom";
import {
  Alert, Box, Button, Checkbox, Chip, Dialog, DialogActions, DialogContent, DialogTitle, FormControlLabel, Link,
  MenuItem, Stack, Tab, Table, TableBody, TableCell, TableHead, TableRow, Tabs, Typography,
} from "@mui/material";
import DownloadIcon from "@mui/icons-material/Download";
import UploadIcon from "@mui/icons-material/Upload";
import { api, fmtTime } from "../api";
import { hasRole, useAuth } from "../App";
import { ErrorSummary, Field, FieldErrors, fromApiError } from "../forms";
import { ActionButton, useConfirm } from "../components/Confirm";

// Services, applications and packages users can add to host groups, on
// top of the template (design §21.4). Users browse and request; admins
// create, import, publish, retire and export.
export interface CatalogItem {
  id: number; kind: "service" | "application" | "package"; name: string; display_name: string; description: string;
  notes: string; status: "draft" | "published" | "retired"; definition: Record<string, unknown>; platforms: string[];
  created_by: string; updated_by: string; updated_at: string;
}
interface CatalogRequest {
  id: number; kind: string; title: string; details: string; status: string; response: string; requested_by: string;
  handled_by: string; created_at: string; updated_at: string;
}

const KINDS = [
  { key: "service", label: "Services", help: "Roles and features added on top of the template: Windows features, or Linux packages, systemd units and an optional configuration role (e.g. syslog_collector)." },
  { key: "application", label: "Applications", help: "Software to install: package-manager names (Chocolatey, apt, dnf) or installers from the Files tab." },
  { key: "package", label: "Packages", help: "Named groups of services, applications and other packages (one level deep)." },
] as const;

// Starting points for the definition editor, one per kind.
export const EXAMPLES: Record<string, string> = {
  service: JSON.stringify({ windows: { features: ["Web-Server"], services: ["W3SVC"] },
    linux: { packages: { dnf: ["nginx"], apt: ["nginx"] }, units: ["nginx"] } }, null, 2),
  application: JSON.stringify({ chocolatey: "7zip", apt: "p7zip-full", dnf: { package: "p7zip", repo: "epel" } }, null, 2),
  package: JSON.stringify({ items: [{ kind: "application", name: "sevenzip" }, { kind: "service", name: "iis" }] }, null, 2),
};

function RequestDialog({ kind, onClose }: { kind: string; onClose: (sent: boolean) => void }) {
  const [form, setForm] = useState({ kind, title: "", details: "" });
  const [errs, setErrs] = useState<{ summary: string; fields: FieldErrors }>({ summary: "", fields: {} });
  const send = async () => {
    try { await api.post("/api/catalog/requests", form); onClose(true); } catch (e) { setErrs(fromApiError(e)); }
  };
  return (
    <Dialog open onClose={() => onClose(false)} maxWidth="sm" fullWidth>
      <DialogTitle>Request a new {form.kind}</DialogTitle>
      <DialogContent>
        <Stack spacing={1} sx={{ mt: 1 }}>
          <ErrorSummary summary={errs.summary} fields={errs.fields} />
          <Field select label="Kind" value={form.kind} onChange={(e) => setForm({ ...form, kind: e.target.value })} sx={{ maxWidth: 200 }}>
            {KINDS.map((k) => <MenuItem key={k.key} value={k.key}>{k.key}</MenuItem>)}
          </Field>
          <Field label="What do you need?" value={form.title} fullWidth error={errs.fields.title}
            help="e.g. SQL Server Express 2022" onChange={(e) => setForm({ ...form, title: e.target.value })} />
          <Field label="Details" value={form.details} fullWidth multiline minRows={4}
            help="Versions, OS, where the installer comes from, licensing" onChange={(e) => setForm({ ...form, details: e.target.value })} />
        </Stack>
      </DialogContent>
      <DialogActions>
        <Button onClick={() => onClose(false)} title="Close without sending">Cancel</Button>
        <Button variant="contained" onClick={send} title="Send the request to the admins">Send request</Button>
      </DialogActions>
    </Dialog>
  );
}

function ItemList({ kind, isAdmin }: { kind: string; isAdmin: boolean }) {
  const [items, setItems] = useState<CatalogItem[]>([]);
  const [requesting, setRequesting] = useState(false);
  const [selected, setSelected] = useState<number[]>([]);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const load = () => api.get<CatalogItem[]>(`/api/catalog?kind=${kind}`).then(setItems, (e) => setMsg({ ok: false, text: e.message }));
  useEffect(() => { load(); setSelected([]); }, [kind]); // eslint-disable-line react-hooks/exhaustive-deps
  const setStatus = async (i: CatalogItem, status: string) => {
    try { await api.put(`/api/catalog/${i.id}/status`, { status }); load(); } catch (e) { setMsg({ ok: false, text: (e as Error).message }); }
  };
  return (
    <Stack spacing={2}>
      <Stack direction="row" spacing={1}>
        {isAdmin && (
          <Button startIcon={<DownloadIcon />} href={`/api/catalog/export?ids=${selected.join(",")}`}
            title="Download the selected items (or all), with what packages need, as labape_catalog YAML">
            Export {selected.length ? `${selected.length} selected` : "all"} as YAML
          </Button>
        )}
        <Box sx={{ flexGrow: 1 }} />
        <Button variant="outlined" onClick={() => setRequesting(true)} title={`Ask the admins to add a ${kind} (opens a form)`}>Request a {kind}</Button>
        {isAdmin && <Button variant="contained" component={RouterLink} to={`/catalog/new?kind=${kind}`}
          title={`New ${kind}: opens the form; it's saved as a draft`}>New {kind}</Button>}
      </Stack>
      {msg && <Alert severity={msg.ok ? "success" : "error"} onClose={() => setMsg(null)}>{msg.text}</Alert>}
      <Table size="small">
        <TableHead>
          <TableRow>
            {isAdmin && <TableCell padding="checkbox" />}
            <TableCell>Name</TableCell><TableCell>Platforms</TableCell><TableCell>Description</TableCell>
            {isAdmin && <TableCell>Status</TableCell>}<TableCell>Updated</TableCell>{isAdmin && <TableCell />}
          </TableRow>
        </TableHead>
        <TableBody>
          {items.map((i) => (
            <TableRow key={i.id} sx={{ opacity: i.status === "retired" ? 0.5 : 1 }}>
              {isAdmin && (
                <TableCell padding="checkbox">
                  <Checkbox size="small" checked={selected.includes(i.id)}
                    onChange={(e) => setSelected(e.target.checked ? [...selected, i.id] : selected.filter((x) => x !== i.id))} />
                </TableCell>
              )}
              <TableCell>
                <Link component={RouterLink} to={`/catalog/${i.id}`}><b>{i.display_name}</b></Link>{" "}
                <Typography component="span" variant="caption" color="text.secondary">{i.name}</Typography>
              </TableCell>
              <TableCell>{i.platforms.map((p) => <Chip key={p} size="small" label={p} sx={{ mr: 0.5 }} />)}</TableCell>
              <TableCell>{i.description}{i.notes && <Typography variant="caption" component="div" color="text.secondary">{i.notes}</Typography>}</TableCell>
              {isAdmin && <TableCell><Chip size="small" label={i.status} color={i.status === "published" ? "success" : "default"} /></TableCell>}
              <TableCell sx={{ whiteSpace: "nowrap" }}>{fmtTime(i.updated_at)}</TableCell>
              {isAdmin && (
                <TableCell sx={{ whiteSpace: "nowrap" }}>
                  {i.status !== "published" && (
                    <ActionButton size="small" onClick={() => setStatus(i, "published")} tip="Make this item available in deploy forms"
                      confirm={{ title: `Publish ${i.display_name}?`, confirmLabel: "Publish",
                        body: "Everyone can then choose it for host groups. A package can be published only when everything in it is." }}>
                      Publish
                    </ActionButton>
                  )}
                  {i.status === "published" && (
                    <ActionButton size="small" color="warning" onClick={() => setStatus(i, "retired")} tip="Stop offering this item in deploy forms"
                      confirm={{ title: `Retire ${i.display_name}?`, confirmLabel: "Retire",
                        body: "It disappears from deploy forms. Environments that already use it keep it on redeploy." }}>
                      Retire
                    </ActionButton>
                  )}
                </TableCell>
              )}
            </TableRow>
          ))}
          {items.length === 0 && <TableRow><TableCell colSpan={7}>Nothing here yet.</TableCell></TableRow>}
        </TableBody>
      </Table>
      {requesting && <RequestDialog kind={kind} onClose={(sent) => { setRequesting(false); if (sent) setMsg({ ok: true, text: "Request sent; an admin will follow up." }); }} />}
    </Stack>
  );
}

function Requests({ isAdmin }: { isAdmin: boolean }) {
  const [rows, setRows] = useState<CatalogRequest[]>([]);
  const [requesting, setRequesting] = useState(false);
  const [sent, setSent] = useState(false);
  const [answering, setAnswering] = useState<CatalogRequest | null>(null);
  const [answer, setAnswer] = useState({ status: "accepted", response: "" });
  const load = () => api.get<CatalogRequest[]>("/api/catalog/requests").then(setRows);
  useEffect(() => { load(); }, []);
  const save = async () => {
    if (!answering) return;
    await api.put(`/api/catalog/requests/${answering.id}`, answer);
    setAnswering(null);
    load();
  };
  return (
    <Stack spacing={2}>
      <Typography variant="body2" color="text.secondary">
        Ask the admins for a service, application or package that isn't in the catalog yet. You see the answer here.
      </Typography>
      <Stack direction="row">
        <Box sx={{ flexGrow: 1 }} />
        <Button variant="contained" onClick={() => setRequesting(true)}
          title="Ask the admins to add a service, application or package (opens a form)">
          New request
        </Button>
      </Stack>
      {sent && <Alert severity="success" onClose={() => setSent(false)}>Request sent; an admin will follow up.</Alert>}
      <Table size="small">
        <TableHead>
          <TableRow>
            <TableCell>Request</TableCell><TableCell>Kind</TableCell><TableCell>By</TableCell><TableCell>Status</TableCell>
            <TableCell>Response</TableCell>{isAdmin && <TableCell />}
          </TableRow>
        </TableHead>
        <TableBody>
          {rows.map((r) => (
            <TableRow key={r.id}>
              <TableCell><b>{r.title}</b><Typography variant="caption" component="div" color="text.secondary" sx={{ whiteSpace: "pre-wrap" }}>{r.details}</Typography></TableCell>
              <TableCell>{r.kind}</TableCell>
              <TableCell sx={{ whiteSpace: "nowrap" }}>{r.requested_by}, {fmtTime(r.created_at)}</TableCell>
              <TableCell><Chip size="small" label={r.status} color={r.status === "done" ? "success" : r.status === "declined" ? "default" : "info"} /></TableCell>
              <TableCell>{r.response}{r.handled_by && <Typography variant="caption" component="div" color="text.secondary">{r.handled_by}</Typography>}</TableCell>
              {isAdmin && <TableCell><Button size="small" title="Accept, decline or close this request, with a note (opens a form)" onClick={() => { setAnswering(r); setAnswer({ status: r.status === "open" ? "accepted" : r.status, response: r.response }); }}>Respond</Button></TableCell>}
            </TableRow>
          ))}
          {rows.length === 0 && <TableRow><TableCell colSpan={6}>No requests.</TableCell></TableRow>}
        </TableBody>
      </Table>
      {requesting && <RequestDialog kind="application" onClose={(ok) => { setRequesting(false); if (ok) { setSent(true); load(); } }} />}
      {answering && (
        <Dialog open onClose={() => setAnswering(null)} maxWidth="sm" fullWidth>
          <DialogTitle>{answering.title}</DialogTitle>
          <DialogContent>
            <Stack spacing={1} sx={{ mt: 1 }}>
              <Field select label="Status" value={answer.status} sx={{ maxWidth: 200 }} onChange={(e) => setAnswer({ ...answer, status: e.target.value })}>
                {["open", "accepted", "done", "declined"].map((s) => <MenuItem key={s} value={s}>{s}</MenuItem>)}
              </Field>
              <Field label="Response" value={answer.response} fullWidth multiline minRows={3} onChange={(e) => setAnswer({ ...answer, response: e.target.value })} />
            </Stack>
          </DialogContent>
          <DialogActions>
            <Button onClick={() => setAnswering(null)} title="Close without saving">Cancel</Button>
            <Button variant="contained" onClick={save} title="Save the response; the requester sees it on the Requests tab">Save</Button>
          </DialogActions>
        </Dialog>
      )}
    </Stack>
  );
}

function Importer({ onDone }: { onDone: (msg: string, ok: boolean) => void }) {
  const input = useRef<HTMLInputElement>(null);
  const confirm = useConfirm();
  const [publish, setPublish] = useState(false);
  const [overwrite, setOverwrite] = useState(false);
  const upload = async (file: File) => {
    const fd = new FormData();
    fd.append("file", file);
    const res = await fetch(`/api/catalog/import?publish=${publish}&overwrite=${overwrite}`, { method: "POST", body: fd, credentials: "same-origin" });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) { onDone(data.detail ?? res.statusText, false); return; }
    onDone(`Imported: ${data.created.length} new, ${data.updated.length} updated, ${data.skipped.length} skipped (already there).`, true);
  };
  return (
    <Stack direction="row" spacing={2} sx={{ alignItems: "center" }}>
      <input ref={input} type="file" accept=".yaml,.yml" hidden onChange={async (e) => {
        const f = e.target.files?.[0];
        e.target.value = "";
        if (f && await confirm({ title: `Import ${f.name}?`, confirmLabel: "Import",
          body: `Its services, applications and packages are added ${publish ? "and published" : "as drafts"}; items that already exist are ${overwrite ? "overwritten" : "skipped"}.` })) upload(f);
      }} />
      <ActionButton startIcon={<UploadIcon />} onClick={() => input.current?.click()}
        tip="Choose a labape_catalog YAML file to import (asks to confirm after you pick it)">Import YAML</ActionButton>
      <FormControlLabel label="Publish on import" control={<Checkbox checked={publish} onChange={(e) => setPublish(e.target.checked)} />} />
      <FormControlLabel label="Overwrite existing" control={<Checkbox checked={overwrite} onChange={(e) => setOverwrite(e.target.checked)} />} />
    </Stack>
  );
}

export default function Catalog() {
  const { me } = useAuth();
  const isAdmin = hasRole(me, "admin");
  const [params, setParams] = useSearchParams();
  const tab = params.get("tab") ?? "service";
  const setTab = (t: string) => setParams({ tab: t }, { replace: true });
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const [reload, setReload] = useState(0);
  const kind = KINDS.find((k) => k.key === tab);
  return (
    <Stack spacing={2}>
      <Typography variant="h5">Catalog</Typography>
      <Tabs value={tab} onChange={(_, v) => setTab(v)}>
        {KINDS.map((k) => <Tab key={k.key} value={k.key} label={k.label} />)}
        <Tab value="requests" label="Requests" />
      </Tabs>
      {kind && <Typography variant="body2" color="text.secondary">{kind.help}</Typography>}
      {isAdmin && tab !== "requests" && <Importer onDone={(text, ok) => { setMsg({ ok, text }); setReload(reload + 1); }} />}
      {msg && <Alert severity={msg.ok ? "success" : "error"} onClose={() => setMsg(null)}>{msg.text}</Alert>}
      {tab === "requests" ? <Requests isAdmin={isAdmin} /> : <ItemList key={`${tab}-${reload}`} kind={tab} isAdmin={isAdmin} />}
    </Stack>
  );
}
