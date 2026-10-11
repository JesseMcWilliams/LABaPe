import { useEffect, useState } from "react";
import {
  Alert, Button, IconButton, MenuItem, Paper, Radio, Stack, Table, TableBody, TableCell, TableHead, TableRow,
  TextField, Typography,
} from "@mui/material";
import DeleteIcon from "@mui/icons-material/Delete";
import { api, HostNetwork, KvmHost, Network } from "../api";

// Which catalog networks a KVM host carries, on which bridge (design §20.2).
export default function HostNetworks({ host, onClose }: { host: KvmHost; onClose: () => void }) {
  const [rows, setRows] = useState<HostNetwork[]>([]);
  const [catalog, setCatalog] = useState<Network[]>([]);
  const [error, setError] = useState("");
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    api.get<HostNetwork[]>(`/api/hosts/${host.id}/networks`).then(setRows, (e) => setError(e.message));
    api.get<Network[]>("/api/networks").then(setCatalog);
  }, [host.id]);

  const set = (i: number, patch: Partial<HostNetwork>) => {
    setSaved(false);
    setRows(rows.map((r, j) => (j === i ? { ...r, ...patch } : patch.is_default ? { ...r, is_default: false } : r)));
  };

  const save = async () => {
    setError("");
    try {
      setRows(await api.put<HostNetwork[]>(`/api/hosts/${host.id}/networks`, rows));
      setSaved(true);
    } catch (e) {
      setError((e as Error).message);
    }
  };

  const unused = catalog.filter((n) => !rows.some((r) => r.network === n.name));

  return (
    <Paper variant="outlined" sx={{ p: 2 }}>
      <Stack spacing={2}>
        <Typography variant="h6">Networks on {host.name}</Typography>
        {error && <Alert severity="error">{error}</Alert>}
        {saved && <Alert severity="success">Saved.</Alert>}
        <Table size="small">
          <TableHead>
            <TableRow>
              <TableCell>Default</TableCell><TableCell>Network</TableCell><TableCell>Bridge on this host</TableCell>
              <TableCell>Static addresses for this host (optional slice)</TableCell><TableCell />
            </TableRow>
          </TableHead>
          <TableBody>
            {rows.map((r, i) => (
              <TableRow key={r.network}>
                <TableCell>
                  <Radio checked={r.is_default} onChange={() => set(i, { is_default: true })} />
                </TableCell>
                <TableCell>{r.network}</TableCell>
                <TableCell>
                  <TextField size="small" value={r.bridge} onChange={(e) => set(i, { bridge: e.target.value })} />
                </TableCell>
                <TableCell>
                  <TextField size="small" fullWidth value={r.static_pool.join(", ")}
                    placeholder="Empty: the network's whole static pool"
                    onChange={(e) => set(i, { static_pool: e.target.value.split(/[\s,]+/).filter(Boolean) })} />
                </TableCell>
                <TableCell>
                  <IconButton aria-label="Detach" onClick={() => { setSaved(false); setRows(rows.filter((_, j) => j !== i)); }}>
                    <DeleteIcon />
                  </IconButton>
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
        <Stack direction="row" spacing={2} sx={{ alignItems: "center" }}>
          <TextField select size="small" label="Attach network" value="" sx={{ minWidth: 220 }}
            disabled={unused.length === 0}
            onChange={(e) => {
              setSaved(false);
              setRows([...rows, { network: e.target.value, bridge: "", static_pool: [], is_default: rows.length === 0 }]);
            }}>
            {unused.map((n) => <MenuItem key={n.id} value={n.name}>{n.name} ({n.cidr})</MenuItem>)}
          </TextField>
          <Button variant="contained" onClick={save}>Save</Button>
          <Button onClick={onClose}>Close</Button>
        </Stack>
      </Stack>
    </Paper>
  );
}
