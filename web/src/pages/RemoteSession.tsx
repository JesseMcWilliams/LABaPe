// A browser SSH/RDP session or VM console (design §28.5-§28.6, questions 116 and 119): guacamole-common-js
// talking to LABaPe's own tunnel. The one-time token comes in the navigation
// state, never in the URL, and the target's credentials never reach the browser.
import { useEffect, useRef, useState } from "react";
import { Link as RouterLink, useLocation, useParams } from "react-router-dom";
import { Alert, Box, Button, Chip, Stack, Typography } from "@mui/material";
import Guacamole from "guacamole-common-js";

interface SessionState { token: string; target: string; protocol: string; recorded: boolean; back: string }

const STATES = ["idle", "connecting", "waiting", "connected", "disconnecting", "disconnected"];

export default function RemoteSession() {
  const { id } = useParams();
  const location = useLocation();
  const info = location.state as SessionState | null;
  const holder = useRef<HTMLDivElement>(null);
  const [state, setState] = useState("connecting");
  const [error, setError] = useState("");
  const client = useRef<Guacamole.Client | null>(null);

  useEffect(() => {
    if (!info?.token || !holder.current) return;
    const scheme = window.location.protocol === "https:" ? "wss" : "ws";
    const tunnel = new Guacamole.WebSocketTunnel(`${scheme}://${window.location.host}/api/remote/tunnel`);
    const c = new Guacamole.Client(tunnel);
    client.current = c;
    const display = c.getDisplay().getElement();
    holder.current.innerHTML = "";
    holder.current.appendChild(display);
    const fit = () => {                                  // a reconnected session keeps its old size: show all of it
      const d = c.getDisplay();
      const w = d.getWidth();
      const room = holder.current?.getBoundingClientRect().width ?? 0;
      if (w > 0 && room > 0) d.scale(Math.min(1, room / w));
    };
    c.getDisplay().onresize = fit;
    c.onstatechange = (s: number) => {
      setState(STATES[s] ?? String(s));
      if (s === 3) {                                       // connected: ask for our size again so Windows repaints the desktop
        const b = holder.current?.getBoundingClientRect();
        if (b) c.sendSize(Math.max(640, Math.floor(b.width)), Math.max(400, Math.floor(window.innerHeight - b.top - 16)));
      }
    };
    c.onerror = (status: Guacamole.Status) => setError(status.message || `error ${status.code}`);
    tunnel.onerror = (status: Guacamole.Status) => setError(status.message || "The connection to LABaPe was lost.");

    const mouse = new Guacamole.Mouse(display);
    const send = (e: Guacamole.Mouse.Event) => c.sendMouseState(e.state, true);
    mouse.onEach(["mousedown", "mouseup", "mousemove"], send as never);
    const keyboard = new Guacamole.Keyboard(document);
    keyboard.onkeydown = (keysym: number) => { c.sendKeyEvent(1, keysym); return false; };
    keyboard.onkeyup = (keysym: number) => c.sendKeyEvent(0, keysym);

    const box = holder.current.getBoundingClientRect();
    const width = Math.max(640, Math.floor(box.width));
    const height = Math.max(400, Math.floor(window.innerHeight - box.top - 16));
    c.connect(`token=${encodeURIComponent(info.token)}&width=${width}&height=${height}&dpi=96`);
    const onResize = () => {
      const b = holder.current?.getBoundingClientRect();
      if (b) c.sendSize(Math.max(640, Math.floor(b.width)), Math.max(400, Math.floor(window.innerHeight - b.top - 16)));
    };
    window.addEventListener("resize", onResize);
    return () => {
      window.removeEventListener("resize", onResize);
      keyboard.onkeydown = null;
      keyboard.onkeyup = null;
      keyboard.reset();
      c.disconnect();
    };
  }, [info?.token]); // eslint-disable-line react-hooks/exhaustive-deps

  if (!info?.token) {
    return (
      <Alert severity="info">
        Session #{id} can&apos;t be reopened from here (its one-time link is used up). Start a new one from the environment or host page.
      </Alert>
    );
  }
  return (
    <Stack spacing={1}>
      <Stack direction="row" spacing={1} sx={{ alignItems: "center" }}>
        <Typography variant="h6">{info.target}</Typography>
        <Chip size="small" label={info.protocol.toUpperCase()} />
        <Chip size="small" label={state} color={state === "connected" ? "success" : state === "disconnected" ? "default" : "warning"} />
        {info.recorded && <Chip size="small" color="error" label="recorded" title="This session is being recorded for the auditors" />}
        <Box sx={{ flexGrow: 1 }} />
        <Button size="small" variant="outlined" onClick={() => client.current?.disconnect()} title="End the session">Disconnect</Button>
        <Button size="small" component={RouterLink} to={info.back} title="Back (ends the session)">Back</Button>
      </Stack>
      {error && <Alert severity="error">{error}</Alert>}
      <Box ref={holder} sx={{ bgcolor: "black", width: "100%", minHeight: 400, overflow: "hidden", cursor: "default",
        "& > div": { margin: "0 auto" } }} />
      <Typography variant="caption" color="text.secondary">
        Keys go to the remote side while this page has focus. A session with no keyboard or mouse input ends after the idle time set by the admins.
      </Typography>
    </Stack>
  );
}
