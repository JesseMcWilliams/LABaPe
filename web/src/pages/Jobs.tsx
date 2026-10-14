import { useEffect, useState } from "react";
import { Link as RouterLink } from "react-router-dom";
import { Alert, Link, Stack, Table, TableBody, TableCell, TableHead, TableRow, Typography } from "@mui/material";
import { api, fmtTime, Job } from "../api";
import { StateChip } from "../components";
import { DateRangeFields, FilterBar, SelectFilter, toQuery, useUrlFilters } from "../components/Filters";
import { Field } from "../forms";
import { ExportButton, Pager, usePageSize } from "../components/Paging";

interface Facets {
  types: string[];
  states: string[];
  requested_by: string[];
  environments: string[];
}

const KEYS = ["environment", "type", "state", "requested_by", "since", "until"];

// Jobs you may see: your own, and those of environments you can see (admins see all).
// Filters are kept in the URL (design §28, O1).
export default function Jobs() {
  const [jobs, setJobs] = useState<Job[]>([]);
  const [facets, setFacets] = useState<Facets>({ types: [], states: [], requested_by: [], environments: [] });
  const [error, setError] = useState("");
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(0);
  const [size] = usePageSize();
  const f = useUrlFilters(KEYS);
  const query = toQuery(f.applied).toString();
  useEffect(() => setPage(0), [query]);

  useEffect(() => {
    api.get<Facets>("/api/jobs/facets").then(setFacets, () => undefined);
  }, []);
  useEffect(() => {
    // Paged on the server (L1); refreshed every 5 seconds.
    const load = () => api.getPage<Job>(`/api/jobs?limit=${size}&offset=${page * size}&${query}`)
      .then((r) => { setJobs(r.rows); setTotal(r.total); setError(""); }, (e) => setError(e.message));
    load();
    const t = setInterval(load, 5000);
    return () => clearInterval(t);
  }, [query, page, size]);
  // Exports (question 160): the jobs as the API gives them (parameters and result, no logs), up to 1000 per call.
  const fetchJobs = async (offset: number, limit: number) => {
    const out: Job[] = [];
    while (out.length < limit) {
      const n = Math.min(1000, limit - out.length);
      const r = await api.getPage<Job>(`/api/jobs?limit=${n}&offset=${offset + out.length}&${query}`);
      out.push(...r.rows);
      if (r.rows.length < n) break;
    }
    return out;
  };

  return (
    <Stack spacing={2}>
      <Typography variant="h5">Jobs</Typography>
      {error && <Alert severity="error">{error}</Alert>}
      <FilterBar onApply={() => f.apply()} onClear={f.clear} active={f.active}>
        <Field label="Environment" value={f.draft.environment} sx={{ width: 200 }} help="Name contains"
          onChange={(e) => f.set("environment", e.target.value)}
          slotProps={{ htmlInput: { list: "job-environments" } }} />
        <datalist id="job-environments">
          {facets.environments.map((n) => <option key={n} value={n} />)}
        </datalist>
        <SelectFilter label="Type" value={f.draft.type} options={facets.types} width={210}
          onChange={(v) => f.set("type", v)} />
        <SelectFilter label="State" value={f.draft.state}
          options={Array.from(new Set(["queued", "running", "succeeded", "failed", "cancelled", ...facets.states]))}
          width={150} onChange={(v) => f.set("state", v)} />
        <SelectFilter label="Requested by" value={f.draft.requested_by} options={facets.requested_by}
          onChange={(v) => f.set("requested_by", v)} />
        <DateRangeFields since={f.draft.since} until={f.draft.until} onChange={f.set} />
      </FilterBar>
      <Stack direction="row" spacing={1} sx={{ alignItems: "center", justifyContent: "space-between", flexWrap: "wrap" }}>
        <Typography variant="body2" color="text.secondary">{total} job{total === 1 ? "" : "s"}{f.active ? " match" : ""}</Typography>
        <Stack direction="row" spacing={1} sx={{ alignItems: "center" }}>
          <ExportButton name="jobs" total={total} page={page} formats={["json", "csv"]} fetchRows={fetchJobs}
            columns={[{ header: "id", value: (j: Job) => j.id }, { header: "type", value: (j: Job) => j.type },
              { header: "state", value: (j: Job) => j.state }, { header: "environment", value: (j: Job) => j.environment_name ?? "" },
              { header: "requested_by", value: (j: Job) => j.requested_by }, { header: "created_at", value: (j: Job) => j.created_at },
              { header: "finished_at", value: (j: Job) => j.finished_at ?? "" }, { header: "exit_code", value: (j: Job) => j.exit_code ?? "" }]} />
          <Pager total={total} page={page} onPage={setPage} />
        </Stack>
      </Stack>
      <Table size="small">
        <TableHead>
          <TableRow>
            <TableCell>Job</TableCell><TableCell>Type</TableCell><TableCell>State</TableCell>
            <TableCell>Environment</TableCell><TableCell>Requested by</TableCell>
            <TableCell>Created</TableCell><TableCell>Finished</TableCell>
          </TableRow>
        </TableHead>
        <TableBody>
          {jobs.map((j) => (
            <TableRow key={j.id} hover>
              <TableCell><Link component={RouterLink} to={`/jobs/${j.id}`}>#{j.id}</Link></TableCell>
              <TableCell>{j.type}</TableCell>
              <TableCell><StateChip state={j.state} /></TableCell>
              <TableCell>
                {j.environment_id && (
                  <Link component={RouterLink} to={`/environments/${j.environment_id}`}
                    title={`Environment #${j.environment_id}`}>
                    {j.environment_name ?? `#${j.environment_id}`}
                  </Link>
                )}
                {j.environment_status === "destroyed" && (
                  <Typography component="span" variant="body2" color="text.secondary"> (destroyed)</Typography>
                )}
              </TableCell>
              <TableCell>{j.requested_by}</TableCell>
              <TableCell>{fmtTime(j.created_at)}</TableCell>
              <TableCell>{fmtTime(j.finished_at)}</TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </Stack>
  );
}
