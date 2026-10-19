// The remote session log and recordings (design §28.5, question 118): admins
// and auditors. Playing a recording is audited.
import { useEffect, useRef, useState } from "react";
import {
  Alert, Box, Button, Chip, Dialog, DialogContent, DialogTitle, Slider, Stack, Table, TableBody, TableCell,
  TableHead, TableRow, Typography,
} from "@mui/material";
import Guacamole from "guacamole-common-js";
import { api, fmtTime } from "../api";
import { Field } from "../forms";
import { DateRangeFields, FilterBar, toQuery, useUrlFilters } from "../components/Filters";

interface Row {
  id: number; username: string; target_type: string; target: string; address: string; protocol: string; state: string;
  recorded: boolean; has_recording: boolean; recording_kind: "video" | "text" | null; error: string; client_ip: string; created_at: string;
  started_at: string | null; ended_at: string | null;
}

const dur = (a: string | null, b: string | null) => {
  if (!a || !b) return "—";
  const s = Math.round((new Date(b).getTime() - new Date(a).getTime()) / 1000);
  return s >= 3600 ? `${Math.floor(s / 3600)} h ${Math.floor((s % 3600) / 60)} min` : s >= 60 ? `${Math.floor(s / 60)} min ${s % 60} s` : `${s} s`;
};
const clock = (ms: number) => `${Math.floor(ms / 60000)}:${String(Math.floor((ms % 60000) / 1000)).padStart(2, "0")}`;

function Player({ row, onClose }: { row: Row; onClose: () => void }) {
  const holder = useRef<HTMLDivElement>(null);
  const rec = useRef<Guacamole.SessionRecording | null>(null);
  const [duration, setDuration] = useState(0);
  const [position, setPosition] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => {
    let alive = true;
    fetch(`/api/remote/sessions/${row.id}/recording`, { credentials: "same-origin" }).then(async (r) => {
      if (!r.ok) throw new Error(`The recording couldn't be loaded (${r.status}).`);
      const blob = await r.blob();
      if (!alive || !holder.current) return;
      const recording = new Guacamole.SessionRecording(blob);
      rec.current = recording;
      const display = recording.getDisplay();
      holder.current.innerHTML = "";
      holder.current.appendChild(display.getElement());
      display.onresize = () => display.scale(Math.min(1, (holder.current?.clientWidth ?? 900) / Math.max(display.getWidth(), 1)));
      recording.onprogress = (d: number) => setDuration(d);
      recording.onload = () => setDuration(recording.getDuration());
      recording.onplay = () => setPlaying(true);
      recording.onpause = () => setPlaying(false);
      recording.onseek = (p: number) => setPosition(p);
    }).catch((e) => setError((e as Error).message));
    return () => { alive = false; rec.current?.pause(); };
  }, [row.id]);
  return (
    <Dialog open onClose={onClose} maxWidth="lg" fullWidth>
      <DialogTitle>Session #{row.id}: {row.username} on {row.target} ({row.protocol.toUpperCase()}), {fmtTime(row.started_at)}</DialogTitle>
      <DialogContent>
        <Stack spacing={1}>
          {error && <Alert severity="error">{error}</Alert>}
          <Box ref={holder} sx={{ bgcolor: "black", minHeight: 300, overflow: "hidden" }} />
          <Stack direction="row" spacing={2} sx={{ alignItems: "center" }}>
            <Button variant="contained" size="small" onClick={() => (playing ? rec.current?.pause() : rec.current?.play())}
              title={playing ? "Pause" : "Play the recording"}>{playing ? "Pause" : "Play"}</Button>
            <Slider size="small" min={0} max={Math.max(duration, 1)} value={position} sx={{ flexGrow: 1 }}
              onChange={(_, v) => rec.current?.seek(v as number)} />
            <Typography variant="body2" sx={{ whiteSpace: "nowrap" }}>{clock(position)} / {clock(duration)}</Typography>
            <Button size="small" href={`/api/remote/sessions/${row.id}/recording`} title="Download the raw Guacamole recording">Download</Button>
          </Stack>
        </Stack>
      </DialogContent>
    </Dialog>
  );
}

export default function Sessions() {
  const [rows, setRows] = useState<Row[]>([]);
  const [error, setError] = useState("");
  const [playing, setPlaying] = useState<Row | null>(null);
  const f = useUrlFilters(["username", "target", "since", "until"]);
  const query = toQuery(f.applied).toString();
  useEffect(() => {
    api.get<Row[]>(`/api/remote/sessions?limit=500&${query}`).then((r) => { setRows(r); setError(""); }, (e) => setError(e.message));
  }, [query]);
  return (
    <Stack spacing={2}>
      <Typography variant="h5">Remote sessions</Typography>
      <Typography variant="body2" color="text.secondary">
        Browser SSH, RDP and console sessions: who opened them, to what, and for how long. Sessions are recorded when Settings → Remote access says so;
        playing a recording is audited.
      </Typography>
      {error && <Alert severity="error">{error}</Alert>}
      <FilterBar onApply={() => f.apply()} onClear={f.clear} active={f.active}>
        <Field label="User" value={f.draft.username} sx={{ width: 170 }} help="Exact user name" onChange={(e) => f.set("username", e.target.value)} />
        <Field label="Target" value={f.draft.target} sx={{ width: 170 }} help="VM or host contains" onChange={(e) => f.set("target", e.target.value)} />
        <DateRangeFields since={f.draft.since} until={f.draft.until} onChange={f.set} />
      </FilterBar>
      <Table size="small">
        <TableHead>
          <TableRow>
            <TableCell>#</TableCell><TableCell>User</TableCell><TableCell>Target</TableCell><TableCell>Protocol</TableCell>
            <TableCell>Started</TableCell><TableCell>Length</TableCell><TableCell>State</TableCell><TableCell>From</TableCell><TableCell />
          </TableRow>
        </TableHead>
        <TableBody>
          {rows.map((r) => (
            <TableRow key={r.id}>
              <TableCell>{r.id}</TableCell><TableCell>{r.username}</TableCell>
              <TableCell>{r.target} <Typography component="span" variant="caption" color="text.secondary">{r.target_type} · {r.address}</Typography></TableCell>
              <TableCell>{r.protocol.toUpperCase()}</TableCell>
              <TableCell sx={{ whiteSpace: "nowrap" }}>{fmtTime(r.started_at ?? r.created_at)}</TableCell>
              <TableCell>{dur(r.started_at, r.ended_at)}</TableCell>
              <TableCell><Chip size="small" label={r.state} color={r.state === "failed" ? "error" : r.state === "open" ? "success" : "default"} title={r.error} /></TableCell>
              <TableCell>{r.client_ip}</TableCell>
              <TableCell sx={{ whiteSpace: "nowrap" }}>
                {r.recording_kind === "video" && <Button size="small" onClick={() => setPlaying(r)} title="Play the recording (audited)">Play</Button>}
                {r.has_recording && (
                  <Button size="small" href={`/api/remote/sessions/${r.id}/recording`}
                    title={r.recording_kind === "text" ? "Download the session's text transcript (audited)" : "Download the raw Guacamole recording (audited)"}>
                    Download
                  </Button>
                )}
              </TableCell>
            </TableRow>
          ))}
          {rows.length === 0 && <TableRow><TableCell colSpan={9}>No sessions{f.active ? " match" : " yet"}.</TableCell></TableRow>}
        </TableBody>
      </Table>
      {playing && <Player row={playing} onClose={() => setPlaying(null)} />}
    </Stack>
  );
}
