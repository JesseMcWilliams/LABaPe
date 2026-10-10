import { useEffect, useState } from "react";
import { Alert, Stack, Table, TableBody, TableCell, TableHead, TableRow, Typography } from "@mui/material";
import { api, AuditEntry, fmtTime } from "../api";

export default function Audit() {
  const [rows, setRows] = useState<AuditEntry[]>([]);
  const [error, setError] = useState("");

  useEffect(() => {
    api.get<AuditEntry[]>("/api/admin/audit?limit=500").then(setRows, (e) => setError(e.message));
  }, []);

  return (
    <Stack spacing={2}>
      <Typography variant="h5">Audit log</Typography>
      {error && <Alert severity="error">{error}</Alert>}
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
