// Connect buttons (design §28.5): ask the API for a one-time session, then open
// the remote session page with the token in the navigation state.
import { useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { Button } from "@mui/material";
import { api } from "../api";

export interface SessionTarget {
  target_type: "vm" | "host"; protocol: "ssh" | "rdp" | "vnc" | "serial"; environment_id?: number; vm?: string; kvm_host_id?: number;
}

export function ConnectButton({ target, label, onError }: { target: SessionTarget; label?: string; onError: (m: string) => void }) {
  const navigate = useNavigate();
  const location = useLocation();
  const [busy, setBusy] = useState(false);
  const go = async () => {
    setBusy(true);
    try {
      const r = await api.post<{ id: number; token: string; target: string; protocol: string; recorded: boolean }>("/api/remote/sessions", target);
      navigate(`/remote/${r.id}`, { state: { ...r, back: location.pathname + location.search } });
    } catch (e) { onError((e as Error).message); } finally { setBusy(false); }
  };
  const proto = target.protocol === "serial" ? "serial console" : target.protocol.toUpperCase();
  return (
    <Button size="small" disabled={busy} onClick={go}
      title={`Open ${proto} to ${target.vm ?? "this host"} in the browser (owners and admins; audited, and recorded if the admins turned recording on)`}>
      {label ?? proto}
    </Button>
  );
}

export function RdpFileLink({ envId, vm }: { envId: number; vm: string }) {
  return (
    <Button size="small" href={`/api/remote/rdp?environment_id=${envId}&vm=${encodeURIComponent(vm)}`}
      title="Download a .rdp file for your own RDP client (you need to reach the lab network; it asks for the password)">
      .rdp
    </Button>
  );
}
