import { useEffect, useState } from "react";
import { Alert, Button, Stack, Table, TableBody, TableCell, TableHead, TableRow, Tooltip, Typography } from "@mui/material";
import DownloadIcon from "@mui/icons-material/Download";
import { api, AuditEntry, fmtTime } from "../api";
import { Field } from "../forms";

export default function Audit() {
  const [rows, setRows] = useState<AuditEntry[]>([]);
  const [error, setError] = useState("");
  const [actor, setActor] = useState("");
  const [action, setAction] = useState("");

  const query = () => {
    const q = new URLSearchParams();
    if (actor.trim()) q.set("actor", actor.trim());
    if (action.trim()) q.set("action", action.trim());
    return q;
  };
  const load = () => {
    const q = query();
    q.set("limit", "500");
    api.get<AuditEntry[]>(`/api/admin/audit?${q}`).then(setRows, (e) => setError(e.message));
  };
  useEffect(() => {
    load();
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <Stack spacing={2}>
      <Typography variant="h5">Audit log</Typography>
      {error && <Alert severity="error">{error}</Alert>}
      <Stack direction="row" spacing={2} useFlexGap sx={{ alignItems: "flex-start", flexWrap: "wrap" }}>
        <Field label="Actor" value={actor} onChange={(e) => setActor(e.target.value)} help="Exact user name" />
        <Field label="Action" value={action} onChange={(e) => setAction(e.target.value)}
          help="Starts with, e.g. environment. or auth." />
        <Button variant="outlined" onClick={load} sx={{ mt: 0.25 }} title="Show only entries matching the actor and action">Filter</Button>
        <Button variant="contained" startIcon={<DownloadIcon />} sx={{ mt: 0.25 }}
          title="Download the matching entries as a JSON file (the export is itself audited)"
          href={`/api/admin/audit/export?format=json&${query()}`}>
          Export JSON
        </Button>
        <Tooltip title="CSV export is planned">
          <span><Button disabled sx={{ mt: 0.25 }}>Export CSV</Button></span>
        </Tooltip>
      </Stack>
      <Table size="small">
        <TableHead>
          <TableRow>
            <TableCell>When</TableCell><TableCell>Actor</TableCell><TableCell>Action</TableCell>
            <TableCell>Object</TableCell><TableCell>Outcome</TableCell><TableCell>Source</TableCell>
            <TableCell>Detail</TableCell>
          </TableRow>
        </TableHead>
        <TableBody>
          {rows.map((r) => (
            <TableRow key={r.id}>
              <TableCell sx={{ whiteSpace: "nowrap" }}>{fmtTime(r.at)}</TableCell>
              <TableCell>{r.actor}</TableCell>
              <TableCell>{r.action}</TableCell>
              <TableCell>{r.object_type && `${r.object_type} ${r.object_id}`}</TableCell>
              <TableCell>{r.outcome}</TableCell>
              <TableCell>{r.source_ip}</TableCell>
              <TableCell sx={{ fontFamily: "monospace", fontSize: 11, maxWidth: 420, overflowWrap: "anywhere" }}>
                {Object.keys(r.detail).length ? JSON.stringify(r.detail) : ""}
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </Stack>
  );
}
