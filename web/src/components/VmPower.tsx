// VM power and start order (design §28.3, questions 108-111): start, shut down,
// restart and force off one VM or the whole environment; the start order says
// which groups come up first (servers before their clients).
import { useCallback, useEffect, useState } from "react";
import { Link as RouterLink, useNavigate } from "react-router-dom";
import {
  Alert, Checkbox, Chip, FormControlLabel, Link, MenuItem, Paper, Stack, Table, TableBody, TableCell, TableHead,
  TableRow, Typography,
} from "@mui/material";
import { api, fmtTime, HostGroup, KvmHost } from "../api";
import { Field } from "../forms";
import { ActionButton } from "./Confirm";
import { ConnectButton, RdpFileLink } from "./Connect";

interface VmRow {
  name: string; group: string; os: string; start_order: number; state: string; online: boolean;
  responding: boolean | null; kvm_host?: string; kvm_host_id?: number;
}
type Action = "start" | "shutdown" | "restart" | "force_off";

const LABEL: Record<Action, string> = { start: "Start", shutdown: "Shut down", restart: "Restart", force_off: "Force off" };
const stateColor = (s: string) => (s === "running" ? "success" : s === "shut off" ? "default" : s === "missing" ? "error" : "warning");

export default function VmPower({ envId, envName, groups, isOwner, onSaved }: {
  envId: string; envName: string; groups: HostGroup[]; isOwner: boolean; onSaved: () => void;
}) {
  const navigate = useNavigate();
  const [vms, setVms] = useState<VmRow[]>([]);
  const spreadOut = new Set(vms.map((v) => v.kvm_host)).size > 1;      // design §45: VMs on several hosts
  const [collected, setCollected] = useState<string | null>(null);
  const [canOperate, setCanOperate] = useState(false);
  const [force, setForce] = useState(false);
  const [timeout, setTimeoutSecs] = useState(300);
  const [order, setOrder] = useState(() => groups.map((g) => ({
    name: g.name, start_order: g.start_order ?? (g.roles.includes("domain_controller") ? 1 : 2),
    start_wait: g.start_wait ?? "agent", start_delay_seconds: g.start_delay_seconds ?? 0,
  })));
  const [error, setError] = useState("");
  const [saved, setSaved] = useState(false);
  // Moving a host group to another KVM host while it runs (design §46).
  const [hosts, setHosts] = useState<KvmHost[]>([]);
  const [moveGroup, setMoveGroup] = useState("");
  const [moveTo, setMoveTo] = useState<number | "">("");
  useEffect(() => {
    api.get<KvmHost[]>("/api/hosts").then((h) => setHosts(h.filter((x) => x.enabled)), () => setHosts([]));
  }, []);
  const groupHost = (g: string) => vms.find((v) => v.group === g)?.kvm_host_id;
  const move = async () => {
    setError("");
    try {
      const r = await api.post<{ job_id: number }>(`/api/environments/${envId}/move`, { group: moveGroup, kvm_host_id: moveTo });
      navigate(`/jobs/${r.job_id}`);
    } catch (e) { setError((e as Error).message); }
  };

  const load = useCallback(() => {
    api.get<{ vms: VmRow[]; collected_at: string | null; can_operate: boolean }>(`/api/environments/${envId}/vms`)
      .then((r) => { setVms(r.vms); setCollected(r.collected_at); setCanOperate(r.can_operate); }, () => undefined);
  }, [envId]);
  useEffect(() => {
    load();
    const t = setInterval(load, 15000);
    return () => clearInterval(t);
  }, [load]);

  const run = async (action: Action, vmNames?: string[]) => {
    setError("");
    try {
      const r = await api.post<{ job_id: number }>(`/api/environments/${envId}/power`, {
        action, vms: vmNames ?? null, force_after_timeout: force, timeout_seconds: timeout,
      });
      navigate(`/jobs/${r.job_id}`);
    } catch (e) { setError((e as Error).message); }
  };
  const saveOrder = async () => {
    setError("");
    try {
      await api.put(`/api/environments/${envId}/start-order`, { groups: order });
      setSaved(true);
      onSaved();
      load();
    } catch (e) { setError((e as Error).message); }
  };

  const confirmFor = (action: Action, who: string) => ({
    title: `${LABEL[action]} ${who}?`, confirmLabel: LABEL[action], danger: action === "force_off",
    body: action === "start" ? `Starts ${who} in start order: each group waits for the one before it to answer through its guest agent.`
      : action === "force_off" ? `Cuts the power to ${who} at once, like pulling the plug. Unsaved work in the guests is lost; use it only when a clean shutdown fails.`
      : action === "shutdown" ? `Asks ${who} to shut down cleanly, clients before servers (reverse start order).${force ? ` Anything still running after ${timeout} s is forced off.` : ` If something hasn't stopped after ${timeout} s, the job fails and you can force it off.`}`
      : `Shuts ${who} down cleanly (clients first), then starts it again in start order.${force ? ` Anything still running after ${timeout} s is forced off first.` : ""}`,
  });

  return (
    <Paper variant="outlined" sx={{ p: 2 }}>
      <Stack spacing={1.5}>
        <Stack direction="row" spacing={1} useFlexGap sx={{ alignItems: "center", flexWrap: "wrap" }}>
          <Typography variant="h6" sx={{ mr: 1 }}>VMs</Typography>
          {canOperate && (["start", "shutdown", "restart", "force_off"] as Action[]).map((a) => (
            <ActionButton key={a} size="small" variant={a === "start" ? "contained" : "outlined"}
              color={a === "force_off" ? "error" : "primary"} onClick={() => run(a)}
              tip={`${LABEL[a]} every VM in ${envName}${a === "force_off" ? "" : ", in start order"}`}
              confirm={confirmFor(a, `all of ${envName}`)}>
              {LABEL[a]} all
            </ActionButton>
          ))}
          {canOperate && (
            <>
              <FormControlLabel sx={{ ml: 1 }} label="Force off after the timeout"
                control={<Checkbox size="small" checked={force} onChange={(e) => setForce(e.target.checked)} />} />
              <Field label="Timeout (s)" type="number" value={timeout} sx={{ width: 120 }} help="30-3600"
                onChange={(e) => setTimeoutSecs(Number(e.target.value))} />
            </>
          )}
        </Stack>
        {error && <Alert severity="error" onClose={() => setError("")}>{error}</Alert>}
        <Table size="small">
          <TableHead>
            <TableRow>
              <TableCell>VM</TableCell><TableCell>Group</TableCell>{spreadOut && <TableCell>KVM host</TableCell>}<TableCell>Start order</TableCell>
              <TableCell>State</TableCell><TableCell>Guest agent</TableCell>{canOperate && <TableCell />}
            </TableRow>
          </TableHead>
          <TableBody>
            {vms.map((v) => (
              <TableRow key={v.name}>
                <TableCell><Link component={RouterLink} to={`/environments/${envId}/vms/${v.name}`} title="Details, accounts and console">{v.name}</Link></TableCell>
                <TableCell>{v.group} <Typography component="span" variant="caption" color="text.secondary">{v.os}</Typography></TableCell>
                {spreadOut && <TableCell>{v.kvm_host}</TableCell>}
                <TableCell>{v.start_order}</TableCell>
                <TableCell><Chip size="small" label={v.state} color={stateColor(v.state)} /></TableCell>
                <TableCell>{!v.online ? "—" : v.responding === true ? "answers" : v.responding === false ? "not answering" : "none"}</TableCell>
                {canOperate && (
                  <TableCell sx={{ whiteSpace: "nowrap" }}>
                    {v.state === "running" && (v.os.startsWith("windows") ? (
                      <>
                        <ConnectButton target={{ target_type: "vm", protocol: "rdp", environment_id: Number(envId), vm: v.name }} label="RDP" onError={setError} />
                        <RdpFileLink envId={Number(envId)} vm={v.name} />
                      </>
                    ) : <ConnectButton target={{ target_type: "vm", protocol: "ssh", environment_id: Number(envId), vm: v.name }} label="SSH" onError={setError} />)}
                    {(v.state === "running" ? ["shutdown", "restart", "force_off"] as Action[] : ["start"] as Action[]).map((a) => (
                      <ActionButton key={a} size="small" color={a === "force_off" ? "error" : "primary"} onClick={() => run(a, [v.name])}
                        tip={`${LABEL[a]} ${v.name}`} confirm={confirmFor(a, v.name)}>
                        {LABEL[a]}
                      </ActionButton>
                    ))}
                  </TableCell>
                )}
              </TableRow>
            ))}
          </TableBody>
        </Table>
        <Typography variant="caption" color="text.secondary">
          States are from the KVM host's last check{collected ? ` (${fmtTime(collected)})` : ""}, every few minutes.
        </Typography>
        {canOperate && hosts.length > 1 && (
          <Stack direction="row" spacing={2} useFlexGap sx={{ alignItems: "flex-start", flexWrap: "wrap", pt: 1 }}>
            <Field select label="Move host group" value={moveGroup} sx={{ minWidth: 180 }}
              help="Its running VMs move with their disks"
              onChange={(e) => { setMoveGroup(e.target.value); setMoveTo(""); }}>
              {groups.map((g) => <MenuItem key={g.name} value={g.name}>{g.name}</MenuItem>)}
            </Field>
            <Field select label="To KVM host" value={moveTo} sx={{ minWidth: 180 }} disabled={!moveGroup}
              help="It must carry the group's network"
              onChange={(e) => setMoveTo(Number(e.target.value))}>
              {hosts.filter((h) => h.id !== groupHost(moveGroup)).map((h) => <MenuItem key={h.id} value={h.id}>{h.name}</MenuItem>)}
            </Field>
            <ActionButton sx={{ mt: 1 }} disabled={!moveGroup || moveTo === ""} onClick={move}
              tip="Live-migrate the group's VMs to that host, then redeploy to record it"
              confirm={{ title: `Move ${moveGroup} to ${hosts.find((h) => h.id === moveTo)?.name ?? ""}?`, confirmLabel: "Move",
                body: "Each VM keeps running while its memory and disks are copied to the other host; large disks take a while. VMs must be running and have no snapshots. A deploy follows to record the new host and run Ansible." }}>
              Move
            </ActionButton>
          </Stack>
        )}
        {isOwner && (
          <>
            <Typography variant="subtitle2" sx={{ pt: 1 }}>Start order</Typography>
            <Typography variant="body2" color="text.secondary">
              Lower numbers start first and shut down last: put whatever serves the others (a domain controller, a
              database) before its clients. Groups with the same number start together.
            </Typography>
            {saved && <Alert severity="success" onClose={() => setSaved(false)}>Start order saved.</Alert>}
            <Table size="small">
              <TableHead>
                <TableRow><TableCell>Group</TableCell><TableCell>Order</TableCell><TableCell>Before the next group</TableCell><TableCell>Then wait (s)</TableCell></TableRow>
              </TableHead>
              <TableBody>
                {order.map((o, i) => (
                  <TableRow key={o.name}>
                    <TableCell>{o.name}</TableCell>
                    <TableCell>
                      <Field type="number" value={o.start_order} sx={{ width: 90 }} help="1-99"
                        onChange={(e) => setOrder(order.map((x, k) => (k === i ? { ...x, start_order: Number(e.target.value) } : x)))} />
                    </TableCell>
                    <TableCell>
                      <Field select value={o.start_wait} sx={{ width: 230 }} help=" "
                        onChange={(e) => setOrder(order.map((x, k) => (k === i ? { ...x, start_wait: e.target.value as "agent" | "none" } : x)))}>
                        <MenuItem value="agent">Wait for its guest agents</MenuItem>
                        <MenuItem value="none">Don't wait</MenuItem>
                      </Field>
                    </TableCell>
                    <TableCell>
                      <Field type="number" value={o.start_delay_seconds} sx={{ width: 110 }} help="0-3600"
                        onChange={(e) => setOrder(order.map((x, k) => (k === i ? { ...x, start_delay_seconds: Number(e.target.value) } : x)))} />
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
            <Stack direction="row">
              <ActionButton variant="outlined" onClick={saveOrder} tip="Save the start order; it's used by the next start or restart"
                confirm={{ title: "Save the start order?", confirmLabel: "Save",
                  body: "The next whole-environment start, restart or shutdown follows it. Nothing is started or stopped now." }}>
                Save start order
              </ActionButton>
            </Stack>
          </>
        )}
      </Stack>
    </Paper>
  );
}
