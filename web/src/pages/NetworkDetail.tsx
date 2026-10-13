import { FormEvent, useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { Alert, Checkbox, Chip, FormControlLabel, Paper, Stack, Typography } from "@mui/material";
import { api, FieldProblem, Network } from "../api";
import { hasRole, useAuth } from "../App";
import GroupPicker from "../components/GroupPicker";
import { DetailHeader, Facts } from "../components/PageParts";
import { ActionButton, useConfirm } from "../components/Confirm";
import { check, ErrorSummary, Field, FieldErrors, fromApiError, reportBlocked, rules, toFieldErrors } from "../forms";

// One network in the catalog (design §20.1, §22.2): admins add and edit it
// here; others see it read-only when they may use it.
type Form = Omit<Network, "id" | "allocated" | "bridge" | "is_default">;
type ListKey = "dns_servers" | "static_pools" | "dhcp_ranges" | "reserved" | "allowed_groups";
const LIST_KEYS: ListKey[] = ["dns_servers", "static_pools", "dhcp_ranges", "reserved", "allowed_groups"];

const blank: Form = {
  name: "", description: "", cidr: "", gateway: "", dns_servers: [], vlan: null, addressing: ["static"],
  static_pools: [], dhcp_ranges: [], reserved: [], allowed_roles: [], allowed_groups: [], enabled: true,
};
const ROLES = ["admin", "template_editor", "deployer", "file_manager", "viewer", "auditor"];

const toList = (t: string) => t.split(/[\s,]+/).map((x) => x.trim()).filter(Boolean);
export const fromList = (l: string[]) => l.join(", ");
const emptyLists = (): Record<ListKey, string> =>
  Object.fromEntries(LIST_KEYS.map((k) => [k, ""])) as Record<ListKey, string>;

export default function NetworkDetail() {
  const { id } = useParams();
  const isNew = id === "new";
  const editing = isNew ? null : Number(id);
  const navigate = useNavigate();
  const { me } = useAuth();
  const isAdmin = hasRole(me, "admin");
  const [net, setNet] = useState<Network | null>(null);
  const [form, setForm] = useState<Form>(blank);
  // List fields are edited as free text and parsed on save, so typing a comma works.
  const [lists, setLists] = useState<Record<ListKey, string>>(emptyLists());
  const [summary, setSummary] = useState("");
  const [errors, setErrors] = useState<FieldErrors>({});
  const confirm = useConfirm();

  useEffect(() => {
    if (isNew) return;
    api.get<Network>(`/api/networks/${id}`).then((n) => {
      const { id: _id, allocated: _a, bridge: _b, is_default: _d, ...rest } = n;
      setNet(n);
      setForm({ ...blank, ...rest });
      setLists(Object.fromEntries(LIST_KEYS.map((k) => [k, fromList(n[k])])) as Record<ListKey, string>);
    }, (e) => setSummary(e.message));
  }, [id, isNew]);

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
      reportBlocked("/admin/networks", problems);
      return;
    }
    if (!(await confirm({
      title: editing ? `Save ${body.name}?` : `Add network ${body.name}?`, confirmLabel: editing ? "Save" : "Add",
      body: editing
        ? "The network's settings change for new deployments. Addresses already handed out stay as they are; ranges that no longer cover them are refused."
        : `${body.name} (${body.cidr}) joins the catalog. Attach it to KVM hosts on each host's page before anyone can use it.`,
    }))) return;
    try {
      if (editing) await api.put(`/api/networks/${editing}`, body);
      else await api.post("/api/networks", body);
      navigate("/admin/networks");
    } catch (err) {
      const { summary: s, fields } = fromApiError(err);
      setSummary(s);
      setErrors(fields);
    }
  };

  const remove = async (n: Network) => {
    try {
      await api.del(`/api/networks/${n.id}`);
      navigate("/admin/networks");
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

  const header = (
    <DetailHeader back="/admin/networks" backLabel="Networks"
      title={isNew ? "Add a network" : net ? `${net.name}${net.vlan ? ` (VLAN ${net.vlan})` : ""}` : "Network"}
      actions={isAdmin && net && (
        <ActionButton color="error" onClick={() => remove(net)} disabled={net.allocated > 0}
          tip={net.allocated ? "Can't delete: addresses are still allocated on this network" : "Delete this network from the catalog"}
          confirm={{ title: `Delete network ${net.name}?`, danger: true, confirmLabel: "Delete",
            body: "The network is removed from the catalog and detached from every KVM host. Host groups can no longer pick it. This can't be undone." }}>
          Delete
        </ActionButton>
      )} />
  );
  if (!isAdmin) {
    return (
      <Stack spacing={2}>
        {header}
        {summary && <Alert severity="error">{summary}</Alert>}
        {net && (
          <Facts rows={[
            ["Description", net.description],
            ["Network", `${net.cidr} via ${net.gateway || "(.1)"}`],
            ["DNS servers", fromList(net.dns_servers) || "the gateway"],
            ["Addressing", net.addressing.map((a) => <Chip key={a} size="small" label={a === "dhcp" ? "DHCP" : "Static"} sx={{ mr: 0.5 }} />)],
            ["Static pools", fromList(net.static_pools)],
            ["DHCP scope", fromList(net.dhcp_ranges)],
            ["Addresses in use", net.allocated],
          ]} />
        )}
      </Stack>
    );
  }

  return (
    <Stack spacing={2}>
      {header}
      <Paper variant="outlined" sx={{ p: 2 }} component="form" onSubmit={submit} noValidate>
        <Stack spacing={1}>
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
            <GroupPicker label="Or limit to groups" value={toList(lists.allowed_groups)} error={errors.allowed_groups}
              help="No roles and no groups: anyone who deploys"
              onChange={(v) => setLists({ ...lists, allowed_groups: fromList(v) })} />
          </Stack>
          <Stack direction="row" spacing={2}>
            <ActionButton type="submit" variant="contained"
              tip={editing ? "Save the changes (asks to confirm)" : "Add this network to the catalog (asks to confirm)"}>
              {editing ? "Save" : "Add"}
            </ActionButton>
            <ActionButton tip="Leave without saving" onClick={() => navigate("/admin/networks")}>Cancel</ActionButton>
          </Stack>
        </Stack>
      </Paper>
    </Stack>
  );
}
