// An environment's own accounts (design §42): domain users on its domain controller and local
// users on chosen roles' hosts, each with a generated password (on the credentials sheet) that
// rotates weekly. Owners edit the list; the next deploy creates them on the VMs.
import { useEffect, useState } from "react";
import {
  Alert, Box, Button, Checkbox, Chip, Dialog, DialogActions, DialogContent, DialogTitle, FormControlLabel, IconButton,
  MenuItem, Paper, Stack, Table, TableBody, TableCell, TableHead, TableRow, TextField, Typography,
} from "@mui/material";
import DeleteIcon from "@mui/icons-material/Delete";
import { api } from "../api";

interface Account { name: string; kind: "domain" | "local"; hosts: string[]; groups: string[]; display_name?: string; ou?: string }
interface AccountList { accounts: Account[]; roles: string[]; can_edit: boolean }

export default function EnvAccounts({ envId }: { envId: string }) {
  const [data, setData] = useState<AccountList | null>(null);
  const [draft, setDraft] = useState<Account[] | null>(null);
  const [error, setError] = useState("");
  const [saved, setSaved] = useState(false);
  const load = () => api.get<AccountList>(`/api/environments/${envId}/accounts`).then(setData, (e) => setError(e.message));
  useEffect(() => { load(); }, [envId]); // eslint-disable-line react-hooks/exhaustive-deps
  if (!data || (!data.can_edit && data.accounts.length === 0)) return null;

  const save = () => api.put(`/api/environments/${envId}/accounts`, { accounts: draft }).then(() => {
    setDraft(null);
    setSaved(true);
    load();
  }, (e) => setError(e.message));
  const set = (i: number, patch: Partial<Account>) => setDraft(draft!.map((a, j) => (j === i ? { ...a, ...patch } : a)));
  const hasDc = data.roles.includes("domain_controller");
  const localRoles = data.roles.filter((r) => r !== "domain_controller");      // a DC has no local accounts

  return (
    <Paper variant="outlined" sx={{ p: 2 }}>
      <Stack spacing={1}>
        <Stack direction="row" spacing={1} sx={{ alignItems: "center" }}>
          <Typography variant="h6" sx={{ flexGrow: 1 }}>Accounts</Typography>
          {data.can_edit && <Button size="small" onClick={() => { setError(""); setDraft(data.accounts.map((a) => ({ ...a }))); }}
            title="Add or remove this environment's own accounts">Edit</Button>}
        </Stack>
        <Typography variant="body2" color="text.secondary">
          The environment's own accounts beyond the built-in admins. Their passwords are on the credentials sheet and change
          weekly (Admin → Secrets). Changes apply at the next deploy (Redeploy).
        </Typography>
        {saved && <Alert severity="success" onClose={() => setSaved(false)}>Saved. Redeploy to create new accounts on the VMs.</Alert>}
        {error && !draft && <Alert severity="error">{error}</Alert>}
        <Box>
          {data.accounts.map((a) => (
            <Chip key={a.name} sx={{ mr: 1, mb: 1 }} label={a.kind === "domain" ? `${a.name} (domain)` : `${a.name} (local on ${a.hosts.join(", ")})`}
              title={a.groups.length ? `Groups: ${a.groups.join(", ")}` : undefined} />
          ))}
          {data.accounts.length === 0 && <Typography variant="body2">None.</Typography>}
        </Box>
      </Stack>
      <Dialog open={!!draft} onClose={() => setDraft(null)} maxWidth="md" fullWidth>
        <DialogTitle>Environment accounts</DialogTitle>
        {draft && <DialogContent>
          <Table size="small">
            <TableHead>
              <TableRow><TableCell>Name</TableCell><TableCell>Kind</TableCell><TableCell>Hosts (local)</TableCell><TableCell>Groups</TableCell><TableCell /></TableRow>
            </TableHead>
            <TableBody>
              {draft.map((a, i) => (
                <TableRow key={i}>
                  <TableCell><TextField size="small" value={a.name} onChange={(e) => set(i, { name: e.target.value })} placeholder="jdoe" /></TableCell>
                  <TableCell>
                    <TextField select size="small" value={a.kind} onChange={(e) => set(i, { kind: e.target.value as Account["kind"], hosts: [] })}>
                      <MenuItem value="domain" disabled={!hasDc}>Domain</MenuItem><MenuItem value="local">Local</MenuItem>
                    </TextField>
                  </TableCell>
                  <TableCell>
                    {a.kind === "local" ? localRoles.map((r) => (
                      <FormControlLabel key={r} label={r} control={<Checkbox size="small" checked={a.hosts.includes(r)}
                        onChange={(e) => set(i, { hosts: e.target.checked ? [...a.hosts, r] : a.hosts.filter((x) => x !== r) })} />} />
                    )) : "—"}
                  </TableCell>
                  <TableCell><TextField size="small" value={a.groups.join(", ")} placeholder={a.kind === "domain" ? "Domain Admins" : "wheel"}
                    onChange={(e) => set(i, { groups: e.target.value.split(",").map((x) => x.trim()).filter(Boolean) })} /></TableCell>
                  <TableCell><IconButton size="small" onClick={() => setDraft(draft.filter((_, j) => j !== i))} title="Remove from the list (not from the VMs)"><DeleteIcon fontSize="small" /></IconButton></TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
          <Button sx={{ mt: 1 }} onClick={() => setDraft([...draft, { name: "", kind: hasDc ? "domain" : "local", hosts: [], groups: [] }])}>Add account</Button>
          <Typography variant="caption" color="text.secondary" component="div" sx={{ mt: 1 }}>
            Removing an account from the list stops LABaPe managing it and forgets its password; it stays on the VMs (remove it there from the VM's Accounts page).
          </Typography>
          {error && <Alert severity="error" sx={{ mt: 1 }}>{error}</Alert>}
        </DialogContent>}
        <DialogActions>
          <Button onClick={() => setDraft(null)}>Cancel</Button>
          <Button variant="contained" onClick={save}>Save</Button>
        </DialogActions>
      </Dialog>
    </Paper>
  );
}
