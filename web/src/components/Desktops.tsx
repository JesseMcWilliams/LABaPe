// Desktop sessions on Linux host groups (E3, question 168): xrdp (RDP for every account, local or
// domain) and VNC (a display of its own for each local account). Saved now; the next Redeploy sets
// them up on the VMs, and the Secrets panel's Connect offers them afterwards.
import { useState } from "react";
import { Alert, Button, Checkbox, FormControlLabel, Paper, Stack, Typography } from "@mui/material";
import { api, HostGroup } from "../api";

type Kind = "xrdp" | "vnc";

export default function Desktops({ envId, groups, onSaved }: { envId: string; groups: HostGroup[]; onSaved: () => void }) {
  const linux = groups.filter((g) => !g.os.startsWith("windows"));
  const [draft, setDraft] = useState<Record<string, Kind[]>>(
    Object.fromEntries(linux.map((g) => [g.name, g.remote_desktop ?? []])));
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  if (!linux.length) return null;
  const changed = linux.some((g) => (g.remote_desktop ?? []).join() !== (draft[g.name] ?? []).join());
  const toggle = (name: string, k: Kind, on: boolean) =>
    setDraft({ ...draft, [name]: on ? [...(draft[name] ?? []), k] : (draft[name] ?? []).filter((x) => x !== k) });
  const save = () => api.put(`/api/environments/${envId}/desktops`, {
    groups: linux.map((g) => ({ name: g.name, remote_desktop: draft[g.name] ?? [] })),
  }).then(() => { setMsg({ ok: true, text: "Saved. Redeploy to set them up on the VMs." }); onSaved(); },
    (e) => setMsg({ ok: false, text: (e as Error).message }));
  return (
    <Paper variant="outlined" sx={{ p: 2 }}>
      <Stack spacing={1}>
        <Typography variant="h6">Desktop sessions (Linux)</Typography>
        <Typography variant="body2" color="text.secondary">
          xrdp gives every account, local or domain, a desktop over RDP; VNC gives each local account a desktop of its own.
          Connect from the Secrets panel once a Redeploy has set them up.
        </Typography>
        {msg && <Alert severity={msg.ok ? "success" : "error"} onClose={() => setMsg(null)}>{msg.text}</Alert>}
        {linux.map((g) => (
          <Stack key={g.name} direction="row" spacing={2} sx={{ alignItems: "center" }}>
            <Typography variant="body2" sx={{ minWidth: 120 }}>{g.name}</Typography>
            {(["xrdp", "vnc"] as const).map((k) => (
              <FormControlLabel key={k} label={k === "xrdp" ? "xrdp (RDP)" : "VNC"}
                control={<Checkbox checked={(draft[g.name] ?? []).includes(k)} onChange={(e) => toggle(g.name, k, e.target.checked)} />} />
            ))}
          </Stack>
        ))}
        <Stack direction="row"><Button size="small" variant="contained" disabled={!changed} onClick={save}
          title="Save; the next Redeploy sets them up">Save</Button></Stack>
      </Stack>
    </Paper>
  );
}
