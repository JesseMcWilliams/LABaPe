// A redeploy's plan preview (design §56.1): a job runs OpenTofu's plan (nothing is applied) and
// this dialog lists what a redeploy would do to each VM, with the reason for a reinstall.
import { useCallback, useEffect, useState } from "react";
import { Link as RouterLink } from "react-router-dom";
import {
  Alert, Button, Chip, Dialog, DialogActions, DialogContent, DialogTitle, Link, Stack, Table, TableBody, TableCell,
  TableHead, TableRow, Typography,
} from "@mui/material";
import { api, fmtTime, Job } from "../api";

interface PlanVm { name: string; action: "replace" | "create" | "delete" | "update" | "none"; reasons: string[] }
interface Preview {
  job_id?: number;
  state?: string;
  finished_at?: string | null;
  stale?: boolean;
  plan?: { vms: PlanVm[]; counts: Record<string, number>; other_changes: number } | null;
}

const ACTIONS: Record<PlanVm["action"], { label: string; color: "error" | "success" | "warning" | "info" | "default" }> = {
  replace: { label: "Reinstalled", color: "error" },
  create: { label: "Created", color: "success" },
  delete: { label: "Removed", color: "warning" },
  update: { label: "Updated", color: "info" },
  none: { label: "Kept", color: "default" },
};

export default function RedeployPreview({ envId, open, onClose }: { envId: number; open: boolean; onClose: () => void }) {
  const [preview, setPreview] = useState<Preview | null>(null);
  const [running, setRunning] = useState<number | null>(null);
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    try {
      setPreview(await api.get<Preview>(`/api/environments/${envId}/plan`));
    } catch (e) {
      setError((e as Error).message);
    }
  }, [envId]);

  useEffect(() => { if (open) { setError(""); load(); } }, [open, load]);

  useEffect(() => {                       // follow the plan job until it finishes
    if (running === null) return;
    const t = setInterval(async () => {
      try {
        const j = await api.get<Job>(`/api/jobs/${running}`);
        if (j.finished_at) {
          setRunning(null);
          load();
        }
      } catch (e) {
        setError((e as Error).message);
        setRunning(null);
      }
    }, 3000);
    return () => clearInterval(t);
  }, [running, load]);

  const start = async () => {
    setError("");
    try {
      setRunning((await api.post<{ job_id: number }>(`/api/environments/${envId}/plan`)).job_id);
    } catch (e) {
      setError((e as Error).message);
    }
  };

  const plan = preview?.plan;
  const replaced = plan?.vms.filter((v) => v.action === "replace").length ?? 0;
  return (
    <Dialog open={open} onClose={onClose} maxWidth="md" fullWidth>
      <DialogTitle>What a redeploy would do</DialogTitle>
      <DialogContent>
        <Stack spacing={2}>
          <Typography variant="body2" color="text.secondary">
            A preview job runs OpenTofu&apos;s plan for this environment and changes nothing. Ansible isn&apos;t previewed: a redeploy
            reconfigures every VM it keeps.
          </Typography>
          {error && <Alert severity="error">{error}</Alert>}
          {running !== null && (
            <Alert severity="info">
              Planning… (<Link component={RouterLink} to={`/jobs/${running}`}>job {running}</Link>). It can take a minute or two.
            </Alert>
          )}
          {running === null && preview && !preview.job_id && <Alert severity="info">No preview has run yet.</Alert>}
          {running === null && preview?.job_id && preview.state !== "succeeded" && (
            <Alert severity="warning">
              The last preview <Link component={RouterLink} to={`/jobs/${preview.job_id}`}>(job {preview.job_id})</Link> {preview.state}:
              see its log.
            </Alert>
          )}
          {running === null && plan && (
            <>
              <Alert severity={replaced ? "warning" : "success"}>
                {replaced ? `${replaced} VM(s) would be reinstalled: everything on them is lost. Snapshot them first if in doubt.`
                  : "No VM would be reinstalled."}
                {" "}Planned {fmtTime(preview?.finished_at ?? null)}
                {preview?.stale ? "; the environment was deployed or destroyed since, so run the preview again." : "."}
              </Alert>
              <Table size="small">
                <TableHead><TableRow><TableCell>VM</TableCell><TableCell>Redeploy</TableCell><TableCell>Why</TableCell></TableRow></TableHead>
                <TableBody>
                  {plan.vms.map((v) => (
                    <TableRow key={v.name}>
                      <TableCell>{v.name}</TableCell>
                      <TableCell><Chip size="small" label={ACTIONS[v.action]?.label ?? v.action} color={ACTIONS[v.action]?.color ?? "default"} /></TableCell>
                      <TableCell>{v.action === "replace" ? `Changed: ${v.reasons.join(", ") || "unknown"}` : ""}</TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
              {plan.other_changes > 0 && (
                <Typography variant="body2" color="text.secondary">
                  {plan.other_changes} other OpenTofu change(s) (answer files, network) come with it.
                </Typography>
              )}
            </>
          )}
        </Stack>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>Close</Button>
        <Button variant="contained" onClick={start} disabled={running !== null}>
          {preview?.job_id ? "Run the preview again" : "Run the preview"}
        </Button>
      </DialogActions>
    </Dialog>
  );
}
