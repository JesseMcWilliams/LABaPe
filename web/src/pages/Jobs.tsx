import { useEffect, useState } from "react";
import { Link as RouterLink } from "react-router-dom";
import { Alert, Link, Stack, Table, TableBody, TableCell, TableHead, TableRow, Typography } from "@mui/material";
import { api, fmtTime, Job } from "../api";
import { StateChip } from "../components";

export default function Jobs() {
  const [jobs, setJobs] = useState<Job[]>([]);
  const [error, setError] = useState("");

  useEffect(() => {
    const load = () => api.get<Job[]>("/api/jobs").then(setJobs, (e) => setError(e.message));
    load();
    const t = setInterval(load, 5000);
    return () => clearInterval(t);
  }, []);

  return (
    <Stack spacing={2}>
      <Typography variant="h5">Jobs</Typography>
      {error && <Alert severity="error">{error}</Alert>}
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
                  <Link component={RouterLink} to={`/environments/${j.environment_id}`}>#{j.environment_id}</Link>
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
