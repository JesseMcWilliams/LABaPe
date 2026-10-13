// The Syslog tab (design §29.1, question 137): what the stack's collector
// received, for admins and auditors. Newest first; Older pages back; Live
// polls for new messages every 5 seconds.
import { Fragment, useCallback, useEffect, useRef, useState } from "react";
import {
  Alert, Box, Button, Chip, FormControlLabel, MenuItem, Stack, Switch, Table, TableBody, TableCell, TableHead,
  TableRow, Typography,
} from "@mui/material";
import { api, fmtTime } from "../api";
import { Field } from "../forms";
import { DateRangeFields, FilterBar, toQuery, useUrlFilters } from "../components/Filters";

interface Msg {
  id: number; received_at: string; reported_at: string | null; from_ip: string; host: string; facility: string;
  severity: string; app_name: string; procid: string; msgid: string; structured_data: string; message: string;
}
interface Status {
  present: boolean; address: string; retention_days: number; require_client_cert: boolean; facilities: string[];
  severities: string[]; table: { rows: number; oldest: string | null; newest: string | null } | null; error: string;
  worker: { at?: string; certs?: string; error?: string };
}

const PAGE = 200;
const sevColor = (s: string) => (["emerg", "alert", "crit", "err"].includes(s) ? "error" : s === "warning" ? "warning" : s === "notice" ? "info" : "default");

export default function Syslog() {
  const [status, setStatus] = useState<Status | null>(null);
  const [rows, setRows] = useState<Msg[]>([]);
  const [error, setError] = useState("");
  const [more, setMore] = useState(false);
  const [live, setLive] = useState(false);
  const [open, setOpen] = useState<number | null>(null);
  const f = useUrlFilters(["host", "from_ip", "facility", "min_severity", "app", "q", "since", "until"]);
  const query = toQuery(f.applied).toString();
  const newest = useRef(0);

  useEffect(() => {
    api.get<Status>("/api/syslog/status").then(setStatus, (e) => setError(e.message));
  }, []);
  const load = useCallback(() => {
    api.get<Msg[]>(`/api/syslog/messages?limit=${PAGE}&${query}`).then((r) => {
      setRows(r); setMore(r.length === PAGE); setError(""); newest.current = r[0]?.id ?? 0;
    }, (e) => setError(e.message));
  }, [query]);
  useEffect(load, [load]);
  useEffect(() => {
    if (!live) return;
    const t = setInterval(() => {
      api.get<Msg[]>(`/api/syslog/messages?limit=${PAGE}&after_id=${newest.current}&${query}`).then((r) => {
        if (r.length) {
          newest.current = r[0].id;
          setRows((cur) => [...r, ...cur].slice(0, 2000));
        }
      }, () => undefined);
    }, 5000);
    return () => clearInterval(t);
  }, [live, query]);
  const older = () => {
    const last = rows[rows.length - 1];
    if (!last) return;
    api.get<Msg[]>(`/api/syslog/messages?limit=${PAGE}&before_id=${last.id}&${query}`).then((r) => {
      setRows((cur) => [...cur, ...r]); setMore(r.length === PAGE);
    }, (e) => setError(e.message));
  };

  return (
    <Stack spacing={2}>
      <Typography variant="h5">Syslog</Typography>
      <Typography variant="body2" color="text.secondary">
        Messages the stack's collector received from KVM hosts, VMs, LABaPe's audit trail and anything else that sends
        to {status?.address || "this host"} (UDP or TCP 514, TLS 6514{status?.require_client_cert ? " with a client certificate" : ""}).
        Kept {status?.retention_days ?? 90} days; forwarding and retention are in Admin → Settings.
      </Typography>
      {status && !status.present && <Alert severity="info">This install has no syslog collector.</Alert>}
      {status?.error && <Alert severity="warning">{status.error}</Alert>}
      {status?.worker?.error && <Alert severity="warning">The collector's last check failed: {status.worker.error}</Alert>}
      {status?.table && (
        <Typography variant="caption" color="text.secondary">
          About {status.table.rows.toLocaleString()} messages{status.table.oldest ? ` since ${fmtTime(status.table.oldest)}` : ""}
          {status.worker?.certs ? ` · certificates: ${status.worker.certs}` : ""}
        </Typography>
      )}
      {error && <Alert severity="error">{error}</Alert>}
      <FilterBar onApply={() => f.apply()} onClear={f.clear} active={f.active}>
        <Field label="Host" value={f.draft.host} sx={{ width: 160 }} help="Host name contains" onChange={(e) => f.set("host", e.target.value)} />
        <Field label="From" value={f.draft.from_ip} sx={{ width: 150 }} help="Sender address starts with" onChange={(e) => f.set("from_ip", e.target.value)} />
        <Field select label="Facility" value={f.draft.facility} sx={{ width: 130 }} onChange={(e) => f.set("facility", e.target.value)}>
          <MenuItem value="">Any</MenuItem>
          {(status?.facilities ?? []).map((x) => <MenuItem key={x} value={x}>{x}</MenuItem>)}
        </Field>
        <Field select label="Severity at least" value={f.draft.min_severity} sx={{ width: 160 }} onChange={(e) => f.set("min_severity", e.target.value)}>
          <MenuItem value="">Any</MenuItem>
          {(status?.severities ?? []).map((x) => <MenuItem key={x} value={x}>{x}</MenuItem>)}
        </Field>
        <Field label="App" value={f.draft.app} sx={{ width: 140 }} help="App name contains" onChange={(e) => f.set("app", e.target.value)} />
        <Field label="Text" value={f.draft.q} sx={{ width: 200 }} help="Message contains" onChange={(e) => f.set("q", e.target.value)} />
        <DateRangeFields since={f.draft.since} until={f.draft.until} onChange={f.set} />
      </FilterBar>
      <Stack direction="row" spacing={2} sx={{ alignItems: "center" }}>
        <FormControlLabel label="Live" title="Add new messages every 5 seconds"
          control={<Switch checked={live} onChange={(e) => setLive(e.target.checked)} />} />
        <Button size="small" onClick={load} title="Reload from the newest message">Refresh</Button>
        <Box sx={{ flexGrow: 1 }} />
        <Typography variant="caption" color="text.secondary">{rows.length} shown</Typography>
      </Stack>
      <Table size="small">
        <TableHead>
          <TableRow>
            <TableCell>Received</TableCell><TableCell>Host</TableCell><TableCell>From</TableCell><TableCell>Facility</TableCell>
            <TableCell>Severity</TableCell><TableCell>App</TableCell><TableCell>Message</TableCell>
          </TableRow>
        </TableHead>
        <TableBody>
          {rows.map((m) => (
            <Fragment key={m.id}>
              <TableRow hover onClick={() => setOpen(open === m.id ? null : m.id)} sx={{ cursor: "pointer" }}>
                <TableCell sx={{ whiteSpace: "nowrap" }}>{fmtTime(m.received_at)}</TableCell>
                <TableCell>{m.host}</TableCell><TableCell>{m.from_ip}</TableCell><TableCell>{m.facility}</TableCell>
                <TableCell><Chip size="small" label={m.severity} color={sevColor(m.severity)} /></TableCell>
                <TableCell>{m.app_name}{m.procid && m.procid !== "-" ? `[${m.procid}]` : ""}</TableCell>
                <TableCell sx={{ fontFamily: "monospace", fontSize: 12, overflowWrap: "anywhere" }}>
                  {open === m.id ? m.message : m.message.length > 240 ? `${m.message.slice(0, 240)}…` : m.message}
                </TableCell>
              </TableRow>
              {open === m.id && (
                <TableRow>
                  <TableCell colSpan={7} sx={{ fontSize: 12, bgcolor: "action.hover" }}>
                    Reported {m.reported_at ? fmtTime(m.reported_at) : "—"} · msgid {m.msgid || "—"} · structured data{" "}
                    <Box component="code" sx={{ overflowWrap: "anywhere" }}>{m.structured_data || "—"}</Box>
                  </TableCell>
                </TableRow>
              )}
            </Fragment>
          ))}
          {rows.length === 0 && <TableRow><TableCell colSpan={7}>No messages{f.active ? " match" : " yet"}.</TableCell></TableRow>}
        </TableBody>
      </Table>
      {more && <Stack direction="row"><Button onClick={older} title={`Load the next ${PAGE} older messages`}>Older</Button></Stack>}
    </Stack>
  );
}
