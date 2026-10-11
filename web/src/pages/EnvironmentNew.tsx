import { FormEvent, useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  Alert, Button, Checkbox, FormControlLabel, IconButton, MenuItem, Paper, Stack, TextField, Typography,
} from "@mui/material";
import DeleteIcon from "@mui/icons-material/Delete";
import { api, HostGroup, KvmHost } from "../api";

// Phase 10a's tfvars-level form; environment templates replace it in 10c.
const OS_KEYS = [
  "rocky9", "ubuntu_lts", "ubuntu_26", "debian_latest",
  "windows_server_2019", "windows_server_2022", "windows_server_2025", "windows_11", "windows_10",
];
const ROLES = ["domain_controller", "windows_server", "windows_workstation", "linux_server", "linux_workstation"];

const blankGroup = (): HostGroup => ({
  name: "", count: 1, os: "rocky9", roles: ["linux_server"], image_source: "packer_template", template: "",
  cpu_count: 2, memory_mb: 4096, disk_gb: 40, windows_core: false,
});

export default function EnvironmentNew() {
  const navigate = useNavigate();
  const [hosts, setHosts] = useState<KvmHost[]>([]);
  const [name, setName] = useState("");
  const [hostId, setHostId] = useState<number | "">("");
  const [offset, setOffset] = useState(150);
  const [groups, setGroups] = useState<HostGroup[]>([blankGroup()]);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api.get<KvmHost[]>("/api/hosts").then((h) => {
      const enabled = h.filter((x) => x.enabled);
      setHosts(enabled);
      if (enabled.length) setHostId(enabled[0].id);
    });
  }, []);

  const setGroup = (i: number, patch: Partial<HostGroup>) =>
    setGroups(groups.map((g, j) => (j === i ? { ...g, ...patch } : g)));

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setError("");
    setBusy(true);
    try {
      const host_groups = groups.map((g) => ({
        ...g,
        template: g.image_source === "packer_template" ? g.template : undefined,
        windows_core: g.os.startsWith("windows_server") ? g.windows_core : false,
      }));
      const r = await api.post<{ environment: { id: number } }>("/api/environments", {
        name, kvm_host_id: hostId, static_ip_offset_start: offset, host_groups,
      });
      navigate(`/environments/${r.environment.id}`);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <Stack spacing={2} component="form" onSubmit={submit}>
      <Typography variant="h5">New environment</Typography>
      {error && <Alert severity="error">{error}</Alert>}
      {hosts.length === 0 && <Alert severity="warning">No KVM host is registered yet; an admin adds one under Hosts.</Alert>}
      <Stack direction="row" spacing={2}>
        <TextField label="Name" value={name} onChange={(e) => setName(e.target.value)} required
          helperText="Lower case, 3–40 characters; also the OpenTofu workspace" />
        <TextField select label="KVM host" value={hostId} onChange={(e) => setHostId(Number(e.target.value))}
          sx={{ minWidth: 180 }} required>
          {hosts.map((h) => (
            <MenuItem key={h.id} value={h.id}>{h.name}</MenuItem>
          ))}
        </TextField>
        <TextField label="First static IP offset" type="number" value={offset}
          onChange={(e) => setOffset(Number(e.target.value))}
          helperText="Host number within the lab network for the first VM" />
      </Stack>
      <Typography variant="h6">Host groups</Typography>
      {groups.map((g, i) => (
        <Paper key={i} variant="outlined" sx={{ p: 2 }}>
          <Stack spacing={2}>
            <Stack direction="row" spacing={2} sx={{ alignItems: "center" }}>
              <TextField label="Group name" value={g.name} onChange={(e) => setGroup(i, { name: e.target.value })}
                required helperText="VM names are host-wide: keep it unique" />
              <TextField label="Count" type="number" value={g.count} sx={{ width: 90 }}
                onChange={(e) => setGroup(i, { count: Number(e.target.value) })} />
              <TextField select label="OS" value={g.os} sx={{ minWidth: 200 }}
                onChange={(e) => setGroup(i, { os: e.target.value })}>
                {OS_KEYS.map((k) => <MenuItem key={k} value={k}>{k}</MenuItem>)}
              </TextField>
              <TextField select label="Roles" value={g.roles} sx={{ minWidth: 220 }}
                slotProps={{ select: { multiple: true } }}
                onChange={(e) => setGroup(i, { roles: e.target.value as unknown as string[] })}>
                {ROLES.map((r) => <MenuItem key={r} value={r}>{r}</MenuItem>)}
              </TextField>
              <IconButton aria-label="Remove group" disabled={groups.length === 1}
                onClick={() => setGroups(groups.filter((_, j) => j !== i))}>
                <DeleteIcon />
              </IconButton>
            </Stack>
            <Stack direction="row" spacing={2} sx={{ alignItems: "center" }}>
              <TextField select label="Image source" value={g.image_source} sx={{ minWidth: 200 }}
                onChange={(e) => setGroup(i, { image_source: e.target.value as HostGroup["image_source"] })}>
                <MenuItem value="packer_template">Clone a template</MenuItem>
                <MenuItem value="iso_direct">Install from ISO</MenuItem>
              </TextField>
              {g.image_source === "packer_template" && (
                <TextField label="Template" value={g.template} required
                  onChange={(e) => setGroup(i, { template: e.target.value })}
                  helperText="Name in the template library, e.g. rocky9-base" />
              )}
              <TextField label="vCPUs" type="number" value={g.cpu_count} sx={{ width: 90 }}
                onChange={(e) => setGroup(i, { cpu_count: Number(e.target.value) })} />
              <TextField label="Memory (MB)" type="number" value={g.memory_mb} sx={{ width: 130 }}
                onChange={(e) => setGroup(i, { memory_mb: Number(e.target.value) })} />
              <TextField label="Disk (GB)" type="number" value={g.disk_gb} sx={{ width: 110 }}
                onChange={(e) => setGroup(i, { disk_gb: Number(e.target.value) })} />
              {g.os.startsWith("windows_server") && (
                <FormControlLabel label="Server Core"
                  control={<Checkbox checked={g.windows_core}
                    onChange={(e) => setGroup(i, { windows_core: e.target.checked })} />} />
              )}
            </Stack>
          </Stack>
        </Paper>
      ))}
      <Stack direction="row" spacing={2}>
        <Button onClick={() => setGroups([...groups, blankGroup()])}>Add host group</Button>
        <Button type="submit" variant="contained" disabled={busy || !hostId}>
          Create and deploy
        </Button>
      </Stack>
    </Stack>
  );
}
