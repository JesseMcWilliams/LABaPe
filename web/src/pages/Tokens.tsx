// API tokens (design §37): personal tokens for every user; admins also make service tokens and
// see and revoke everyone's. A token's secret is shown once.
import { useEffect, useState } from "react";
import {
  Alert, Box, Button, Checkbox, Chip, Dialog, DialogActions, DialogContent, DialogTitle, FormControlLabel, MenuItem,
  Stack, Table, TableBody, TableCell, TableHead, TableRow, TextField, Typography,
} from "@mui/material";
import { api, fmtTime } from "../api";
import { useAuth } from "../App";
import { ActionButton } from "../components/Confirm";

interface Token {
  id: number; name: string; kind: string; owner: string; roles: string[]; prefix: string; created_at: string;
  expires_at: string; last_used_at: string | null; last_used_ip: string; revoked_at: string | null; revoked_by: string;
  state: string;
}
interface TokenList { roles: string[]; my_roles: string[]; tokens: Token[] }

const STATE_COLOR: Record<string, "success" | "default" | "error"> = { active: "success", expired: "default", revoked: "error" };

export default function Tokens() {
  const { me } = useAuth();
  const admin = !!me?.roles.includes("admin");
  const [all, setAll] = useState(false);
  const [data, setData] = useState<TokenList | null>(null);
  const [error, setError] = useState("");
  const [making, setMaking] = useState(false);
  const [form, setForm] = useState({ name: "", kind: "personal", roles: [] as string[], days: 90 });
  const [made, setMade] = useState<(Token & { secret: string }) | null>(null);
  const [formError, setFormError] = useState("");

  const load = () => api.get<TokenList>(`/api/tokens${all ? "?all=true" : ""}`).then(setData, (e) => setError(e.message));
  useEffect(() => { load(); }, [all]); // eslint-disable-line react-hooks/exhaustive-deps

  const create = () => api.post<Token & { secret: string }>("/api/tokens", form).then((t) => {
    setMaking(false);
    setMade(t);
    setForm({ name: "", kind: "personal", roles: [], days: 90 });
    load();
  }, (e) => setFormError(e.message));

  if (error) return <Alert severity="error">{error}</Alert>;
  if (!data) return null;
  const choosable = form.kind === "service" ? data.roles : data.my_roles;
  return (
    <Stack spacing={2}>
      <Stack direction="row" spacing={1} sx={{ alignItems: "center" }}>
        <Typography variant="h5">API tokens</Typography>
        <Box sx={{ flexGrow: 1 }} />
        {admin && <FormControlLabel label="Everyone's, and service tokens" control={<Checkbox checked={all} onChange={(e) => setAll(e.target.checked)} />} />}
        <Button variant="contained" disabled={me?.breakglass} onClick={() => { setFormError(""); setMaking(true); }}
          title="A token for a script or CI to call the LABaPe API without a browser sign-in">New token</Button>
      </Stack>
      <Typography variant="body2" color="text.secondary">
        Send a token as <code>Authorization: Bearer &lt;token&gt;</code>. A personal token acts as you, with the roles you choose
        but never more than you have when it's used. Writes made with a token are in the audit log. Tokens expire within a year.
      </Typography>
      <Table size="small">
        <TableHead>
          <TableRow>
            <TableCell>Name</TableCell>{all && <TableCell>Owner</TableCell>}<TableCell>Kind</TableCell><TableCell>Roles</TableCell>
            <TableCell>Token</TableCell><TableCell>Expires</TableCell><TableCell>Last used</TableCell><TableCell>State</TableCell><TableCell />
          </TableRow>
        </TableHead>
        <TableBody>
          {data.tokens.map((t) => (
            <TableRow key={t.id}>
              <TableCell>{t.name}</TableCell>{all && <TableCell>{t.owner}</TableCell>}<TableCell>{t.kind}</TableCell>
              <TableCell>{t.roles.length ? t.roles.join(", ") : "all of the owner's"}</TableCell>
              <TableCell><code>{t.prefix}…</code></TableCell>
              <TableCell>{fmtTime(t.expires_at)}</TableCell>
              <TableCell>{t.last_used_at ? `${fmtTime(t.last_used_at)} from ${t.last_used_ip}` : "never"}</TableCell>
              <TableCell><Chip size="small" color={STATE_COLOR[t.state]} label={t.state}
                title={t.revoked_at ? `revoked by ${t.revoked_by} ${fmtTime(t.revoked_at)}` : undefined} /></TableCell>
              <TableCell>
                {t.state === "active" && <ActionButton size="small" color="error" tip="It stops working at once"
                  confirm={{ title: `Revoke ${t.name}?`, confirmLabel: "Revoke", body: "Anything using this token is refused from now on." }}
                  onClick={() => api.del(`/api/tokens/${t.id}`).then(load)}>Revoke</ActionButton>}
              </TableCell>
            </TableRow>
          ))}
          {data.tokens.length === 0 && <TableRow><TableCell colSpan={9}>No tokens.</TableCell></TableRow>}
        </TableBody>
      </Table>

      <Dialog open={making} onClose={() => setMaking(false)} maxWidth="sm" fullWidth>
        <DialogTitle>New API token</DialogTitle>
        <DialogContent>
          <Stack spacing={2} sx={{ mt: 1 }}>
            <TextField label="Name" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} placeholder="e.g. ci-deploy" />
            {admin && (
              <TextField select label="Kind" value={form.kind} onChange={(e) => setForm({ ...form, kind: e.target.value, roles: [] })}
                helperText={form.kind === "service" ? "Not tied to your groups: acts as svc:<name> with exactly these roles" : "Acts as you"}>
                <MenuItem value="personal">Personal</MenuItem><MenuItem value="service">Service</MenuItem>
              </TextField>
            )}
            <Box>
              <Typography variant="body2" gutterBottom>
                Roles{form.kind === "personal" ? " (none chosen: all of yours)" : ""}
              </Typography>
              {choosable.map((r) => (
                <FormControlLabel key={r} label={r} control={<Checkbox checked={form.roles.includes(r)}
                  onChange={(e) => setForm({ ...form, roles: e.target.checked ? [...form.roles, r] : form.roles.filter((x) => x !== r) })} />} />
              ))}
            </Box>
            <TextField select label="Expires after" value={form.days} onChange={(e) => setForm({ ...form, days: Number(e.target.value) })}>
              {[7, 30, 90, 180, 365].map((d) => <MenuItem key={d} value={d}>{d} days</MenuItem>)}
            </TextField>
            {formError && <Alert severity="error">{formError}</Alert>}
          </Stack>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setMaking(false)}>Cancel</Button>
          <Button variant="contained" disabled={!form.name.trim()} onClick={create}>Create</Button>
        </DialogActions>
      </Dialog>

      <Dialog open={!!made} onClose={() => setMade(null)} maxWidth="md" fullWidth>
        <DialogTitle>Token {made?.name}</DialogTitle>
        <DialogContent>
          <Stack spacing={2}>
            <Alert severity="warning">Copy it now: it isn't shown again. Keep it like a password.</Alert>
            <TextField value={made?.secret ?? ""} slotProps={{ input: { readOnly: true } }} onFocus={(e) => e.target.select()} />
          </Stack>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => made && navigator.clipboard?.writeText(made.secret)}>Copy</Button>
          <Button variant="contained" onClick={() => setMade(null)}>Done</Button>
        </DialogActions>
      </Dialog>
    </Stack>
  );
}
