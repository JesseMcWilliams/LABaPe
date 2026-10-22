// Certificates from the platform CA (design §65, batch C2): the register, requesting, and the CA's own details.
// Everyone sees the certificates they requested and those of their environments; CA administrators and auditors
// see all; requesting needs the ca_requester (or ca_admin) role; revoking and the settings need ca_admin.
import { useEffect, useMemo, useState } from "react";
import {
  Alert, Box, Button, Checkbox, Chip, Dialog, DialogActions, DialogContent, DialogTitle, FormControlLabel, MenuItem, Paper, Stack, Tab, Tabs,
  Table, TableBody, TableCell, TableHead, TableRow, TextField, ToggleButton, ToggleButtonGroup, Typography,
} from "@mui/material";
import { api, fmtTime } from "../api";
import { useAuth } from "../App";
import { ErrorSummary, Field, FieldErrors, fromApiError } from "../forms";
import { FilterBar, SelectFilter, useUrlFilters } from "../components/Filters";
import { Facts, ListHeader } from "../components/PageParts";
import { pageOf, Pager, usePageSize } from "../components/Paging";

interface Cert {
  id: number; serial: string; serial_hex: string; subject: string; sans: string[]; not_before: string; not_after: string; days_left: number;
  state: "active" | "expiring" | "expired" | "revoked"; fingerprint: string; issuer: string; requested_by: string; environment: string; environment_id: number | null;
  host: string; source: string; note: string; created_at: string; revoked_at: string | null; revoked_by: string; revoke_reason: string; pem?: string;
}
interface CaInfo {
  configured: boolean; healthy: boolean | null; health: string; url: string; total: number; counts: Record<string, number>;
  root: { subject: string; serial: string; not_before: string; not_after: string; fingerprint: string } | null; provisioners: { type: string; name: string }[];
}
interface CertSettings { allowed_domains: string[]; allowed_networks: string[]; default_days: number; max_days: number; always_allowed: string[]; can_edit: boolean }
interface Issued { certificate: Cert; chain: string; key: string; root: string }

const STATE_COLOR: Record<string, "success" | "warning" | "error" | "default"> = { active: "success", expiring: "warning", expired: "error", revoked: "default" };
const lines = (v: string) => v.split(/[\s,]+/).map((x) => x.trim()).filter(Boolean);
const save = (name: string, text: string) => {
  const a = document.createElement("a");
  a.href = URL.createObjectURL(new Blob([text], { type: "application/x-pem-file" }));
  a.download = name;
  a.click();
  URL.revokeObjectURL(a.href);
};

export default function Certificates() {
  const { me } = useAuth();
  const has = (r: string) => Boolean(me?.roles.includes(r) || me?.roles.includes("admin"));
  const seesAll = has("ca_admin") || has("auditor");
  const canRequest = has("ca_requester") || has("ca_admin");
  const [tab, setTab] = useState<"list" | "request" | "ca">("list");
  const [prefill, setPrefill] = useState<Cert | null>(null);
  return (
    <Stack spacing={2}>
      <ListHeader title="Certificates"
        intro="Certificates signed by the platform CA. You see the ones you requested and those of your environments; CA administrators and auditors see all." />
      <Tabs value={tab} onChange={(_, v) => setTab(v)}>
        <Tab value="list" label="Certificates" />
        {canRequest && <Tab value="request" label="Request" />}
        {seesAll && <Tab value="ca" label="The CA" />}
      </Tabs>
      {tab === "list" && <CertList seesAll={seesAll} canRevoke={has("ca_admin")} canRequest={canRequest}
        onRenew={(c) => { setPrefill(c); setTab("request"); }} />}
      {tab === "request" && <RequestForm prefill={prefill} onDone={() => setPrefill(null)} />}
      {tab === "ca" && <CaPage canEdit={has("ca_admin")} />}
    </Stack>
  );
}

function CertList({ seesAll, canRevoke, canRequest, onRenew }: { seesAll: boolean; canRevoke: boolean; canRequest: boolean; onRenew: (c: Cert) => void }) {
  const [all, setAll] = useState(seesAll);
  const [rows, setRows] = useState<Cert[]>([]);
  const [error, setError] = useState("");
  const [open, setOpen] = useState<Cert | null>(null);
  const flt = useUrlFilters(["q", "state", "source"]);
  const a = flt.applied;
  const [page, setPage] = useState(0);
  const [pageSize] = usePageSize();
  const load = () => api.get<Cert[]>(`/api/certificates?scope=${all ? "all" : "mine"}&limit=1000`).then((r) => { setRows(r); setError(""); }, (e) => setError(e.message));
  useEffect(() => { load(); }, [all]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => setPage(0), [JSON.stringify(a), all]); // eslint-disable-line react-hooks/exhaustive-deps
  const shown = rows.filter((c) => (!a.q || [c.subject, c.serial_hex, c.requested_by, c.host, c.environment, ...c.sans].join(" ").toLowerCase().includes(a.q.toLowerCase()))
    && (!a.state || c.state === a.state) && (!a.source || c.source === a.source));
  return (
    <Stack spacing={2}>
      {error && <Alert severity="error">{error}</Alert>}
      <Stack direction="row" spacing={2} sx={{ alignItems: "center" }}>
        {seesAll && <FormControlLabel label="Everyone's certificates" control={<Checkbox checked={all} onChange={(e) => setAll(e.target.checked)} />} />}
        <Box sx={{ flexGrow: 1 }} />
        <Typography variant="body2" color="text.secondary">{flt.active ? `${shown.length} of ${rows.length}` : `${rows.length} certificate${rows.length === 1 ? "" : "s"}`}</Typography>
      </Stack>
      <FilterBar onApply={() => flt.apply()} onClear={flt.clear} active={flt.active}>
        <Field label="Search" value={flt.draft.q} sx={{ width: 240 }} help="Name, address, serial, requester, host" onChange={(e) => flt.set("q", e.target.value)} />
        <SelectFilter label="State" value={flt.draft.state} width={150} options={["active", "expiring", "expired", "revoked"]} onChange={(v) => flt.set("state", v)} />
        <SelectFilter label="Source" value={flt.draft.source} width={150} options={["request", "engine", "stack", "imported"]}
          help="request: asked for here; engine: a deploy; stack: the stack's own" onChange={(v) => flt.set("source", v)} />
      </FilterBar>
      <Pager total={shown.length} page={page} onPage={setPage} />
      <Table size="small">
        <TableHead><TableRow><TableCell>Name</TableCell><TableCell>Issued</TableCell><TableCell>Expires</TableCell><TableCell>State</TableCell><TableCell>Requested by</TableCell><TableCell>For</TableCell><TableCell>Source</TableCell></TableRow></TableHead>
        <TableBody>
          {pageOf(shown, page, pageSize).map((c) => (
            <TableRow key={c.id} hover sx={{ cursor: "pointer", opacity: c.state === "revoked" ? 0.6 : 1 }} onClick={() => setOpen(c)}>
              <TableCell>
                <Typography variant="body2" sx={{ fontWeight: 600 }}>{c.subject || c.serial_hex}</Typography>
                <Typography variant="caption" color="text.secondary">{c.sans.filter((s) => s !== c.subject).join(", ")}</Typography>
              </TableCell>
              <TableCell>{fmtTime(c.not_before)}</TableCell>
              <TableCell>{fmtTime(c.not_after)}{c.state === "active" || c.state === "expiring" ? <Typography variant="caption" color="text.secondary" component="div">{c.days_left} days left</Typography> : null}</TableCell>
              <TableCell><Chip size="small" color={STATE_COLOR[c.state]} label={c.state} /></TableCell>
              <TableCell>{c.requested_by}</TableCell>
              <TableCell>{[c.environment, c.host].filter(Boolean).join(" / ")}</TableCell>
              <TableCell>{c.source}</TableCell>
            </TableRow>
          ))}
          {shown.length === 0 && <TableRow><TableCell colSpan={6}>No certificates{flt.active ? " match" : ""}.</TableCell></TableRow>}
        </TableBody>
      </Table>
      {open && <CertDialog id={open.id} canRevoke={canRevoke} canRequest={canRequest} onClose={() => setOpen(null)} onChanged={load}
        onRenew={(c) => { setOpen(null); onRenew(c); }} />}
    </Stack>
  );
}

function CertDialog({ id, canRevoke, canRequest, onClose, onChanged, onRenew }: {
  id: number; canRevoke: boolean; canRequest: boolean; onClose: () => void; onChanged: () => void; onRenew: (c: Cert) => void;
}) {
  const [c, setC] = useState<Cert | null>(null);
  const [error, setError] = useState("");
  const [revoking, setRevoking] = useState(false);
  const [reason, setReason] = useState("");
  const load = () => api.get<Cert>(`/api/certificates/${id}`).then(setC, (e) => setError(e.message));
  useEffect(() => { load(); }, [id]); // eslint-disable-line react-hooks/exhaustive-deps
  const revoke = () => api.post(`/api/certificates/${id}/revoke`, { reason }).then(() => { setRevoking(false); load(); onChanged(); }, (e) => setError(fromApiError(e).summary || e.message));
  return (
    <Dialog open onClose={onClose} maxWidth="md" fullWidth>
      <DialogTitle>{c?.subject || c?.serial_hex || "Certificate"}</DialogTitle>
      <DialogContent>
        <Stack spacing={2} sx={{ mt: 1 }}>
          {error && <Alert severity="error">{error}</Alert>}
          {c && (
            <>
              {c.state === "revoked" && <Alert severity="warning">Revoked by {c.revoked_by} on {fmtTime(c.revoked_at)}{c.revoke_reason ? `: ${c.revoke_reason}` : ""}. Clients that never check revocation still accept it until it expires.</Alert>}
              <Facts rows={[
                ["Names", c.sans.join(", ") || "—"], ["Valid", `${fmtTime(c.not_before)} to ${fmtTime(c.not_after)} (${c.state})`],
                ["Serial (hex)", c.serial_hex], ["SHA-256 fingerprint", c.fingerprint], ["Issuer", c.issuer],
                ["Requested by", `${c.requested_by} (${c.source})`], ["For", [c.environment, c.host].filter(Boolean).join(" / ") || "—"],
                ...(c.note ? [["Note", c.note] as [string, string]] : []),
              ]} />
              <Typography variant="subtitle2">Certificate and chain (PEM)</Typography>
              <Paper variant="outlined" sx={{ p: 1, maxHeight: 220, overflow: "auto" }}><pre style={{ margin: 0, fontSize: 11 }}>{c.pem}</pre></Paper>
            </>
          )}
        </Stack>
      </DialogContent>
      <DialogActions>
        {c && <Button href={`/api/certificates/${c.id}/download`} title="Download the certificate and chain as a .crt file">Download</Button>}
        {c && canRequest && c.state !== "revoked" && <Button onClick={() => onRenew(c)} title="Open a new request with these names (a new key, a new certificate)">Renew…</Button>}
        {c && canRevoke && c.state !== "revoked" && <Button color="error" onClick={() => { setReason(""); setRevoking(true); }}>Revoke…</Button>}
        <Button variant="contained" onClick={onClose}>Close</Button>
      </DialogActions>
      <Dialog open={revoking} onClose={() => setRevoking(false)} maxWidth="sm" fullWidth>
        <DialogTitle>Revoke {c?.subject}?</DialogTitle>
        <DialogContent>
          <Stack spacing={2} sx={{ mt: 1 }}>
            <Alert severity="warning">
              step-ca revocation is <b>passive</b>: the CA will refuse to renew this certificate and will say it is revoked when asked, but a
              client or server that never checks revocation keeps trusting it until it expires on {c ? fmtTime(c.not_after) : ""}. Replace the certificate wherever it is used.
            </Alert>
            <TextField label="Reason (recorded)" value={reason} onChange={(e) => setReason(e.target.value)} size="small" placeholder="e.g. key leaked" />
          </Stack>
        </DialogContent>
        <DialogActions><Button onClick={() => setRevoking(false)}>Cancel</Button><Button color="error" variant="contained" onClick={revoke}>Revoke</Button></DialogActions>
      </Dialog>
    </Dialog>
  );
}

function RequestForm({ prefill, onDone }: { prefill: Cert | null; onDone: () => void }) {
  const [cn, setCn] = useState(prefill?.subject ?? "");
  const [dns, setDns] = useState((prefill?.sans ?? []).filter((s) => s !== prefill?.subject && !/^[\d.]+$/.test(s) && !s.includes(":")).join("\n"));
  const [ips, setIps] = useState((prefill?.sans ?? []).filter((s) => /^[\d.]+$/.test(s) || s.includes(":")).join("\n"));
  const [how, setHow] = useState<"key" | "csr">("key");
  const [csr, setCsr] = useState("");
  const [days, setDays] = useState<number | "">("");
  const [env, setEnv] = useState<number | "">(prefill?.environment_id ?? "");
  const [envs, setEnvs] = useState<{ id: number; name: string; status?: string }[]>([]);
  const [cfg, setCfg] = useState<CertSettings | null>(null);
  const [errs, setErrs] = useState<{ summary: string; fields: FieldErrors }>({ summary: "", fields: {} });
  const [busy, setBusy] = useState(false);
  const [issued, setIssued] = useState<Issued | null>(null);
  useEffect(() => {
    api.get<typeof envs>("/api/environments").then((e) => setEnvs(e.filter((x) => x.status !== "destroyed")), () => setEnvs([]));
    api.get<CertSettings>("/api/certificates/settings").then(setCfg, () => setCfg(null));
  }, []);
  const submit = async () => {
    setBusy(true); setErrs({ summary: "", fields: {} });
    try {
      const body = { common_name: cn, dns: lines(dns), ips: lines(ips), days: days === "" ? undefined : days, environment_id: env === "" ? undefined : env,
        csr: how === "csr" ? csr : "" };
      setIssued(await api.post<Issued>("/api/certificates", body));
    } catch (e) { setErrs(fromApiError(e)); } finally { setBusy(false); }
  };
  const bad = how === "csr" ? !csr.trim() : !(cn.trim() || lines(dns).length || lines(ips).length);
  return (
    <Stack spacing={2} sx={{ maxWidth: 760 }}>
      <ErrorSummary summary={errs.summary} fields={errs.fields} />
      {cfg && <Alert severity="info">
        You may request names under {cfg.always_allowed.join(", ")}{cfg.allowed_domains.length ? ` and ${cfg.allowed_domains.join(", ")}` : ""} (no wildcards), and addresses in {cfg.allowed_networks.join(", ")}.
        Lifetime {cfg.default_days} days by default, at most {cfg.max_days}.
      </Alert>}
      <ToggleButtonGroup size="small" exclusive value={how} onChange={(_, v) => v && setHow(v)}>
        <ToggleButton value="key" title="LABaPe makes the key pair; you download the key once">LABaPe makes the key</ToggleButton>
        <ToggleButton value="csr" title="You made the key and a certificate request (CSR); only the certificate comes back">I have a CSR</ToggleButton>
      </ToggleButtonGroup>
      {how === "key" ? (
        <>
          <Field label="Common name" value={cn} onChange={(e) => setCn(e.target.value)} error={errs.fields.dns} help="The main DNS name, e.g. app1.jlm.abq" />
          <Field label="More DNS names" value={dns} onChange={(e) => setDns(e.target.value)} multiline minRows={2} help="One per line or separated by commas" />
          <Field label="IP addresses" value={ips} onChange={(e) => setIps(e.target.value)} multiline minRows={1} help="Optional" />
        </>
      ) : (
        <Field label="Certificate request (PEM)" value={csr} onChange={(e) => setCsr(e.target.value)} multiline minRows={8} error={errs.fields.csr}
          slotProps={{ input: { sx: { fontFamily: "monospace", fontSize: 12 } } }} help="-----BEGIN CERTIFICATE REQUEST----- ... Its names are checked like the fields above" />
      )}
      <Stack direction="row" spacing={2} useFlexGap sx={{ flexWrap: "wrap" }}>
        <Field label="Lifetime (days)" type="number" value={days} placeholder={String(cfg?.default_days ?? 30)} sx={{ width: 150 }} error={errs.fields.days}
          onChange={(e) => setDays(e.target.value === "" ? "" : Number(e.target.value))} help={cfg ? `At most ${cfg.max_days}` : ""} />
        <Field select label="For environment" value={env} sx={{ minWidth: 240 }} onChange={(e) => setEnv(e.target.value === "" ? "" : Number(e.target.value))}
          help="Optional: its owners will see this certificate">
          <MenuItem value="">None</MenuItem>
          {envs.map((e) => <MenuItem key={e.id} value={e.id}>{e.name}</MenuItem>)}
        </Field>
      </Stack>
      <Box><Button variant="contained" disabled={busy || bad} onClick={submit}>Request the certificate</Button></Box>
      {issued && (
        <Dialog open onClose={() => { setIssued(null); onDone(); }} maxWidth="md" fullWidth>
          <DialogTitle>Certificate for {issued.certificate.subject}</DialogTitle>
          <DialogContent>
            <Stack spacing={2} sx={{ mt: 1 }}>
              <Alert severity="success">Signed. Valid until {fmtTime(issued.certificate.not_after)}. It is in the list under Certificates.</Alert>
              {issued.key && <Alert severity="warning"><b>The private key is shown once</b> and LABaPe does not keep it: download it now and store it safely.</Alert>}
              <PemBox title="Certificate and chain" text={issued.chain} file={`${issued.certificate.subject || "certificate"}.crt`} />
              {issued.key && <PemBox title="Private key" text={issued.key} file={`${issued.certificate.subject || "certificate"}.key`} />}
              <Typography variant="body2">The CA's root certificate, for clients to trust: <a href="/api/ca/root.crt">download labape-root-ca.crt</a>.</Typography>
            </Stack>
          </DialogContent>
          <DialogActions><Button variant="contained" onClick={() => { setIssued(null); onDone(); }}>Done</Button></DialogActions>
        </Dialog>
      )}
    </Stack>
  );
}

function PemBox({ title, text, file }: { title: string; text: string; file: string }) {
  return (
    <Box>
      <Stack direction="row" spacing={1} sx={{ alignItems: "center" }}>
        <Typography variant="subtitle2" sx={{ flexGrow: 1 }}>{title}</Typography>
        <Button size="small" onClick={() => navigator.clipboard?.writeText(text)}>Copy</Button>
        <Button size="small" onClick={() => save(file, text)}>Download</Button>
      </Stack>
      <Paper variant="outlined" sx={{ p: 1, maxHeight: 180, overflow: "auto" }}><pre style={{ margin: 0, fontSize: 11 }}>{text}</pre></Paper>
    </Box>
  );
}

function CaPage({ canEdit }: { canEdit: boolean }) {
  const [info, setInfo] = useState<CaInfo | null>(null);
  const [cfg, setCfg] = useState<CertSettings | null>(null);
  const [form, setForm] = useState({ domains: "", networks: "", def: 30, max: 90 });
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const [pem, setPem] = useState("");
  const [note, setNote] = useState("");
  const load = () => {
    api.get<CaInfo>("/api/certificates/ca").then(setInfo, (e) => setMsg({ ok: false, text: e.message }));
    api.get<CertSettings>("/api/certificates/settings").then((c) => { setCfg(c); setForm({ domains: c.allowed_domains.join("\n"), networks: c.allowed_networks.join("\n"), def: c.default_days, max: c.max_days }); }, () => undefined);
  };
  useEffect(load, []);
  const counts = useMemo(() => Object.entries(info?.counts ?? {}).map(([k, v]) => `${v} ${k}`).join(" · "), [info]);
  const saveCfg = () => api.put("/api/certificates/settings", { allowed_domains: lines(form.domains), allowed_networks: lines(form.networks), default_days: form.def, max_days: form.max })
    .then(() => { setMsg({ ok: true, text: "Saved." }); load(); }, (e) => setMsg({ ok: false, text: fromApiError(e).summary || e.message }));
  const doImport = () => api.post("/api/certificates/import", { pem, note }).then(() => { setMsg({ ok: true, text: "Added to the register." }); setPem(""); setNote(""); load(); },
    (e) => setMsg({ ok: false, text: fromApiError(e).summary || e.message }));
  return (
    <Stack spacing={2} sx={{ maxWidth: 820 }}>
      {msg && <Alert severity={msg.ok ? "success" : "error"} onClose={() => setMsg(null)}>{msg.text}</Alert>}
      {info && (
        <Facts rows={[
          ["Status", <Chip key="s" size="small" color={info.healthy ? "success" : "error"} label={info.healthy ? "answering" : "not answering"} title={info.health} />],
          ["Address", info.url || "—"],
          ["Root certificate", info.root ? `${info.root.subject}, valid until ${fmtTime(info.root.not_after)}` : "—"],
          ["Root fingerprint", info.root?.fingerprint ?? "—"],
          ["Provisioners", info.provisioners.map((p) => `${p.name} (${p.type})`).join(", ") || "—"],
          ["In the register", `${info.total}${counts ? ` (${counts})` : ""}`],
        ]} />
      )}
      <Typography variant="body2"><a href="/api/ca/root.crt">Download the root certificate</a> to trust it on a client.</Typography>
      {cfg && (
        <Paper variant="outlined" sx={{ p: 2 }}>
          <Stack spacing={1}>
            <Typography variant="subtitle1">What may be requested</Typography>
            <Typography variant="body2" color="text.secondary">Always allowed: {cfg.always_allowed.join(", ")}. CA administrators are not limited by these lists.</Typography>
            <Field label="Allowed domains" value={form.domains} onChange={(e) => setForm({ ...form, domains: e.target.value })} multiline minRows={2} disabled={!canEdit}
              help="A name is allowed if it is, or ends in, one of these (one per line)" />
            <Field label="Allowed networks" value={form.networks} onChange={(e) => setForm({ ...form, networks: e.target.value })} multiline minRows={2} disabled={!canEdit}
              help="IP addresses must be inside one of these (CIDR, one per line)" />
            <Stack direction="row" spacing={2}>
              <Field label="Default lifetime (days)" type="number" value={form.def} sx={{ width: 190 }} disabled={!canEdit} onChange={(e) => setForm({ ...form, def: Number(e.target.value) })} />
              <Field label="Longest lifetime (days)" type="number" value={form.max} sx={{ width: 190 }} disabled={!canEdit} help="step-ca allows up to 90" onChange={(e) => setForm({ ...form, max: Number(e.target.value) })} />
            </Stack>
            {canEdit && <Box><Button variant="contained" onClick={saveCfg}>Save</Button></Box>}
          </Stack>
        </Paper>
      )}
      {canEdit && (
        <Paper variant="outlined" sx={{ p: 2 }}>
          <Stack spacing={1}>
            <Typography variant="subtitle1">Add a certificate to the register</Typography>
            <Typography variant="body2" color="text.secondary">For a certificate this CA signed outside LABaPe (the stack's own, signed before the register existed). Paste the PEM.</Typography>
            <Field label="Certificate (PEM)" value={pem} onChange={(e) => setPem(e.target.value)} multiline minRows={4} slotProps={{ input: { sx: { fontFamily: "monospace", fontSize: 12 } } }} />
            <Field label="Note" value={note} onChange={(e) => setNote(e.target.value)} />
            <Box><Button variant="outlined" disabled={!pem.trim()} onClick={doImport}>Add</Button></Box>
          </Stack>
        </Paper>
      )}
    </Stack>
  );
}
