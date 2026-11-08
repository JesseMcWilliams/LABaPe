// Admin -> Notifications (plan N1): where alerts go besides the built-in email list and syslog, and the rules that
// pick which alert goes where. Webhook addresses and ntfy tokens are write-only: they are kept in the secret store.
import { useCallback, useEffect, useState } from "react";
import {
  Alert, Button, Checkbox, Chip, Dialog, DialogActions, DialogContent, DialogTitle, FormControlLabel, MenuItem, Paper, Stack,
  Table, TableBody, TableCell, TableHead, TableRow, Typography,
} from "@mui/material";
import { Link as RouterLink } from "react-router-dom";
import { api } from "../api";
import { Field } from "../forms";
import { useConfirm } from "../components/Confirm";

interface Channel {
  id: number; name: string; kind: string; enabled: boolean; secret_set: boolean; last_ok: boolean | null; last_error: string; last_at: string | null;
  config: { to?: string[]; shape?: string; host?: string; server?: string; topic?: string };
}
interface Rule {
  id: number; name: string; enabled: boolean; min_level: string; host_ids: number[]; categories: string[]; channel_ids: number[];
  quiet_start: string; quiet_end: string; reminder_hours: number; send_cleared: boolean; send_digest: boolean;
}
interface View {
  builtin: { email_to: string[]; syslog: boolean }; kinds: string[]; shapes: string[]; categories: string[];
  channels: Channel[]; rules: Rule[]; hosts: { id: number; name: string }[];
  log: { at: string; channel: string; rule: string; ok: boolean; error: string; summary: string }[];
}

const KIND_LABEL: Record<string, string> = { email: "Email", webhook: "Webhook", ntfy: "ntfy", syslog: "Syslog" };
const SHAPE_LABEL: Record<string, string> = { generic: "Generic JSON", slack: "Slack", teams: "Microsoft Teams", discord: "Discord" };
const CATEGORY_HELP: Record<string, string> = {
  disk: "Drives, SMART, free space, temperature", raid: "RAID arrays, ZFS pools, volume groups", container: "Containers stopped, unhealthy or restarting",
  certificate: "Certificates close to expiry", backup: "Missing or failed backups", secret: "Secrets that failed their check",
  stack: "The stack and linked stacks", "alert-path": "Alert delivery itself (email, channels)",
};
const when = (t: string | null) => (t ? new Date(t).toLocaleString() : "—");

function ChannelDialog({ view, edit, onClose, onSaved }: { view: View; edit: Channel | null; onClose: () => void; onSaved: (v: View) => void }) {
  const [f, setF] = useState({
    name: edit?.name ?? "", kind: edit?.kind ?? "webhook", enabled: edit?.enabled ?? true, to: (edit?.config.to ?? []).join(", "),
    shape: edit?.config.shape ?? "generic", server: edit?.config.server ?? "", topic: edit?.config.topic ?? "", secret: "",
  });
  const [error, setError] = useState("");
  const set = (p: Partial<typeof f>) => setF({ ...f, ...p });
  const save = async () => {
    const body = { ...f, to: f.to.split(/[\s,;]+/).filter(Boolean) };
    try {
      onSaved(edit ? await api.put<View>(`/api/admin/notifications/channels/${edit.id}`, body) : await api.post<View>("/api/admin/notifications/channels", body));
    } catch (e) {
      setError((e as Error).message);
    }
  };
  return (
    <Dialog open onClose={onClose} maxWidth="sm" fullWidth>
      <DialogTitle>{edit ? `Channel ${edit.name}` : "New channel"}</DialogTitle>
      <DialogContent>
        <Stack spacing={1.5} sx={{ pt: 1 }}>
          {error && <Alert severity="error">{error}</Alert>}
          <Field label="Name" value={f.name} onChange={(e) => set({ name: e.target.value })} help="Shown in rules and the delivery log" />
          <Field select label="Kind" value={f.kind} disabled={Boolean(edit)} onChange={(e) => set({ kind: e.target.value })}>
            {view.kinds.map((k) => <MenuItem key={k} value={k}>{KIND_LABEL[k] ?? k}</MenuItem>)}
          </Field>
          {f.kind === "email" && <Field label="Send to" value={f.to} onChange={(e) => set({ to: e.target.value })}
            help="Comma-separated addresses. Goes through Settings → Email." />}
          {f.kind === "webhook" && (
            <>
              <Field select label="Message shape" value={f.shape} onChange={(e) => set({ shape: e.target.value })}>
                {view.shapes.map((s) => <MenuItem key={s} value={s}>{SHAPE_LABEL[s] ?? s}</MenuItem>)}
              </Field>
              <Field label="Webhook address" type="password" autoComplete="off" value={f.secret} onChange={(e) => set({ secret: e.target.value })}
                help={edit?.secret_set ? `Saved (${edit.config.host}). Type a new one to replace it.` : "The full https:// address. Kept in the secret store; never shown again."} />
            </>
          )}
          {f.kind === "ntfy" && (
            <>
              <Field label="Server" value={f.server} onChange={(e) => set({ server: e.target.value })} help="Blank: https://ntfy.sh" />
              <Field label="Topic" value={f.topic} onChange={(e) => set({ topic: e.target.value })} help="Anyone who knows the topic on a public server can read it; use a long random one." />
              <Field label="Access token" type="password" autoComplete="off" value={f.secret} onChange={(e) => set({ secret: e.target.value })}
                help={edit?.secret_set ? "Saved. Type a new one to replace it." : "Optional, for a protected topic."} />
            </>
          )}
          {f.kind === "syslog" && <Typography variant="body2" color="text.secondary">Sends through the collector set in Settings → Syslog.</Typography>}
          <FormControlLabel label="On" control={<Checkbox checked={f.enabled} onChange={(e) => set({ enabled: e.target.checked })} />} />
        </Stack>
      </DialogContent>
      <DialogActions><Button onClick={onClose}>Cancel</Button><Button variant="contained" onClick={save}>Save</Button></DialogActions>
    </Dialog>
  );
}

function RuleDialog({ view, edit, onClose, onSaved }: { view: View; edit: Rule | null; onClose: () => void; onSaved: (v: View) => void }) {
  const [f, setF] = useState<Omit<Rule, "id">>({
    name: edit?.name ?? "", enabled: edit?.enabled ?? true, min_level: edit?.min_level ?? "warning", host_ids: edit?.host_ids ?? [],
    categories: edit?.categories ?? [], channel_ids: edit?.channel_ids ?? [], quiet_start: edit?.quiet_start ?? "", quiet_end: edit?.quiet_end ?? "",
    reminder_hours: edit?.reminder_hours ?? 0, send_cleared: edit?.send_cleared ?? true, send_digest: edit?.send_digest ?? false,
  });
  const [error, setError] = useState("");
  const set = (p: Partial<typeof f>) => setF({ ...f, ...p });
  const save = async () => {
    try {
      onSaved(edit ? await api.put<View>(`/api/admin/notifications/rules/${edit.id}`, f) : await api.post<View>("/api/admin/notifications/rules", f));
    } catch (e) {
      setError((e as Error).message);
    }
  };
  const multi = <T extends string | number>(v: T[]) => v as unknown as string;
  return (
    <Dialog open onClose={onClose} maxWidth="sm" fullWidth>
      <DialogTitle>{edit ? `Rule ${edit.name}` : "New rule"}</DialogTitle>
      <DialogContent>
        <Stack spacing={1.5} sx={{ pt: 1 }}>
          {error && <Alert severity="error">{error}</Alert>}
          <Field label="Name" value={f.name} onChange={(e) => set({ name: e.target.value })} />
          <Field select label="Send to" value={multi(f.channel_ids)} slotProps={{ select: { multiple: true } }}
            onChange={(e) => set({ channel_ids: e.target.value as unknown as number[] })} help="One or more channels">
            {view.channels.map((c) => <MenuItem key={c.id} value={c.id}>{c.name} ({KIND_LABEL[c.kind]})</MenuItem>)}
          </Field>
          <Field select label="Level" value={f.min_level} onChange={(e) => set({ min_level: e.target.value })}>
            <MenuItem value="warning">Warnings and errors</MenuItem><MenuItem value="error">Errors only</MenuItem>
          </Field>
          <Field select label="Categories" value={multi(f.categories)} slotProps={{ select: { multiple: true } }}
            onChange={(e) => set({ categories: e.target.value as unknown as string[] })} help="None chosen: every category">
            {view.categories.map((c) => <MenuItem key={c} value={c}>{c} — {CATEGORY_HELP[c]}</MenuItem>)}
          </Field>
          <Field select label="Hosts" value={multi(f.host_ids)} slotProps={{ select: { multiple: true } }}
            onChange={(e) => set({ host_ids: e.target.value as unknown as number[] })} help="None chosen: every host, and the stack itself">
            {view.hosts.map((h) => <MenuItem key={h.id} value={h.id}>{h.name}</MenuItem>)}
          </Field>
          <Stack direction="row" spacing={2}>
            <Field label="Quiet from (UTC)" value={f.quiet_start} placeholder="22:00" onChange={(e) => set({ quiet_start: e.target.value })} />
            <Field label="Quiet until (UTC)" value={f.quiet_end} placeholder="06:00" onChange={(e) => set({ quiet_end: e.target.value })}
              help="Alerts raised in between are held and sent after" />
          </Stack>
          <Field label="Remind every (hours)" type="number" value={f.reminder_hours} onChange={(e) => set({ reminder_hours: Number(e.target.value) })}
            help="0: no reminders. Otherwise an alert still open (and not acknowledged) is sent again this often." />
          <FormControlLabel label="Also send the all-clear" control={<Checkbox checked={f.send_cleared} onChange={(e) => set({ send_cleared: e.target.checked })} />} />
          <FormControlLabel label="Also send the daily digest (open alerts, what cleared, hosts whose alerts may not arrive)" control={<Checkbox checked={f.send_digest} onChange={(e) => set({ send_digest: e.target.checked })} />} />
          <FormControlLabel label="On" control={<Checkbox checked={f.enabled} onChange={(e) => set({ enabled: e.target.checked })} />} />
          <Typography variant="caption" color="text.secondary">A new rule sends alerts that start after it is saved, not the ones already open.</Typography>
        </Stack>
      </DialogContent>
      <DialogActions><Button onClick={onClose}>Cancel</Button><Button variant="contained" onClick={save}>Save</Button></DialogActions>
    </Dialog>
  );
}

export default function Notifications() {
  const confirm = useConfirm();
  const [view, setView] = useState<View | null>(null);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const [chDlg, setChDlg] = useState<Channel | null | "new">(null);
  const [ruleDlg, setRuleDlg] = useState<Rule | null | "new">(null);
  const load = useCallback(() => api.get<View>("/api/admin/notifications").then(setView, (e) => setMsg({ ok: false, text: e.message })), []);
  useEffect(() => { void load(); }, [load]);
  if (!view) return msg ? <Alert severity="error">{msg.text}</Alert> : null;
  const name = (id: number) => view.channels.find((c) => c.id === id)?.name ?? `#${id}`;
  const hostName = (id: number) => view.hosts.find((h) => h.id === id)?.name ?? `#${id}`;
  const saved = (v: View) => { setView(v); setChDlg(null); setRuleDlg(null); };
  const test = (c: Channel) => api.post<View & { ok: boolean; error: string }>(`/api/admin/notifications/channels/${c.id}/test`)
    .then((r) => { setView(r); setMsg({ ok: r.ok, text: r.ok ? `Test message sent through ${c.name}.` : `${c.name}: ${r.error}` }); }, (e) => setMsg({ ok: false, text: e.message }));
  const remove = async (what: "channels" | "rules", id: number, label: string) => {
    if (!(await confirm({ title: `Delete ${label}?`, confirmLabel: "Delete", danger: true, body: what === "channels" ? "Its saved address or token is not removed from the secret store until the next clean-up." : "Alerts stop going where it sent them." }))) return;
    api.del<View>(`/api/admin/notifications/${what}/${id}`).then(setView, (e) => setMsg({ ok: false, text: e.message }));
  };
  return (
    <Stack spacing={2}>
      <Typography variant="h5">Notifications</Typography>
      <Typography variant="body2" color="text.secondary">
        Where alerts go. The built-in email list and syslog switch (Settings → Monitoring) keep working as they are; channels and rules below add to them,
        so you can send errors to a phone, a chat room or a ticketing webhook, hold the night&apos;s warnings until morning, and be reminded of what stays open.
      </Typography>
      {msg && <Alert severity={msg.ok ? "success" : "error"} onClose={() => setMsg(null)}>{msg.text}</Alert>}
      <Paper variant="outlined" sx={{ p: 2 }}>
        <Typography variant="subtitle2">Built in</Typography>
        <Typography variant="body2">
          Email to {view.builtin.email_to.length ? view.builtin.email_to.join(", ") : "nobody (none set)"}; syslog {view.builtin.syslog ? "on" : "off"}.{" "}
          <RouterLink to="/admin/settings?section=stack">Change in Settings → Monitoring</RouterLink>
        </Typography>
      </Paper>

      <Paper variant="outlined" sx={{ p: 2 }}>
        <Stack spacing={1}>
          <Stack direction="row" sx={{ justifyContent: "space-between", alignItems: "center" }}>
            <Typography variant="h6">Channels</Typography>
            <Button variant="contained" size="small" onClick={() => setChDlg("new")}>New channel</Button>
          </Stack>
          {view.channels.length === 0 ? <Typography variant="body2" color="text.secondary">No channels yet.</Typography> : (
            <Table size="small">
              <TableHead><TableRow><TableCell>Name</TableCell><TableCell>Kind</TableCell><TableCell>Goes to</TableCell><TableCell>Last send</TableCell><TableCell /></TableRow></TableHead>
              <TableBody>
                {view.channels.map((c) => (
                  <TableRow key={c.id}>
                    <TableCell>{c.name}{!c.enabled && <Chip size="small" label="off" sx={{ ml: 1 }} />}</TableCell>
                    <TableCell>{KIND_LABEL[c.kind]}{c.kind === "webhook" ? ` · ${SHAPE_LABEL[c.config.shape ?? "generic"]}` : ""}</TableCell>
                    <TableCell sx={{ fontSize: 12 }}>
                      {c.kind === "email" ? (c.config.to ?? []).join(", ") : c.kind === "webhook" ? `${c.config.host ?? ""} (address kept in the secret store)`
                        : c.kind === "ntfy" ? `${c.config.server || "ntfy.sh"}/${c.config.topic}` : "Settings → Syslog"}
                    </TableCell>
                    <TableCell>{c.last_ok === null ? "never" : c.last_ok ? <>ok · {when(c.last_at)}</> : <Chip size="small" color="error" label={`failed: ${c.last_error.slice(0, 80)}`} />}</TableCell>
                    <TableCell align="right">
                      <Button size="small" onClick={() => test(c)} title="Send a test message through this channel now">Test</Button>
                      <Button size="small" onClick={() => setChDlg(c)}>Edit</Button>
                      <Button size="small" color="error" onClick={() => remove("channels", c.id, c.name)}>Delete</Button>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </Stack>
      </Paper>

      <Paper variant="outlined" sx={{ p: 2 }}>
        <Stack spacing={1}>
          <Stack direction="row" sx={{ justifyContent: "space-between", alignItems: "center" }}>
            <Typography variant="h6">Rules</Typography>
            <Button variant="contained" size="small" disabled={view.channels.length === 0} onClick={() => setRuleDlg("new")}
              title={view.channels.length === 0 ? "Add a channel first" : "Choose which alerts go to which channels"}>New rule</Button>
          </Stack>
          {view.rules.length === 0 ? <Typography variant="body2" color="text.secondary">No rules yet; only the built-in email and syslog are used.</Typography> : (
            <Table size="small">
              <TableHead><TableRow><TableCell>Name</TableCell><TableCell>Matches</TableCell><TableCell>Sends to</TableCell><TableCell>Timing</TableCell><TableCell /></TableRow></TableHead>
              <TableBody>
                {view.rules.map((r) => (
                  <TableRow key={r.id}>
                    <TableCell>{r.name}{!r.enabled && <Chip size="small" label="off" sx={{ ml: 1 }} />}</TableCell>
                    <TableCell sx={{ fontSize: 12 }}>
                      {r.min_level === "error" ? "errors" : "warnings and errors"} · {r.categories.length ? r.categories.join(", ") : "all categories"} · {r.host_ids.length ? r.host_ids.map(hostName).join(", ") : "all hosts"}
                    </TableCell>
                    <TableCell sx={{ fontSize: 12 }}>{r.channel_ids.map(name).join(", ")}</TableCell>
                    <TableCell sx={{ fontSize: 12 }}>
                      {r.quiet_start ? `quiet ${r.quiet_start}–${r.quiet_end} UTC` : "always"}{r.reminder_hours ? ` · remind every ${r.reminder_hours} h` : ""}{r.send_cleared ? " · all-clear" : ""}{r.send_digest ? " · daily digest" : ""}
                    </TableCell>
                    <TableCell align="right">
                      <Button size="small" onClick={() => setRuleDlg(r)}>Edit</Button>
                      <Button size="small" color="error" onClick={() => remove("rules", r.id, r.name)}>Delete</Button>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </Stack>
      </Paper>

      <Paper variant="outlined" sx={{ p: 2 }}>
        <Typography variant="h6" sx={{ mb: 1 }}>Delivery log</Typography>
        {view.log.length === 0 ? <Typography variant="body2" color="text.secondary">Nothing sent yet. The last 100 sends appear here.</Typography> : (
          <Table size="small">
            <TableHead><TableRow><TableCell>When</TableCell><TableCell>Channel</TableCell><TableCell>Rule</TableCell><TableCell>Result</TableCell><TableCell>Message</TableCell></TableRow></TableHead>
            <TableBody>
              {view.log.map((e, i) => (
                <TableRow key={i}>
                  <TableCell>{when(e.at)}</TableCell><TableCell>{e.channel}</TableCell><TableCell>{e.rule}</TableCell>
                  <TableCell>{e.ok ? "sent" : <Chip size="small" color="error" label={e.error.slice(0, 100)} />}</TableCell>
                  <TableCell sx={{ fontSize: 12 }}>{e.summary}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
      </Paper>
      {chDlg && <ChannelDialog view={view} edit={chDlg === "new" ? null : chDlg} onClose={() => setChDlg(null)} onSaved={saved} />}
      {ruleDlg && <RuleDialog view={view} edit={ruleDlg === "new" ? null : ruleDlg} onClose={() => setRuleDlg(null)} onSaved={saved} />}
    </Stack>
  );
}
