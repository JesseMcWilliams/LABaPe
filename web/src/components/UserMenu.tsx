import { useState } from "react";
import {
  Box, Button, Chip, Divider, Popover, Stack, Typography,
} from "@mui/material";
import AccountCircleIcon from "@mui/icons-material/AccountCircle";
import RefreshIcon from "@mui/icons-material/Refresh";
import { fmtTime, Me } from "../api";
import { ActionButton } from "./Confirm";

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
        aria-haspopup="dialog" title="Your account, roles and sign-in details">
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
            <ActionButton variant="outlined" startIcon={<RefreshIcon />} disabled={!canReload}
              tip="Re-read your groups from Authentik so permission changes apply now"
              confirm={{ title: "Reload your rights?", body: "LABaPe asks Authentik for your current groups without signing you out, then recalculates your roles. You'll come back to the dashboard; anything unsaved on this page is lost.", confirmLabel: "Reload rights" }}
              onClick={() => { window.location.href = "/api/auth/oidc/login?silent=true"; }}>
              Reload rights
            </ActionButton>
            <ActionButton color="error" tip="End this session"
              confirm={{ title: "Sign out?", body: "Your session ends here. Jobs you started keep running.", confirmLabel: "Sign out" }}
              onClick={onLogout}>Sign out</ActionButton>
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
