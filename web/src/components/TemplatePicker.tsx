// "Start from a template" on the New environment form (design §68): pick a template, fill its few parameters and
// the form is filled in; nothing is created until the form is submitted as usual.
import { useEffect, useState } from "react";
import { Link as RouterLink } from "react-router-dom";
import { Alert, Accordion, AccordionDetails, AccordionSummary, Button, Chip, Link, MenuItem, Stack, TextField, Typography } from "@mui/material";
import ExpandMoreIcon from "@mui/icons-material/ExpandMore";
import { api, HostGroup } from "../api";

export interface TemplateParam { key: string; group: string; field: string; label: string; default: string | number | null }
export interface TemplateInclude {
  template_id: number; name: string; prefix: string; label: string; overrides: Record<string, Record<string, string | number>>;
  version: number | null; latest: number | null; behind: boolean;
}
export interface EnvTemplateInfo {
  id: number; name: string; description: string; owner: string; shared: boolean; builtin: boolean; can_edit: boolean;
  summary: { name: string; count: number; os: string; role: string }[]; params: TemplateParam[]; own_params: TemplateParam[];
  includes: TemplateInclude[]; used_by: string[]; broken: string; version: number;
}
interface Applied { host_groups: HostGroup[]; timezone: string | null; warnings: { group: string; level: string; message: string }[]; template: string }

export default function TemplatePicker({ hostId, initialId, onApply }: {
  hostId: number | ""; initialId: number | null; onApply: (groups: HostGroup[], timezone: string | null, template: string) => void;
}) {
  const [list, setList] = useState<EnvTemplateInfo[]>([]);
  const [id, setId] = useState<number | "">(initialId ?? "");
  const [values, setValues] = useState<Record<string, string>>({});
  const [notes, setNotes] = useState<Applied["warnings"]>([]);
  const [error, setError] = useState("");
  const [open, setOpen] = useState(initialId !== null);
  useEffect(() => { api.get<EnvTemplateInfo[]>("/api/env-templates").then(setList, () => setList([])); }, []);
  const t = list.find((x) => x.id === id);
  useEffect(() => {
    setValues(Object.fromEntries((t?.params ?? []).map((p) => [p.key, p.default === null ? "" : String(p.default)])));
    setNotes([]);
  }, [id, list]); // eslint-disable-line react-hooks/exhaustive-deps
  const apply = async () => {
    if (!t) return;
    setError("");
    try {
      const r = await api.post<Applied>(`/api/env-templates/${t.id}/instantiate`, { values, kvm_host_id: hostId === "" ? null : hostId });
      setNotes(r.warnings);
      onApply(r.host_groups, r.timezone, r.template);
    } catch (e) { setError((e as Error).message); }
  };
  if (list.length === 0) return null;
  return (
    <Accordion expanded={open} onChange={(_, v) => setOpen(v)} variant="outlined" disableGutters>
      <AccordionSummary expandIcon={<ExpandMoreIcon />}><Typography>Start from a template</Typography></AccordionSummary>
      <AccordionDetails>
        <Stack spacing={2}>
          <Stack direction="row" spacing={2} useFlexGap sx={{ flexWrap: "wrap", alignItems: "flex-start" }}>
            <TextField select size="small" label="Template" value={id} sx={{ minWidth: 280 }} onChange={(e) => setId(Number(e.target.value))}>
              {list.map((x) => <MenuItem key={x.id} value={x.id}>{x.name}{x.builtin ? " (starter)" : x.shared ? "" : " (private)"}</MenuItem>)}
            </TextField>
            <Link component={RouterLink} to="/environment-templates" variant="body2" sx={{ mt: 1 }}>Manage templates</Link>
          </Stack>
          {t && (
            <>
              {t.description && <Typography variant="body2" color="text.secondary">{t.description}</Typography>}
              <Stack direction="row" spacing={1} useFlexGap sx={{ flexWrap: "wrap" }}>
                {t.summary.map((g) => (
                  <Chip key={g.name} size="small" variant="outlined" label={`${g.name}: ${g.count} × ${g.os} (${g.role.replace("_", " ")})`} />
                ))}
              </Stack>
              {t.params.length > 0 && (
                <Stack direction="row" spacing={2} useFlexGap sx={{ flexWrap: "wrap" }}>
                  {t.params.map((p) => (
                    <TextField key={p.key} size="small" label={p.label} value={values[p.key] ?? ""} sx={{ minWidth: 200 }}
                      type={["os", "network"].includes(p.field) ? "text" : "number"}
                      onChange={(e) => setValues({ ...values, [p.key]: e.target.value })} />
                  ))}
                </Stack>
              )}
              {error && <Alert severity="error">{error}</Alert>}
              {notes.map((n, i) => <Alert key={i} severity={n.level === "info" ? "info" : "warning"}><b>{n.group}:</b> {n.message}</Alert>)}
              <div>
                <Button variant="outlined" onClick={apply} title="Fill the form below from this template; you can still change anything">
                  Use this template
                </Button>
              </div>
            </>
          )}
        </Stack>
      </AccordionDetails>
    </Accordion>
  );
}
