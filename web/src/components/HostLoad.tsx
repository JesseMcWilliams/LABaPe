import { Alert, Box, LinearProgress, Paper, Stack, Typography } from "@mui/material";
import { fmtTime, HostResources } from "../api";

// A KVM host's capacity and load (design §21.2). With a demand, the bars
// show the host as if this environment were deployed too.
type Demand = { vcpus: number; memory_mb: number; disk_gb: number };

const gb = (mb: number) => `${(mb / 1024).toFixed(mb >= 10240 ? 0 : 1)} GB`;

function Bar({ label, value, max, warnAt, blockAt, detail }: {
  label: string; value: number; max: number; warnAt: number; blockAt?: number; detail: string;
}) {
  const ratio = max > 0 ? value / max : 0;
  const color = blockAt !== undefined && ratio > blockAt ? "error" : ratio > warnAt ? "warning" : "success";
  return (
    <Box sx={{ minWidth: 220, flex: 1 }}>
      <Stack direction="row" spacing={1} sx={{ justifyContent: "space-between", alignItems: "flex-end" }}>
        <Typography variant="caption" color="text.secondary">{label}</Typography>
        <Typography variant="caption">{detail}</Typography>
      </Stack>
      <LinearProgress variant="determinate" value={Math.min(100, ratio * 100)} color={color}
        sx={{ height: 8, borderRadius: 1 }} aria-label={label} />
    </Box>
  );
}

export default function HostLoad({ resources, demand, compact }: {
  resources?: HostResources; demand?: Demand; compact?: boolean;
}) {
  if (!resources) return null;
  if (!resources.stats || !resources.assessment) {
    return (
      <Typography variant="body2" color="text.secondary">
        {resources.error ? `Resources unavailable: ${resources.error}` : "Resources not collected yet (the worker reports every minute)."}
      </Typography>
    );
  }
  const s = resources.stats;
  const a = resources.assessment;
  const t = a.thresholds;
  const d = demand ?? { vcpus: 0, memory_mb: 0, disk_gb: 0 };
  const vm = s.storage.vm;
  const tpl = s.storage.templates;
  const plus = (n: number, unit: string) => (n ? ` (+${n}${unit} this env)` : "");
  return (
    <Paper variant="outlined" sx={{ p: 1.5 }}>
      <Stack spacing={1}>
        {!compact && (
          <Typography variant="body2">
            {s.cores} cores / {s.cpus} logical CPUs · {gb(s.memory_total_mb)} RAM · {s.vms_running} of {s.vms_defined} VMs
            running{s.load.length ? ` · load ${s.load.map((x) => x.toFixed(1)).join(" ")}` : ""}
          </Typography>
        )}
        <Stack direction="row" spacing={2} useFlexGap sx={{ flexWrap: "wrap", alignItems: "flex-end" }}>
          <Bar label="vCPUs allocated" value={s.vcpus_allocated + d.vcpus} max={s.cpus * t.vcpu_per_cpu_warn}
            warnAt={1} detail={`${s.vcpus_allocated + d.vcpus} = ${a.vcpu_ratio}/CPU${plus(d.vcpus, "")}`} />
          <Bar label="RAM committed" value={s.memory_committed_mb + d.memory_mb} max={s.memory_total_mb}
            warnAt={t.ram_committed_warn} blockAt={t.ram_committed_block}
            detail={`${gb(s.memory_committed_mb + d.memory_mb)} of ${gb(s.memory_total_mb)}${d.memory_mb ? ` (+${gb(d.memory_mb)})` : ""}`} />
          <Bar label="RAM in use now" value={s.memory_total_mb - s.memory_available_mb} max={s.memory_total_mb}
            warnAt={0.9} detail={`${gb(s.memory_available_mb)} available`} />
          <Bar label={`VM storage (${vm.path})`} value={vm.total_gb - vm.free_gb + d.disk_gb} max={vm.total_gb}
            warnAt={1 - t.disk_free_warn}
            detail={`${Math.max(0, vm.free_gb - d.disk_gb).toFixed(0)} of ${vm.total_gb.toFixed(0)} GB free${plus(d.disk_gb, " GB")}`} />
          {!compact && tpl.path !== vm.path && (
            <Bar label={`Templates (${tpl.path})`} value={tpl.total_gb - tpl.free_gb} max={tpl.total_gb}
              warnAt={1 - t.disk_free_warn} detail={`${tpl.free_gb.toFixed(0)} GB free`} />
          )}
        </Stack>
        {a.findings.map((f, k) => (
          <Alert key={k} severity={f.level === "block" ? "error" : "warning"} sx={{ py: 0 }}>{f.message}</Alert>
        ))}
        <Typography variant="caption" color="text.secondary">
          Collected {fmtTime(resources.collected_at)}. Committed RAM counts every defined VM's memory; disk counts
          each new VM's full size, though clones start small.
        </Typography>
      </Stack>
    </Paper>
  );
}
