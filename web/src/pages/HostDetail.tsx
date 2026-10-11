import { FormEvent, useEffect, useState } from "react";
import { Link as RouterLink, useNavigate, useParams } from "react-router-dom";
import {
  Alert, Box, Button, Checkbox, FormControlLabel, Link, Paper, Stack, Table, TableBody, TableCell, TableHead,
  TableRow, Typography,
} from "@mui/material";
import { api, KvmHost, Network } from "../api";
import { hasRole, useAuth } from "../App";
import HostLoad from "../components/HostLoad";
import { DetailHeader, Facts } from "../components/PageParts";
import { ErrorSummary, Field, FieldErrors, fromApiError } from "../forms";
import HostNetworks from "./HostNetworks";

type HostForm = Omit<KvmHost, "id" | "resources">;

const blank: HostForm = {
  name: "", libvirt_uri: "qemu:///system", vm_storage_path: "/data/VMs/LABaPe",
  template_storage_path: "/data/VMs/LABaPe/templates", bridge: "br0", concurrency_limit: 2, enabled: true,
};

// One KVM host (design §22.2): settings, the networks it carries, and its
// load. Admins edit; others see it read-only.
export default function HostDetail() {
  const { id } = useParams();
  const isNew = id === "new";
  const navigate = useNavigate();
  const { me } = useAuth();
  const isAdmin = hasRole(me, "admin");
  const [host, setHost] = useState<KvmHost | null>(null);
  const [form, setForm] = useState<HostForm>(blank);
  const [nets, setNets] = useState<Network[]>([]);
  const [errs, setErrs] = useState<{ summary: string; fields: FieldErrors }>({ summary: "", fields: {} });

  useEffect(() => {
    if (isNew) return;
    api.get<KvmHost>(`/api/hosts/${id}`).then((h) => {
      const { id: _id, resources: _r, ...rest } = h;
      setHost(h);
      setForm(rest);
    }, (e) => setErrs({ summary: e.message, fields: {} }));
    api.get<Network[]>(`/api/networks?kvm_host_id=${id}`).then(setNets, () => setNets([]));
  }, [id, isNew]);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setErrs({ summary: "", fields: {} });
    try {
      if (isNew) {
        const created = await api.post<KvmHost>("/api/hosts", form);
        navigate(`/hosts/${created.id}`);
      } else {
        await api.put(`/api/hosts/${id}`, form);
        navigate("/hosts");
      }
    } catch (err) {
      setErrs(fromApiError(err));
    }
  };

  const field = (key: keyof HostForm, label: string, help?: string, type = "text") => (
    <Field label={label} type={type} value={form[key] as string | number} help={help} error={errs.fields[key]}
      onChange={(e) => setForm({ ...form, [key]: type === "number" ? Number(e.target.value) : e.target.value })} />
  );

  const header = (
    <DetailHeader back="/hosts" backLabel="KVM hosts" title={isNew ? "Register a KVM host" : host?.name ?? "KVM host"} />
  );

  return (
    <Stack spacing={2}>
      {header}
      {isAdmin ? (
        <Paper variant="outlined" sx={{ p: 2 }} component="form" onSubmit={submit} noValidate>
          <Stack spacing={2}>
            <ErrorSummary summary={errs.summary} fields={errs.fields} />
            <Stack direction="row" spacing={2} useFlexGap sx={{ flexWrap: "wrap", alignItems: "flex-start" }}>
              {field("name", "Name")}
              {field("libvirt_uri", "libvirt URI", "qemu:///system for this machine")}
              {field("vm_storage_path", "VM storage path")}
              {field("template_storage_path", "Template storage path")}
              {field("bridge", "Fallback bridge", "Used when a network has no bridge here")}
              {field("concurrency_limit", "Concurrent jobs", "Jobs run at once on this host", "number")}
              <FormControlLabel label="Enabled" sx={{ mt: 0.5 }}
                control={<Checkbox checked={form.enabled} onChange={(e) => setForm({ ...form, enabled: e.target.checked })} />} />
            </Stack>
            <Typography variant="body2" color="text.secondary">
              Only the local host (qemu:///system, the libvirt socket mounted into the container) is supported for now.
              Remote hosts over qemu+ssh come with the installer profiles (design §22.4).
            </Typography>
            <Stack direction="row" spacing={2}>
              <Button type="submit" variant="contained">{isNew ? "Register" : "Save"}</Button>
              <Button component={RouterLink} to="/hosts">Cancel</Button>
            </Stack>
          </Stack>
        </Paper>
      ) : (
        <>
          {errs.summary && <Alert severity="error">{errs.summary}</Alert>}
          {host && (
            <Facts rows={[
              ["libvirt URI", host.libvirt_uri],
              ["VM storage", host.vm_storage_path],
              ["Templates", host.template_storage_path],
              ["State", host.enabled ? "enabled" : "disabled"],
            ]} />
          )}
        </>
      )}

      {host && (
        <Box>
          <Typography variant="h6" gutterBottom>Resources and load</Typography>
          <HostLoad resources={host.resources} />
        </Box>
      )}

      {host && isAdmin && <HostNetworks host={host} />}
      {host && !isAdmin && (
        <Box>
          <Typography variant="h6" gutterBottom>Networks you can use here</Typography>
          <Table size="small">
            <TableHead>
              <TableRow><TableCell>Network</TableCell><TableCell>Range</TableCell><TableCell>Addressing</TableCell></TableRow>
            </TableHead>
            <TableBody>
              {nets.map((n) => (
                <TableRow key={n.id}>
                  <TableCell>
                    <Link component={RouterLink} to={`/networks/${n.id}`}>{n.name}</Link>{n.is_default ? " (default)" : ""}
                  </TableCell>
                  <TableCell>{n.cidr}</TableCell>
                  <TableCell>{n.addressing.join(", ")}</TableCell>
                </TableRow>
              ))}
              {nets.length === 0 && <TableRow><TableCell colSpan={3}>None.</TableCell></TableRow>}
            </TableBody>
          </Table>
        </Box>
      )}
    </Stack>
  );
}
