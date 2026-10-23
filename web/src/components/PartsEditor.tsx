// The parts of a template made of other templates (design §69): each part is a template with the prefix its host groups
// get, an optional label for its parameters, and values the including template sets for the part's groups (count, OS,
// network, CPUs, memory or disk). A value replaces the part's own; when the part lets it be changed at start, it is
// still asked for, with this value as the starting one.
import { useState } from "react";
import {
  Accordion, AccordionDetails, AccordionSummary, Button, IconButton, MenuItem, Stack, TextField, Typography,
} from "@mui/material";
import DeleteIcon from "@mui/icons-material/Delete";
import ExpandMoreIcon from "@mui/icons-material/ExpandMore";
import type { EnvTemplateInfo, TemplateInclude } from "./TemplatePicker";

export interface PartRow {
  template_id: number | ""; prefix: string; label: string; overrides: Record<string, Record<string, string>>;
  version: number | "";                       // "" follows the part's latest version; a number stays on that one
}

export const FIXABLE: [string, string][] = [
  ["count", "Hosts"], ["os", "OS"], ["network", "Network"], ["cpu_count", "CPUs"], ["memory_mb", "Memory MB"], ["disk_gb", "Disk GB"],
];
const NUMERIC = new Set(["count", "cpu_count", "memory_mb", "disk_gb"]);

export const rowsOf = (includes: TemplateInclude[]): PartRow[] => includes.map((i) => ({
  template_id: i.template_id, prefix: i.prefix, label: i.label ?? "", version: i.version ?? "",
  overrides: Object.fromEntries(Object.entries(i.overrides ?? {}).map(([g, f]) => [g, Object.fromEntries(Object.entries(f).map(([k, v]) => [k, String(v)]))])),
}));

/** The request body for `includes`: blank fixed values are dropped, numbers are numbers. */
export const includesBody = (rows: PartRow[]) => rows.map((r) => ({
  template_id: r.template_id, prefix: r.prefix, label: r.label, version: r.version === "" ? null : r.version,
  overrides: Object.fromEntries(Object.entries(r.overrides).map(([g, f]) => [g, Object.fromEntries(
    Object.entries(f).filter(([, v]) => v.trim() !== "").map(([k, v]) => [k, NUMERIC.has(k) ? Number(v) : v]))]).filter(([, f]) => Object.keys(f).length)),
}));

export const partsReady = (rows: PartRow[]) => rows.every((r) => r.template_id !== "" && r.prefix);

export default function PartsEditor({ rows, onChange, list, selfId }: {
  rows: PartRow[]; onChange: (rows: PartRow[]) => void; list: EnvTemplateInfo[]; selfId?: number;
}) {
  const [open, setOpen] = useState<number | null>(null);
  const set = (i: number, patch: Partial<PartRow>) => onChange(rows.map((r, j) => (j === i ? { ...r, ...patch } : r)));
  const setFixed = (i: number, group: string, field: string, value: string) =>
    set(i, { overrides: { ...rows[i].overrides, [group]: { ...(rows[i].overrides[group] ?? {}), [field]: value } } });
  const fixedCount = (r: PartRow) => Object.values(r.overrides).reduce((n, f) => n + Object.values(f).filter((v) => v.trim() !== "").length, 0);
  return (
    <Stack spacing={1.5}>
      {rows.map((r, i) => {
        const part = list.find((x) => x.id === r.template_id);
        return (
          <Stack key={i} spacing={0.5}>
            <Stack direction="row" spacing={1} sx={{ alignItems: "center" }}>
              <TextField select size="small" label="Part" value={r.template_id} sx={{ flexGrow: 1 }}
                onChange={(e) => set(i, { template_id: Number(e.target.value), overrides: {}, version: "" })}>
                {list.filter((x) => x.id !== selfId).map((x) => <MenuItem key={x.id} value={x.id}>{x.name}</MenuItem>)}
              </TextField>
              <TextField select size="small" label="Version" value={r.version} sx={{ width: 130 }} disabled={!part}
                helperText={part && r.version !== "" && r.version < part.version ? `v${part.version} is newer` : " "}
                onChange={(e) => set(i, { version: e.target.value === "" ? "" : Number(e.target.value) })}>
                <MenuItem value="">Latest</MenuItem>
                {part && Array.from({ length: part.version }, (_, k) => part.version - k).map((v) => <MenuItem key={v} value={v}>v{v}</MenuItem>)}
              </TextField>
              <TextField size="small" label="Prefix" value={r.prefix} sx={{ width: 100 }} inputProps={{ maxLength: 6 }}
                onChange={(e) => set(i, { prefix: e.target.value.replace(/[^A-Za-z0-9]/g, "") })} />
              <TextField size="small" label="Label" value={r.label} sx={{ width: 140 }} inputProps={{ maxLength: 60 }}
                helperText="for its parameters" onChange={(e) => set(i, { label: e.target.value })} />
              <IconButton size="small" aria-label="Remove this part" onClick={() => onChange(rows.filter((_, j) => j !== i))}>
                <DeleteIcon fontSize="small" />
              </IconButton>
            </Stack>
            {part && (
              <Accordion expanded={open === i} onChange={(_, v) => setOpen(v ? i : null)} variant="outlined" disableGutters>
                <AccordionSummary expandIcon={<ExpandMoreIcon />}>
                  <Typography variant="body2">Values for this part{fixedCount(r) ? ` (${fixedCount(r)})` : ""}</Typography>
                </AccordionSummary>
                <AccordionDetails>
                  <Typography variant="caption" color="text.secondary" component="div" sx={{ mb: 1 }}>
                    A value set here replaces the part&apos;s own. If the part lets it be changed when starting from the template, it is still asked
                    for, with this one as the starting value. Blank leaves the part&apos;s own.
                  </Typography>
                  {part.summary.map((g) => (
                    <Stack key={g.name} direction="row" spacing={1} useFlexGap sx={{ flexWrap: "wrap", alignItems: "center", mb: 1 }}>
                      <Typography variant="body2" sx={{ minWidth: 90 }}>{g.name}</Typography>
                      {FIXABLE.map(([f, label]) => (
                        <TextField key={f} size="small" label={label} sx={{ width: 110 }} value={r.overrides[g.name]?.[f] ?? ""}
                          type={NUMERIC.has(f) ? "number" : "text"} onChange={(e) => setFixed(i, g.name, f, e.target.value)} />
                      ))}
                    </Stack>
                  ))}
                </AccordionDetails>
              </Accordion>
            )}
          </Stack>
        );
      })}
      <div><Button size="small" onClick={() => onChange([...rows, { template_id: "", prefix: "", label: "", overrides: {}, version: "" }])}>Add a part</Button></div>
    </Stack>
  );
}
