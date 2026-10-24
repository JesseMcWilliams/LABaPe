// Users (question 202): who has signed in and when, what they hold, and a switch that turns an account off in
// LABaPe (no sessions, no personal tokens) without touching the sign-in provider.
import { useCallback, useEffect, useState } from "react";
import {
  Alert, Chip, FormControlLabel, Stack, Switch, Table, TableBody, TableCell, TableHead, TableRow, TextField,
  Typography,
} from "@mui/material";
import { api, fmtTime } from "../api";
import { ActionButton } from "../components/Confirm";

interface UserRow {
  id: number; username: string; display_name: string; email: string; provider: string; groups: string[]; roles: string[];
  last_login: string | null; disabled: boolean; disabled_at: string | null; disabled_reason: string;
  active_tokens: number; open_sessions: number;
}
interface UserList { total: number; users: UserRow[] }

const PAGE = 50;

export default function Users() {
  const [q, setQ] = useState("");
  const [onlyDisabled, setOnlyDisabled] = useState(false);
  const [offset, setOffset] = useState(0);
  const [data, setData] = useState<UserList | null>(null);
  const [error, setError] = useState("");
  const load = useCallback(() => {
    const params = new URLSearchParams({ q, disabled: String(onlyDisabled), limit: String(PAGE), offset: String(offset) });
    api.get<UserList>(`/api/admin/users?${params}`).then((d) => { setData(d); setError(""); }, (e) => setError(e.message));
  }, [q, onlyDisabled, offset]);
  useEffect(() => { load(); }, [load]);

  const act = (u: UserRow, what: "disable" | "enable") =>
    api.post(`/api/admin/users/${u.id}/${what}`, {}).then(load, (e) => setError(e.message));

  return (
    <Stack spacing={2}>
      <Typography variant="h5">Users</Typography>
      <Typography variant="body2" color="text.secondary">
        Everyone who has signed in. <b>Disable</b> turns an account off in LABaPe only: its sessions stop at the next
        request, its personal API tokens are revoked, and it can't sign in here until you enable it again. The sign-in
        provider is not changed. An account that is deactivated or removed in the provider is disabled here by itself
        within a quarter of an hour.
      </Typography>
      {error && <Alert severity="error" onClose={() => setError("")}>{error}</Alert>}
      <Stack direction="row" spacing={2} sx={{ alignItems: "center" }}>
        <TextField size="small" label="Search" value={q} onChange={(e) => { setOffset(0); setQ(e.target.value); }}
          helperText="Name, display name or email" />
        <FormControlLabel label="Only disabled" control={<Switch checked={onlyDisabled}
          onChange={(e) => { setOffset(0); setOnlyDisabled(e.target.checked); }} />} />
      </Stack>
      <Table size="small">
        <TableHead>
          <TableRow>
            <TableCell>User</TableCell><TableCell>Roles</TableCell><TableCell>Last sign-in</TableCell>
            <TableCell>Tokens</TableCell><TableCell>Open sessions</TableCell><TableCell>State</TableCell><TableCell />
          </TableRow>
        </TableHead>
        <TableBody>
          {data?.users.map((u) => (
            <TableRow key={u.id}>
              <TableCell>
                {u.username}
                <Typography variant="caption" color="text.secondary" sx={{ display: "block" }}>
                  {[u.display_name !== u.username ? u.display_name : "", u.email].filter(Boolean).join(" · ")}
                </Typography>
              </TableCell>
              <TableCell>{u.roles.join(", ") || "none"}</TableCell>
              <TableCell>{u.last_login ? fmtTime(u.last_login) : "never"}</TableCell>
              <TableCell>{u.active_tokens}</TableCell>
              <TableCell>{u.open_sessions}</TableCell>
              <TableCell>
                {u.disabled
                  ? <Chip size="small" color="warning" label={u.disabled_reason === "provider" ? "disabled (not active in the provider)" : "disabled"}
                      title={u.disabled_at ? `since ${fmtTime(u.disabled_at)}` : undefined} />
                  : <Chip size="small" label="active" />}
              </TableCell>
              <TableCell align="right">
                {u.disabled
                  ? <ActionButton size="small" tip="Let this account sign in and use LABaPe again" onClick={() => act(u, "enable")}>Enable</ActionButton>
                  : <ActionButton size="small" color="warning" tip="Stop its sessions and revoke its personal tokens"
                      confirm={{ title: `Disable ${u.username}?`, confirmLabel: "Disable",
                        body: `${u.username} is signed out at the next request and ${u.active_tokens} personal token(s) are revoked. Their environments stay as they are. Enable them again at any time.` }}
                      onClick={() => act(u, "disable")}>Disable</ActionButton>}
              </TableCell>
            </TableRow>
          ))}
          {data && data.users.length === 0 && <TableRow><TableCell colSpan={7}>No users.</TableCell></TableRow>}
        </TableBody>
      </Table>
      {data && data.total > PAGE && (
        <Stack direction="row" spacing={1} sx={{ alignItems: "center" }}>
          <ActionButton size="small" tip="Previous page" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE))}>Previous</ActionButton>
          <Typography variant="body2">{offset + 1}–{Math.min(offset + PAGE, data.total)} of {data.total}</Typography>
          <ActionButton size="small" tip="Next page" disabled={offset + PAGE >= data.total} onClick={() => setOffset(offset + PAGE)}>Next</ActionButton>
        </Stack>
      )}
    </Stack>
  );
}
