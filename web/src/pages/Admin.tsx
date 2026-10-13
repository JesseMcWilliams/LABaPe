// The Admin area (design §28, O1; question 107): every admin page in one place,
// listed on the left. Admins only; the API enforces the same.
import { Link as RouterLink, Navigate, Outlet, useLocation } from "react-router-dom";
import { Alert, Box, List, ListItemButton, ListItemIcon, ListItemText, Paper, Typography } from "@mui/material";
import DnsIcon from "@mui/icons-material/Dns";
import LanIcon from "@mui/icons-material/Lan";
import SettingsIcon from "@mui/icons-material/Settings";
import KeyIcon from "@mui/icons-material/Key";
import FactCheckIcon from "@mui/icons-material/FactCheck";
import ReportProblemIcon from "@mui/icons-material/ReportProblem";
import { hasRole, useAuth } from "../App";

export const ADMIN_PAGES = [
  { path: "hosts", label: "KVM hosts", icon: <DnsIcon />, help: "Register KVM hosts; their load, storage and networks" },
  { path: "networks", label: "Networks", icon: <LanIcon />, help: "The networks VMs may use, their pools and who may deploy on them" },
  { path: "settings", label: "Settings", icon: <SettingsIcon />, help: "Branding, files, backups, history, syslog, email, directory, stores" },
  { path: "secrets", label: "Secrets", icon: <KeyIcon />, help: "Internal secrets, stored secrets and their rotation" },
  { path: "audit", label: "Audit log", icon: <FactCheckIcon />, help: "Who did what, when" },
  { path: "errors", label: "User errors", icon: <ReportProblemIcon />, help: "Requests the app refused and forms the browser blocked" },
] as const;

export default function AdminLayout() {
  const { me } = useAuth();
  const location = useLocation();
  if (!hasRole(me, "admin")) {
    return <Alert severity="warning">The Admin pages are for admins.</Alert>;
  }
  const current = location.pathname.split("/")[2] ?? "";
  if (!current) return <Navigate to="/admin/hosts" replace />;
  return (
    <Box sx={{ display: "flex", gap: 3, alignItems: "flex-start" }}>
      <Paper variant="outlined" sx={{ width: 220, flexShrink: 0, position: "sticky", top: 16 }}>
        <Typography variant="overline" sx={{ px: 2, pt: 1, display: "block", color: "text.secondary" }}>Admin</Typography>
        <List dense disablePadding>
          {ADMIN_PAGES.map((p) => (
            <ListItemButton key={p.path} component={RouterLink} to={`/admin/${p.path}`} selected={current === p.path}
              title={p.help}>
              <ListItemIcon sx={{ minWidth: 36 }}>{p.icon}</ListItemIcon>
              <ListItemText primary={p.label} />
            </ListItemButton>
          ))}
        </List>
      </Paper>
      <Box sx={{ flexGrow: 1, minWidth: 0 }}>
        <Outlet />
      </Box>
    </Box>
  );
}

/** Old URLs (/hosts/3, /settings?tab=x) keep working: they go to the same page under /admin. */
export function ToAdmin() {
  const location = useLocation();
  return <Navigate to={`/admin${location.pathname}${location.search}`} replace />;
}
