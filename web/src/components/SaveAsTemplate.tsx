// "Save as template" on an environment (design §68): keeps its host groups, not its name, host, VM names, addresses or accounts.
import { useState } from "react";
import { Link as RouterLink } from "react-router-dom";
import {
  Alert, Box, Button, Checkbox, Chip, Dialog, DialogActions, DialogContent, DialogTitle, FormControlLabel, Link, Stack, TextField, Typography,
} from "@mui/material";
import { api, Environment } from "../api";

const FIELDS: [string, string][] = [["count", "number of hosts"], ["os", "OS"], ["network", "network"], ["cpu_count", "CPUs"], ["memory_mb", "memory"], ["disk_gb", "disk"]];

export default function SaveAsTemplate({ env, disabled }: { env: Environment; disabled?: boolean }) {
  const [open, setOpen] = useState(false);
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [shared, setShared] = useState(false);
  const [picked, setPicked] = useState<Set<string>>(() => new Set(env.spec.host_groups.map((g) => `${g.name}.count`)));
  const [error, setError] = useState("");
  const [done, setDone] = useState(false);
  const toggle = (k: string) => setPicked((p) => { const n = new Set(p); if (n.has(k)) n.delete(k); else n.add(k); return n; });
  const save = async () => {
    setError("");
    try {
      await api.post("/api/env-templates", {
        name, description, shared, from_environment_id: env.id,
        params: [...picked].map((k) => ({ group: k.split(".")[0], field: k.split(".")[1] })),
      });
      setDone(true);
    } catch (e) { setError((e as Error).message); }
  };
  return (
    <>
      <Button variant="outlined" disabled={disabled} onClick={() => { setOpen(true); setDone(false); }}
        title="Keep this environment's host groups as a template to start new environments from (no names, addresses, accounts or passwords)">
        Save as template
      </Button>
      {open && (
        <Dialog open onClose={() => setOpen(false)} maxWidth="sm" fullWidth>
          <DialogTitle>Save {env.name} as a template</DialogTitle>
          <DialogContent>
            <Stack spacing={2} sx={{ pt: 1 }}>
              {done ? (
                <Alert severity="success">Saved. <Link component={RouterLink} to="/environment-templates">Open the templates</Link></Alert>
              ) : (
                <>
                  <TextField size="small" label="Template name" value={name} onChange={(e) => setName(e.target.value)} required
                    helperText="3-60 letters, digits, spaces, dots, dashes" />
                  <TextField size="small" label="Description" value={description} onChange={(e) => setDescription(e.target.value)} multiline minRows={2} />
                  <FormControlLabel label="Share it with everyone who can deploy"
                    control={<Checkbox checked={shared} onChange={(e) => setShared(e.target.checked)} />} />
                  <Box>
                    <Typography variant="subtitle2">What can be changed when starting from it</Typography>
                    {env.spec.host_groups.map((g) => (
                      <Stack key={g.name} direction="row" spacing={1} useFlexGap sx={{ flexWrap: "wrap", alignItems: "center", my: 0.5 }}>
                        <Typography variant="body2" sx={{ minWidth: 80 }}>{g.name}</Typography>
                        {FIELDS.map(([f, label]) => (
                          <Chip key={f} size="small" label={label} clickable color={picked.has(`${g.name}.${f}`) ? "primary" : "default"}
                            variant={picked.has(`${g.name}.${f}`) ? "filled" : "outlined"} onClick={() => toggle(`${g.name}.${f}`)} />
                        ))}
                      </Stack>
                    ))}
                  </Box>
                  {error && <Alert severity="error">{error}</Alert>}
                </>
              )}
            </Stack>
          </DialogContent>
          <DialogActions>
            <Button onClick={() => setOpen(false)}>{done ? "Close" : "Cancel"}</Button>
            {!done && <Button variant="contained" onClick={save} disabled={name.trim().length < 3}>Save</Button>}
          </DialogActions>
        </Dialog>
      )}
    </>
  );
}
