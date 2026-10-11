import { useEffect, useState } from "react";
import { Link as RouterLink, useNavigate, useParams } from "react-router-dom";
import { Alert, Box, Button, Checkbox, FormControlLabel, MenuItem, Paper, Stack } from "@mui/material";
import { api, fmtTime } from "../api";
import { hasRole, useAuth } from "../App";
import { DetailHeader, Facts } from "../components/PageParts";
import { ErrorSummary, Field, FieldErrors, fromApiError } from "../forms";
import { OS_KEYS, size, StoredFile } from "./Files";

// One file in the file manager (design §21.3, §22.2): admins and file
// managers edit its OS, description and notes or delete it; others read it.
export default function FileDetail() {
  const { id } = useParams();
  const navigate = useNavigate();
  const { me } = useAuth();
  const canManage = hasRole(me, "file_manager");
  const [file, setFile] = useState<StoredFile | null>(null);
  const [form, setForm] = useState({ description: "", notes: "", os_key: "", is_default: false });
  const [errs, setErrs] = useState<{ summary: string; fields: FieldErrors }>({ summary: "", fields: {} });
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    api.get<StoredFile>(`/api/files/${id}`).then((f) => {
      setFile(f);
      setForm({ description: f.description, notes: f.notes, os_key: f.os_key, is_default: f.is_default });
    }, (e) => setErrs({ summary: e.message, fields: {} }));
  }, [id]);

  const back = `/files?tab=${file?.category ?? "isos"}`;
  const save = async () => {
    setSaved(false);
    try {
      await api.put(`/api/files/${id}`, form);
      setErrs({ summary: "", fields: {} });
      setSaved(true);
    } catch (e) { setErrs(fromApiError(e)); }
  };
  const remove = async () => {
    if (!file || !confirm(`Delete ${file.name}? The file is removed from disk.`)) return;
    try { await api.del(`/api/files/${id}`); navigate(back); } catch (e) { setErrs(fromApiError(e)); }
  };

  const facts: [string, React.ReactNode][] = file ? [
    ["Path", file.path], ["Size", size(file.size)],
    ["SHA-256", <Box component="span" sx={{ fontFamily: "monospace", fontSize: 12 }}>{file.sha256 || "computing…"}</Box>],
    ["Added", file.uploaded_by ? `${file.uploaded_by}, ${fmtTime(file.uploaded_at)}` : `found ${fmtTime(file.mtime)}`],
  ] : [];

  return (
    <Stack spacing={2}>
      <DetailHeader back={back} backLabel="Files" title={file?.name ?? "File"}
        actions={canManage && file && <Button color="error" onClick={remove}>Delete</Button>} />
      {saved && <Alert severity="success" onClose={() => setSaved(false)}>Saved.</Alert>}
      {!canManage && errs.summary && <Alert severity="error">{errs.summary}</Alert>}
      {file && <Facts rows={canManage ? facts : [
        ...facts, ["Installs OS", file.os_key ? `${file.os_key}${file.is_default ? " (default)" : ""}` : ""],
        ["Description", file.description], ["Notes", <Box sx={{ whiteSpace: "pre-wrap" }}>{file.notes}</Box>],
      ]} />}
      {canManage && file && (
        <Paper variant="outlined" sx={{ p: 2 }}>
          <Stack spacing={1}>
            <ErrorSummary summary={errs.summary} fields={errs.fields} />
            {file.category === "isos" && (
              <Stack direction="row" spacing={2} sx={{ alignItems: "flex-start" }}>
                <Field select label="Installs OS" value={form.os_key} sx={{ minWidth: 220 }} error={errs.fields.os_key}
                  onChange={(e) => setForm({ ...form, os_key: e.target.value })} help="Which OS key builds from this ISO">
                  <MenuItem value="">(none)</MenuItem>
                  {OS_KEYS.map((k) => <MenuItem key={k} value={k}>{k}</MenuItem>)}
                </Field>
                <FormControlLabel label="Default for this OS" control={<Checkbox checked={form.is_default}
                  onChange={(e) => setForm({ ...form, is_default: e.target.checked })} />} />
              </Stack>
            )}
            <Field label="Description" value={form.description} fullWidth onChange={(e) => setForm({ ...form, description: e.target.value })} />
            <Field label="Notes" value={form.notes} fullWidth multiline minRows={3} help="Quirks, after-tasks, licensing"
              onChange={(e) => setForm({ ...form, notes: e.target.value })} />
            <Stack direction="row" spacing={2}>
              <Button variant="contained" onClick={save}>Save</Button>
              <Button component={RouterLink} to={back}>Back</Button>
            </Stack>
          </Stack>
        </Paper>
      )}
    </Stack>
  );
}
