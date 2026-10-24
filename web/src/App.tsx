import { createContext, useCallback, useContext, useEffect, useState } from "react";
import { Link as RouterLink, Navigate, Route, Routes, useLocation } from "react-router-dom";
import { Alert, AppBar, Box, Button, CircularProgress, Container, Toolbar, Typography } from "@mui/material";
import { api, ApiError, Me } from "./api";
import Login from "./pages/Login";
import Breakglass from "./pages/Breakglass";
import Environments from "./pages/Environments";
import EnvironmentNew from "./pages/EnvironmentNew";
import EnvTemplates from "./pages/EnvTemplates";
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
import Certificates from "./pages/Certificates";
import DiskDetail from "./pages/DiskDetail";
import HostDetail from "./pages/HostDetail";
import NetworkDetail from "./pages/NetworkDetail";
import FileDetail from "./pages/FileDetail";
import TemplateDetail from "./pages/TemplateDetail";
import Secrets from "./pages/Secrets";
import Mail from "./pages/Mail";
import SecretDetail from "./pages/SecretDetail";
import AdminLayout, { ToAdmin } from "./pages/Admin";
import RemoteSession from "./pages/RemoteSession";
import Sessions from "./pages/Sessions";
import VmDetail from "./pages/VmDetail";
import Syslog from "./pages/Syslog";
import Links from "./pages/Links";
import Tokens from "./pages/Tokens";
import Quotas from "./pages/Quotas";
import Users from "./pages/Users";
import UserMenu from "./components/UserMenu";
import HelpHere from "./components/HelpHere";
import Help from "./pages/Help";
import { MaintenanceBanner, MaintenanceProvider } from "./components/Maintenance";

interface AuthState {
  me: Me | null;
  refresh: () => Promise<void>;
  appName: string;                       // Settings > Branding (design §22.2)
  logo: string | null;                   // its version, or null: no logo (design §44)
  setLogo: (v: string | null) => void;
  setAppName: (name: string) => void;
}

const AuthContext = createContext<AuthState>({ me: null, refresh: async () => {}, appName: "LABaPe", setAppName: () => {},
  logo: null, setLogo: () => {} });
export const useAuth = () => useContext(AuthContext);

export const hasRole = (me: Me | null, ...roles: string[]) =>
  !!me && (me.roles.includes("admin") || roles.some((r) => me.roles.includes(r)));

const SOURCE_URL = "https://github.com/JesseMcWilliams/LABaPe";

export default function App() {
  const [me, setMe] = useState<Me | null>(null);
  const [loading, setLoading] = useState(true);
  const [appName, setAppName] = useState("LABaPe");
  const [logo, setLogo] = useState<string | null>(null);
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
    api.get<{ app_name: string; logo: string | null }>("/api/branding").then((b) => { setAppName(b.app_name); setLogo(b.logo); }, () => undefined);
  }, [refresh]);
  useEffect(() => {
    document.title = appName;
  }, [appName]);
  useEffect(() => {                       // the logo as the tab's icon too
    const link = document.querySelector<HTMLLinkElement>("link[rel~='icon']");
    if (link && logo) link.href = `/api/branding/logo?v=${logo}`;
  }, [logo]);

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
    <AuthContext.Provider value={{ me, refresh, appName, setAppName, logo, setLogo }}>
      <MaintenanceProvider signedIn={Boolean(me)}>
      {me && (
        <AppBar position="sticky">
          <Toolbar sx={{ gap: 1 }}>
            <Typography variant="h6" component={RouterLink} to="/" title="Dashboard: load, environments and platform health"
              sx={{ mr: 2, color: "inherit", textDecoration: "none", display: "flex", alignItems: "center", gap: 1 }}>
              {logo && <img src={`/api/branding/logo?v=${logo}`} alt="" style={{ height: 28, maxWidth: 120, objectFit: "contain" }} />}
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
            <Button color="inherit" component={RouterLink} to="/certificates" title="Certificates from the platform CA: yours and your environments', requesting and (for CA administrators) revoking">
              Certificates
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
            <Button color="inherit" component={RouterLink} to="/help" title="The user guides: how to use each page">
              Help
            </Button>
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
      <Container maxWidth={false} sx={{ py: 3, position: "relative" }}>
        {me && <HelpHere />}
        <Routes>
          <Route path="/help" element={<Help />} />
          <Route path="/login" element={me ? <Navigate to="/" replace /> : <Login />} />
          <Route path="/breakglass" element={<Breakglass />} />
          <Route path="/" element={<Dashboard />} />
          <Route path="/environments" element={<Environments />} />
          <Route path="/environments/new" element={<EnvironmentNew />} />
          <Route path="/environment-templates" element={<EnvTemplates />} />
          <Route path="/environments/:id" element={<EnvironmentDetail />} />
          <Route path="/environments/:id/vms/:vm" element={<VmDetail />} />
          <Route path="/jobs" element={<Jobs />} />
          <Route path="/syslog" element={<Syslog />} />
          <Route path="/jobs/:id" element={<JobDetail />} />
          <Route path="/tokens" element={<Tokens />} />
          <Route path="/admin" element={<AdminLayout />}>
            <Route path="hosts" element={<Hosts />} />
            <Route path="hosts/:id" element={<HostDetail />} />
            <Route path="hosts/:id/disks/:disk" element={<DiskDetail />} />
            <Route path="networks" element={<Networks />} />
            <Route path="networks/:id" element={<NetworkDetail />} />
            <Route path="settings" element={<Settings />} />
            <Route path="mail" element={<Mail />} />
            <Route path="secrets" element={<Secrets />} />
            <Route path="secrets/:id" element={<SecretDetail />} />
            <Route path="audit" element={<Audit />} />
            <Route path="errors" element={<Errors />} />
            <Route path="sessions" element={<Sessions />} />
            <Route path="links" element={<Links />} />
            <Route path="quotas" element={<Quotas />} />
            <Route path="users" element={<Users />} />
          </Route>
          <Route path="/remote/:id" element={<RemoteSession />} />
          {["/hosts", "/networks", "/settings", "/secrets", "/audit", "/errors"].map((p) => (
            <Route key={p} path={`${p}/*`} element={<ToAdmin />} />
          ))}
          <Route path="/files" element={<Files />} />
          <Route path="/files/:id" element={<FileDetail />} />
          <Route path="/templates/:id" element={<TemplateDetail />} />
          <Route path="/certificates" element={<Certificates />} />
          <Route path="/catalog" element={<Catalog />} />
          <Route path="/catalog/:id" element={<CatalogItemPage />} />
          <Route path="*" element={<Typography>Not found.</Typography>} />
        </Routes>
      </Container>
      {/* AGPL-3.0 section 13: whoever uses a modified LABaPe over the network is offered its source. A modified
          copy points this at its own source. */}
      <Box component="footer" sx={{ px: 3, py: 2, color: "text.secondary", typography: "caption" }}>
        LABaPe is free software under the GNU Affero General Public License v3.{" "}
        <a href={SOURCE_URL} target="_blank" rel="noreferrer">Source code</a>
      </Box>
      </MaintenanceProvider>
    </AuthContext.Provider>
  );
}
