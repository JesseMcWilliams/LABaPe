import { FormEvent, useEffect, useState } from "react";
import {
  Alert, Button, Checkbox, Paper, Stack, Table, TableBody, TableCell, TableHead, TableRow, TextField, Typography,
} from "@mui/material";
import { api, KvmHost } from "../api";
import HostNetworks from "./HostNetworks";

type HostForm = Omit<KvmHost, "id">;

const blank: HostForm = {
  name: "", libvirt_uri: "qemu:///system", vm_storage_path: "/data/VMs/LABaPe",
  template_storage_path: "/data/VMs/LABaPe/templates", bridge: "br0", concurrency_limit: 2, enabled: true,
};

export default function Hosts() {
  const [hosts, setHosts] = useState<KvmHost[]>([]);
  const [form, setForm] = useState<HostForm>(blank);
  const [editing, setEditing] = useState<number | null>(null);
  const [netsFor, setNetsFor] = useState<KvmHost | null>(null);
  const [error, setError] = useState("");

  const load = () => api.get<KvmHost[]>("/api/hosts").then(setHosts, (e) => setError(e.message));
  useEffect(() => {
    load();
  }, []);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setError("");
    try {
      if (editing) await api.put(`/api/hosts/${editing}`, form);
      else await api.post("/api/hosts", form);
      setForm(blank);
      setEditing(null);
      load();
    } catch (err) {
      setError((err as Error).message);
    }
  };

  const field = (key: keyof HostForm, label: string, type = "text") => (
    <TextField label={label} type={type} value={form[key] as string | number} size="small"
      onChange={(e) => setForm({ ...form, [key]: type === "number" ? Number(e.target.value) : e.target.value })} />
  );

  return (
    <Stack spacing={2}>
      <Typography variant="h5">KVM hosts</Typography>
      {error && <Alert severity="error">{error}</Alert>}
      <Table size="small">
        <TableHead>
          <TableRow>
            <TableCell>Name</TableCell><TableCell>libvirt URI</TableCell><TableCell>VM storage</TableCell>
            <TableCell>Templates</TableCell><TableCell>Bridge</TableCell><TableCell>Concurrent jobs</TableCell>
            <TableCell>Enabled</TableCell><TableCell />
          </TableRow>
        </TableHead>
        <TableBody>
          {hosts.map((h) => (
            <TableRow key={h.id}>
              <TableCell>{h.name}</TableCell><TableCell>{h.libvirt_uri}</TableCell>
              <TableCell>{h.vm_storage_path}</TableCell><TableCell>{h.template_storage_path}</TableCell>
              <TableCell>{h.bridge}</TableCell><TableCell>{h.concurrency_limit}</TableCell>
              <TableCell>{h.enabled ? "yes" : "no"}</TableCell>
              <TableCell>
                <Button size="small" onClick={() => { const { id, ...rest } = h; setEditing(id); setForm(rest); }}>
                  Edit
                </Button>
                <Button size="small" onClick={() => setNetsFor(h)}>Networks</Button>
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
      {netsFor && <HostNetworks host={netsFor} onClose={() => setNetsFor(null)} />}
      <Paper variant="outlined" sx={{ p: 2 }} component="form" onSubmit={submit}>
        <Stack spacing={2}>
          <Typography variant="h6">{editing ? `Edit ${form.name}` : "Register a host"}</Typography>
          <Stack direction="row" spacing={2} useFlexGap sx={{ flexWrap: "wrap" }}>
            {field("name", "Name")}
            {field("libvirt_uri", "libvirt URI")}
            {field("vm_storage_path", "VM storage path")}
            {field("template_storage_path", "Template storage path")}
            {field("bridge", "Fallback bridge")}
            {field("concurrency_limit", "Concurrent jobs", "number")}
            <Stack direction="row" sx={{ alignItems: "center" }}>
              <Checkbox checked={form.enabled} onChange={(e) => setForm({ ...form, enabled: e.target.checked })} />
              Enabled
            </Stack>
          </Stack>
          <Typography variant="body2" color="text.secondary">
            Phase 10a supports the local host (qemu:///system, the libvirt socket mounted into the container).
            Remote hosts over qemu+ssh arrive in phase 10e.
          </Typography>
          <Stack direction="row" spacing={2}>
            <Button type="submit" variant="contained">{editing ? "Save" : "Register"}</Button>
            {editing && <Button onClick={() => { setEditing(null); setForm(blank); }}>Cancel</Button>}
          </Stack>
        </Stack>
      </Paper>
    </Stack>
  );
}
