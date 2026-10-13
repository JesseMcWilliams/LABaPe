import { createContext, useCallback, useContext, useEffect, useState } from "react";
import { Link as RouterLink, Navigate, Route, Routes, useLocation } from "react-router-dom";
import { Alert, AppBar, Box, Button, CircularProgress, Container, Toolbar, Typography } from "@mui/material";
import { api, ApiError, Me } from "./api";
import Login from "./pages/Login";
import Breakglass from "./pages/Breakglass";
import Environments from "./pages/Environments";
import EnvironmentNew from "./pages/EnvironmentNew";
import EnvironmentDetail from "./pages/EnvironmentDetail";
import Jobs from "./pages/Jobs";
import JobDetail from "./pages/JobDetail";
import Hosts from "./pages/Hosts";
import Networks from "./pages/Networks";
import Audit from "./pages/Audit";
import Errors from "./pages/Errors";
import Dashboard from "./pages/Dashboard";
import Settings from "./pages/Settings";
import Files from "./pages/Files";
import Catalog from "./pages/Catalog";
import CatalogItemPage from "./pages/CatalogItem";
import HostDetail from "./pages/HostDetail";
import NetworkDetail from "./pages/NetworkDetail";
import FileDetail from "./pages/FileDetail";
import TemplateDetail from "./pages/TemplateDetail";
import Secrets from "./pages/Secrets";
import SecretDetail from "./pages/SecretDetail";
import AdminLayout, { ToAdmin } from "./pages/Admin";
import RemoteSession from "./pages/RemoteSession";
import Sessions from "./pages/Sessions";
import VmDetail from "./pages/VmDetail";
import Syslog from "./pages/Syslog";
import UserMenu from "./components/UserMenu";
import { MaintenanceBanner, MaintenanceProvider } from "./components/Maintenance";

interface AuthState {
  me: Me | null;
  refresh: () => Promise<void>;
  appName: string;                       // Settings > Branding (design §22.2)
  setAppName: (name: string) => void;
}

const AuthContext = createContext<AuthState>({ me: null, refresh: async () => {}, appName: "LABaPe", setAppName: () => {} });
export const useAuth = () => useContext(AuthContext);

export const hasRole = (me: Me | null, ...roles: string[]) =>
  !!me && (me.roles.includes("admin") || roles.some((r) => me.roles.includes(r)));

export default function App() {
  const [me, setMe] = useState<Me | null>(null);
  const [loading, setLoading] = useState(true);
  const [appName, setAppName] = useState("LABaPe");
  const location = useLocation();
  // After Reload rights the callback lands on /?reload=done (or signin-needed).
  const [reloadNotice, setReloadNotice] = useState(() => new URLSearchParams(window.location.search).get("reload"));

  const refresh = useCallback(async () => {
    try {
      setMe(await api.get<Me>("/api/auth/me"));
    } catch (e) {
      if (!(e instanceof ApiError && e.status === 401)) throw e;
      setMe(null);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    refresh();
    api.get<{ app_name: string }>("/api/branding").then((b) => setAppName(b.app_name), () => undefined);
  }, [refresh]);
  useEffect(() => {
    document.title = appName;
  }, [appName]);

  if (loading) {
    return (
      <Box sx={{ display: "flex", justifyContent: "center", mt: 10 }}>
        <CircularProgress />
      </Box>
    );
  }

  const publicPaths = ["/login", "/breakglass"];
  if (!me && !publicPaths.includes(location.pathname)) {
    return <Navigate to="/login" replace />;
  }

  const logout = async () => {
    await api.post("/api/auth/logout");
    setMe(null);
  };

  return (
    <AuthContext.Provider value={{ me, refresh, appName, setAppName }}>
      <MaintenanceProvider signedIn={Boolean(me)}>
      {me && (
        <AppBar position="static">
          <Toolbar sx={{ gap: 1 }}>
            <Typography variant="h6" component={RouterLink} to="/" title="Dashboard: load, environments and platform health"
              sx={{ mr: 2, color: "inherit", textDecoration: "none" }}>
              {appName}
            </Typography>
            <Button color="inherit" component={RouterLink} to="/environments" title="Your lab environments: create, deploy, destroy">
              Environments
            </Button>
            <Button color="inherit" component={RouterLink} to="/jobs" title="Deploy, destroy, build and download jobs, with live logs">
              Jobs
            </Button>
            <Button color="inherit" component={RouterLink} to="/files" title="ISOs, installers, other files and the template library">
              Files
            </Button>
            <Button color="inherit" component={RouterLink} to="/catalog" title="Services, applications and packages to add to host groups">
              Catalog
            </Button>
            {(hasRole(me, "admin") || hasRole(me, "auditor")) && (
              <Button color="inherit" component={RouterLink} to="/syslog" title="Messages the stack's syslog collector received (admins and auditors)">
                Syslog
              </Button>
            )}
            {(hasRole(me, "admin") || hasRole(me, "auditor")) && (
              <Button color="inherit" component={RouterLink} to="/admin" title="KVM hosts, networks, settings, secrets, audit log, remote sessions and user errors (admins; auditors see the logs)">
                Admin
              </Button>
            )}
            <Box sx={{ flexGrow: 1 }} />
            <UserMenu me={me} onLogout={logout} />
          </Toolbar>
        </AppBar>
      )}
      {me?.breakglass && (
        <Alert severity="warning" square>
          Break-glass session: full admin rights, time-limited, and every action is audited. Fix sign-in and sign
          out as soon as you can.
        </Alert>
      )}
      {me && <MaintenanceBanner />}
      {me && reloadNotice && (
        <Alert square severity={reloadNotice === "done" ? "success" : "info"} onClose={() => setReloadNotice(null)}>
          {reloadNotice === "done"
            ? `Rights reloaded: your roles are now ${me.roles.join(", ") || "none"}.`
            : "Authentik needs you to sign in again before your rights can be reloaded. Sign out and sign back in."}
        </Alert>
      )}
      <Container maxWidth={false} sx={{ py: 3 }}>
        <Routes>
          <Route path="/login" element={me ? <Navigate to="/" replace /> : <Login />} />
          <Route path="/breakglass" element={<Breakglass />} />
          <Route path="/" element={<Dashboard />} />
          <Route path="/environments" element={<Environments />} />
          <Route path="/environments/new" element={<EnvironmentNew />} />
          <Route path="/environments/:id" element={<EnvironmentDetail />} />
          <Route path="/environments/:id/vms/:vm" element={<VmDetail />} />
          <Route path="/jobs" element={<Jobs />} />
          <Route path="/syslog" element={<Syslog />} />
          <Route path="/jobs/:id" element={<JobDetail />} />
          <Route path="/admin" element={<AdminLayout />}>
            <Route path="hosts" element={<Hosts />} />
            <Route path="hosts/:id" element={<HostDetail />} />
            <Route path="networks" element={<Networks />} />
            <Route path="networks/:id" element={<NetworkDetail />} />
            <Route path="settings" element={<Settings />} />
            <Route path="secrets" element={<Secrets />} />
            <Route path="secrets/:id" element={<SecretDetail />} />
            <Route path="audit" element={<Audit />} />
            <Route path="errors" element={<Errors />} />
            <Route path="sessions" element={<Sessions />} />
          </Route>
          <Route path="/remote/:id" element={<RemoteSession />} />
          {["/hosts", "/networks", "/settings", "/secrets", "/audit", "/errors"].map((p) => (
            <Route key={p} path={`${p}/*`} element={<ToAdmin />} />
          ))}
          <Route path="/files" element={<Files />} />
          <Route path="/files/:id" element={<FileDetail />} />
          <Route path="/templates/:id" element={<TemplateDetail />} />
          <Route path="/catalog" element={<Catalog />} />
          <Route path="/catalog/:id" element={<CatalogItemPage />} />
          <Route path="*" element={<Typography>Not found.</Typography>} />
        </Routes>
      </Container>
      </MaintenanceProvider>
    </AuthContext.Provider>
  );
}
