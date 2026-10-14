import { useEffect, useState } from "react";
import { Link as RouterLink, useNavigate, useParams } from "react-router-dom";
import { Alert, Box, Button, Checkbox, FormControlLabel, MenuItem, Paper, Stack } from "@mui/material";
import { api, fmtTime } from "../api";
import { hasRole, useAuth } from "../App";
import { DetailHeader, Facts } from "../components/PageParts";
import { ActionButton } from "../components/Confirm";
import { ErrorSummary, Field, FieldErrors, fromApiError } from "../forms";
import { OS_KEYS, size, StoredFile } from "./Files";
import { TargetChips, TargetPicker, useTargetVocabulary } from "../components/Targets";

// One file in the file manager (design §21.3, §22.2): admins and file
// managers edit its OS, description and notes or delete it; others read it.
export default function FileDetail() {
  const { id } = useParams();
  const navigate = useNavigate();
  const { me } = useAuth();
  const canManage = hasRole(me, "file_manager");
  const [file, setFile] = useState<StoredFile | null>(null);
  const [form, setForm] = useState({ description: "", notes: "", os_key: "", is_default: false, targets: [] as string[] });
  const vocab = useTargetVocabulary();
  const [errs, setErrs] = useState<{ summary: string; fields: FieldErrors }>({ summary: "", fields: {} });
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    api.get<StoredFile>(`/api/files/${id}`).then((f) => {
      setFile(f);
      setForm({ description: f.description, notes: f.notes, os_key: f.os_key, is_default: f.is_default,
        targets: f.targets ?? [] });
    }, (e) => setErrs({ summary: e.message, fields: {} }));
  }, [id]);

  const back = `/files?tab=${file?.category ?? "isos"}`;
  const save = async () => {
    setSaved(false);
    try {
      await api.put(`/api/files/${id}`, file?.category !== "isos" ? form : { ...form, targets: null });
      setErrs({ summary: "", fields: {} });
      setSaved(true);
    } catch (e) { setErrs(fromApiError(e)); }
  };
  const remove = async () => {
    if (!file) return;
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
        actions={canManage && file && (
          <ActionButton color="error" onClick={remove} tip="Delete this file from disk"
            confirm={{ title: `Delete ${file.name}?`, danger: true, confirmLabel: "Delete",
              body: `The file is removed from ${file.path}. Templates already built from it aren't affected. This can't be undone.` }}>
            Delete
          </ActionButton>
        )} />
      {saved && <Alert severity="success" onClose={() => setSaved(false)}>Saved.</Alert>}
      {!canManage && errs.summary && <Alert severity="error">{errs.summary}</Alert>}
      {file && <Facts rows={canManage ? facts : [
        ...facts, ["Installs OS", file.os_key ? `${file.os_key}${file.is_default ? " (default)" : ""}` : ""],
        ...(file.category !== "isos" ? [["OS targets", <TargetChips targets={file.targets ?? []} vocab={vocab} />] as [string, React.ReactNode]] : []),
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
            {file.category !== "isos" && (
              <TargetPicker value={form.targets} onChange={(targets) => setForm({ ...form, targets })}
                error={errs.fields.targets?.message} />
            )}
            <Field label="Description" value={form.description} fullWidth onChange={(e) => setForm({ ...form, description: e.target.value })} />
            <Field label="Notes" value={form.notes} fullWidth multiline minRows={3} help="Quirks, after-tasks, licensing"
              onChange={(e) => setForm({ ...form, notes: e.target.value })} />
            <Stack direction="row" spacing={2}>
              <ActionButton variant="contained" onClick={save} tip="Save the file's details"
                confirm={{ title: `Save ${file.name}?`, confirmLabel: "Save",
                  body: file.category === "isos" ? "The OS it installs and whether it's the default for that OS change for new builds and ISO installs."
                    : file.category === "installers" ? "Its targets, description and notes are updated. New deploys use the installer only on the OSes it targets."
                    : "The description and notes are updated." }}>
                Save
              </ActionButton>
              <Button component={RouterLink} to={back} title="Back to the file list without saving">Back</Button>
            </Stack>
          </Stack>
        </Paper>
      )}
    </Stack>
  );
}
