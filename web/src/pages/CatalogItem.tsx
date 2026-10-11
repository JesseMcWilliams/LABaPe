import { useEffect, useState } from "react";
import { Link as RouterLink, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { Alert, Box, Button, Chip, Paper, Stack } from "@mui/material";
import { api, fmtTime } from "../api";
import { hasRole, useAuth } from "../App";
import { DetailHeader, Facts } from "../components/PageParts";
import { ActionButton } from "../components/Confirm";
import { ErrorSummary, Field, FieldErrors, fromApiError } from "../forms";
import { CatalogItem, EXAMPLES } from "./Catalog";

// One catalog item (design §21.4, §22.2): admins create, edit, publish and
// retire it here; everyone else sees a published item read-only.
export default function CatalogItemPage() {
  const { id } = useParams();
  const [params] = useSearchParams();
  const isNew = id === "new";
  const navigate = useNavigate();
  const { me } = useAuth();
  const isAdmin = hasRole(me, "admin");
  const [item, setItem] = useState<CatalogItem | null>(null);
  const kind = item?.kind ?? params.get("kind") ?? "application";
  const [form, setForm] = useState({ name: "", display_name: "", description: "", notes: "", definition: EXAMPLES[kind] ?? "{}" });
  const [errs, setErrs] = useState<{ summary: string; fields: FieldErrors }>({ summary: "", fields: {} });
  const [saved, setSaved] = useState("");

  const load = () => api.get<CatalogItem>(`/api/catalog/${id}`).then((i) => {
    setItem(i);
    setForm({ name: i.name, display_name: i.display_name, description: i.description, notes: i.notes,
      definition: JSON.stringify(i.definition, null, 2) });
  }, (e) => setErrs({ summary: e.message, fields: {} }));
  useEffect(() => {
    if (!isNew) load();
  }, [id]); // eslint-disable-line react-hooks/exhaustive-deps

  const save = async () => {
    setSaved("");
    let definition: unknown;
    try { definition = JSON.parse(form.definition); } catch (e) {
      setErrs({ summary: "", fields: { definition: { field: "definition", label: "Definition", message: `Not valid JSON: ${(e as Error).message}` } } });
      return;
    }
    const body = { ...form, kind, definition };
    try {
      if (isNew) {
        const created = await api.post<CatalogItem>("/api/catalog", body);
        navigate(`/catalog/${created.id}`, { replace: true });
      } else {
        await api.put(`/api/catalog/${id}`, body);
        await load();
        setSaved("Saved.");
      }
      setErrs({ summary: "", fields: {} });
    } catch (e) { setErrs(fromApiError(e)); }
  };
  const setStatus = async (status: string) => {
    try { await api.put(`/api/catalog/${id}/status`, { status }); await load(); setSaved(`Now ${status}.`); }
    catch (e) { setErrs(fromApiError(e)); }
  };

  const title = isNew ? `New ${kind}` : item ? item.display_name : "Catalog item";
  const header = (
    <DetailHeader back={`/catalog?tab=${kind}`} backLabel="Catalog"
      title={<>{title} {item && <Chip size="small" label={item.status} color={item.status === "published" ? "success" : "default"} sx={{ ml: 1 }} />}</>}
      actions={isAdmin && item && (
        <>
          {item.status !== "published" && (
            <ActionButton onClick={() => setStatus("published")} tip="Make this item available in deploy forms"
              confirm={{ title: `Publish ${item.display_name}?`, confirmLabel: "Publish",
                body: "Everyone can then choose it for host groups. A package can be published only when everything in it is." }}>
              Publish
            </ActionButton>
          )}
          {item.status === "published" && (
            <ActionButton color="warning" onClick={() => setStatus("retired")} tip="Stop offering this item in deploy forms"
              confirm={{ title: `Retire ${item.display_name}?`, confirmLabel: "Retire",
                body: "It disappears from deploy forms. Environments that already use it keep it on redeploy." }}>
              Retire
            </ActionButton>
          )}
        </>
      )} />
  );

  if (!isAdmin) {
    return (
      <Stack spacing={2}>
        {header}
        {errs.summary && <Alert severity="error">{errs.summary}</Alert>}
        {item && (
          <Facts rows={[
            ["Kind", item.kind], ["Name", item.name], ["Platforms", item.platforms.join(", ")],
            ["Description", item.description], ["Notes", <Box sx={{ whiteSpace: "pre-wrap" }}>{item.notes}</Box>],
            ["Definition", <Box component="pre" sx={{ m: 0, fontSize: 12 }}>{JSON.stringify(item.definition, null, 2)}</Box>],
            ["Updated", `${fmtTime(item.updated_at)} by ${item.updated_by || item.created_by}`],
          ]} />
        )}
      </Stack>
    );
  }

  return (
    <Stack spacing={2}>
      {header}
      {saved && <Alert severity="success" onClose={() => setSaved("")}>{saved}</Alert>}
      <Paper variant="outlined" sx={{ p: 2 }}>
        <Stack spacing={1}>
          <ErrorSummary summary={errs.summary} fields={errs.fields} />
          <Stack direction="row" spacing={2} useFlexGap sx={{ alignItems: "flex-start", flexWrap: "wrap" }}>
            <Field label="Name" value={form.name} disabled={!isNew} error={errs.fields.name}
              help="ID: lower-case letters, digits, _ and -" onChange={(e) => setForm({ ...form, name: e.target.value })} />
            <Field label="Display name" value={form.display_name} sx={{ minWidth: 280 }} error={errs.fields.display_name}
              onChange={(e) => setForm({ ...form, display_name: e.target.value })} />
          </Stack>
          <Field label="Description" value={form.description} fullWidth onChange={(e) => setForm({ ...form, description: e.target.value })} />
          <Field label="Notes" value={form.notes} fullWidth multiline minRows={2} help="Quirks, licensing, after-tasks"
            onChange={(e) => setForm({ ...form, notes: e.target.value })} />
          <Field label="Definition (JSON)" value={form.definition} fullWidth multiline minRows={10} error={errs.fields.definition ?? errs.fields.items}
            slotProps={{ input: { sx: { fontFamily: "monospace", fontSize: 13 } } }}
            help={kind === "application" ? "chocolatey/apt/dnf/zypper names, or windows/linux installers {type, source: url|local, url|path, arguments, product_id}"
              : kind === "service" ? "windows: {features, services}; linux: {packages: {dnf, apt}, units}" : "items: [{kind, name}]"}
            onChange={(e) => setForm({ ...form, definition: e.target.value })} />
          <Stack direction="row" spacing={2}>
            <ActionButton variant="contained" onClick={save} tip="Save the item; its status doesn't change"
              confirm={{ title: isNew ? `Create ${form.name || "this item"}?` : `Save ${item?.display_name ?? form.name}?`,
                confirmLabel: "Save", body: isNew ? "It's saved as a draft; publish it to offer it in deploy forms."
                  : item?.status === "published" ? "The change applies to new deployments and redeploys of environments that use it."
                  : "The draft is updated." }}>
              Save as {item?.status ?? "draft"}
            </ActionButton>
            <Button component={RouterLink} to={`/catalog?tab=${kind}`} title="Back to the catalog without saving">Back</Button>
          </Stack>
        </Stack>
      </Paper>
    </Stack>
  );
}
