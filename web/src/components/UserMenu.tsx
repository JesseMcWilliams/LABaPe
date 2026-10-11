import { useState } from "react";
import {
  Box, Button, Chip, Divider, Popover, Stack, Typography,
} from "@mui/material";
import AccountCircleIcon from "@mui/icons-material/AccountCircle";
import RefreshIcon from "@mui/icons-material/Refresh";
import { fmtTime, Me } from "../api";

// The signed-in user's details, with Reload rights: a silent re-sign-in
// (prompt=none) that re-reads the user's groups, and so roles, from the
// identity provider without signing out (question 54).
export default function UserMenu({ me, onLogout }: { me: Me; onLogout: () => void }) {
  const [anchor, setAnchor] = useState<HTMLElement | null>(null);
  const canReload = !me.breakglass && me.provider === "oidc";

  const row = (label: string, value: React.ReactNode) => (
    <Stack direction="row" spacing={1}>
      <Typography variant="body2" color="text.secondary" sx={{ minWidth: 120 }}>{label}</Typography>
      <Typography variant="body2" component="div">{value}</Typography>
    </Stack>
  );

  return (
    <>
      <Button color="inherit" startIcon={<AccountCircleIcon />} onClick={(e) => setAnchor(e.currentTarget)}
        aria-haspopup="dialog">
        {me.display_name}
      </Button>
      <Popover open={Boolean(anchor)} anchorEl={anchor} onClose={() => setAnchor(null)}
        anchorOrigin={{ vertical: "bottom", horizontal: "right" }} transformOrigin={{ vertical: "top", horizontal: "right" }}>
        <Box sx={{ p: 2, width: 380 }}>
          <Typography variant="subtitle1">{me.display_name}</Typography>
          <Typography variant="body2" color="text.secondary" gutterBottom>
            {me.username}{me.email ? ` · ${me.email}` : ""}
          </Typography>
          <Divider sx={{ my: 1 }} />
          <Stack spacing={0.75}>
            {row("Roles", me.roles.length
              ? me.roles.map((r) => <Chip key={r} size="small" label={r} sx={{ mr: 0.5, mb: 0.5 }} />)
              : "none (ask an admin for access)")}
            {row("Groups", me.groups.length ? me.groups.join(", ") : "none")}
            {row("Signed in with", me.breakglass ? "Break-glass (temporary admin)" : me.provider === "oidc" ? "Authentik (OIDC)" : me.provider)}
            {row("This sign-in", fmtTime(me.last_login))}
            {row("Previous sign-in", fmtTime(me.previous_login) || "first sign-in")}
            {row("Session ends", fmtTime(me.session_expires))}
            {row("Account since", fmtTime(me.member_since))}
          </Stack>
          <Divider sx={{ my: 1.5 }} />
          <Stack direction="row" spacing={1} sx={{ justifyContent: "space-between" }}>
            <Button variant="outlined" startIcon={<RefreshIcon />} disabled={!canReload}
              href="/api/auth/oidc/login?silent=true"
              title="Re-read your groups from Authentik, so new or removed permissions apply now">
              Reload rights
            </Button>
            <Button color="error" onClick={onLogout}>Sign out</Button>
          </Stack>
          {!canReload && (
            <Typography variant="caption" color="text.secondary">
              Reload rights works for Authentik sign-ins; break-glass sessions have fixed admin rights.
            </Typography>
          )}
        </Box>
      </Popover>
    </>
  );
}
