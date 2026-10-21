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
  state: string; environments: number[] | null; host_consoles: boolean; require_account: boolean;
  rate_per_minute: number; max_session_minutes: number; max_output_kb: number;
}
interface EnvName { id: number; name: string; status?: string }
interface TokenList { roles: string[]; my_roles: string[]; tokens: Token[] }

const STATE_COLOR: Record<string, "success" | "default" | "error"> = { active: "success", expired: "default", revoked: "error" };

export default function Tokens() {
  const { me } = useAuth();
  const admin = !!me?.roles.includes("admin");
  const [all, setAll] = useState(false);
  const [inactive, setInactive] = useState(false);       // revoked and expired tokens are hidden unless asked for
  const [data, setData] = useState<TokenList | null>(null);
  const [error, setError] = useState("");
  const [making, setMaking] = useState(false);
  const NEW_FORM = {
    name: "", kind: "personal", roles: [] as string[], days: 90, environments: null as number[] | null,
    host_consoles: false, require_account: true, rate_per_minute: 30, max_session_minutes: 30, max_output_kb: 256,
  };
  const [form, setForm] = useState(NEW_FORM);
  const [envs, setEnvs] = useState<EnvName[]>([]);
  const [made, setMade] = useState<(Token & { secret: string }) | null>(null);
  const [formError, setFormError] = useState("");

  const load = () => api.get<TokenList>(`/api/tokens?all=${all}&inactive=${inactive}`).then(setData, (e) => setError(e.message));
  useEffect(() => { load(); }, [all, inactive]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { api.get<EnvName[]>("/api/environments").then(setEnvs, () => setEnvs([])); }, []);

  const create = () => api.post<Token & { secret: string }>("/api/tokens", form).then((t) => {
    setMaking(false);
    setMade(t);
    setForm(NEW_FORM);
    load();
  }, (e) => setFormError(e.message));

  if (error) return <Alert severity="error">{error}</Alert>;
  if (!data) return null;
  const choosable = form.kind === "service" ? data.roles : data.my_roles;
  const console_ = form.roles.includes("console");
  const envName = (id: number) => envs.find((e) => e.id === id)?.name ?? `#${id}`;
  return (
    <Stack spacing={2}>
      <Stack direction="row" spacing={1} sx={{ alignItems: "center" }}>
        <Typography variant="h5">API tokens</Typography>
        <Box sx={{ flexGrow: 1 }} />
        <FormControlLabel label="Show revoked and expired" title="They are removed after the retention period in Settings → History"
          control={<Checkbox checked={inactive} onChange={(e) => setInactive(e.target.checked)} />} />
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
              <TableCell>
                {t.roles.length ? t.roles.join(", ") : "all of the owner's"}
                {t.roles.includes("console") && (
                  <Typography variant="caption" color="text.secondary" component="div">
                    console: {t.environments === null ? "every environment the owner can use" : t.environments.map(envName).join(", ") || "no environment"}
                    {t.host_consoles ? "; KVM hosts" : ""}; {t.require_account ? "needs an account" : "any account"};
                    {" "}{t.rate_per_minute}/min, {t.max_session_minutes} min sessions, {t.max_output_kb} KB output
                  </Typography>
                )}
              </TableCell>
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
            {console_ && (
              <Stack spacing={1} sx={{ border: 1, borderColor: "divider", borderRadius: 1, p: 1.5 }}>
                <Typography variant="body2">Console access (commands and text sessions on VMs)</Typography>
                <FormControlLabel label="Only chosen environments" control={<Checkbox checked={form.environments !== null}
                  onChange={(e) => setForm({ ...form, environments: e.target.checked ? [] : null })} />} />
                {form.environments !== null && (
                  <TextField select label="Environments" value={form.environments}
                    slotProps={{ select: { multiple: true } }}
                    onChange={(e) => setForm({ ...form, environments: (e.target.value as unknown as number[]) })}
                    helperText="The token reaches only these. With none chosen it reaches no environment.">
                    {envs.filter((e) => e.status !== "destroyed").map((e) => <MenuItem key={e.id} value={e.id}>{e.name}</MenuItem>)}
                  </TextField>
                )}
                {form.environments === null && (
                  <Typography variant="caption" color="text.secondary">Every environment the owner can use (admins: all; others: the ones they own).</Typography>
                )}
                <FormControlLabel label="The call must name an account to run as" control={<Checkbox checked={form.require_account}
                  onChange={(e) => setForm({ ...form, require_account: e.target.checked })} />} />
                {admin && <FormControlLabel label="May open KVM host consoles (needs the admin role too)" control={<Checkbox checked={form.host_consoles}
                  onChange={(e) => setForm({ ...form, host_consoles: e.target.checked })} />} />}
                <Stack direction="row" spacing={1}>
                  <TextField label="Calls per minute" type="number" size="small" value={form.rate_per_minute}
                    onChange={(e) => setForm({ ...form, rate_per_minute: Number(e.target.value) })} />
                  <TextField label="Session minutes" type="number" size="small" value={form.max_session_minutes}
                    onChange={(e) => setForm({ ...form, max_session_minutes: Number(e.target.value) })} />
                  <TextField label="Output KB" type="number" size="small" value={form.max_output_kb}
                    onChange={(e) => setForm({ ...form, max_output_kb: Number(e.target.value) })} />
                </Stack>
              </Stack>
            )}
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
