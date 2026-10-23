// Environment templates (design §68): the starters and the saved ones, with who owns them.
import { useCallback, useEffect, useState } from "react";
import { Link as RouterLink, useNavigate } from "react-router-dom";
import {
  Alert, Button, Checkbox, Chip, Dialog, DialogActions, DialogContent, DialogTitle, FormControlLabel, Stack, Table, TableBody,
  TableCell, TableHead, TableRow, TextField, Typography,
} from "@mui/material";
import { api } from "../api";
import { ActionButton } from "../components/Confirm";
import type { EnvTemplateInfo } from "../components/TemplatePicker";
import { hasRole, useAuth } from "../App";

function Edit({ t, onClose, onSaved }: { t: EnvTemplateInfo; onClose: () => void; onSaved: () => void }) {
  const [name, setName] = useState(t.name);
  const [description, setDescription] = useState(t.description);
  const [shared, setShared] = useState(t.shared);
  const [error, setError] = useState("");
  const save = async () => {
    try {
      await api.put(`/api/env-templates/${t.id}`, {
        name, description, shared, params: t.params.map((p) => ({ group: p.group, field: p.field, label: p.label })),
      });
      onSaved();
    } catch (e) { setError((e as Error).message); }
  };
  return (
    <Dialog open onClose={onClose} maxWidth="sm" fullWidth>
      <DialogTitle>Edit template</DialogTitle>
      <DialogContent>
        <Stack spacing={2} sx={{ pt: 1 }}>
          <TextField size="small" label="Name" value={name} onChange={(e) => setName(e.target.value)} />
          <TextField size="small" label="Description" value={description} onChange={(e) => setDescription(e.target.value)} multiline minRows={2} />
          <FormControlLabel label="Share it with everyone who can deploy"
            control={<Checkbox checked={shared} onChange={(e) => setShared(e.target.checked)} />} />
          {error && <Alert severity="error">{error}</Alert>}
        </Stack>
      </DialogContent>
      <DialogActions><Button onClick={onClose}>Cancel</Button><Button variant="contained" onClick={save}>Save</Button></DialogActions>
    </Dialog>
  );
}

export default function EnvTemplates() {
  const { me } = useAuth();
  const navigate = useNavigate();
  const [list, setList] = useState<EnvTemplateInfo[]>([]);
  const [error, setError] = useState("");
  const [editing, setEditing] = useState<EnvTemplateInfo | null>(null);
  const load = useCallback(() => { api.get<EnvTemplateInfo[]>("/api/env-templates").then(setList, (e) => setError((e as Error).message)); }, []);
  useEffect(load, [load]);
  const canDeploy = hasRole(me, "deployer", "template_editor");
  return (
    <Stack spacing={2}>
      <Stack direction="row" spacing={2} sx={{ alignItems: "center" }}>
        <Typography variant="h5" sx={{ flexGrow: 1 }}>Environment templates</Typography>
        <Button component={RouterLink} to="/environments">Environments</Button>
      </Stack>
      <Typography variant="body2" color="text.secondary">
        A template is an environment&apos;s host groups without its name, host, VM names, addresses or accounts, plus the few values you fill in when
        you start from it. Start from one on the New environment form; save any environment as a template from its page. The starters ship
        with LABaPe and can&apos;t be changed.
      </Typography>
      {error && <Alert severity="error">{error}</Alert>}
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
                {t.description && <Typography variant="caption" component="div" color="text.secondary">{t.description}</Typography>}
              </TableCell>
              <TableCell>{t.summary.map((g) => `${g.name} ${g.count}×${g.os}`).join(", ")}</TableCell>
              <TableCell>{t.params.map((p) => p.label).join(", ") || "nothing"}</TableCell>
              <TableCell>{t.builtin ? "LABaPe" : t.owner}</TableCell>
              <TableCell align="right">
                {canDeploy && (
                  <Button size="small" onClick={() => navigate(`/environments/new?template=${t.id}`)} title="Open the New environment form from this template">
                    Use
                  </Button>
                )}
                {t.can_edit && <Button size="small" onClick={() => setEditing(t)}>Edit</Button>}
                {t.can_edit && (
                  <ActionButton size="small" color="error" tip="Delete this template (environments made from it are not affected)"
                    confirm={{ title: `Delete ${t.name}?`, body: "Environments made from it are not affected.", confirmLabel: "Delete" }}
                    onClick={async () => { await api.del(`/api/env-templates/${t.id}`); load(); }}>
                    Delete
                  </ActionButton>
                )}
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
      {editing && <Edit t={editing} onClose={() => setEditing(null)} onSaved={() => { setEditing(null); load(); }} />}
    </Stack>
  );
}
