import { useEffect, useState } from "react";
import { Link as RouterLink, useParams } from "react-router-dom";
import { Alert, Box, Button, Chip, MenuItem, Paper, Stack } from "@mui/material";
import { api, fmtTime, KvmHost, Template } from "../api";
import { hasRole, useAuth } from "../App";
import { DetailHeader, Facts } from "../components/PageParts";
import { ErrorSummary, Field, FieldErrors, fromApiError } from "../forms";
import { OS_KEYS } from "./Files";

// One template in the library (design §21.3, §22.2): template editors set
// its OS, minimums, description and notes; everyone can read it.
export default function TemplateDetail() {
  const { id } = useParams();
  const { me } = useAuth();
  const canEdit = hasRole(me, "template_editor");
  const [t, setT] = useState<Template | null>(null);
  const [hosts, setHosts] = useState<KvmHost[]>([]);
  const [form, setForm] = useState({ description: "", notes: "", os_key: "", edition: "", min_cpu: 1, min_memory_mb: 1024 });
  const [errs, setErrs] = useState<{ summary: string; fields: FieldErrors }>({ summary: "", fields: {} });
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    api.get<Template>(`/api/templates/${id}`).then((x) => {
      setT(x);
      setForm({ description: x.description, notes: x.notes, os_key: x.os_key, edition: x.edition,
        min_cpu: x.min_cpu, min_memory_mb: x.min_memory_mb });
    }, (e) => setErrs({ summary: e.message, fields: {} }));
    api.get<KvmHost[]>("/api/hosts").then(setHosts, () => setHosts([]));
  }, [id]);

  const save = async () => {
    setSaved(false);
    try {
      setT(await api.put<Template>(`/api/templates/${id}`, form));
      setErrs({ summary: "", fields: {} });
      setSaved(true);
    } catch (e) { setErrs(fromApiError(e)); }
  };

  return (
    <Stack spacing={2}>
      <DetailHeader back="/files?tab=templates" backLabel="Templates"
        title={<>{t?.name ?? "Template"}{t?.source === "inferred" && <Chip size="small" label="inferred" sx={{ ml: 1 }} />}</>} />
      {saved && <Alert severity="success" onClose={() => setSaved(false)}>Saved.</Alert>}
      {!canEdit && errs.summary && <Alert severity="error">{errs.summary}</Alert>}
      {t && (
        <Facts rows={[
          ["KVM host", hosts.find((h) => h.id === t.kvm_host_id)?.name ?? t.kvm_host_id],
          ["Disk", `${t.disk_gb} GB`],
          ...(canEdit ? [] : [
            ["OS", `${t.os_key}${t.edition ? ` (${t.edition})` : ""}`],
            ["Minimums", `${t.min_cpu} vCPU · ${t.min_memory_mb} MB`],
            ["Description", t.description],
            ["Notes", <Box sx={{ whiteSpace: "pre-wrap" }}>{t.notes}</Box>],
          ] as [string, React.ReactNode][]),
          ["Updated", fmtTime(t.updated_at)],
        ]} />
      )}
      {canEdit && t && (
        <Paper variant="outlined" sx={{ p: 2 }}>
          <Stack spacing={1}>
            <ErrorSummary summary={errs.summary} fields={errs.fields} />
            <Stack direction="row" spacing={2} useFlexGap sx={{ alignItems: "flex-start", flexWrap: "wrap" }}>
              <Field select label="OS" value={form.os_key} sx={{ minWidth: 200 }} error={errs.fields.os_key}
                onChange={(e) => setForm({ ...form, os_key: e.target.value })}>
                {OS_KEYS.map((k) => <MenuItem key={k} value={k}>{k}</MenuItem>)}
              </Field>
              <Field select label="Edition" value={form.edition} sx={{ minWidth: 140 }} onChange={(e) => setForm({ ...form, edition: e.target.value })}>
                <MenuItem value="">(n/a)</MenuItem><MenuItem value="desktop">Desktop</MenuItem><MenuItem value="core">Core</MenuItem>
              </Field>
              <Field label="Min vCPUs" type="number" value={form.min_cpu} sx={{ width: 110 }} error={errs.fields.min_cpu}
                onChange={(e) => setForm({ ...form, min_cpu: Number(e.target.value) })} />
              <Field label="Min memory (MB)" type="number" value={form.min_memory_mb} sx={{ width: 150 }} error={errs.fields.min_memory_mb}
                onChange={(e) => setForm({ ...form, min_memory_mb: Number(e.target.value) })} />
            </Stack>
            <Field label="Description" value={form.description} fullWidth onChange={(e) => setForm({ ...form, description: e.target.value })} />
            <Field label="Notes" value={form.notes} fullWidth multiline minRows={4} help="Quirks and after-tasks for anyone using this template"
              onChange={(e) => setForm({ ...form, notes: e.target.value })} />
            <Stack direction="row" spacing={2}>
              <Button variant="contained" onClick={save}>Save</Button>
              <Button component={RouterLink} to="/files?tab=templates">Back</Button>
            </Stack>
          </Stack>
        </Paper>
      )}
    </Stack>
  );
}
