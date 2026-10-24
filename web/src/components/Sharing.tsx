// Who can see an environment, and who owns it (question 201). An owner or an admin shares it with a user or a
// group (they see it and its details; their roles still limit what they can do) and can hand ownership to
// another account, for instance when the owner leaves.
import { useEffect, useState } from "react";
import {
  Alert, Autocomplete, Box, Button, Checkbox, Chip, Dialog, DialogActions, DialogContent, DialogTitle, FormControlLabel,
  MenuItem, Paper, Stack, TextField, Typography,
} from "@mui/material";
import { api } from "../api";
import { ActionButton } from "./Confirm";

interface Grant { id: number; principal_type: "user" | "group"; principal: string; level: "owner" | "user" }
interface Candidate { username: string; display_name: string }

export default function Sharing({ envId, isOwner }: { envId: string; isOwner: boolean }) {
  const [grants, setGrants] = useState<Grant[] | null>(null);
  const [error, setError] = useState("");
  const [kind, setKind] = useState<"user" | "group">("user");
  const [name, setName] = useState("");
  const [options, setOptions] = useState<Candidate[]>([]);
  const [handing, setHanding] = useState(false);
  const [newOwner, setNewOwner] = useState("");
  const [keep, setKeep] = useState(true);
  const base = `/api/environments/${envId}`;
  const load = () => api.get<{ grants: Grant[] }>(`${base}/sharing`).then((d) => setGrants(d.grants), (e) => setError(e.message));
  useEffect(() => { if (isOwner) load(); }, [envId, isOwner]); // eslint-disable-line react-hooks/exhaustive-deps
  const search = (q: string) => api.get<{ users: Candidate[] }>(`${base}/sharing/candidates?q=${encodeURIComponent(q)}`)
    .then((d) => setOptions(d.users), () => setOptions([]));
  if (!isOwner || !grants) return null;

  const add = () => api.post(`${base}/sharing`, { principal_type: kind, principal: name.trim() })
    .then(() => { setName(""); setError(""); load(); }, (e) => setError(e.message));
  const hand = () => api.post(`${base}/transfer`, { username: newOwner.trim(), keep_access: keep })
    .then(() => { setHanding(false); setNewOwner(""); setError(""); load(); }, (e) => setError(e.message));

  return (
    <Paper variant="outlined" sx={{ p: 2 }}>
      <Stack spacing={1}>
        <Stack direction="row" spacing={1} sx={{ alignItems: "center" }}>
          <Typography variant="h6" sx={{ flexGrow: 1 }}>Sharing</Typography>
          <Button size="small" onClick={() => { setError(""); setHanding(true); }}
            title="Make another account the owner (for example when the owner has left)">Hand over</Button>
        </Stack>
        <Typography variant="body2" color="text.secondary">
          Owners and admins manage the environment. People and groups you share it with can see it; what they can do in it
          is still limited by their roles.
        </Typography>
        {error && <Alert severity="error" onClose={() => setError("")}>{error}</Alert>}
        <Box>
          {grants.map((g) => (
            <Chip key={g.id} sx={{ mr: 1, mb: 1 }} color={g.level === "owner" ? "primary" : "default"}
              label={`${g.principal}${g.principal_type === "group" ? " (group)" : ""}${g.level === "owner" ? " · owner" : ""}`}
              onDelete={g.level === "owner" ? undefined : () => api.del(`${base}/sharing/${g.id}`).then(load, (e) => setError(e.message))}
              title={g.level === "owner" ? "Owners change through Hand over" : "Stop sharing with this " + g.principal_type} />
          ))}
        </Box>
        <Stack direction="row" spacing={1} sx={{ alignItems: "flex-start" }}>
          <TextField select size="small" label="Share with" value={kind} onChange={(e) => { setKind(e.target.value as "user" | "group"); setName(""); }}
            sx={{ minWidth: 120 }}>
            <MenuItem value="user">A user</MenuItem>
            <MenuItem value="group">A group</MenuItem>
          </TextField>
          {kind === "user"
            ? <Autocomplete freeSolo size="small" sx={{ minWidth: 240 }} options={options.map((o) => o.username)}
                inputValue={name} onInputChange={(_, v) => { setName(v); search(v); }}
                renderInput={(params) => <TextField {...params} label="Username" helperText="Someone who has signed in to LABaPe" />} />
            : <TextField size="small" label="Group name" value={name} onChange={(e) => setName(e.target.value)}
                helperText="As it is named in the sign-in provider" />}
          <Button variant="contained" disabled={!name.trim()} onClick={add} title="Let them see this environment">Share</Button>
        </Stack>
      </Stack>
      <Dialog open={handing} onClose={() => setHanding(false)} maxWidth="xs" fullWidth>
        <DialogTitle>Hand this environment over</DialogTitle>
        <DialogContent>
          <Stack spacing={2} sx={{ pt: 1 }}>
            {error && <Alert severity="error">{error}</Alert>}
            <Autocomplete freeSolo size="small" options={options.map((o) => o.username)} inputValue={newOwner}
              onInputChange={(_, v) => { setNewOwner(v); search(v); }}
              renderInput={(params) => <TextField {...params} label="New owner" helperText="Someone who has signed in to LABaPe" />} />
            <FormControlLabel label="The current owners keep access to it" control={<Checkbox checked={keep} onChange={(e) => setKeep(e.target.checked)} />} />
          </Stack>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setHanding(false)}>Cancel</Button>
          <ActionButton variant="contained" disabled={!newOwner.trim()} tip="Make this account the owner"
            confirm={{ title: `Hand over to ${newOwner.trim()}?`, confirmLabel: "Hand over",
              body: "They become the owner and can see its credentials, redeploy and destroy it." }}
            onClick={hand}>Hand over</ActionButton>
        </DialogActions>
      </Dialog>
    </Paper>
  );
}
