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
  const [note, setNote] = useState("");
  const sync = useRef<{ push: () => Promise<boolean>; pull: () => void } | null>(null);

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

    // Clipboard (both ways, plain text). Remote -> browser: written to the browser's clipboard, or held and written at
    // the next click or key when the browser refuses without a gesture. Browser -> remote: sent when the page gains
    // focus, when Ctrl/Cmd goes down (so the remote's paste sees it), on a paste event and from the Paste button.
    let held: string | null = null;
    let sent = "";
    const toBrowser = (text: string) => {
      held = text;
      navigator.clipboard?.writeText(text).then(() => { held = null; }, () => { /* wait for a gesture */ });
    };
    c.onclipboard = (stream: Guacamole.InputStream, mimetype: string) => {
      if (!mimetype.startsWith("text/")) return;
      const reader = new Guacamole.StringReader(stream);
      let data = "";
      reader.ontext = (t: string) => { data += t; };
      reader.onend = () => toBrowser(data);
    };
    const toRemote = (text: string) => {
      if (!text || text === sent) return;
      sent = text;
      const out = c.createClipboardStream("text/plain");
      const w = new Guacamole.StringWriter(out);
      w.sendText(text);
      w.sendEnd();
    };
    const push = async () => {
      try {
        toRemote(await navigator.clipboard.readText());
        return true;
      } catch {
        return false;                                      // no permission, or the page isn't focused
      }
    };
    const flushHeld = () => {
      if (held !== null) navigator.clipboard?.writeText(held).then(() => { held = null; }, () => undefined);
    };
    const onPaste = (e: ClipboardEvent) => toRemote(e.clipboardData?.getData("text") ?? "");
    const onFocus = () => { void push(); };
    document.addEventListener("paste", onPaste);
    window.addEventListener("focus", onFocus);
    display.addEventListener("mousedown", flushHeld);
    sync.current = {
      push: async () => { const ok = await push(); if (!ok) setNote("The browser didn't allow reading the clipboard: click the page, allow it, or use Ctrl+V in the session."); return ok; },
      pull: flushHeld,
    };

    const mouse = new Guacamole.Mouse(display);
    const send = (e: Guacamole.Mouse.Event) => c.sendMouseState(e.state, true);
    mouse.onEach(["mousedown", "mouseup", "mousemove"], send as never);
    const keyboard = new Guacamole.Keyboard(document);
    keyboard.onkeydown = (keysym: number) => {
      if (keysym === 0xffe3 || keysym === 0xffe4 || keysym === 0xffe7 || keysym === 0xffe8) void push();   // Ctrl or Meta: sync before the V arrives
      flushHeld();
      c.sendKeyEvent(1, keysym);
      return false;
    };
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
      document.removeEventListener("paste", onPaste);
      window.removeEventListener("focus", onFocus);
      display.removeEventListener("mousedown", flushHeld);
      sync.current = null;
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
        <Button size="small" variant="outlined" onClick={() => void sync.current?.push()}
          title="Send what is on this computer's clipboard to the remote side (Ctrl+V in the session does it too)">Paste</Button>
        <Button size="small" variant="outlined" onClick={() => client.current?.disconnect()} title="End the session">Disconnect</Button>
        <Button size="small" component={RouterLink} to={info.back} title="Back (ends the session)">Back</Button>
      </Stack>
      {error && <Alert severity="error">{error}</Alert>}
      {note && <Alert severity="info" onClose={() => setNote("")}>{note}</Alert>}
      <Box ref={holder} sx={{ bgcolor: "black", width: "100%", minHeight: 400, overflow: "hidden", cursor: "default",
        "& > div": { margin: "0 auto" } }} />
      <Typography variant="caption" color="text.secondary">
        Keys go to the remote side while this page has focus. Text copied on the remote side lands on this computer’s clipboard (click the page once if the browser holds it back); text copied here is sent when you press Ctrl or use Paste. A session with no keyboard or mouse input ends after the idle time set by the admins.
      </Typography>
    </Stack>
  );
}
