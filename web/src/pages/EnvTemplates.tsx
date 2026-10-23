// Environment templates (design §68, §69): the starters and the saved ones, templates made of other templates, YAML
// export and import.
import { ChangeEvent, useCallback, useEffect, useRef, useState } from "react";
import { Link as RouterLink, useNavigate } from "react-router-dom";
import {
  Alert, Button, Checkbox, Chip, Dialog, DialogActions, DialogContent, DialogTitle, FormControlLabel, Link,
  Stack, Table, TableBody, TableCell, TableHead, TableRow, TextField, Typography,
} from "@mui/material";
import { api } from "../api";
import { ActionButton } from "../components/Confirm";
import PartsEditor, { includesBody, PartRow, partsReady, rowsOf } from "../components/PartsEditor";
import type { EnvTemplateInfo } from "../components/TemplatePicker";
import { hasRole, useAuth } from "../App";

function Edit({ t, list, onClose, onSaved }: { t: EnvTemplateInfo; list: EnvTemplateInfo[]; onClose: () => void; onSaved: () => void }) {
  const [name, setName] = useState(t.name);
  const [description, setDescription] = useState(t.description);
  const [shared, setShared] = useState(t.shared);
  const [parts, setParts] = useState<PartRow[]>(() => rowsOf(t.includes));
  const [error, setError] = useState("");
  const save = async () => {
    try {
      // The template's own parameters are sent as they are; its parts (with their fixed values) are what this edits.
      await api.put(`/api/env-templates/${t.id}`, {
        name, description, shared, params: t.own_params.map((p) => ({ group: p.group, field: p.field, label: p.label })),
        includes: includesBody(parts),
      });
      onSaved();
    } catch (e) { setError((e as Error).message); }
  };
  return (
    <Dialog open onClose={onClose} maxWidth="md" fullWidth>
      <DialogTitle>Edit template</DialogTitle>
      <DialogContent>
        <Stack spacing={2} sx={{ pt: 1 }}>
          <TextField size="small" label="Name" value={name} onChange={(e) => setName(e.target.value)} />
          <TextField size="small" label="Description" value={description} onChange={(e) => setDescription(e.target.value)} multiline minRows={2} />
          <FormControlLabel label="Share it with everyone who can deploy"
            control={<Checkbox checked={shared} onChange={(e) => setShared(e.target.checked)} />} />
          <Typography variant="subtitle2">Parts</Typography>
          <PartsEditor rows={parts} onChange={setParts} list={list} selfId={t.id} />
          {error && <Alert severity="error">{error}</Alert>}
        </Stack>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>Cancel</Button>
        <Button variant="contained" onClick={save} disabled={!partsReady(parts)}>Save</Button>
      </DialogActions>
    </Dialog>
  );
}

// A new template made of other templates: each part with the prefix its host groups get.
function FromParts({ list, onClose, onSaved }: { list: EnvTemplateInfo[]; onClose: () => void; onSaved: () => void }) {
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [shared, setShared] = useState(false);
  const [parts, setParts] = useState<PartRow[]>([{ template_id: "", prefix: "", label: "", overrides: {}, version: "" }]);
  const [error, setError] = useState("");
  const ready = name.trim().length >= 3 && parts.length > 0 && partsReady(parts);
  const save = async () => {
    try {
      await api.post("/api/env-templates", { name, description, shared, includes: includesBody(parts) });
      onSaved();
    } catch (e) { setError((e as Error).message); }
  };
  return (
    <Dialog open onClose={onClose} maxWidth="md" fullWidth>
      <DialogTitle>New template from parts</DialogTitle>
      <DialogContent>
        <Stack spacing={2} sx={{ pt: 1 }}>
          <Typography variant="body2" color="text.secondary">
            Each part brings its host groups, with its prefix put in front of their names (up to 6 letters, so the same part can be used twice).
            Its parameters become this template&apos;s, labelled with the prefix; you can also set values for a part&apos;s groups.
          </Typography>
          <TextField size="small" label="Template name" value={name} onChange={(e) => setName(e.target.value)} required />
          <TextField size="small" label="Description" value={description} onChange={(e) => setDescription(e.target.value)} multiline minRows={2} />
          <PartsEditor rows={parts} onChange={setParts} list={list} />
          <FormControlLabel label="Share it with everyone who can deploy"
            control={<Checkbox checked={shared} onChange={(e) => setShared(e.target.checked)} />} />
          {error && <Alert severity="error">{error}</Alert>}
        </Stack>
      </DialogContent>
      <DialogActions><Button onClick={onClose}>Cancel</Button><Button variant="contained" onClick={save} disabled={!ready}>Save</Button></DialogActions>
    </Dialog>
  );
}

export default function EnvTemplates() {
  const { me } = useAuth();
  const navigate = useNavigate();
  const [list, setList] = useState<EnvTemplateInfo[]>([]);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [editing, setEditing] = useState<EnvTemplateInfo | null>(null);
  const [parts, setParts] = useState(false);
  const file = useRef<HTMLInputElement>(null);
  const load = useCallback(() => { api.get<EnvTemplateInfo[]>("/api/env-templates").then(setList, (e) => setError((e as Error).message)); }, []);
  useEffect(load, [load]);
  const canDeploy = hasRole(me, "deployer", "template_editor");
  const importFile = async (e: ChangeEvent<HTMLInputElement>) => {
    const f = e.target.files?.[0];
    e.target.value = "";
    if (!f) return;
    setError("");
    setNotice("");
    try {
      const r = await api.post<{ created: { name: string }[] }>("/api/env-templates/import", { yaml: await f.text(), shared: false });
      setNotice(`Imported ${r.created.map((c) => c.name).join(", ")} (private: share them from Edit).`);
      load();
    } catch (err) { setError((err as Error).message); }
  };
  return (
    <Stack spacing={2}>
      <Stack direction="row" spacing={1} useFlexGap sx={{ alignItems: "center", flexWrap: "wrap" }}>
        <Typography variant="h5" sx={{ flexGrow: 1 }}>Environment templates</Typography>
        {canDeploy && <Button variant="outlined" onClick={() => setParts(true)} title="A new template made of other templates">New from parts</Button>}
        {canDeploy && (
          <Button variant="outlined" onClick={() => file.current?.click()} title="Create templates from a YAML file exported from this or another stack">
            Import
          </Button>
        )}
        <Button variant="outlined" component="a" href="/api/env-templates/export" download
          title="Every template you can see except the starters, as one YAML file">
          Export all
        </Button>
        <Button component={RouterLink} to="/environments">Environments</Button>
        <input ref={file} type="file" accept=".yaml,.yml,text/yaml" hidden onChange={importFile} />
      </Stack>
      <Typography variant="body2" color="text.secondary">
        A template is an environment&apos;s host groups without its name, host, VM names, addresses or accounts, plus the few values you fill in when
        you start from it. It can be made of other templates (parts), and exported as YAML to use on another stack. Start from one on the New
        environment form; save any environment as a template from its page. The starters ship with LABaPe and can&apos;t be changed.
      </Typography>
      {error && <Alert severity="error" onClose={() => setError("")}>{error}</Alert>}
      {notice && <Alert severity="success" onClose={() => setNotice("")}>{notice}</Alert>}
      <Table size="small">
        <TableHead>
          <TableRow>
            <TableCell>Name</TableCell><TableCell>Host groups</TableCell><TableCell>You fill in</TableCell><TableCell>Owner</TableCell><TableCell />
          </TableRow>
        </TableHead>
        <TableBody>
          {list.map((t) => (
            <TableRow key={t.id} hover>
              <TableCell>
                {t.name} {t.builtin && <Chip size="small" label="starter" sx={{ ml: 0.5 }} />}
                {!t.builtin && !t.shared && <Chip size="small" variant="outlined" label="private" sx={{ ml: 0.5 }} />}
                {t.version > 1 && <Chip size="small" variant="outlined" label={`v${t.version}`} title="Each change to the groups, parameters or parts is a new version" sx={{ ml: 0.5 }} />}
                {t.broken && <Chip size="small" color="error" label="broken" title={t.broken} sx={{ ml: 0.5 }} />}
                {t.description && <Typography variant="caption" component="div" color="text.secondary">{t.description}</Typography>}
                {t.includes.length > 0 && (
                  <Typography variant="caption" component="div" color="text.secondary">
                    Made of: {t.includes.map((i) => `${i.name} (${i.prefix}${i.version !== null ? `, pinned to v${i.version}` : ""})`).join(", ")}
                    {t.includes.some((i) => i.behind) && " · a pinned part has a newer version"}
                  </Typography>
                )}
                {t.used_by.length > 0 && <Typography variant="caption" component="div" color="text.secondary">Part of: {t.used_by.join(", ")}</Typography>}
              </TableCell>
              <TableCell>{t.summary.map((g) => `${g.name} ${g.count}×${g.os}`).join(", ")}</TableCell>
              <TableCell>{t.params.map((p) => p.label).join(", ") || "nothing"}</TableCell>
              <TableCell>{t.builtin ? "LABaPe" : t.owner}</TableCell>
              <TableCell align="right" sx={{ whiteSpace: "nowrap" }}>
                {canDeploy && (
                  <Button size="small" onClick={() => navigate(`/environments/new?template=${t.id}`)} title="Open the New environment form from this template">
                    Use
                  </Button>
                )}
                {!t.builtin && (
                  <Link href={`/api/env-templates/${t.id}/export`} download variant="button" sx={{ mx: 1, fontSize: "0.8125rem" }}
                    title="Download this template, with the templates it is made of, as YAML">
                    Export
                  </Link>
                )}
                {t.can_edit && <Button size="small" onClick={() => setEditing(t)}>Edit</Button>}
                {t.can_edit && (
                  <ActionButton size="small" color="error" tip="Delete this template (environments made from it are not affected)"
                    confirm={{ title: `Delete ${t.name}?`, body: "Environments made from it are not affected.", confirmLabel: "Delete" }}
                    onClick={async () => {
                      try { await api.del(`/api/env-templates/${t.id}`); load(); } catch (e) { setError((e as Error).message); }
                    }}>
                    Delete
                  </ActionButton>
                )}
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
      {editing && <Edit t={editing} list={list} onClose={() => setEditing(null)} onSaved={() => { setEditing(null); load(); }} />}
      {parts && <FromParts list={list} onClose={() => setParts(false)} onSaved={() => { setParts(false); load(); }} />}
    </Stack>
  );
}
