import { FormEvent, useEffect, useMemo, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import {
  Alert, Autocomplete, Box, Button, Checkbox, Chip, FormControlLabel, ListItemText, MenuItem, Paper, Stack, TextField, Tooltip, Typography,
} from "@mui/material";
import type { CatalogItem } from "./Catalog";
import DeleteIcon from "@mui/icons-material/Delete";
import InfoOutlinedIcon from "@mui/icons-material/InfoOutlined";
import { api, Environment, FieldProblem, HostGroup, LinkedStack, HostResources, KvmHost, Network, Template } from "../api";
import HostLoad from "../components/HostLoad";
import TemplatePicker from "../components/TemplatePicker";
import { ActionButton, useConfirm } from "../components/Confirm";
import { check, ErrorSummary, Field, FieldErrors, fromApiError, reportBlocked, rules, toFieldErrors } from "../forms";

// Phase 10a's tfvars-level form; environment templates replace it in 10c.
const OS_KEYS = [
  "rocky9", "ubuntu_lts", "ubuntu_26", "debian_latest",
  "windows_server_2019", "windows_server_2022", "windows_server_2025", "windows_11", "windows_10",
];
// The smallest disk an OS installs on (GB); matches ostargets.MIN_DISK_GB and the OpenTofu catalog.
const MIN_DISK_GB: Record<string, number> = { windows_11: 64 };
const ROLES = ["domain_controller", "windows_server", "windows_workstation", "linux_server", "linux_workstation"];
// A role belongs to one OS family; the Role box offers only the group's own, and an OS change picks a fitting one.
const roleFamily = (r: string) => (r.startsWith("linux_") ? "linux" : "windows");

const blankGroup = (): HostGroup => ({
  name: "", count: 1, os: "rocky9", roles: ["linux_server"], image_source: "packer_template", template: "",
  cpu_count: 2, memory_mb: 4096, disk_gb: 40, windows_core: false, windows_datacenter: false, addressing: "static", names: [""],
  services: [], applications: [], packages: [],
});

const generated = (g: HostGroup, k: number) => `${g.name || "group"}${k + 1}`;

export default function EnvironmentNew() {
  const navigate = useNavigate();
  // D185: ?draft=<id> edits a saved draft; ?from=<id> starts from a copy of another environment.
  const [params] = useSearchParams();
  const draftId = Number(params.get("draft")) || null;
  const fromId = Number(params.get("from")) || null;
  const templateId = Number(params.get("template")) || null;       // design §68: start from an environment template
  const [hosts, setHosts] = useState<KvmHost[]>([]);
  const [name, setName] = useState("");
  const [hostId, setHostId] = useState<number | "">("");
  // The environment's time zone (design §52): the browser's, when LABaPe offers it, else UTC.
  const [zones, setZones] = useState<string[]>(["UTC"]);
  const [timezone, setTimezone] = useState("UTC");
  useEffect(() => {
    api.get<{ timezones: string[]; default: string }>("/api/environments/timezones").then((z) => {
      setZones(z.timezones);
      const mine = Intl.DateTimeFormat().resolvedOptions().timeZone;
      setTimezone(z.timezones.includes(mine) ? mine : z.default);
    }, () => undefined);
  }, []);
  // Each host's networks and templates, loaded as hosts are chosen (design §45: a group may go elsewhere).
  const [netsBy, setNetsBy] = useState<Record<number, Network[]>>({});
  const [tplsBy, setTplsBy] = useState<Record<number, Template[]>>({});
  const [resources, setResources] = useState<HostResources | undefined>();
  const [catalog, setCatalog] = useState<CatalogItem[]>([]);
  useEffect(() => {
    api.get<CatalogItem[]>("/api/catalog").then((items) => setCatalog(items.filter((i) => i.status === "published")), () => setCatalog([]));
  }, []);
  // Published items that support an OS family (a package: when any of its members does; the API works that out).
  const familyOf = (os: string) => (os.startsWith("windows") ? "windows" : "linux");
  const catalogFor = (g: HostGroup, kind: CatalogItem["kind"]) =>
    catalog.filter((i) => i.kind === kind && i.platforms.includes(familyOf(g.os)));
  // Changing a group's OS drops the choices that don't fit the new one.
  const fitting = (g: HostGroup, os: string): Partial<HostGroup> => {
    const keep = (kind: CatalogItem["kind"], names: string[] | undefined) =>
      (names ?? []).filter((n) => catalog.some((i) => i.kind === kind && i.name === n && i.platforms.includes(familyOf(os))));
    const roles = g.roles.filter((r) => roleFamily(r) === familyOf(os));
    return {
      os, roles: roles.length ? roles : [familyOf(os) === "windows" ? "windows_server" : "linux_server"],
      services: keep("service", g.services), applications: keep("application", g.applications), packages: keep("package", g.packages),
    };
  };
  const [groups, setGroups] = useState<HostGroup[]>([blankGroup()]);
  useEffect(() => {
    const id = draftId ?? fromId;
    if (!id) return;
    api.get<Environment>(`/api/environments/${id}`).then((src) => {
      const copy = draftId === null;
      setName(copy ? `${src.name}-copy`.slice(0, 40) : src.name);
      const tz = (src.spec as { timezone?: string }).timezone;
      if (tz) setTimezone(tz);
      setGroups(src.spec.host_groups.map((g) => ({ ...blankGroup(), ...g, names: copy ? [] : g.names ?? [] })));   // a copy gets its own VM names
      const h = hosts.find((x) => x.name === src.kvm_host);
      if (h) setHostId(h.id);
    }, (e) => setSummary((e as Error).message));
  }, [draftId, fromId, hosts]);
  const [summary, setSummary] = useState("");
  const [errors, setErrors] = useState<FieldErrors>({});
  // Fields the user has edited since the last Create: they show the live check, not an older answer from the server.
  const [touched, setTouched] = useState<Set<string>>(new Set());
  const touch = (...keys: string[]) => setTouched((t) => new Set([...t, ...keys]));
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api.get<KvmHost[]>("/api/hosts").then((h) => {
      const enabled = h.filter((x) => x.enabled);
      setHosts(enabled);
      if (enabled.length) setHostId(enabled[0].id);
    });
  }, []);

  // What each chosen host offers: its networks (that this user may use) and its template library.
  const usedHosts = useMemo(() => [...new Set([hostId, ...groups.map((g) => g.kvm_host_id ?? "")])]
    .filter((h): h is number => h !== ""), [hostId, groups]);
  useEffect(() => {
    usedHosts.filter((h) => !(h in netsBy)).forEach((h) => {
      setNetsBy((m) => ({ ...m, [h]: [] }));
      api.get<Network[]>(`/api/networks?kvm_host_id=${h}`).then((n) => setNetsBy((m) => ({ ...m, [h]: n })), (e) => setSummary(e.message));
      api.get<Template[]>(`/api/templates?kvm_host_id=${h}`).then((t) => setTplsBy((m) => ({ ...m, [h]: t })), () => undefined);
    });
  }, [usedHosts, netsBy]);
  const hostOf = (g: HostGroup) => g.kvm_host_id ?? hostId;
  const networks = hostId === "" ? [] : netsBy[hostId] ?? [];
  const netsOf = (g: HostGroup) => { const h = hostOf(g); return h === "" ? [] : netsBy[h] ?? []; };
  const tplsOf = (g: HostGroup) => { const h = hostOf(g); return h === "" ? [] : tplsBy[h] ?? []; };

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
    netsOf(g).find((n) => n.name === g.network) ?? netsOf(g).find((n) => n.is_default) ?? netsOf(g)[0];
  const hasDc = groups.some((g) => g.roles.includes("domain_controller"));   // D184: the Domain choice only matters then
  const modesFor = (g: HostGroup) =>
    (netOf(g)?.addressing ?? ["static"]).filter((m) => m === "static" || !g.roles.includes("domain_controller"));
  const templatesFor = (g: HostGroup) => tplsOf(g).filter((t) => !t.os_key || t.os_key === g.os);
  // Linked stacks' templates (row 22): offered too, and copied here when the environment is deployed.
  const [linked, setLinked] = useState<LinkedStack[]>([]);
  useEffect(() => { api.get<{ stacks: LinkedStack[] }>("/api/linked/library").then((r) => setLinked(r.stacks), () => undefined); }, []);
  const linkedFor = (g: HostGroup) => linked.flatMap((s) => s.templates.filter((t) => !t.os_key || t.os_key === g.os)
    .filter((t) => !templatesFor(g).some((l) => l.name === t.name)).map((t) => ({ ...t, link_id: s.link_id, stack: s.name })));
  const templateOf = (g: HostGroup) => g.template_from
    ? linked.find((s) => s.link_id === g.template_from)?.templates.find((t) => t.name === g.template)
    : tplsOf(g).find((t) => t.name === g.template);

  const setGroup = (i: number, patch: Partial<HostGroup>) => {
    touch(...Object.keys(patch).map((k) => `host_groups.${i}.${k}`),
      ...(patch.os ? [`host_groups.${i}.disk_gb`, `host_groups.${i}.template`] : []),     // a new OS can change the minimum disk
      ...(patch.names || patch.count !== undefined ? Array.from({ length: 20 }, (_, k) => `host_groups.${i}.names.${k}`) : []),
      ...(patch.names || patch.count !== undefined || patch.name !== undefined ? [`host_groups.${i}.name`] : []));
    setGroups(groups.map((g, j) => {
      if (j !== i) return g;
      const next = { ...g, ...patch };
      // Keep one name box per host.
      const names = [...(next.names ?? [])];
      next.names = Array.from({ length: Math.max(1, Math.min(next.count || 1, 20)) }, (_, k) => names[k] ?? "");
      // A new OS invalidates a template for another OS.
      if (patch.os && templateOf(next)?.os_key && templateOf(next)?.os_key !== patch.os) { next.template = ""; next.template_from = undefined; }
      return next;
    }));
  };

  // A count of 0 means no hosts: the group would be pointless, so typing or stepping to 0 asks whether to remove it
  // (a cleared box is not 0 yet: the person is about to type a number). The only group can't be removed.
  const countChanged = async (i: number, raw: string) => {
    if (raw !== "0" || groups.length < 2) { setGroup(i, { count: Number(raw) }); return; }
    const g = groups[i];
    const ok = await confirm({
      title: `Remove host group ${g.name || i + 1}?`, confirmLabel: "Remove", danger: true,
      body: "A count of 0 means no hosts, so the host group isn't needed. Its settings in this form are discarded; nothing has been created yet. Keep it to set a count of 1 or more.",
    });
    if (ok) setGroups((all) => all.filter((_, j) => j !== i));
    else setGroup(i, { count: Math.max(1, g.count || 1) });
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
    const groupNames = new Map<string, number>();                       // compared without case, as the server does
    groups.forEach((g, i) => {
      const at = `Host group ${i + 1}`;
      const dup = g.name && groupNames.has(g.name.toLowerCase())
        ? `Also used by host group ${(groupNames.get(g.name.toLowerCase()) ?? 0) + 1} (names are compared without case); group names must be unique.` : "";
      if (g.name) groupNames.set(g.name.toLowerCase(), groupNames.get(g.name.toLowerCase()) ?? i);
      entries.push([`host_groups.${i}.name`, `${at}: Group name`, rules.groupName(g.name) || dup]);
      entries.push([`host_groups.${i}.count`, `${at}: Count`, rules.intRange(1, 20)(g.count)]);
      entries.push([`host_groups.${i}.cpu_count`, `${at}: vCPUs`, rules.intRange(1, 32)(g.cpu_count)]);
      entries.push([`host_groups.${i}.memory_mb`, `${at}: Memory`, rules.intRange(512, 262144, "MB")(g.memory_mb)]);
      entries.push([`host_groups.${i}.disk_gb`, `${at}: Disk`, rules.intRange(MIN_DISK_GB[g.os] ?? 10, 2048, "GB")(g.disk_gb)]);
      if (g.image_source === "packer_template" && !g.template) {
        entries.push([`host_groups.${i}.template`, `${at}: Template`, "Choose a template for this OS."]);
      }
      (g.names ?? []).forEach((n, k) => {
        entries.push([`host_groups.${i}.names.${k}`, `${at}: Host name ${k + 1}`, rules.hostName(n)]);
      });
      Array.from({ length: g.count }, (_, k) => (g.names?.[k] || generated(g, k)).toLowerCase()).forEach((n, k) => {
        const where = `host_groups.${i}.${g.names?.[k] ? `names.${k}` : "name"}`;
        const nameIsDup = !g.names?.[k] && groupNames.has(g.name.toLowerCase()) && groupNames.get(g.name.toLowerCase()) !== i;
        if (seen.has(n) && !nameIsDup) entries.push([where, `${at}: Host name`, `The VM name ${n} is used twice.`]);
        seen.set(n, where);
      });
    });
    return check(entries);
  };

  const confirm = useConfirm();
  const submit = async (e: FormEvent, asDraft = false) => {
    e.preventDefault();
    setSummary("");
    setTouched(new Set());
    const problems = validate();
    setErrors(toFieldErrors(problems));
    if (problems.length) {
      reportBlocked("/environments/new", problems);
      return;
    }
    const vms = groups.reduce((n, g) => n + (g.count ?? 1), 0);
    if (!asDraft && !(await confirm({
      title: `Create and deploy ${name}?`, confirmLabel: "Create and deploy",
      body: `The environment is saved and a deploy job starts at once on ${hostName}: ${vms} VM(s) in ${groups.length} host group(s), using ${groups.reduce((n, g) => n + (g.count ?? 1) * g.memory_mb, 0) / 1024} GB of RAM. You can follow it live, and cancel it from the job's page.`,
    }))) return;
    setBusy(true);
    try {
      const host_groups = groups.map((g) => ({
        ...g,
        network: netOf(g)?.name,
        template: g.image_source === "packer_template" ? g.template : undefined,
        template_from: g.image_source === "packer_template" ? g.template_from : undefined,
        windows_core: g.os.startsWith("windows_server") ? g.windows_core : false,
        windows_datacenter: g.os.startsWith("windows_server") ? g.windows_datacenter : false,
        remote_desktop: g.os.startsWith("windows") ? [] : (g.remote_desktop ?? []),
        domain_member: hasDc && !g.roles.includes("domain_controller") ? g.domain_member !== false : true,
        names: (g.names ?? []).slice(0, g.count),
        kvm_host_id: g.kvm_host_id && g.kvm_host_id !== hostId ? g.kvm_host_id : undefined,
      }));
      const body = { name, kvm_host_id: hostId, timezone, host_groups };
      if (draftId) {                                    // an edited draft: save it, then deploy unless it stays a draft
        await api.put(`/api/environments/${draftId}/draft`, body);
        if (!asDraft) await api.post(`/api/environments/${draftId}/deploy`, {});
        navigate(`/environments/${draftId}`);
        return;
      }
      const r = await api.post<{ environment: { id: number } }>("/api/environments", { ...body, draft: asDraft });
      navigate(`/environments/${r.environment.id}`);
    } catch (err) {
      const { summary: s, fields } = fromApiError(err);
      setSummary(s);
      setErrors(fields);
    } finally {
      setBusy(false);
    }
  };

  // A field the user has edited shows the live check (so a bad group name or a too-small disk is flagged as it is
  // typed); the others show what Create or the server last said.
  const live = toFieldErrors(validate());
  const err = (f: string) => (touched.has(f) ? live[f] : errors[f]);
  const row = { alignItems: "flex-start", flexWrap: "wrap" } as const;
  // Best fit (design §43): rank the hosts for these host groups and pick the first that fits.
  const [placement, setPlacement] = useState<{ id: number; name: string; ok: boolean; reasons: string[] }[] | null>(null);
  const suggest = async () => {
    try {
      const host_groups = groups.map((g) => ({ ...g, network: netOf(g)?.name,
        template: g.image_source === "packer_template" ? g.template : undefined, names: (g.names ?? []).slice(0, g.count) }));
      const r = await api.post<{ hosts: { id: number; name: string; ok: boolean; reasons: string[] }[] }>("/api/environments/placement", { host_groups });
      setPlacement(r.hosts);
      const best = r.hosts.find((h) => h.ok);
      if (best) setHostId(best.id);
    } catch (e) {
      setSummary((e as Error).message);
    }
  };
  const nameOf = (id: number | "") => hosts.find((h) => h.id === id)?.name ?? "";
  const hostName = nameOf(hostId);

  return (
    <Stack spacing={2} component="form" onSubmit={submit} noValidate>
      <Typography variant="h5">New environment</Typography>
      <ErrorSummary summary={summary} fields={errors} />
      {!draftId && (
        <TemplatePicker hostId={hostId} initialId={templateId} onApply={(gs, tz) => {
          setGroups(gs.map((g) => ({ ...blankGroup(), ...g, names: g.names?.length ? g.names : [""] })));
          if (tz) setTimezone(tz);
        }} />
      )}
      {hosts.length === 0 && <Alert severity="warning">No KVM host is registered yet; an admin adds one under Hosts.</Alert>}
      {hostId !== "" && networks.length === 0 && (
        <Alert severity="warning">This host has no networks you may deploy on; an admin attaches them under Hosts.</Alert>
      )}
      <Stack direction="row" spacing={2} useFlexGap sx={row}>
        <Field label="Name" value={name} onChange={(e) => { touch("name"); setName(e.target.value); }} required error={err("name")}
          help="3-40 lower-case letters, digits, hyphens; also the OpenTofu workspace" />
        <Field select label="KVM host" value={hostId} onChange={(e) => setHostId(Number(e.target.value))}
          sx={{ minWidth: 200 }} required error={err("kvm_host_id")}>
          {hosts.map((h) => <MenuItem key={h.id} value={h.id}>{h.name}</MenuItem>)}
        </Field>
        <Field select label="Time zone" value={timezone} onChange={(e) => setTimezone(e.target.value)}
          sx={{ minWidth: 220 }} error={err("timezone")}
          help="The VMs show this zone; the default is your browser's. Their clock is UTC either way.">
          {zones.map((z) => <MenuItem key={z} value={z}>{z}</MenuItem>)}
        </Field>
        {hosts.length > 1 && <Button sx={{ mt: 1 }} onClick={suggest}
          title="Rank the KVM hosts for these host groups (memory, CPU, running jobs, templates there) and choose the best">Suggest best host</Button>}
      </Stack>
      {placement && (
        <Alert severity="info" onClose={() => setPlacement(null)}>
          {placement.map((h, i) => (
            <div key={h.id}>{h.ok ? `${i + 1}. ${h.name}` : `${h.name}: doesn't fit`} — {h.reasons.join("; ") || "no findings"}</div>
          ))}
        </Alert>
      )}
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
                  required error={err(`${at}.name`)} help="1-12 letters and digits" />
                <Field label="Count" type="number" value={g.count} sx={{ width: 90 }} error={err(`${at}.count`)}
                  help={groups.length > 1 ? "0 removes the group" : ""}
                  onChange={(e) => { void countChanged(i, e.target.value); }} />
                <Field select label="OS" value={g.os} sx={{ minWidth: 200 }} error={err(`${at}.os`)}
                  onChange={(e) => setGroup(i, fitting(g, e.target.value))}>
                  {OS_KEYS.map((k) => <MenuItem key={k} value={k}>{k}</MenuItem>)}
                </Field>
                <Field select label="Role" value={g.roles[0] ?? ""} sx={{ minWidth: 220 }} error={err(`${at}.roles`)}
                  help="One role per host group"
                  onChange={(e) => setGroup(i, { roles: [e.target.value] })}>
                  {ROLES.filter((r) => roleFamily(r) === familyOf(g.os)).map((r) => <MenuItem key={r} value={r}>{r}</MenuItem>)}
                </Field>
                <ActionButton variant="outlined" color="error" size="small" startIcon={<DeleteIcon />} sx={{ mt: 1, whiteSpace: "nowrap" }}
                  disabled={groups.length === 1}
                  tip={groups.length === 1 ? "An environment needs at least one host group" : "Remove this host group from the form"}
                  confirm={{ title: `Remove host group ${g.name || i + 1}?`, confirmLabel: "Remove",
                    body: "Its settings in this form are discarded. Nothing has been created yet." }}
                  onClick={() => setGroups(groups.filter((_, j) => j !== i))}>
                  Remove host group
                </ActionButton>
              </Stack>
              <Stack direction="row" spacing={2} useFlexGap sx={row}>
                {hosts.length > 1 && (
                  <Field select label="KVM host" value={g.kvm_host_id && g.kvm_host_id !== hostId ? g.kvm_host_id : ""} sx={{ minWidth: 200 }}
                    error={err(`${at}.kvm_host_id`)} help="Another host spreads the environment (same lab network)"
                    onChange={(e) => setGroup(i, { kvm_host_id: e.target.value === "" ? undefined : Number(e.target.value),
                      template: "", network: undefined })}>
                    <MenuItem value="">Same as the environment{hostName ? ` (${hostName})` : ""}</MenuItem>
                    {hosts.filter((h) => h.id !== hostId).map((h) => <MenuItem key={h.id} value={h.id}>{h.name}</MenuItem>)}
                  </Field>
                )}
                <Field select label="Image source" value={g.image_source} sx={{ minWidth: 200 }}
                  onChange={(e) => setGroup(i, { image_source: e.target.value as HostGroup["image_source"] })}>
                  <MenuItem value="packer_template">Build from template</MenuItem>
                  <MenuItem value="iso_direct">Build from ISO</MenuItem>
                </Field>
                {g.image_source === "packer_template" && (
                  <Field select label="Template" value={g.template_from ? `link:${g.template_from}:${g.template}` : g.template ?? ""} sx={{ minWidth: 260 }} required
                    error={err(`${at}.template`)}
                    help={templatesFor(g).length ? `${templatesFor(g).length} on ${nameOf(hostOf(g))} for ${g.os}`
                      : `No ${g.os} template on ${nameOf(hostOf(g))} yet`}
                    onChange={(e) => {
                      const v = String(e.target.value);
                      const m = /^link:(\d+):(.*)$/.exec(v);
                      setGroup(i, m ? { template: m[2], template_from: Number(m[1]) } : { template: v, template_from: undefined });
                    }}>
                    {templatesFor(g).map((tp) => (
                      <MenuItem key={tp.id} value={tp.name}>
                        <ListItemText primary={tp.name}
                          secondary={[tp.from_primary ? "copied from the primary file host at deploy" : "", tp.edition,
                            tp.disk_gb ? `${tp.disk_gb} GB` : "", tp.description]
                            .filter(Boolean).join(" · ")} />
                      </MenuItem>
                    ))}
                    {linkedFor(g).map((tp) => (
                      <MenuItem key={`${tp.link_id}:${tp.name}`} value={`link:${tp.link_id}:${tp.name}`}>
                        <ListItemText primary={tp.name}
                          secondary={[`on the linked stack ${tp.stack}: copied here at deploy`, tp.disk_gb ? `${tp.disk_gb} GB` : "", tp.description]
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
                  {netsOf(g).map((n) => <MenuItem key={n.id} value={n.name}>{n.name}{n.is_default ? " (default)" : ""}</MenuItem>)}
                </Field>
                <Field select label="Addressing" value={g.addressing} sx={{ minWidth: 150 }}
                  error={err(`${at}.addressing`)}
                  help={g.addressing === "static" ? "Assigned from the pool" : "Found after boot"}
                  onChange={(e) => setGroup(i, { addressing: e.target.value as HostGroup["addressing"] })}>
                  {modesFor(g).map((m) => <MenuItem key={m} value={m}>{m === "dhcp" ? "DHCP" : "Static"}</MenuItem>)}
                </Field>
                {hasDc && !g.roles.includes("domain_controller") && (
                  <Field select label="Domain" value={g.domain_member === false ? "standalone" : "member"} sx={{ minWidth: 150 }}
                    help={g.domain_member === false ? "Not joined to the domain" : "Joins the domain"}
                    onChange={(e) => setGroup(i, { domain_member: e.target.value === "member" })}>
                    <MenuItem value="member">Member</MenuItem><MenuItem value="standalone">Standalone</MenuItem>
                  </Field>
                )}
                {!g.os.startsWith("windows") && (["xrdp", "vnc"] as const).map((k) => (
                  <FormControlLabel key={k} label={k === "xrdp" ? "xrdp (RDP)" : "VNC"} sx={{ mt: 0.5 }}
                    title={k === "xrdp" ? "A desktop over RDP for every account, local or domain, like Windows"
                      : "A VNC desktop of its own for each local account on these VMs"}
                    control={<Checkbox checked={(g.remote_desktop ?? []).includes(k)}
                      onChange={(e) => setGroup(i, { remote_desktop: e.target.checked
                        ? [...(g.remote_desktop ?? []), k] : (g.remote_desktop ?? []).filter((x) => x !== k) })} />} />
                ))}
                {g.os.startsWith("windows_server") && g.image_source === "iso_direct" && (
                  <FormControlLabel label="Server Core" sx={{ mt: 0.5 }}
                    control={<Checkbox checked={g.windows_core}
                      onChange={(e) => setGroup(i, { windows_core: e.target.checked })} />} />
                )}
                {g.os.startsWith("windows_server") && g.image_source === "iso_direct" && (
                  <FormControlLabel label="Datacenter" sx={{ mt: 0.5 }} title="Install Datacenter instead of Standard"
                    control={<Checkbox checked={g.windows_datacenter}
                      onChange={(e) => setGroup(i, { windows_datacenter: e.target.checked })} />} />
                )}
              </Stack>
              <Typography variant="subtitle2">Added on top of the template</Typography>
              <Stack direction="row" spacing={2} useFlexGap sx={row}>
                {(["service", "application", "package"] as const).map((kind) => {
                  const key = `${kind}s` as "services" | "applications" | "packages";
                  const options = catalogFor(g, kind);
                  const fe = err(`${at}.${key}`);
                  return (
                    <Autocomplete key={kind} multiple filterSelectedOptions size="small" sx={{ minWidth: 280, flex: 1 }}
                      options={options.map((o) => o.name)} value={g[key] ?? []}
                      getOptionLabel={(n) => options.find((o) => o.name === n)?.display_name ?? n}
                      onChange={(_, v) => setGroup(i, { [key]: v } as Partial<HostGroup>)}
                      renderValue={(vals, getItemProps) => vals.map((v, k) => {
                        const { key: chipKey, ...chipProps } = getItemProps({ index: k });
                        return <Chip key={chipKey} size="small" label={options.find((o) => o.name === v)?.display_name ?? v} {...chipProps} />;
                      })}
                      renderInput={(params) => (
                        <TextField {...params} label={kind === "service" ? "Services" : kind === "application" ? "Applications" : "Packages"}
                          error={Boolean(fe)} helperText={fe?.message ?? (options.length ? " " : `No published ${kind}s for this OS`)} />
                      )} />
                  );
                })}
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
        <ActionButton tip="Add another group of identical VMs to this form" onClick={() => setGroups([...groups, blankGroup()])}>
          Add host group
        </ActionButton>
        <ActionButton type="submit" variant="contained" disabled={busy || !hostId || networks.length === 0 || blocked}
          tip="Save the environment and start deploying it (asks to confirm)">
          Create and deploy
        </ActionButton>
        <ActionButton variant="outlined" disabled={busy || !hostId || !name} onClick={() => submit({ preventDefault() { /* not a form event */ } } as FormEvent, true)}
          tip="Keep what is typed without deploying: no quota or addresses are used until you deploy it; only you and admins see it">
          Save as draft
        </ActionButton>
      </Stack>
    </Stack>
  );
}
