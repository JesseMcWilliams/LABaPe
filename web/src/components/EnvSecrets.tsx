// An environment's secrets (V1a, design §49): what LABaPe generates (Accounts) and what owners store
// themselves, shared or for one VM. Owners and admins see the list, reveal or copy a value (each
// audited, fetched only when asked) and store, replace or delete their own; LABaPe's own change
// through rotation. In OpenBao they sit in the environment's folder, shown as each one's place.
import { useEffect, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import {
  Alert, Button, Checkbox, Dialog, DialogActions, DialogContent, DialogTitle, FormControlLabel, IconButton, Menu, MenuItem, Paper, Stack, Table,
  TableBody, TableCell, TableHead, TableRow, TextField, Tooltip, Typography,
} from "@mui/material";
import ContentCopyIcon from "@mui/icons-material/ContentCopy";
import DeleteIcon from "@mui/icons-material/Delete";
import EditIcon from "@mui/icons-material/Edit";
import TerminalIcon from "@mui/icons-material/Terminal";
import VisibilityIcon from "@mui/icons-material/Visibility";
import VisibilityOffIcon from "@mui/icons-material/VisibilityOff";
import { api } from "../api";
import { Pager, pageOf, usePageSize } from "./Paging";

interface EnvSecret {
  name: string; area: "accounts" | "user" | "vm"; vm: string; short: string; store: string; path: string;
  updated_at: string; editable: boolean; rotation: string;
  connect: { vm: string; protocol: "rdp" | "ssh" | "vnc" }[];      // E2: where the account signs in
  readers: string[];                                               // V2: the VMs that read it themselves
}
interface OpenBaoAccess { url: string; mount: string; path: string; cli: string }
interface MailServer { vm: string; address: string; smtp: string; ui: string; user: string; relay: string[] }
interface SecretList { secrets: EnvSecret[]; vms: string[]; folder: string; openbao: OpenBaoAccess | null; mail: MailServer[]; vm_access: boolean }
interface Draft { name: string; vm: string; value: string; replacing: boolean; readers: string[] }

const AREAS: { area: EnvSecret["area"]; title: string; note: string }[] = [
  { area: "user", title: "Stored by owners, shared", note: "For the whole environment." },
  { area: "vm", title: "Stored by owners, for one VM", note: "Each for the VM it names." },
  { area: "accounts", title: "LABaPe's (generated)", note: "Admin and account passwords, the credentials sheet and inventory; they change through rotation." },
];

async function copyText(value: string) {
  try {
    await navigator.clipboard.writeText(value);
  } catch {
    // Clipboard API needs HTTPS (or localhost); fall back to a hidden textarea.
    const ta = document.createElement("textarea");
    ta.value = value;
    document.body.appendChild(ta);
    ta.select();
    document.execCommand("copy");
    ta.remove();
  }
}

export default function EnvSecrets({ envId }: { envId: string }) {
  const [data, setData] = useState<SecretList | null>(null);
  const [values, setValues] = useState<Record<string, string>>({});
  const [copied, setCopied] = useState("");
  const [draft, setDraft] = useState<Draft | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const navigate = useNavigate();
  const location = useLocation();
  const [menu, setMenu] = useState<{ anchor: HTMLElement; secret: EnvSecret } | null>(null);
  // E2: a browser session signed in as this account; the password stays on the server.
  const connect = async (secret: EnvSecret, vm: string, protocol: string) => {
    setMenu(null);
    try {
      const r = await api.post<{ id: number; token: string; target: string; protocol: string; recorded: boolean }>(
        "/api/remote/sessions", { target_type: "vm", protocol, environment_id: Number(envId), vm, account: secret.name });
      navigate(`/remote/${r.id}`, { state: { ...r, back: location.pathname + location.search } });
    } catch (e) { setError((e as Error).message); }
  };
  // E1: search and filters over the whole list, then pages of the user's own size.
  const [search, setSearch] = useState("");
  const [area, setArea] = useState("");
  const [vmFilter, setVmFilter] = useState("");
  const [storeFilter, setStoreFilter] = useState("");
  const [page, setPage] = useState(0);
  const [size] = usePageSize();
  const load = () => api.get<SecretList>(`/api/environments/${envId}/secrets`).then(setData, (e) => setError(e.message));
  useEffect(() => { load(); }, [envId]); // eslint-disable-line react-hooks/exhaustive-deps
  if (!data) return error ? <Alert severity="error">{error}</Alert> : null;
  const q = search.trim().toLowerCase();
  const matching = data.secrets.filter((s) => (!area || s.area === area) && (!vmFilter || s.vm === vmFilter)
    && (!storeFilter || s.store === storeFilter)
    && (!q || [s.short, s.name, s.vm, s.path, s.rotation].some((v) => v.toLowerCase().includes(q))));
  const order = { user: 0, vm: 1, accounts: 2 } as const;
  const sorted = [...matching].sort((a, b) => order[a.area] - order[b.area] || a.name.localeCompare(b.name));
  const shown = pageOf(sorted, page, size);
  const storesInUse = [...new Set(data.secrets.map((s) => s.store))].sort();
  const vmsInUse = [...new Set(data.secrets.map((s) => s.vm).filter(Boolean))].sort();
  const filtered = Boolean(q || area || vmFilter || storeFilter);
  const refilter = (f: () => void) => { f(); setPage(0); };

  const fetchValue = (name: string, action: "show" | "copy") =>
    api.post<{ value: string }>(`/api/environments/${envId}/secrets/reveal`, { name, action }).then((r) => r.value);
  const toggle = (s: EnvSecret) => {
    if (s.name in values) {
      const rest = { ...values };
      delete rest[s.name];
      setValues(rest);
      return;
    }
    fetchValue(s.name, "show").then((v) => setValues({ ...values, [s.name]: v }), (e) => setError(e.message));
  };
  const copy = (s: EnvSecret) => fetchValue(s.name, "copy").then(async (v) => {
    await copyText(v);
    setCopied(s.name);
    setTimeout(() => setCopied(""), 1500);
  }, (e) => setError(e.message));
  const save = () => {
    setBusy(true);
    api.put(`/api/environments/${envId}/secrets`, { name: draft!.name, vm: draft!.vm, value: draft!.value, readers: draft!.readers }).then(() => {
      setDraft(null);
      setValues({});
      load();
    }, (e) => setError(e.message)).finally(() => setBusy(false));
  };
  const remove = (s: EnvSecret) => {
    if (!window.confirm(`Delete ${s.short}${s.vm ? ` (${s.vm})` : ""}? It's gone from the store too.`)) return;
    api.del(`/api/environments/${envId}/secrets?name=${encodeURIComponent(s.name)}`).then(load, (e) => setError(e.message));
  };

  return (
    <Paper variant="outlined" sx={{ p: 2 }}>
      <Stack spacing={1}>
        <Stack direction="row" spacing={1} sx={{ alignItems: "center" }}>
          <Typography variant="h6" sx={{ flexGrow: 1 }}>Secrets</Typography>
          <Button size="small" onClick={() => { setError(""); setDraft({ name: "", vm: "", value: "", replacing: false, readers: [] }); }}
            title="Store a secret of your own for this environment, or for one of its VMs">Store a secret</Button>
        </Stack>
        <Typography variant="body2" color="text.secondary">
          For the environment's owners and admins. Each value is fetched only when you show or copy it, and each time is in
          the audit log.{data.folder && <> In OpenBao they're in <code>{data.folder}</code>.</>}
        </Typography>
        {(data.mail ?? []).map((m) => (
          <Alert key={m.vm} severity="info" icon={false}>
            Mail server on {m.vm}: the VMs send to <code>{m.smtp}</code> (STARTTLS); read what arrived at{" "}
            <a href={m.ui} target="_blank" rel="noreferrer">{m.ui}</a> as <code>{m.user}</code> (its password is
            {" "}<code>vm/{m.vm}/mailpit</code> below). {m.relay.length ? <>Forwarded outside: {m.relay.join(", ")}.</> : "Nothing is forwarded outside."}
          </Alert>
        ))}
        {data.openbao && (
          <Alert severity="info" icon={false}>
            You can also use OpenBao itself, signing in with your LABaPe account (OIDC):{" "}
            <a href={data.openbao.url} target="_blank" rel="noreferrer">OpenBao's web UI</a>, secrets engine{" "}
            <code>{data.openbao.mount}</code>, folder <code>{data.openbao.path}/</code>. From a shell:{" "}
            <code>{data.openbao.cli}</code>. Owners read the whole folder and change <code>user/</code> and <code>vm/</code>;
            what you store there shows here within a few minutes.
          </Alert>
        )}
        {error && !draft && <Alert severity="error" onClose={() => setError("")}>{error}</Alert>}
        <Stack direction="row" spacing={1} sx={{ flexWrap: "wrap", rowGap: 1 }}>
          <TextField size="small" label="Search" value={search} sx={{ minWidth: 220 }}
            onChange={(e) => refilter(() => setSearch(e.target.value))} title="Name, VM, place in the store or rotation" />
          <TextField select size="small" label="Group" value={area} sx={{ minWidth: 200 }}
            onChange={(e) => refilter(() => setArea(e.target.value))}>
            <MenuItem value="">All</MenuItem>
            {AREAS.map((a) => <MenuItem key={a.area} value={a.area}>{a.title}</MenuItem>)}
          </TextField>
          <TextField select size="small" label="VM" value={vmFilter} sx={{ minWidth: 140 }}
            onChange={(e) => refilter(() => setVmFilter(e.target.value))}>
            <MenuItem value="">All</MenuItem>
            {vmsInUse.map((v) => <MenuItem key={v} value={v}>{v}</MenuItem>)}
          </TextField>
          <TextField select size="small" label="Store" value={storeFilter} sx={{ minWidth: 140 }}
            onChange={(e) => refilter(() => setStoreFilter(e.target.value))}>
            <MenuItem value="">All</MenuItem>
            {storesInUse.map((v) => <MenuItem key={v} value={v}>{v}</MenuItem>)}
          </TextField>
          {filtered && <Button size="small" onClick={() => refilter(() => { setSearch(""); setArea(""); setVmFilter(""); setStoreFilter(""); })}
            title="Show every secret again">Clear</Button>}
        </Stack>
        {AREAS.map(({ area, title, note }) => {
          const rows = shown.filter((s) => s.area === area);
          if (!rows.length && (filtered || area !== "user" || page > 0)) return null;
          return (
            <Stack key={area} spacing={0.5}>
              <Typography variant="subtitle2" sx={{ mt: 1 }}>{title}</Typography>
              <Typography variant="caption" color="text.secondary">{note}</Typography>
              {rows.length === 0 ? <Typography variant="body2">None yet.</Typography> : (
                <Table size="small">
                  <TableHead>
                    <TableRow>
                      <TableCell>Name</TableCell><TableCell>VM</TableCell><TableCell>Value</TableCell>
                      <TableCell>Changed</TableCell><TableCell>Where</TableCell><TableCell />
                    </TableRow>
                  </TableHead>
                  <TableBody>
                    {rows.map((s) => (
                      <TableRow key={s.name}>
                        <TableCell title={s.rotation ? `Rotation: ${s.rotation}` : undefined}>{s.short}</TableCell>
                        <TableCell title={s.area === "user" && data.vm_access ? "The VMs that read it themselves" : undefined}>
                          {s.vm || (s.area === "user" && data.vm_access ? ((s.readers ?? []).join(", ") || "none") : "—")}
                        </TableCell>
                        <TableCell sx={{ fontFamily: "monospace", wordBreak: "break-all", maxWidth: 360 }}>
                          {s.name in values ? (values[s.name] || "(empty)") : "••••••••••••"}
                        </TableCell>
                        <TableCell>{new Date(s.updated_at).toLocaleString()}</TableCell>
                        <TableCell><Typography variant="caption" sx={{ fontFamily: "monospace" }}>{s.path || s.store}</Typography></TableCell>
                        <TableCell sx={{ whiteSpace: "nowrap" }}>
                          <Tooltip title={s.name in values ? "Hide the value again" : "Show the value here (audited)"}>
                            <IconButton size="small" aria-label={s.name in values ? "Hide" : "Show"} onClick={() => toggle(s)}>
                              {s.name in values ? <VisibilityOffIcon fontSize="small" /> : <VisibilityIcon fontSize="small" />}
                            </IconButton>
                          </Tooltip>
                          <Tooltip title={copied === s.name ? "Copied" : "Copy the value to the clipboard (audited)"}>
                            <IconButton size="small" aria-label="Copy" onClick={() => copy(s)}>
                              <ContentCopyIcon fontSize="small" color={copied === s.name ? "success" : undefined} />
                            </IconButton>
                          </Tooltip>
                          {s.connect.length > 0 && <Tooltip title="Sign in as this account in the browser (RDP or SSH; audited, recorded if the admins turned recording on)">
                            <IconButton size="small" aria-label="Connect" onClick={(e) => setMenu({ anchor: e.currentTarget, secret: s })}>
                              <TerminalIcon fontSize="small" />
                            </IconButton>
                          </Tooltip>}
                          {s.editable && <Tooltip title="Replace the value">
                            <IconButton size="small" aria-label="Replace"
                              onClick={() => { setError(""); setDraft({ name: s.short, vm: s.vm, value: "", replacing: true, readers: s.readers ?? [] }); }}>
                              <EditIcon fontSize="small" />
                            </IconButton>
                          </Tooltip>}
                          {s.editable && <Tooltip title="Delete it, here and in the store">
                            <IconButton size="small" aria-label="Delete" onClick={() => remove(s)}><DeleteIcon fontSize="small" /></IconButton>
                          </Tooltip>}
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              )}
            </Stack>
          );
        })}
        {filtered && matching.length === 0 && <Typography variant="body2">No secret matches.</Typography>}
        {sorted.length > 0 && <Pager total={sorted.length} page={page} onPage={setPage} />}
      </Stack>
      <Menu open={!!menu} anchorEl={menu?.anchor} onClose={() => setMenu(null)}>
        {menu?.secret.connect.map((t) => (
          <MenuItem key={`${t.vm}-${t.protocol}`} onClick={() => connect(menu.secret, t.vm, t.protocol)}>
            {t.vm}: {t.protocol.toUpperCase()}
          </MenuItem>
        ))}
      </Menu>
      <Dialog open={!!draft} onClose={() => setDraft(null)} maxWidth="sm" fullWidth>
        <DialogTitle>{draft?.replacing ? `Replace ${draft.name}` : "Store a secret"}</DialogTitle>
        {draft && <DialogContent>
          <Stack spacing={2} sx={{ mt: 1 }}>
            {error && <Alert severity="error">{error}</Alert>}
            <TextField label="Name" size="small" value={draft.name} disabled={draft.replacing} required
              helperText="Letters, digits, dot, dash and underscore" onChange={(e) => setDraft({ ...draft, name: e.target.value })} />
            <TextField select label="For" size="small" value={draft.vm} disabled={draft.replacing}
              onChange={(e) => setDraft({ ...draft, vm: e.target.value })}>
              <MenuItem value="">The whole environment (shared)</MenuItem>
              {data.vms.map((vm) => <MenuItem key={vm} value={vm}>{vm} only</MenuItem>)}
            </TextField>
            <TextField label="Value" size="small" type="password" value={draft.value} required={!draft.replacing} multiline={false}
              autoComplete="new-password" helperText={draft.replacing ? "Empty: keep the value (change only the VMs below)" : undefined}
              onChange={(e) => setDraft({ ...draft, value: e.target.value })} />
            {data.vm_access && !draft.vm && (
              <Stack>
                <Typography variant="body2">VMs that read it themselves (<code>sudo labape-secret get {draft.name || "NAME"}</code> /
                  {" "}<code>Get-LabapeSecret {draft.name || "NAME"}</code>); the others can't:</Typography>
                <Stack direction="row" sx={{ flexWrap: "wrap" }}>
                  {data.vms.map((vm) => (
                    <FormControlLabel key={vm} label={vm} control={<Checkbox size="small" checked={draft.readers.includes(vm)}
                      onChange={(e) => setDraft({ ...draft, readers: e.target.checked ? [...draft.readers, vm] : draft.readers.filter((x) => x !== vm) })} />} />
                  ))}
                </Stack>
              </Stack>
            )}
          </Stack>
        </DialogContent>}
        <DialogActions>
          <Button onClick={() => setDraft(null)} title="Close without saving">Cancel</Button>
          <Button variant="contained" disabled={busy || !draft?.name || (!draft?.value && !draft?.replacing)} onClick={save}
            title="Save it in the environment's store">{draft?.replacing ? "Replace" : "Store"}</Button>
        </DialogActions>
      </Dialog>
    </Paper>
  );
}
