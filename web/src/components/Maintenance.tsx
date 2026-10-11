import { createContext, ReactNode, useContext, useEffect, useState } from "react";
import { Alert } from "@mui/material";
import { api, fmtTime } from "../api";

// Maintenance mode (design §23.3): the app polls the state; while it's on,
// a banner shows on every page and change buttons are disabled.

export interface MaintenanceState {
  active: boolean; reason: string; source: string; started_by: string; started_at: string | null;
  expected_end: string | null; scheduled_start: string | null; running_jobs: number; queued_jobs: number;
  window: { start: string; end: string; in_window: boolean; heads_up: boolean } | null;
}

const MaintenanceContext = createContext<MaintenanceState | null>(null);
export const useMaintenance = () => useContext(MaintenanceContext);

export function MaintenanceProvider({ signedIn, children }: { signedIn: boolean; children: ReactNode }) {
  const [state, setState] = useState<MaintenanceState | null>(null);
  useEffect(() => {
    if (!signedIn) return;
    const load = () => api.get<MaintenanceState>("/api/maintenance").then(setState, () => undefined);
    load();
    const t = setInterval(load, 30000);
    return () => clearInterval(t);
  }, [signedIn]);
  return <MaintenanceContext.Provider value={state}>{children}</MaintenanceContext.Provider>;
}

export function MaintenanceBanner() {
  const m = useMaintenance();
  if (!m) return null;
  if (m.active) {
    const overdue = m.expected_end && new Date(m.expected_end) < new Date();
    return (
      <Alert square severity="warning">
        <b>Maintenance:</b> {m.reason}. Changes are paused; everything can still be viewed.
        {" "}Started {fmtTime(m.started_at)}{m.started_by ? ` by ${m.started_by}` : ""}
        {m.expected_end ? `, expected to end ${fmtTime(m.expected_end)}${overdue ? " (overdue)" : ""}` : ""}.
        {m.running_jobs > 0 && ` ${m.running_jobs} running job(s) are finishing; no new jobs start.`}
      </Alert>
    );
  }
  if (m.scheduled_start) {
    return <Alert square severity="info">Maintenance is scheduled for {fmtTime(m.scheduled_start)}: {m.reason}.</Alert>;
  }
  if (m.window?.heads_up) {
    return (
      <Alert square severity="info">
        The maintenance window starts {fmtTime(m.window.start)} and ends {fmtTime(m.window.end)}; services may restart then.
      </Alert>
    );
  }
  return null;
}
