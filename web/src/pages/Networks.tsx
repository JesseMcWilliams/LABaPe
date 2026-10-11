import { FormEvent, useEffect, useState } from "react";
import {
  Alert, Button, Checkbox, Chip, FormControlLabel, Paper, Stack, Table, TableBody, TableCell, TableHead, TableRow,
  Typography,
} from "@mui/material";
import { api, FieldProblem, Network } from "../api";
import { check, ErrorSummary, Field, FieldErrors, fromApiError, reportBlocked, rules, toFieldErrors } from "../forms";

// The central network catalog: what VMs may use (design §20.1). Admin only.
type Form = Omit<Network, "id" | "allocated" | "bridge" | "is_default">;
type ListKey = "dns_servers" | "static_pools" | "dhcp_ranges" | "reserved" | "allowed_groups";
const LIST_KEYS: ListKey[] = ["dns_servers", "static_pools", "dhcp_ranges", "reserved", "allowed_groups"];

const blank: Form = {
  name: "", description: "", cidr: "", gateway: "", dns_servers: [], vlan: null, addressing: ["static"],
  static_pools: [], dhcp_ranges: [], reserved: [], allowed_roles: [], allowed_groups: [], enabled: true,
};
const ROLES = ["admin", "template_editor", "deployer", "file_manager", "viewer"];

const toList = (t: string) => t.split(/[\s,]+/).map((x) => x.trim()).filter(Boolean);
const fromList = (l: string[]) => l.join(", ");
const emptyLists = (): Record<ListKey, string> =>
  Object.fromEntries(LIST_KEYS.map((k) => [k, ""])) as Record<ListKey, string>;

export default function Networks() {
  const [nets, setNets] = useState<Network[]>([]);
  const [form, setForm] = useState<Form>(blank);
  // List fields are edited as free text and parsed on save, so typing a comma works.
  const [lists, setLists] = useState<Record<ListKey, string>>(emptyLists());
  const [editing, setEditing] = useState<number | null>(null);
  const [summary, setSummary] = useState("");
  const [errors, setErrors] = useState<FieldErrors>({});

  const load = () => api.get<Network[]>("/api/networks").then(setNets, (e) => setSummary(e.message));
  useEffect(() => {
    load();
  }, []);

  const startEdit = (n: Network) => {
    const { id, allocated: _a, ...rest } = n;
    setEditing(id);
    setForm(rest);
    setLists(Object.fromEntries(LIST_KEYS.map((k) => [k, fromList(n[k])])) as Record<ListKey, string>);
    setErrors({});
    setSummary("");
  };
  const reset = () => {
    setEditing(null);
    setForm(blank);
    setLists(emptyLists());
    setErrors({});
    setSummary("");
  };

  const validate = (body: Form): FieldProblem[] => check([
    ["name", "Name", rules.netName(body.name)],
    ["cidr", "CIDR", rules.cidr(body.cidr)],
    ["gateway", "Gateway", rules.ip(body.gateway)],
    ["dns_servers", "DNS servers", body.dns_servers.every((d) => !rules.ip(d)) ? "" : "Use IPv4 addresses, separated by commas."],
    ["static_pools", "Static pools", rules.ranges(body.static_pools) ||
      (body.addressing.includes("static") && !body.static_pools.length
        ? "Static addressing needs at least one static pool: the range LABaPe may hand out, outside the DHCP scope." : "")],
    ["dhcp_ranges", "DHCP scope", rules.ranges(body.dhcp_ranges)],
    ["reserved", "Reserved", rules.ranges(body.reserved)],
    ["addressing", "Addressing", body.addressing.length ? "" : "Choose static, DHCP or both."],
    ["vlan", "VLAN", body.vlan === null || (body.vlan >= 1 && body.vlan <= 4094) ? "" : "Use a number from 1 to 4094, or leave it empty."],
  ]);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setSummary("");
    const body: Form = { ...form, ...Object.fromEntries(LIST_KEYS.map((k) => [k, toList(lists[k])])) };
    const problems = validate(body);
    setErrors(toFieldErrors(problems));
    if (problems.length) {
      reportBlocked("/networks", problems);
      return;
    }
    try {
      if (editing) await api.put(`/api/networks/${editing}`, body);
      else await api.post("/api/networks", body);
      reset();
      load();
    } catch (err) {
      const { summary: s, fields } = fromApiError(err);
      setSummary(s);
      setErrors(fields);
    }
  };

  const remove = async (n: Network) => {
    if (!confirm(`Delete network ${n.name}?`)) return;
    try {
      await api.del(`/api/networks/${n.id}`);
      load();
    } catch (err) {
      setSummary((err as Error).message);
    }
  };

  const listField = (key: ListKey, label: string, help: string) => (
    <Field label={label} value={lists[key]} help={help} sx={{ minWidth: 300 }} error={errors[key]}
      onChange={(e) => setLists({ ...lists, [key]: e.target.value })} />
  );
  const toggle = <T extends string>(list: T[], v: T) => (list.includes(v) ? list.filter((x) => x !== v) : [...list, v]);
  const row = { alignItems: "flex-start", flexWrap: "wrap" } as const;

  return (
    <Stack spacing={2}>
      <Typography variant="h5">Networks</Typography>
      <Typography variant="body2" color="text.secondary">
        The networks VMs may use. Attach them to KVM hosts under Hosts. Static addresses are handed out from the
        static pools automatically; DHCP addresses are found after boot.
      </Typography>
      {!editing && summary && !Object.keys(errors).length && <Alert severity="error">{summary}</Alert>}
      <Table size="small">
        <TableHead>
          <TableRow>
            <TableCell>Name</TableCell><TableCell>Network</TableCell><TableCell>Addressing</TableCell>
            <TableCell>Static pools</TableCell><TableCell>DHCP scope</TableCell><TableCell>Who</TableCell>
            <TableCell>In use</TableCell><TableCell />
          </TableRow>
        </TableHead>
        <TableBody>
          {nets.map((n) => (
            <TableRow key={n.id} sx={{ opacity: n.enabled ? 1 : 0.5 }}>
              <TableCell>{n.name}{n.vlan ? ` (VLAN ${n.vlan})` : ""}</TableCell>
              <TableCell>{n.cidr} via {n.gateway || "(.1)"}; DNS {fromList(n.dns_servers) || "gateway"}</TableCell>
              <TableCell>{n.addressing.map((a) => <Chip key={a} size="small" label={a === "dhcp" ? "DHCP" : "Static"} sx={{ mr: 0.5 }} />)}</TableCell>
              <TableCell>{fromList(n.static_pools)}</TableCell>
              <TableCell>{fromList(n.dhcp_ranges)}</TableCell>
              <TableCell>
                {n.allowed_roles.length || n.allowed_groups.length
                  ? fromList([...n.allowed_roles, ...n.allowed_groups.map((g) => `group ${g}`)])
                  : "anyone who deploys"}
              </TableCell>
              <TableCell>{n.allocated}</TableCell>
              <TableCell sx={{ whiteSpace: "nowrap" }}>
                <Button size="small" onClick={() => startEdit(n)}>Edit</Button>
                <Button size="small" color="error" onClick={() => remove(n)}>Delete</Button>
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
      <Paper variant="outlined" sx={{ p: 2 }} component="form" onSubmit={submit} noValidate>
        <Stack spacing={1}>
          <Typography variant="h6">{editing ? `Edit ${form.name}` : "Add a network"}</Typography>
          <ErrorSummary summary={summary} fields={errors} />
          <Stack direction="row" spacing={2} useFlexGap sx={row}>
            <Field label="Name" value={form.name} required error={errors.name}
              onChange={(e) => setForm({ ...form, name: e.target.value })} help="e.g. lab-vlan48" />
            <Field label="CIDR" value={form.cidr} required error={errors.cidr}
              onChange={(e) => setForm({ ...form, cidr: e.target.value })} help="e.g. 172.21.48.0/22" />
            <Field label="Gateway" value={form.gateway} error={errors.gateway}
              onChange={(e) => setForm({ ...form, gateway: e.target.value })} help="Empty: the network's .1" />
            <Field label="VLAN ID (reference only)" type="number" value={form.vlan ?? ""} sx={{ width: 200 }}
              error={errors.vlan} help="A label; LABaPe doesn't tag traffic"
              onChange={(e) => setForm({ ...form, vlan: e.target.value ? Number(e.target.value) : null })} />
            <Field label="Description" value={form.description} sx={{ minWidth: 280 }}
              onChange={(e) => setForm({ ...form, description: e.target.value })} />
          </Stack>
          <Stack direction="row" spacing={2} useFlexGap sx={row}>
            {listField("dns_servers", "DNS servers", "Empty: the gateway")}
            {listField("static_pools", "Static pools", "Ranges LABaPe may assign, e.g. 172.21.50.1-172.21.51.254")}
            {listField("dhcp_ranges", "DHCP scope", "The DHCP server's range; never assigned statically")}
            {listField("reserved", "Reserved", "Never assigned (infrastructure)")}
          </Stack>
          <Stack direction="row" spacing={2} useFlexGap sx={{ ...row, alignItems: "center" }}>
            <Typography variant="body2" color={errors.addressing ? "error" : undefined}>Addressing:</Typography>
            {(["static", "dhcp"] as const).map((m) => (
              <FormControlLabel key={m} label={m === "dhcp" ? "DHCP" : "Static"} control={<Checkbox checked={form.addressing.includes(m)}
                onChange={() => setForm({ ...form, addressing: toggle(form.addressing, m) })} />} />
            ))}
            <FormControlLabel label="Enabled" control={<Checkbox checked={form.enabled}
              onChange={(e) => setForm({ ...form, enabled: e.target.checked })} />} />
          </Stack>
          <Stack direction="row" spacing={2} useFlexGap sx={{ ...row, alignItems: "center" }}>
            <Typography variant="body2">Limit to roles:</Typography>
            {ROLES.map((r) => (
              <FormControlLabel key={r} label={r} control={<Checkbox checked={form.allowed_roles.includes(r)}
                onChange={() => setForm({ ...form, allowed_roles: toggle(form.allowed_roles, r) })} />} />
            ))}
          </Stack>
          <Stack direction="row" spacing={2} useFlexGap sx={row}>
            {listField("allowed_groups", "Or limit to groups",
              "Authentik groups, comma-separated. No roles and no groups: anyone who deploys")}
          </Stack>
          <Stack direction="row" spacing={2}>
            <Button type="submit" variant="contained">{editing ? "Save" : "Add"}</Button>
            {editing && <Button onClick={reset}>Cancel</Button>}
          </Stack>
        </Stack>
      </Paper>
    </Stack>
  );
}
