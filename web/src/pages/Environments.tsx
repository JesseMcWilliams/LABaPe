import { useEffect, useState } from "react";
import { Link as RouterLink } from "react-router-dom";
import { Alert, Button, Chip, Link, Stack, Table, TableBody, TableCell, TableHead, TableRow, Typography } from "@mui/material";
import { api, Environment, fmtTime, LEASE_COLOR } from "../api";
import { hasRole, useAuth } from "../App";
import { StateChip } from "../components";
import { DateRangeFields, FilterBar, SelectFilter, useUrlFilters } from "../components/Filters";
import { ExportButton, pageOf, Pager, usePageSize } from "../components/Paging";
import { Field } from "../forms";

// Filters and paging in the browser (L1): the list is the environments this user can see.
const KEYS = ["name", "status", "host", "os", "owner", "since", "until"];
const has = (hay: string, needle: string) => !needle || hay.toLowerCase().includes(needle.toLowerCase());
const hostsOf = (e: Environment) => e.spec.host_groups.map((g) => `${g.name}×${g.count ?? 1} (${g.os})`).join(", ");

export default function Environments() {
  const { me } = useAuth();
  const [envs, setEnvs] = useState<Environment[]>([]);
  const [error, setError] = useState("");
  const [page, setPage] = useState(0);
  const [size] = usePageSize();
  const f = useUrlFilters(KEYS);
  const a = f.applied;
  useEffect(() => setPage(0), [JSON.stringify(a)]); // eslint-disable-line react-hooks/exhaustive-deps
  const shown = envs.filter((e) => has(e.name, a.name) && (!a.status || e.status === a.status)
    && (!a.host || e.kvm_host === a.host) && (!a.os || e.spec.host_groups.some((g) => g.os === a.os))
    && (!a.owner || e.created_by === a.owner)
    && (!a.since || new Date(e.created_at) >= new Date(a.since)) && (!a.until || new Date(e.created_at) <= new Date(a.until)));
  const opts = (xs: (string | null | undefined)[]) => [...new Set(xs.filter((x): x is string => Boolean(x)))].sort();
  const [quota, setQuota] = useState<{ limits: Record<string, number | null | string> | null; usage: Record<string, number> } | null>(null);
  useEffect(() => { api.get<typeof quota>("/api/quotas/me").then(setQuota, () => setQuota(null)); }, [envs.length]);

  useEffect(() => {
    const load = () => api.get<Environment[]>("/api/environments").then(setEnvs, (e) => setError(e.message));
    load();
    const t = setInterval(load, 5000);
    return () => clearInterval(t);
  }, []);

  return (
    <Stack spacing={2}>
      <Stack direction="row" sx={{ justifyContent: "space-between", alignItems: "center" }}>
        <Typography variant="h5">Environments</Typography>
        {quota?.limits && (
          <Typography variant="body2" color="text.secondary" sx={{ flexGrow: 1, ml: 2 }} title={`Your quota comes from ${quota.limits.source}`}>
            Your quota: {[["max_environments", "environments", "environments"], ["max_vcpus", "vcpus", "vCPUs"],
              ["max_memory_mb", "memory_mb", "MB memory"], ["max_disk_gb", "disk_gb", "GB disk"]]
              .filter(([k]) => quota.limits![k] !== null)
              .map(([k, u, l]) => `${quota.usage[u]} of ${quota.limits![k]} ${l}`).join(" · ") || "no limits"}
          </Typography>
        )}
        {hasRole(me, "deployer", "template_editor") && (
          <Button variant="contained" component={RouterLink} to="/environments/new"
            title="New environment: opens the form; nothing is created until you confirm there">
            New environment
          </Button>
        )}
      </Stack>
      {error && <Alert severity="error">{error}</Alert>}
      <FilterBar onApply={() => f.apply()} onClear={f.clear} active={f.active}>
        <Field label="Name" value={f.draft.name} sx={{ width: 170 }} help="Contains" onChange={(e) => f.set("name", e.target.value)} />
        <SelectFilter label="Status" value={f.draft.status} width={150} options={opts(envs.map((e) => e.status))} onChange={(v) => f.set("status", v)} />
        <SelectFilter label="KVM host" value={f.draft.host} width={150} options={opts(envs.map((e) => e.kvm_host))} onChange={(v) => f.set("host", v)} />
        <SelectFilter label="OS" value={f.draft.os} width={180} help="A host group runs it"
          options={opts(envs.flatMap((e) => e.spec.host_groups.map((g) => g.os)))} onChange={(v) => f.set("os", v)} />
        <SelectFilter label="Created by" value={f.draft.owner} width={160} options={opts(envs.map((e) => e.created_by))} onChange={(v) => f.set("owner", v)} />
        <DateRangeFields since={f.draft.since} until={f.draft.until} onChange={f.set} />
      </FilterBar>
      <Stack direction="row" spacing={1} sx={{ alignItems: "center", justifyContent: "space-between", flexWrap: "wrap" }}>
        <Typography variant="body2" color="text.secondary">
          {f.active ? `${shown.length} of ${envs.length} environments match` : `${envs.length} environment${envs.length === 1 ? "" : "s"}`}
        </Typography>
        <Stack direction="row" spacing={1} sx={{ alignItems: "center" }}>
          <ExportButton name="environments" total={shown.length} page={page}
            fetchRows={async (offset, limit) => shown.slice(offset, offset + limit)}
            columns={[{ header: "name", value: (e: Environment) => e.name }, { header: "status", value: (e: Environment) => e.status },
              { header: "kvm_host", value: (e: Environment) => e.kvm_host ?? "" }, { header: "hosts", value: hostsOf },
              { header: "addresses", value: (e: Environment) => Object.entries(e.addresses ?? {}).map(([k, v]) => `${k}=${v}`).join("; ") },
              { header: "created_by", value: (e: Environment) => e.created_by }, { header: "created_at", value: (e: Environment) => e.created_at },
              { header: "expires_at", value: (e: Environment) => e.lease?.expires_at ?? "" }, { header: "updated_at", value: (e: Environment) => e.updated_at }]} />
          <Pager total={shown.length} page={page} onPage={setPage} />
        </Stack>
      </Stack>
      <Table size="small">
        <TableHead>
          <TableRow>
            <TableCell>Name</TableCell>
            <TableCell>Status</TableCell>
            <TableCell>KVM host</TableCell>
            <TableCell>Hosts</TableCell>
            <TableCell>Created by</TableCell>
            <TableCell>Expires</TableCell>
            <TableCell>Updated</TableCell>
          </TableRow>
        </TableHead>
        <TableBody>
          {pageOf(shown, page, size).map((e) => (
            <TableRow key={e.id} hover>
              <TableCell>
                <Link component={RouterLink} to={`/environments/${e.id}`}>
                  {e.name}
                </Link>
              </TableCell>
              <TableCell>
                <StateChip state={e.status} />
              </TableCell>
              <TableCell>{e.kvm_host}</TableCell>
              <TableCell>
                {hostsOf(e)}
              </TableCell>
              <TableCell>{e.created_by}</TableCell>
              <TableCell>{e.lease?.expires_at
                ? <Chip size="small" variant="outlined" color={LEASE_COLOR[e.lease.phase]} label={fmtTime(e.lease.expires_at)}
                    title={e.lease.phase === "ended" ? "The lease ended: its VMs were shut down" : `Lease: ${e.lease.phase}`} />
                : "never"}</TableCell>
              <TableCell>{fmtTime(e.updated_at)}</TableCell>
            </TableRow>
          ))}
          {shown.length === 0 && (
            <TableRow>
              <TableCell colSpan={7}>{envs.length ? "No environments match these filters." : "No environments you can see yet."}</TableCell>
            </TableRow>
          )}
        </TableBody>
      </Table>
    </Stack>
  );
}
