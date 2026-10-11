import { FormEvent, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Alert, Button, Paper, Stack, TextField, Typography } from "@mui/material";
import { api } from "../api";
import { useAuth } from "../App";

// Not linked from anywhere: `labape breakglass enable` prints this page's URL.
export default function Breakglass() {
  const [token, setToken] = useState("");
  const [error, setError] = useState("");
  const { refresh } = useAuth();
  const navigate = useNavigate();

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setError("");
    try {
      await api.post("/api/auth/breakglass", { token });
      await refresh();
      navigate("/");
    } catch (err) {
      setError((err as Error).message);
    }
  };

  return (
    <Paper sx={{ maxWidth: 480, mx: "auto", mt: 8, p: 4 }} component="form" onSubmit={submit}>
      <Stack spacing={2}>
        <Typography variant="h5">Break-glass sign-in</Typography>
        <Typography variant="body2">
          Paste the one-time credential printed by <code>labape breakglass enable</code>. It works once and
          expires after a few minutes.
        </Typography>
        {error && <Alert severity="error">{error}</Alert>}
        <TextField label="Credential" value={token} onChange={(e) => setToken(e.target.value)} autoFocus
          autoComplete="off" />
        <Button type="submit" variant="contained" disabled={!token.trim()}>
          Sign in
        </Button>
      </Stack>
    </Paper>
  );
}
