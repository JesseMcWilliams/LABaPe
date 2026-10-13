import { useEffect, useState } from "react";
import {
  Alert, Box, Button, Chip, Stack, Table, TableBody, TableCell, TableHead, TableRow, Typography,
} from "@mui/material";
import DownloadIcon from "@mui/icons-material/Download";
import ErrorOutlineIcon from "@mui/icons-material/ErrorOutline";
import { api, FieldProblem, fmtTime } from "../api";
import { Field } from "../forms";
import { ActionButton } from "../components/Confirm";
import { DateRangeFields, FilterBar, SelectFilter, toQuery, useUrlFilters } from "../components/Filters";

interface UserError {
  id: number;
  at: string;
  username: string;
  method: string;
  path: string;
  status: number;
  message: string;
  fields: FieldProblem[];
  origin: "server" | "browser";
}

const KEYS = ["username", "path", "message", "origin", "status", "since", "until"];

// Requests the app refused, and submits the browser blocked, so admins can
// see where users struggle (question 59). Admin only. Filters like the audit
// log's, kept in the URL (design §28, O1).
export default function Errors() {
  const [rows, setRows] = useState<UserError[]>([]);
  const [retention, setRetention] = useState(90);
  const [error, setError] = useState("");
  const [saved, setSaved] = useState("");
  const f = useUrlFilters(KEYS);
  const query = toQuery(f.applied).toString();

  useEffect(() => {
    api.get<{ retention_days: number; errors: UserError[] }>(`/api/admin/errors?limit=500&${query}`).then((r) => {
      setRows(r.errors);
      setRetention(r.retention_days);
      setError("");
    }, (e) => setError(e.message));
  }, [query]);

  const saveRetention = async () => {
    setError("");
    try {
      await api.put("/api/admin/errors/retention", { days: retention });
      setSaved(`Errors are kept ${retention} days.`);
    } catch (e) {
      setError((e as Error).message);
    }
  };

  return (
    <Stack spacing={2}>
      <Typography variant="h5">User errors</Typography>
      <Typography variant="body2" color="text.secondary">
        Requests the app refused (validation, policy, permission) and submits the browser blocked, newest first.
      </Typography>
      {error && <Alert severity="error">{error}</Alert>}
      {saved && <Alert severity="success" onClose={() => setSaved("")}>{saved}</Alert>}
      <FilterBar onApply={() => f.apply()} onClear={f.clear} active={f.active}
        extra={
          <Button variant="contained" startIcon={<DownloadIcon />} sx={{ mt: 0.25 }}
            title="Download the matching errors as a JSON file (the export is itself audited)"
            href={`/api/admin/errors/export?${query}`}>
            Export JSON
          </Button>
        }>
        <Field label="User" value={f.draft.username} sx={{ width: 170 }} help="Exact user name"
          onChange={(e) => f.set("username", e.target.value)} />
        <Field label="Where" value={f.draft.path} sx={{ width: 190 }} help="Path or page contains"
          onChange={(e) => f.set("path", e.target.value)} />
        <Field label="Message" value={f.draft.message} sx={{ width: 190 }} help="Contains"
          onChange={(e) => f.set("message", e.target.value)} />
        <SelectFilter label="Source" value={f.draft.origin} options={["server", "browser"]} width={130}
          onChange={(v) => f.set("origin", v)} />
        <Field label="Status" value={f.draft.status} sx={{ width: 110 }} help="e.g. 422"
          onChange={(e) => f.set("status", e.target.value.replace(/\D/g, ""))} />
        <DateRangeFields since={f.draft.since} until={f.draft.until} onChange={f.set} />
      </FilterBar>
      <Stack direction="row" spacing={2} useFlexGap sx={{ alignItems: "flex-start", flexWrap: "wrap" }}>
        <Typography variant="body2" color="text.secondary" sx={{ mt: 1 }}>
          {rows.length} error{rows.length === 1 ? "" : "s"}{f.active ? " match" : ""}
        </Typography>
        <Box sx={{ flexGrow: 1 }} />
        <Field label="Keep (days)" type="number" value={retention} sx={{ width: 140 }}
          onChange={(e) => setRetention(Number(e.target.value))} help="1-3650" />
        <ActionButton onClick={saveRetention} sx={{ mt: 0.25 }} tip="Keep user errors this many days"
          confirm={{ title: "Change how long errors are kept?", confirmLabel: "Save",
            body: `User errors older than ${retention} days are deleted at the next daily purge. The same value is under Settings > History.` }}>
          Save
        </ActionButton>
      </Stack>
      <Table size="small">
        <TableHead>
          <TableRow>
            <TableCell>When</TableCell><TableCell>User</TableCell><TableCell>Where</TableCell>
            <TableCell>Problem</TableCell>
          </TableRow>
        </TableHead>
        <TableBody>
          {rows.map((r) => (
            <TableRow key={r.id}>
              <TableCell sx={{ whiteSpace: "nowrap" }}>{fmtTime(r.at)}</TableCell>
              <TableCell>{r.username || "(signed out)"}</TableCell>
              <TableCell sx={{ whiteSpace: "nowrap" }}>
                <Chip size="small" label={r.origin === "browser" ? "browser" : `${r.method} ${r.status}`}
                  color={r.origin === "browser" ? "default" : "warning"} sx={{ mr: 1 }} />
                {r.path}
              </TableCell>
              <TableCell>
                <Stack direction="row" spacing={0.5} sx={{ alignItems: "flex-start" }}>
                  <ErrorOutlineIcon color="error" sx={{ fontSize: 18, mt: "2px" }} />
                  <span>{r.message}</span>
                </Stack>
                {r.fields.length > 0 && (
                  <Box component="ul" sx={{ m: 0, pl: 3 }}>
                    {r.fields.map((fp, k) => <li key={k}><b>{fp.label || fp.field}</b>: {fp.message}</li>)}
                  </Box>
                )}
              </TableCell>
            </TableRow>
          ))}
          {rows.length === 0 && (
            <TableRow><TableCell colSpan={4}>{f.active ? "No errors match these filters." : "No errors recorded."}</TableCell></TableRow>
          )}
        </TableBody>
      </Table>
    </Stack>
  );
}
