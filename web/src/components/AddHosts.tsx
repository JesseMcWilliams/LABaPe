// Add hosts to an existing environment (design §63.1, batch E2): more VMs in a group, or a new group that
// copies an existing one. The server runs the checks of a new environment and queues a redeploy.
import { useState } from "react";
import {
  Alert, Button, Dialog, DialogActions, DialogContent, DialogTitle, MenuItem, Stack, ToggleButton, ToggleButtonGroup, Typography,
} from "@mui/material";
import { api, Environment } from "../api";
import { ErrorSummary, Field, FieldErrors, fromApiError, rules } from "../forms";

const MAX = 20;                                          // hosts in one group, as in a new environment

export default function AddHosts({ env, disabled, onStarted }: {
  env: Environment; disabled?: boolean; onStarted: (jobId: number) => void;
}) {
  const groups = env.spec.host_groups;
  const [open, setOpen] = useState(false);
  const [mode, setMode] = useState<"group" | "like">("group");
  const [group, setGroup] = useState(groups[0]?.name ?? "");
  const [name, setName] = useState("");
  const [add, setAdd] = useState(1);
  const [names, setNames] = useState<string[]>([""]);
  const [busy, setBusy] = useState(false);
  const [errs, setErrs] = useState<{ summary: string; fields: FieldErrors }>({ summary: "", fields: {} });

  const src = groups.find((g) => g.name === group);
  const have = src?.count ?? 1;
  const room = mode === "group" ? MAX - have : MAX;
  const clash = mode === "like" && groups.some((g) => g.name.toLowerCase() === name.toLowerCase());
  const nameProblem = mode === "like" ? (name ? (rules.groupName(name) || (clash ? "That group name is already used (names are compared without case)." : "")) : "Give the new group a name.") : "";
  const fresh = (k: number) => `${mode === "like" ? name || "group" : group}${(mode === "group" ? have : 0) + k + 1}`;
  const resize = (n: number) => { setAdd(n); setNames((cur) => Array.from({ length: Math.max(1, Math.min(n, MAX)) }, (_, k) => cur[k] ?? "")); };
  const reset = () => { setMode("group"); setGroup(groups[0]?.name ?? ""); setName(""); resize(1); setNames([""]); setErrs({ summary: "", fields: {} }); };

  const submit = async () => {
    setBusy(true); setErrs({ summary: "", fields: {} });
    try {
      const body = { ...(mode === "group" ? { group } : { like: group, name }), add, names: names.slice(0, add) };
      const r = await api.post<{ job_id: number }>(`/api/environments/${env.id}/hosts`, body);
      setOpen(false);
      onStarted(r.job_id);
    } catch (e) { setErrs(fromApiError(e)); } finally { setBusy(false); }
  };
  const bad = !group || add < 1 || add > room || Boolean(nameProblem) || room < 1;
  return (
    <>
      <Button variant="outlined" disabled={disabled} onClick={() => { reset(); setOpen(true); }}
        title="Add VMs to a host group, or a new group like an existing one, and deploy them">Add host</Button>
      <Dialog open={open} onClose={() => setOpen(false)} maxWidth="sm" fullWidth>
        <DialogTitle>Add hosts to {env.name}</DialogTitle>
        <DialogContent>
          <Stack spacing={2} sx={{ mt: 1 }}>
            <ErrorSummary summary={errs.summary} fields={errs.fields} />
            <ToggleButtonGroup size="small" exclusive value={mode} onChange={(_, v) => v && setMode(v)}>
              <ToggleButton value="group" title="More VMs with the same settings as an existing host group">More hosts in a group</ToggleButton>
              <ToggleButton value="like" title="A new host group that copies an existing one's OS, size, network and software">A new group like an existing one</ToggleButton>
            </ToggleButtonGroup>
            <Stack direction="row" spacing={2} useFlexGap sx={{ flexWrap: "wrap", alignItems: "flex-start" }}>
              <Field select label={mode === "group" ? "Host group" : "Copy of group"} value={group} sx={{ minWidth: 220 }}
                onChange={(e) => setGroup(e.target.value)} error={errs.fields.group ?? errs.fields.like}>
                {groups.map((g) => <MenuItem key={g.name} value={g.name}>{g.name} ({g.count ?? 1} host{(g.count ?? 1) === 1 ? "" : "s"}, {g.os})</MenuItem>)}
              </Field>
              {mode === "like" && (
                <Field label="New group name" value={name} onChange={(e) => setName(e.target.value)}
                  error={name || clash ? nameProblem || errs.fields.name : errs.fields.name} help="1-12 letters and digits" />
              )}
              <Field label="How many" type="number" value={add} sx={{ width: 110 }} error={errs.fields.add ?? (add < 1 || add > room ? `1 to ${Math.max(room, 0)}` : undefined)}
                onChange={(e) => resize(Math.max(1, Math.min(Number(e.target.value) || 1, MAX)))}
                help={mode === "group" ? `${have} now, up to ${MAX}` : `up to ${MAX}`} />
            </Stack>
            <Typography variant="body2">Host names (optional; blank names are generated):</Typography>
            <Stack direction="row" spacing={1} useFlexGap sx={{ flexWrap: "wrap" }}>
              {names.map((n, k) => (
                <Field key={k} label={`New host ${k + 1}`} value={n} placeholder={fresh(k)} sx={{ width: 160 }}
                  error={k === 0 ? errs.fields.names : undefined}
                  onChange={(e) => setNames(names.map((x, j) => (j === k ? e.target.value : x)))} />
              ))}
            </Stack>
            <Alert severity="info">
              This changes the environment and starts a redeploy. Only the new VMs are created, with the settings of the group
              they join; addresses come from the pool. Existing VMs are reinstalled only if their install recipe changed (see the
              redeploy preview). The same checks as a new environment apply: names across the KVM host, capacity, quotas and
              software for the OS.
            </Alert>
          </Stack>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setOpen(false)}>Cancel</Button>
          <Button variant="contained" disabled={busy || bad} onClick={submit} title="Create the VMs and deploy them to the environment">Add {add} host{add === 1 ? "" : "s"} and deploy</Button>
        </DialogActions>
      </Dialog>
    </>
  );
}
