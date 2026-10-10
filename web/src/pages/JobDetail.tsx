import { useEffect, useRef, useState } from "react";
import { Link as RouterLink, useNavigate, useParams } from "react-router-dom";
import { Alert, Box, Button, FormControlLabel, Link, Paper, Stack, Switch, Typography } from "@mui/material";
import { api, fmtTime, Job } from "../api";
import { StateChip } from "../components";

const FINISHED = ["succeeded", "failed", "cancelled"];

export default function JobDetail() {
  const { id } = useParams();
  const navigate = useNavigate();
  const [job, setJob] = useState<Job | null>(null);
  const [log, setLog] = useState("");
  const [follow, setFollow] = useState(true);
  const [error, setError] = useState("");
  const logRef = useRef<HTMLPreElement>(null);

  // Job metadata, refreshed until it finishes.
  useEffect(() => {
    let timer: ReturnType<typeof setInterval>;
    const load = async () => {
      try {
        const j = await api.get<Job>(`/api/jobs/${id}`);
        setJob(j);
        if (FINISHED.includes(j.state)) clearInterval(timer);
      } catch (e) {
        setError((e as Error).message);
      }
    };
    load();
    timer = setInterval(load, 3000);
    return () => clearInterval(timer);
  }, [id]);

  // Live log over Server-Sent Events; on a reconnect, resume from the last offset.
  useEffect(() => {
    setLog("");
    let offset = 0;
    let es: EventSource | null = null;
    let closed = false;
    const open = () => {
      es = new EventSource(`/api/jobs/${id}/log?offset=${offset}`);
      es.addEventListener("log", (ev) => {
        const data = JSON.parse((ev as MessageEvent).data);
        offset = data.offset;
        setLog((prev) => prev + data.text);
      });
      es.addEventListener("end", () => {
        closed = true;
        es?.close();
      });
      es.onerror = () => {
        es?.close();
        if (!closed) setTimeout(open, 3000);
      };
    };
    open();
    return () => {
      closed = true;
      es?.close();
    };
  }, [id]);

  useEffect(() => {
    if (follow && logRef.current) logRef.current.scrollTop = logRef.current.scrollHeight;
  }, [log, follow]);

  const cancel = async () => {
    try {
      setJob(await api.post<Job>(`/api/jobs/${id}/cancel`));
    } catch (e) {
      setError((e as Error).message);
    }
  };
  const retry = async () => {
    try {
      const j = await api.post<Job>(`/api/jobs/${id}/retry`);
      navigate(`/jobs/${j.id}`);
    } catch (e) {
      setError((e as Error).message);
    }
  };

  if (!job) return error ? <Alert severity="error">{error}</Alert> : null;
  const done = FINISHED.includes(job.state);

  return (
    <Stack spacing={2}>
      <Stack direction="row" spacing={2} sx={{ alignItems: "center" }}>
        <Typography variant="h5">Job #{job.id}</Typography>
        <StateChip state={job.state} />
        <Typography variant="body2" color="text.secondary">
          {job.type}
          {job.environment_id && (
            <> for <Link component={RouterLink} to={`/environments/${job.environment_id}`}>environment #{job.environment_id}</Link></>
          )}
          {" "}· {job.requested_by} · started {fmtTime(job.started_at)}
          {job.finished_at && ` · finished ${fmtTime(job.finished_at)}`}
          {job.exit_code !== null && ` · exit ${job.exit_code}`}
          {job.retry_of && ` · retry of #${job.retry_of}`}
        </Typography>
      </Stack>
      {error && <Alert severity="error">{error}</Alert>}
      <Stack direction="row" spacing={2} sx={{ alignItems: "center" }}>
        {!done && (
          <Button color="error" variant="outlined" onClick={cancel} disabled={job.cancel_requested}>
            {job.cancel_requested ? "Cancelling…" : "Cancel"}
          </Button>
        )}
        {(job.state === "failed" || job.state === "cancelled") && (
          <Button variant="outlined" onClick={retry}>Retry</Button>
        )}
        <Box sx={{ flexGrow: 1 }} />
        <FormControlLabel control={<Switch checked={follow} onChange={(e) => setFollow(e.target.checked)} />}
          label="Follow" />
      </Stack>
      <Paper variant="outlined" sx={{ bgcolor: "#111", color: "#ddd" }}>
        <pre ref={logRef} style={{ margin: 0, padding: 12, height: "65vh", overflow: "auto", fontSize: 12,
          whiteSpace: "pre-wrap", wordBreak: "break-all" }}>
          {log || (job.state === "queued" ? "Waiting for a worker…" : "")}
        </pre>
      </Paper>
    </Stack>
  );
}
