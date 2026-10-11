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
import Audit from "./pages/Audit";

interface AuthState {
  me: Me | null;
  refresh: () => Promise<void>;
}

const AuthContext = createContext<AuthState>({ me: null, refresh: async () => {} });
export const useAuth = () => useContext(AuthContext);

export const hasRole = (me: Me | null, ...roles: string[]) =>
  !!me && (me.roles.includes("admin") || roles.some((r) => me.roles.includes(r)));

export default function App() {
  const [me, setMe] = useState<Me | null>(null);
  const [loading, setLoading] = useState(true);
  const location = useLocation();

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
  }, [refresh]);

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
    <AuthContext.Provider value={{ me, refresh }}>
      {me && (
        <AppBar position="static">
          <Toolbar sx={{ gap: 1 }}>
            <Typography variant="h6" sx={{ mr: 2 }}>
              LABaPe
            </Typography>
            <Button color="inherit" component={RouterLink} to="/environments">
              Environments
            </Button>
            <Button color="inherit" component={RouterLink} to="/jobs">
              Jobs
            </Button>
            {hasRole(me, "admin") && (
              <>
                <Button color="inherit" component={RouterLink} to="/hosts">
                  Hosts
                </Button>
                <Button color="inherit" component={RouterLink} to="/audit">
                  Audit
                </Button>
              </>
            )}
            <Box sx={{ flexGrow: 1 }} />
            <Typography variant="body2" sx={{ mr: 1 }}>
              {me.display_name} ({me.roles.join(", ") || "no roles"})
            </Typography>
            <Button color="inherit" onClick={logout}>
              Sign out
            </Button>
          </Toolbar>
        </AppBar>
      )}
      {me?.breakglass && (
        <Alert severity="warning" square>
          Break-glass session: full admin rights, time-limited, and every action is audited. Fix sign-in and sign
          out as soon as you can.
        </Alert>
      )}
      <Container maxWidth="lg" sx={{ py: 3 }}>
        <Routes>
          <Route path="/login" element={me ? <Navigate to="/" replace /> : <Login />} />
          <Route path="/breakglass" element={<Breakglass />} />
          <Route path="/" element={<Navigate to="/environments" replace />} />
          <Route path="/environments" element={<Environments />} />
          <Route path="/environments/new" element={<EnvironmentNew />} />
          <Route path="/environments/:id" element={<EnvironmentDetail />} />
          <Route path="/jobs" element={<Jobs />} />
          <Route path="/jobs/:id" element={<JobDetail />} />
          <Route path="/hosts" element={<Hosts />} />
          <Route path="/audit" element={<Audit />} />
          <Route path="*" element={<Typography>Not found.</Typography>} />
        </Routes>
      </Container>
    </AuthContext.Provider>
  );
}
