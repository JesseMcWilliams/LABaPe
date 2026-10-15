import { useEffect, useState } from "react";
import { Alert, Button, Stack, Table, TableBody, TableCell, TableHead, TableRow, Tooltip, Typography } from "@mui/material";
import DownloadIcon from "@mui/icons-material/Download";
import { api, AuditEntry, fmtTime } from "../api";
import { Field } from "../forms";
import { DateRangeFields, FilterBar, toQuery, useUrlFilters } from "../components/Filters";
import { Pager, usePageSize } from "../components/Paging";

const KEYS = ["actor", "action", "since", "until"];

// Who did what, when. Filters are kept in the URL (design §28, O1).
export default function Audit() {
  const [rows, setRows] = useState<AuditEntry[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(0);
  const [size] = usePageSize();
  const [error, setError] = useState("");
  const f = useUrlFilters(KEYS);
  const query = toQuery(f.applied).toString();

  useEffect(() => { setPage(0); }, [query]);
  useEffect(() => {
    api.getPage<AuditEntry>(`/api/admin/audit?limit=${size}&offset=${page * size}&${query}`).then((r) => {
      setRows(r.rows); setTotal(r.total); setError("");
    }, (e) => setError(e.message));
  }, [query, page, size]);

  return (
    <Stack spacing={2}>
      <Typography variant="h5">Audit log</Typography>
      {error && <Alert severity="error">{error}</Alert>}
      <FilterBar onApply={() => f.apply()} onClear={f.clear} active={f.active}
        extra={
          <>
            <Button variant="contained" startIcon={<DownloadIcon />} sx={{ mt: 0.25 }}
              title="Download the matching entries as a JSON file (the export is itself audited)"
              href={`/api/admin/audit/export?format=json&${query}`}>
              Export JSON
            </Button>
            <Tooltip title="CSV export is planned">
              <span><Button disabled sx={{ mt: 0.25 }}>Export CSV</Button></span>
            </Tooltip>
          </>
        }>
        <Field label="Actor" value={f.draft.actor} sx={{ width: 170 }} help="Exact user name"
          onChange={(e) => f.set("actor", e.target.value)} />
        <Field label="Action" value={f.draft.action} sx={{ width: 200 }} help="Starts with, e.g. environment. or auth."
          onChange={(e) => f.set("action", e.target.value)} />
        <DateRangeFields since={f.draft.since} until={f.draft.until} onChange={f.set} />
      </FilterBar>
      <Pager total={total} page={page} onPage={setPage} />
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
      <Pager total={total} page={page} onPage={setPage} />
    </Stack>
  );
}
