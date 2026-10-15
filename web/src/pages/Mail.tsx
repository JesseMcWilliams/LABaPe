// Admin -> Mail (design §52): the stack's own mail server, Mailpit. LABaPe's email (alerts, job emails) goes
// to it by default and it keeps every message, readable in its web page. Here: whether it's running, a test
// message, and the relay that forwards chosen mail to real inboxes through an SMTP server.
import { useCallback, useEffect, useRef, useState } from "react";
import { Alert, Button, Checkbox, FormControlLabel, MenuItem, Paper, Radio, RadioGroup, Stack, Typography } from "@mui/material";
import { Link as RouterLink } from "react-router-dom";
import { api } from "../api";
import { Field } from "../forms";

interface Status {
  available: boolean; reachable: boolean; version: string; messages: number; unread: number; database_bytes: number;
  applied: boolean; error: string; email_uses_mailpit: boolean;
  email: { enabled: boolean; host: string; port: number; from_address: string }; alert_recipients: string[];
}
interface Relay {
  relay: boolean; host: string; port: number; security: "none" | "starttls" | "ssl"; verify_tls: boolean; username: string;
  auth: "plain" | "login"; forward: "listed" | "all"; allow: string[]; override_from: string;
  keep_messages: number; keep_days: number; has_password?: boolean;
}

export default function Mail() {
  const [status, setStatus] = useState<Status | null>(null);
  const [relay, setRelay] = useState<Relay | null>(null);
  const [password, setPassword] = useState("");
  const [allowText, setAllowText] = useState("");
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const [testTo, setTestTo] = useState("");
  const [applying, setApplying] = useState(false);
  const timer = useRef<number | undefined>(undefined);

  const load = useCallback(async () => {
    const [s, all] = await Promise.all([api.get<Status>("/api/admin/mail"), api.get<{ stack_mail: Relay }>("/api/admin/settings")]);
    setStatus(s);
    setRelay((cur) => cur ?? all.stack_mail);
    setAllowText((cur) => (cur === "" ? all.stack_mail.allow.join(", ") : cur));
    return s;
  }, []);
  useEffect(() => { load().catch((e) => setMsg({ ok: false, text: e.message })); return () => window.clearTimeout(timer.current); }, [load]);

  const waitForApplied = (tries = 0) => {
    setApplying(true);
    timer.current = window.setTimeout(async () => {
      try {
        const s = await load();
        if (s.applied || tries >= 15) { setApplying(false); return; }
      } catch { setApplying(false); return; }
      waitForApplied(tries + 1);
    }, 3000);
  };

  if (!status || !relay) return msg ? <Alert severity="error">{msg.text}</Alert> : null;
  const set = (patch: Partial<Relay>) => setRelay({ ...relay, ...patch });
  const allow = allowText.split(/[\s,;]+/).filter(Boolean);

  const save = () => api.put<Relay>("/api/admin/settings/stack_mail", {
    ...relay, allow, ...(password ? { password } : {}),
  }).then(() => {
    setPassword("");
    setMsg({ ok: true, text: "Saved. Mailpit restarts with the new options within a few seconds." });
    waitForApplied();
    return load();
  }, (e) => setMsg({ ok: false, text: e.message }));
  const test = () => api.post<{ message: string }>("/api/admin/mail/test", { to: testTo })
    .then((r) => { setMsg({ ok: true, text: r.message }); load(); }, (e) => setMsg({ ok: false, text: e.message }));
  const useForEmail = () => api.post<Status>("/api/admin/mail/use-for-email", {})
    .then((s) => { setStatus(s); setMsg({ ok: true, text: "Settings → Email now sends through this mail server." }); }, (e) => setMsg({ ok: false, text: e.message }));
  const mb = (n: number) => `${(n / 1048576).toFixed(1)} MB`;

  return (
    <Stack spacing={2}>
      <Typography variant="h5">Mail</Typography>
      <Typography variant="body2" color="text.secondary">
        This stack's own mail server (Mailpit). LABaPe sends its email (alerts, job emails) to it; it keeps every message and shows
        them in its web page. Nothing leaves the stack unless you set up forwarding below.
      </Typography>
      {msg && <Alert severity={msg.ok ? "success" : "error"} onClose={() => setMsg(null)}>{msg.text}</Alert>}

      <Paper variant="outlined" sx={{ p: 2 }}>
        <Stack spacing={1.5}>
          <Typography variant="h6">Mail server</Typography>
          {!status.available && <Alert severity="info">{status.error}</Alert>}
          {status.available && !status.reachable && <Alert severity="error">{status.error || "Mailpit doesn't answer."}</Alert>}
          {status.available && status.reachable && (
            <Typography variant="body2">
              Running ({status.version}). {status.messages} message{status.messages === 1 ? "" : "s"} kept, {status.unread} unread
              ({mb(status.database_bytes)}). {applying ? "Applying the new options…" : status.applied ? "Running with the saved options." : "Waiting to pick up the saved options."}
            </Typography>
          )}
          <Stack direction="row" spacing={2} sx={{ alignItems: "center", flexWrap: "wrap" }}>
            <Button variant="outlined" size="small" disabled={!status.reachable} href="/mail/" target="_blank" rel="noreferrer"
              title="Mailpit's web page: every message LABaPe sent, opened in a new tab (admins only)">Open Mailpit</Button>
            <Typography variant="body2" color="text.secondary">
              Alerts go to the addresses in <RouterLink to="/admin/settings?section=stack">Settings → Monitoring</RouterLink>
              {status.alert_recipients.length ? `: ${status.alert_recipients.join(", ")}.` : " (none set yet, so none are emailed)."}
            </Typography>
          </Stack>
          {status.available && !status.email_uses_mailpit && (
            <Alert severity="warning" action={<Button color="inherit" size="small" onClick={useForEmail}>Use this server</Button>}>
              Settings → Email is {status.email.enabled ? `set to ${status.email.host}:${status.email.port}` : "off"}, not to this mail server.
            </Alert>
          )}
          <Stack direction="row" spacing={2} sx={{ alignItems: "flex-start", flexWrap: "wrap" }}>
            <Field label="Send a test email to" size="small" value={testTo} sx={{ minWidth: 260 }} help="Through Settings → Email, as LABaPe sends its own"
              onChange={(e) => setTestTo(e.target.value)} />
            <Button size="small" variant="contained" disabled={!testTo.includes("@") || !status.available} onClick={test}
              title="Send a test message and see it in Mailpit">Send test</Button>
          </Stack>
        </Stack>
      </Paper>

      <Paper variant="outlined" sx={{ p: 2 }}>
        <Stack spacing={1.5}>
          <Typography variant="h6">Forwarding</Typography>
          <Typography variant="body2" color="text.secondary">
            To have mail reach real inboxes, give Mailpit an SMTP server to forward through, and say which recipients it forwards.
            Everything is still kept in Mailpit too. With forwarding off, or for any address not listed, mail stays here
            (you can still release a message by hand in Mailpit).
          </Typography>
          <FormControlLabel label="Forward mail through an SMTP server" control={<Checkbox checked={relay.relay}
            onChange={(e) => set({ relay: e.target.checked })} />} />
          <Stack direction="row" spacing={2} useFlexGap sx={{ flexWrap: "wrap", alignItems: "flex-start" }}>
            <Field label="Server" size="small" value={relay.host} sx={{ minWidth: 240 }} disabled={!relay.relay}
              onChange={(e) => set({ host: e.target.value })} />
            <Field label="Port" size="small" type="number" value={relay.port} sx={{ width: 110 }} disabled={!relay.relay}
              onChange={(e) => set({ port: Number(e.target.value) })} />
            <Field select label="Security" size="small" value={relay.security} sx={{ width: 150 }} disabled={!relay.relay}
              onChange={(e) => set({ security: e.target.value as Relay["security"] })}>
              <MenuItem value="starttls">STARTTLS</MenuItem><MenuItem value="ssl">SSL/TLS</MenuItem><MenuItem value="none">None</MenuItem>
            </Field>
            <FormControlLabel label="Check its certificate" sx={{ mt: 0.5 }} disabled={!relay.relay || relay.security === "none"}
              control={<Checkbox checked={relay.verify_tls} onChange={(e) => set({ verify_tls: e.target.checked })} />} />
          </Stack>
          <Stack direction="row" spacing={2} useFlexGap sx={{ flexWrap: "wrap", alignItems: "flex-start" }}>
            <Field label="User name" size="small" value={relay.username} sx={{ minWidth: 240 }} disabled={!relay.relay} help="Blank: no sign-in"
              onChange={(e) => set({ username: e.target.value })} />
            <Field label="Password" size="small" type="password" value={password} sx={{ minWidth: 240 }} disabled={!relay.relay || !relay.username}
              autoComplete="new-password" help={relay.has_password ? "Saved. Type a new one to replace it." : "Not set"}
              onChange={(e) => setPassword(e.target.value)} />
            <Field select label="Sign-in type" size="small" value={relay.auth} sx={{ width: 150 }} disabled={!relay.relay || !relay.username}
              onChange={(e) => set({ auth: e.target.value as Relay["auth"] })}>
              <MenuItem value="plain">PLAIN</MenuItem><MenuItem value="login">LOGIN</MenuItem>
            </Field>
            <Field label="Replace the From address" size="small" value={relay.override_from} sx={{ minWidth: 260 }} disabled={!relay.relay}
              help="Some servers only accept their own sender" onChange={(e) => set({ override_from: e.target.value })} />
          </Stack>
          <Typography variant="subtitle2">Which recipients are forwarded</Typography>
          <RadioGroup value={relay.forward} onChange={(e) => set({ forward: e.target.value as Relay["forward"] })}>
            <FormControlLabel value="listed" disabled={!relay.relay} control={<Radio size="small" />} label="Only these addresses and domains" />
            <FormControlLabel value="all" disabled={!relay.relay} control={<Radio size="small" />}
              label="Every recipient (anything LABaPe or a test sends goes out)" />
          </RadioGroup>
          <Field label="Addresses and @domains" size="small" value={allowText} multiline minRows={2} disabled={!relay.relay || relay.forward !== "listed"}
            help="Comma-separated: you@example.com, @example.org (every address at a domain)" onChange={(e) => setAllowText(e.target.value)} />
          <Typography variant="subtitle2">What Mailpit keeps</Typography>
          <Stack direction="row" spacing={2}>
            <Field label="Messages" size="small" type="number" value={relay.keep_messages} sx={{ width: 140 }}
              onChange={(e) => set({ keep_messages: Number(e.target.value) })} />
            <Field label="Days" size="small" type="number" value={relay.keep_days} sx={{ width: 110 }}
              onChange={(e) => set({ keep_days: Number(e.target.value) })} />
          </Stack>
          <Stack direction="row"><Button variant="contained" onClick={save} disabled={applying}
            title="Save and restart Mailpit with these options (it keeps its messages)">Save</Button></Stack>
        </Stack>
      </Paper>
    </Stack>
  );
}
