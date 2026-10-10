import { useEffect, useState } from "react";
import { Alert, Button, Paper, Stack, Typography } from "@mui/material";
import { api, Provider } from "../api";

export default function Login() {
  const [providers, setProviders] = useState<Provider[] | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    api.get<Provider[]>("/api/auth/providers").then(setProviders, (e) => setError(String(e.message)));
  }, []);

  return (
    <Paper sx={{ maxWidth: 420, mx: "auto", mt: 8, p: 4 }}>
      <Stack spacing={2}>
        <Typography variant="h5">Sign in to LABaPe</Typography>
        {error && <Alert severity="error">{error}</Alert>}
        {providers?.length === 0 && (
          <Alert severity="info">
            No sign-in provider is configured. An administrator can run <code>labape breakglass enable</code> in
            the container for one-time access.
          </Alert>
        )}
        {providers
          ?.filter((p) => p.kind === "redirect")
          .map((p) => (
            <Button key={p.name} variant="contained" size="large" href={`/api/auth/${p.name}/login`}>
              Sign in with {p.display_name}
            </Button>
          ))}
      </Stack>
    </Paper>
  );
}
