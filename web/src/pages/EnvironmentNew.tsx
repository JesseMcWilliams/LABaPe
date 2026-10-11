import { FormEvent, useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  Alert, Box, Button, Checkbox, FormControlLabel, IconButton, ListItemText, MenuItem, Paper, Stack, Tooltip, Typography,
} from "@mui/material";
import DeleteIcon from "@mui/icons-material/Delete";
import InfoOutlinedIcon from "@mui/icons-material/InfoOutlined";
import { api, FieldProblem, HostGroup, HostResources, KvmHost, Network, Template } from "../api";
import HostLoad from "../components/HostLoad";
import { check, ErrorSummary, Field, FieldErrors, fromApiError, reportBlocked, rules, toFieldErrors } from "../forms";

// Phase 10a's tfvars-level form; environment templates replace it in 10c.
const OS_KEYS = [
  "rocky9", "ubuntu_lts", "ubuntu_26", "debian_latest",
  "windows_server_2019", "windows_server_2022", "windows_server_2025", "windows_11", "windows_10",
];
const ROLES = ["domain_controller", "windows_server", "windows_workstation", "linux_server", "linux_workstation"];

const blankGroup = (): HostGroup => ({
  name: "", count: 1, os: "rocky9", roles: ["linux_server"], image_source: "packer_template", template: "",
  cpu_count: 2, memory_mb: 4096, disk_gb: 40, windows_core: false, addressing: "static", names: [""],
});

const generated = (g: HostGroup, k: number) => `${g.name || "group"}${k + 1}`;

export default function EnvironmentNew() {
  const navigate = useNavigate();
  const [hosts, setHosts] = useState<KvmHost[]>([]);
  const [name, setName] = useState("");
  const [hostId, setHostId] = useState<number | "">("");
  const [networks, setNetworks] = useState<Network[]>([]);
  const [templates, setTemplates] = useState<Template[]>([]);
  const [resources, setResources] = useState<HostResources | undefined>();
  const [groups, setGroups] = useState<HostGroup[]>([blankGroup()]);
  const [summary, setSummary] = useState("");
  const [errors, setErrors] = useState<FieldErrors>({});
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api.get<KvmHost[]>("/api/hosts").then((h) => {
      const enabled = h.filter((x) => x.enabled);
      setHosts(enabled);
      if (enabled.length) setHostId(enabled[0].id);
    });
  }, []);

  // What the chosen host offers: its networks (that this user may use) and its template library.
  useEffect(() => {
    if (hostId === "") return;
    api.get<Network[]>(`/api/networks?kvm_host_id=${hostId}`).then(setNetworks, (e) => setSummary(e.message));
    api.get<Template[]>(`/api/templates?kvm_host_id=${hostId}`).then(setTemplates, () => setTemplates([]));
  }, [hostId]);

  // This environment's demand, projected onto the host's load.
  const demand = useMemo(() => ({
    vcpus: groups.reduce((n, g) => n + (g.count || 0) * (g.cpu_count || 0), 0),
    memory_mb: groups.reduce((n, g) => n + (g.count || 0) * (g.memory_mb || 0), 0),
    disk_gb: groups.reduce((n, g) => n + (g.count || 0) * (g.disk_gb || 0), 0),
  }), [groups]);
  useEffect(() => {
    if (hostId === "") return;
    const timer = setTimeout(() => {
      api.get<HostResources>(`/api/hosts/${hostId}/resources?vcpus=${demand.vcpus}&memory_mb=${demand.memory_mb}&disk_gb=${demand.disk_gb}`)
        .then(setResources, () => setResources(undefined));
    }, 300);
    return () => clearTimeout(timer);
  }, [hostId, demand]);
  const blocked = Boolean(resources?.assessment?.findings.some((f) => f.level === "block"));

  const netOf = (g: HostGroup) =>
    networks.find((n) => n.name === g.network) ?? networks.find((n) => n.is_default) ?? networks[0];
  const modesFor = (g: HostGroup) =>
    (netOf(g)?.addressing ?? ["static"]).filter((m) => m === "static" || !g.roles.includes("domain_controller"));
  const templatesFor = (g: HostGroup) => templates.filter((t) => !t.os_key || t.os_key === g.os);
  const templateOf = (g: HostGroup) => templates.find((t) => t.name === g.template);

  const setGroup = (i: number, patch: Partial<HostGroup>) => {
    setGroups(groups.map((g, j) => {
      if (j !== i) return g;
      const next = { ...g, ...patch };
      // Keep one name box per host.
      const names = [...(next.names ?? [])];
      next.names = Array.from({ length: Math.max(1, Math.min(next.count || 1, 20)) }, (_, k) => names[k] ?? "");
      // A new OS invalidates a template for another OS.
      if (patch.os && templateOf(next)?.os_key && templateOf(next)?.os_key !== patch.os) next.template = "";
      return next;
    }));
  };

  // Advisory: sizes below the template's needs (question: warn, don't block).
  const sizeWarnings = (g: HostGroup) => {
    const t = g.image_source === "packer_template" ? templateOf(g) : undefined;
    return {
      cpu: t && g.cpu_count < t.min_cpu ? `${t.name} needs at least ${t.min_cpu} vCPUs.` : "",
      mem: t && g.memory_mb < t.min_memory_mb ? `${t.name} needs at least ${t.min_memory_mb} MB.` : "",
      disk: t && t.disk_gb && g.disk_gb < t.disk_gb
        ? `${t.name}'s disk is ${t.disk_gb} GB; the clone will use ${t.disk_gb} GB.` : "",
    };
  };

  const validate = (): FieldProblem[] => {
    const entries: [string, string, string][] = [["name", "Name", rules.envName(name)]];
    const seen = new Map<string, string>();
    groups.forEach((g, i) => {
      const at = `Host group ${i + 1}`;
      entries.push([`host_groups.${i}.name`, `${at}: Group name`, rules.groupName(g.name)]);
      entries.push([`host_groups.${i}.count`, `${at}: Count`, rules.intRange(1, 20)(g.count)]);
      entries.push([`host_groups.${i}.cpu_count`, `${at}: vCPUs`, rules.intRange(1, 32)(g.cpu_count)]);
      entries.push([`host_groups.${i}.memory_mb`, `${at}: Memory`, rules.intRange(512, 262144, "MB")(g.memory_mb)]);
      entries.push([`host_groups.${i}.disk_gb`, `${at}: Disk`, rules.intRange(10, 2048, "GB")(g.disk_gb)]);
      if (g.image_source === "packer_template" && !g.template) {
        entries.push([`host_groups.${i}.template`, `${at}: Template`, "Choose a template for this OS."]);
      }
      (g.names ?? []).forEach((n, k) => {
        entries.push([`host_groups.${i}.names.${k}`, `${at}: Host name ${k + 1}`, rules.hostName(n)]);
      });
      Array.from({ length: g.count }, (_, k) => (g.names?.[k] || generated(g, k)).toLowerCase()).forEach((n, k) => {
        const where = `host_groups.${i}.${g.names?.[k] ? `names.${k}` : "name"}`;
        if (seen.has(n)) entries.push([where, `${at}: Host name`, `The VM name ${n} is used twice.`]);
        seen.set(n, where);
      });
    });
    return check(entries);
  };

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setSummary("");
    const problems = validate();
    setErrors(toFieldErrors(problems));
    if (problems.length) {
      reportBlocked("/environments/new", problems);
      return;
    }
    setBusy(true);
    try {
      const host_groups = groups.map((g) => ({
        ...g,
        network: netOf(g)?.name,
        template: g.image_source === "packer_template" ? g.template : undefined,
        windows_core: g.os.startsWith("windows_server") ? g.windows_core : false,
        names: (g.names ?? []).slice(0, g.count),
      }));
      const r = await api.post<{ environment: { id: number } }>("/api/environments", {
        name, kvm_host_id: hostId, host_groups,
      });
      navigate(`/environments/${r.environment.id}`);
    } catch (err) {
      const { summary: s, fields } = fromApiError(err);
      setSummary(s);
      setErrors(fields);
    } finally {
      setBusy(false);
    }
  };

  const err = (f: string) => errors[f];
  const row = { alignItems: "flex-start", flexWrap: "wrap" } as const;
  const hostName = useMemo(() => hosts.find((h) => h.id === hostId)?.name ?? "", [hosts, hostId]);

  return (
    <Stack spacing={2} component="form" onSubmit={submit} noValidate>
      <Typography variant="h5">New environment</Typography>
      <ErrorSummary summary={summary} fields={errors} />
      {hosts.length === 0 && <Alert severity="warning">No KVM host is registered yet; an admin adds one under Hosts.</Alert>}
      {hostId !== "" && networks.length === 0 && (
        <Alert severity="warning">This host has no networks you may deploy on; an admin attaches them under Hosts.</Alert>
      )}
      <Stack direction="row" spacing={2} useFlexGap sx={row}>
        <Field label="Name" value={name} onChange={(e) => setName(e.target.value)} required error={err("name")}
          help="3-40 lower-case letters, digits, hyphens; also the OpenTofu workspace" />
        <Field select label="KVM host" value={hostId} onChange={(e) => setHostId(Number(e.target.value))}
          sx={{ minWidth: 200 }} required error={err("kvm_host_id")}>
          {hosts.map((h) => <MenuItem key={h.id} value={h.id}>{h.name}</MenuItem>)}
        </Field>
      </Stack>
      {hostId !== "" && <HostLoad resources={resources} demand={demand} />}
      <Typography variant="h6">Host groups</Typography>
      {groups.map((g, i) => {
        const w = sizeWarnings(g);
        const t = templateOf(g);
        const at = `host_groups.${i}`;
        return (
          <Paper key={i} variant="outlined" sx={{ p: 2 }}>
            <Stack spacing={1}>
              <Stack direction="row" spacing={2} useFlexGap sx={row}>
                <Field label="Group name" value={g.name} onChange={(e) => setGroup(i, { name: e.target.value })}
                  required error={err(`${at}.name`)} help="1-12 lower-case letters and digits" />
                <Field label="Count" type="number" value={g.count} sx={{ width: 90 }} error={err(`${at}.count`)}
                  onChange={(e) => setGroup(i, { count: Number(e.target.value) })} />
                <Field select label="OS" value={g.os} sx={{ minWidth: 200 }} error={err(`${at}.os`)}
                  onChange={(e) => setGroup(i, { os: e.target.value })}>
                  {OS_KEYS.map((k) => <MenuItem key={k} value={k}>{k}</MenuItem>)}
                </Field>
                <Field select label="Roles" value={g.roles} sx={{ minWidth: 220 }} error={err(`${at}.roles`)}
                  slotProps={{ select: { multiple: true } }}
                  onChange={(e) => setGroup(i, { roles: e.target.value as unknown as string[] })}>
                  {ROLES.map((r) => <MenuItem key={r} value={r}>{r}</MenuItem>)}
                </Field>
                <IconButton aria-label="Remove group" disabled={groups.length === 1} sx={{ mt: 0.5 }}
                  onClick={() => setGroups(groups.filter((_, j) => j !== i))}>
                  <DeleteIcon />
                </IconButton>
              </Stack>
              <Stack direction="row" spacing={2} useFlexGap sx={row}>
                <Field select label="Image source" value={g.image_source} sx={{ minWidth: 200 }}
                  onChange={(e) => setGroup(i, { image_source: e.target.value as HostGroup["image_source"] })}>
                  <MenuItem value="packer_template">Build from template</MenuItem>
                  <MenuItem value="iso_direct">Build from ISO</MenuItem>
                </Field>
                {g.image_source === "packer_template" && (
                  <Field select label="Template" value={g.template ?? ""} sx={{ minWidth: 260 }} required
                    error={err(`${at}.template`)}
                    help={templatesFor(g).length ? `${templatesFor(g).length} on ${hostName} for ${g.os}`
                      : `No ${g.os} template on ${hostName} yet`}
                    onChange={(e) => setGroup(i, { template: e.target.value })}>
                    {templatesFor(g).map((tp) => (
                      <MenuItem key={tp.id} value={tp.name}>
                        <ListItemText primary={tp.name}
                          secondary={[tp.edition, tp.disk_gb ? `${tp.disk_gb} GB` : "", tp.description]
                            .filter(Boolean).join(" · ")} />
                      </MenuItem>
                    ))}
                  </Field>
                )}
                {t?.notes && (
                  <Tooltip title={<Box sx={{ whiteSpace: "pre-wrap" }}>{t.notes}</Box>}>
                    <InfoOutlinedIcon color="info" sx={{ mt: 1 }} aria-label="Template notes" />
                  </Tooltip>
                )}
                <Field label="vCPUs" type="number" value={g.cpu_count} sx={{ width: 100 }} error={err(`${at}.cpu_count`)}
                  warning={w.cpu} onChange={(e) => setGroup(i, { cpu_count: Number(e.target.value) })} />
                <Field label="Memory (MB)" type="number" value={g.memory_mb} sx={{ width: 140 }}
                  error={err(`${at}.memory_mb`)} warning={w.mem}
                  onChange={(e) => setGroup(i, { memory_mb: Number(e.target.value) })} />
                <Field label="Disk (GB)" type="number" value={g.disk_gb} sx={{ width: 120 }}
                  error={err(`${at}.disk_gb`)} warning={w.disk}
                  onChange={(e) => setGroup(i, { disk_gb: Number(e.target.value) })} />
              </Stack>
              <Stack direction="row" spacing={2} useFlexGap sx={row}>
                <Field select label="Network" value={netOf(g)?.name ?? ""} sx={{ minWidth: 200 }}
                  error={err(`${at}.network`)} help={netOf(g) ? netOf(g)!.cidr : ""}
                  onChange={(e) => setGroup(i, { network: e.target.value, addressing: "static" })}>
                  {networks.map((n) => <MenuItem key={n.id} value={n.name}>{n.name}{n.is_default ? " (default)" : ""}</MenuItem>)}
                </Field>
                <Field select label="Addressing" value={g.addressing} sx={{ minWidth: 150 }}
                  error={err(`${at}.addressing`)}
                  help={g.addressing === "static" ? "Assigned from the pool" : "Found after boot"}
                  onChange={(e) => setGroup(i, { addressing: e.target.value as HostGroup["addressing"] })}>
                  {modesFor(g).map((m) => <MenuItem key={m} value={m}>{m === "dhcp" ? "DHCP" : "Static"}</MenuItem>)}
                </Field>
                {g.os.startsWith("windows_server") && g.image_source === "iso_direct" && (
                  <FormControlLabel label="Server Core" sx={{ mt: 0.5 }}
                    control={<Checkbox checked={g.windows_core}
                      onChange={(e) => setGroup(i, { windows_core: e.target.checked })} />} />
                )}
              </Stack>
              <Typography variant="subtitle2">Host names</Typography>
              <Stack direction="row" spacing={2} useFlexGap sx={row}>
                {(g.names ?? [""]).map((n, k) => (
                  <Field key={k} label={`Host ${k + 1}`} value={n} placeholder={generated(g, k)} sx={{ width: 170 }}
                    error={err(`${at}.names.${k}`) ?? (k === 0 ? err(`${at}.names`) : undefined)}
                    help={n ? "" : `Blank: ${generated(g, k)}`}
                    onChange={(e) => setGroup(i, { names: (g.names ?? []).map((x, m) => (m === k ? e.target.value : x)) })} />
                ))}
              </Stack>
            </Stack>
          </Paper>
        );
      })}
      <Stack direction="row" spacing={2}>
        <Button onClick={() => setGroups([...groups, blankGroup()])}>Add host group</Button>
        <Button type="submit" variant="contained" disabled={busy || !hostId || networks.length === 0 || blocked}>
          Create and deploy
        </Button>
      </Stack>
    </Stack>
  );
}
