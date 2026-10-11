import { useEffect, useState } from "react";
import {
  Alert, Box, Button, Chip, Stack, Table, TableBody, TableCell, TableHead, TableRow, Typography,
} from "@mui/material";
import ErrorOutlineIcon from "@mui/icons-material/ErrorOutline";
import { api, FieldProblem, fmtTime } from "../api";
import { Field } from "../forms";
import { ActionButton } from "../components/Confirm";

interface UserError {
  id: number;
  at: string;
  username: string;
  method: string;
  path: string;
  status: number;
  message: string;
  fields: FieldProblem[];
  origin: "server" | "browser";
}

// Requests the app refused, and submits the browser blocked, so admins can
// see where users struggle (question 59). Admin only.
export default function Errors() {
  const [rows, setRows] = useState<UserError[]>([]);
  const [retention, setRetention] = useState(90);
  const [username, setUsername] = useState("");
  const [error, setError] = useState("");
  const [saved, setSaved] = useState("");

  const load = () => {
    const q = new URLSearchParams({ limit: "500" });
    if (username.trim()) q.set("username", username.trim());
    api.get<{ retention_days: number; errors: UserError[] }>(`/api/admin/errors?${q}`).then((r) => {
      setRows(r.errors);
      setRetention(r.retention_days);
    }, (e) => setError(e.message));
  };
  useEffect(() => {
    load();
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const saveRetention = async () => {
    setError("");
    try {
      await api.put("/api/admin/errors/retention", { days: retention });
      setSaved(`Errors are kept ${retention} days.`);
    } catch (e) {
      setError((e as Error).message);
    }
  };

  return (
    <Stack spacing={2}>
      <Typography variant="h5">User errors</Typography>
      <Typography variant="body2" color="text.secondary">
        Requests the app refused (validation, policy, permission) and submits the browser blocked, newest first.
      </Typography>
      {error && <Alert severity="error">{error}</Alert>}
      {saved && <Alert severity="success" onClose={() => setSaved("")}>{saved}</Alert>}
      <Stack direction="row" spacing={2} useFlexGap sx={{ alignItems: "flex-start", flexWrap: "wrap" }}>
        <Field label="User" value={username} onChange={(e) => setUsername(e.target.value)} help="Exact user name" />
        <Button variant="outlined" onClick={load} sx={{ mt: 0.25 }} title="Show only this user's errors">Filter</Button>
        <Box sx={{ flexGrow: 1 }} />
        <Field label="Keep (days)" type="number" value={retention} sx={{ width: 140 }}
          onChange={(e) => setRetention(Number(e.target.value))} help="1-3650" />
        <ActionButton onClick={saveRetention} sx={{ mt: 0.25 }} tip="Keep user errors this many days"
          confirm={{ title: "Change how long errors are kept?", confirmLabel: "Save",
            body: `User errors older than ${retention} days are deleted at the next daily purge. The same value is under Settings > History.` }}>
          Save
        </ActionButton>
      </Stack>
      <Table size="small">
        <TableHead>
          <TableRow>
            <TableCell>When</TableCell><TableCell>User</TableCell><TableCell>Where</TableCell>
            <TableCell>Problem</TableCell>
          </TableRow>
        </TableHead>
        <TableBody>
          {rows.map((r) => (
            <TableRow key={r.id}>
              <TableCell sx={{ whiteSpace: "nowrap" }}>{fmtTime(r.at)}</TableCell>
              <TableCell>{r.username || "(signed out)"}</TableCell>
              <TableCell sx={{ whiteSpace: "nowrap" }}>
                <Chip size="small" label={r.origin === "browser" ? "browser" : `${r.method} ${r.status}`}
                  color={r.origin === "browser" ? "default" : "warning"} sx={{ mr: 1 }} />
                {r.path}
              </TableCell>
              <TableCell>
                <Stack direction="row" spacing={0.5} sx={{ alignItems: "flex-start" }}>
                  <ErrorOutlineIcon color="error" sx={{ fontSize: 18, mt: "2px" }} />
                  <span>{r.message}</span>
                </Stack>
                {r.fields.length > 0 && (
                  <Box component="ul" sx={{ m: 0, pl: 3 }}>
                    {r.fields.map((f, k) => <li key={k}><b>{f.label || f.field}</b>: {f.message}</li>)}
                  </Box>
                )}
              </TableCell>
            </TableRow>
          ))}
          {rows.length === 0 && (
            <TableRow><TableCell colSpan={4}>No errors recorded.</TableCell></TableRow>
          )}
        </TableBody>
      </Table>
    </Stack>
  );
}
