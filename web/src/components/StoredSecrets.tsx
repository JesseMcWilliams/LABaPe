import { useEffect, useState } from "react";
import { Link as RouterLink } from "react-router-dom";
import {
  Alert, Checkbox, Chip, Link, MenuItem, Stack, Table, TableBody, TableCell, TableHead, TableRow, Tooltip, Typography,
} from "@mui/material";
import { api, fmtTime } from "../api";
import { Field, fromApiError } from "../forms";
import { ActionButton } from "./Confirm";

// The secret catalog (design §25): every stored secret, the store it lives
// in and the rotation linked to it, with Move between stores. Never a value.
interface StoredSecret {
  name: string; store: string; updated_at: string; pinned: boolean; empty: boolean;
  rotation: { policy_id: number; label: string; kind: string; enabled: boolean; interval_hours: number;
    elevated_ref: string; status: string; next_due_at: string | null } | null;
}
interface Catalog { secrets: StoredSecret[]; stores: { name: string; display_name: string }[] }
interface MoveResult { name: string; ok: boolean; message: string }

const every = (h: number) => (h % 24 === 0 ? `${h / 24} d` : `${h} h`);

export default function StoredSecrets({ onMoved }: { onMoved?: () => void }) {
  const [data, setData] = useState<Catalog | null>(null);
  const [error, setError] = useState("");
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const [target, setTarget] = useState("builtin");
  const [results, setResults] = useState<MoveResult[]>([]);
  const load = () => api.get<Catalog>("/api/admin/stored-secrets").then(setData, (e) => setError(e.message));
  useEffect(() => { load(); }, []);
  if (!data) return error ? <Alert severity="error">{error}</Alert> : null;

  const label = (s: string) => data.stores.find((x) => x.name === s)?.display_name ?? s;
  const toggle = (name: string) => {
    const next = new Set(picked);
    if (next.has(name)) next.delete(name); else next.add(name);
    setPicked(next);
  };
  const movable = data.secrets.filter((s) => !s.pinned || target === "builtin");
  const move = async () => {
    setResults([]);
    try {
      const r = await api.post<{ results: MoveResult[] }>("/api/admin/stored-secrets/move",
        { names: [...picked], store: target });
      setResults(r.results);
      setPicked(new Set());
      await load();
      onMoved?.();
    } catch (e) {
      setResults([{ name: "", ok: false, message: fromApiError(e).summary }]);
    }
  };

  return (
    <Stack spacing={1}>
      <Typography variant="h6">Stored secrets</Typography>
      <Typography variant="body2" color="text.secondary">
        Where each secret lives. Moving one copies it, reads it back, switches it over, then deletes the old copy; if
        any step fails it stays where it was. Store logins, elevated accounts and job tokens always stay in the
        built-in store. New environment secrets go to the default store in Settings → Secret stores.
      </Typography>
      <Stack direction="row" spacing={2} useFlexGap sx={{ alignItems: "center", flexWrap: "wrap" }}>
        <Field select label="Move to" value={target} sx={{ width: 240 }} onChange={(e) => setTarget(e.target.value)}>
          {data.stores.map((s) => <MenuItem key={s.name} value={s.name}>{s.display_name}</MenuItem>)}
        </Field>
        <ActionButton variant="outlined" disabled={picked.size === 0}
          tip="Move the ticked secrets to the chosen store (audited)"
          confirm={{ title: "Move secrets?", confirmLabel: "Move",
            body: `Move ${picked.size} secret(s) to ${label(target)}. Each is read back before the switch, and the ` +
              "old copy is deleted afterwards. Jobs and sign-ins then read them from there, so the store must stay reachable." }}
          onClick={move}>Move {picked.size || ""}</ActionButton>
      </Stack>
      {results.length > 0 && (
        <Alert severity={results.every((r) => r.ok) ? "success" : "warning"} onClose={() => setResults([])}>
          {results.map((r) => <div key={r.name}>{r.message}</div>)}
        </Alert>
      )}
      <Table size="small">
        <TableHead>
          <TableRow>
            <TableCell padding="checkbox" />
            <TableCell>Secret</TableCell><TableCell>Store</TableCell><TableCell>Rotation</TableCell>
            <TableCell>Elevated account</TableCell><TableCell>Changed</TableCell>
          </TableRow>
        </TableHead>
        <TableBody>
          {data.secrets.map((s) => (
            <TableRow key={s.name} hover sx={{ opacity: s.empty ? 0.5 : 1 }}>
              <TableCell padding="checkbox">
                <Checkbox size="small" checked={picked.has(s.name)} disabled={!movable.includes(s) || s.store === target}
                  onChange={() => toggle(s.name)} inputProps={{ "aria-label": `pick ${s.name}` }} />
              </TableCell>
              <TableCell sx={{ fontFamily: "monospace" }}>
                {s.name}
                {s.pinned && <Tooltip title="LABaPe needs it to reach the other stores, or it's a one-time token">
                  <Chip size="small" label="built-in only" sx={{ ml: 1 }} /></Tooltip>}
                {s.empty && <Chip size="small" label="cleared" sx={{ ml: 1 }} />}
              </TableCell>
              <TableCell>{label(s.store)}</TableCell>
              <TableCell>
                {s.rotation
                  ? <Link component={RouterLink} to={`/secrets/${s.rotation.policy_id}`}>
                      {s.rotation.enabled ? `every ${every(s.rotation.interval_hours)}` : "off"} ({s.rotation.status})
                    </Link>
                  : "—"}
              </TableCell>
              <TableCell>{s.rotation?.elevated_ref || "—"}</TableCell>
              <TableCell>{fmtTime(s.updated_at)}</TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </Stack>
  );
}
