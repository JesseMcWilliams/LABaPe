// Clean-up for the Containers page (plan C3): a preview of what could be removed and what it frees, then only what is ticked.
// Stopped containers (those the host's setting allows), images nothing uses, and volumes no container refers to.
// The runtime itself refuses anything still in use; removing volumes deletes their data and asks for the word "delete".
import { useCallback, useEffect, useState } from "react";
import {
  Alert, Button, Checkbox, Dialog, DialogActions, DialogContent, DialogTitle, FormControlLabel, Stack, Table, TableBody, TableCell, TableHead, TableRow, Typography,
} from "@mui/material";
import { api } from "../api";
import { Field } from "../forms";
import { useConfirm } from "./Confirm";

interface Item { key: string; label: string; bytes: number; detail: string }
type Kind = "containers" | "dangling_images" | "unused_images" | "volumes";
interface Preview { level: string; totals: Record<Kind, { count: number; bytes: number }> & Record<string, unknown> }
type Candidates = Preview & Record<Kind, Item[]>;

const KINDS: { kind: Kind; title: string; note: string; label: string }[] = [
  { kind: "containers", title: "Stopped containers", note: "Containers that have exited. Their logs go with them.", label: "Remove" },
  { kind: "dangling_images", title: "Untagged images", note: "Left behind by builds and updates; nothing uses them.", label: "Remove" },
  { kind: "unused_images", title: "Tagged images nothing uses", note: "They are downloaded again if something needs them.", label: "Remove" },
  { kind: "volumes", title: "Unused volumes", note: "No container refers to them. Their data is deleted and can't be brought back.", label: "Delete" },
];

const size = (b: number) => (b >= 1e9 ? `${(b / 1e9).toFixed(1)} GB` : b >= 1e6 ? `${(b / 1e6).toFixed(0)} MB` : b > 0 ? `${(b / 1e3).toFixed(0)} kB` : "—");

export function CleanupDialog({ hostId, onClose, onChanged }: { hostId: number; onClose: () => void; onChanged: () => void }) {
  const confirm = useConfirm();
  const [data, setData] = useState<Candidates | null>(null);
  const [picked, setPicked] = useState<Record<string, boolean>>({});
  const [typed, setTyped] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [done, setDone] = useState("");
  const load = useCallback(async () => {
    try {
      const d = await api.get<Candidates>(`/api/hosts/${hostId}/containers/cleanup`);
      setData(d);
      setPicked(Object.fromEntries(d.dangling_images.map((i) => [`dangling_images:${i.key}`, true])));   // the safest kind starts ticked
    } catch (e) {
      setError((e as Error).message);
    }
  }, [hostId]);
  useEffect(() => { void load(); }, [load]);
  const keys = (kind: Kind) => (data?.[kind] ?? []).filter((i) => picked[`${kind}:${i.key}`]);
  const run = async (kind: Kind, label: string) => {
    const items = keys(kind);
    if (!items.length) return;
    const total = items.reduce((a, i) => a + i.bytes, 0);
    const ok = await confirm({
      title: `${label} ${items.length} ${KINDS.find((k) => k.kind === kind)?.title.toLowerCase()}?`, confirmLabel: label, danger: true,
      body: `${items.map((i) => i.label).slice(0, 8).join(", ")}${items.length > 8 ? `, and ${items.length - 8} more` : ""}. Their sizes add up to ${size(total)}; images share layers, so less is freed.${kind === "volumes" ? " The data in these volumes is deleted for good." : ""}`,
    });
    if (!ok) return;
    setBusy(true);
    setError("");
    setDone("");
    try {
      const r = await api.post<{ removed: string[]; kept: string[] }>(`/api/hosts/${hostId}/containers/cleanup`,
        { kind, keys: items.map((i) => i.key), confirm: kind === "volumes" ? typed : "" });
      setDone(`${r.removed.length} removed${r.kept.length ? `, ${r.kept.length} kept (still in use: ${r.kept.slice(0, 5).join(", ")})` : ""}.`);
      setTyped("");
      onChanged();
      await load();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <Dialog open onClose={onClose} maxWidth="md" fullWidth>
      <DialogTitle>Clean up</DialogTitle>
      <DialogContent>
        <Stack spacing={2} sx={{ pt: 1 }}>
          {!data && !error && <Typography color="text.secondary">Loading…</Typography>}
          {error && <Alert severity="error">{error}</Alert>}
          {done && <Alert severity="success">{done}</Alert>}
          {data && data.level === "off" && (
            <Alert severity="info">Changes are off for this host, so nothing can be removed. Choose what the page may change first (&ldquo;Changes from this page&rdquo;).</Alert>
          )}
          {data && (
            <Typography variant="body2" color="text.secondary">
              Worked out from the last read; nothing is removed until you pick items and confirm. Image sizes are counted one by one, but images share layers, so removing them frees less than the sum. The LABaPe stack&apos;s own containers and volumes are never offered.
            </Typography>
          )}
          {data && KINDS.map(({ kind, title, note, label }) => (
            <Stack key={kind} spacing={1}>
              <Typography variant="subtitle2">
                {title} · {data.totals[kind].count}{data.totals[kind].bytes > 0 ? ` · sizes add up to ${size(data.totals[kind].bytes)}` : ""}
              </Typography>
              <Typography variant="caption" color="text.secondary">{note}</Typography>
              {data[kind].length > 0 && (
                <>
                  <Table size="small">
                    <TableHead><TableRow><TableCell padding="checkbox" /><TableCell>Name</TableCell><TableCell>Details</TableCell><TableCell align="right">Size</TableCell></TableRow></TableHead>
                    <TableBody>
                      {data[kind].map((i) => (
                        <TableRow key={i.key}>
                          <TableCell padding="checkbox">
                            <Checkbox size="small" checked={Boolean(picked[`${kind}:${i.key}`])} disabled={data.level === "off"}
                              onChange={(e) => setPicked({ ...picked, [`${kind}:${i.key}`]: e.target.checked })} />
                          </TableCell>
                          <TableCell sx={{ fontFamily: "monospace", fontSize: 12, wordBreak: "break-all" }}>{i.label}</TableCell>
                          <TableCell sx={{ fontSize: 12 }}>{i.detail}</TableCell><TableCell align="right">{size(i.bytes)}</TableCell>
                        </TableRow>
                      ))}
                    </TableBody>
                  </Table>
                  <Stack direction="row" spacing={2} sx={{ alignItems: "flex-start" }}>
                    <FormControlLabel label="Select all" control={<Checkbox size="small" checked={data[kind].every((i) => picked[`${kind}:${i.key}`])}
                      onChange={(e) => setPicked({ ...picked, ...Object.fromEntries(data[kind].map((i) => [`${kind}:${i.key}`, e.target.checked])) })} />} />
                    {kind === "volumes" && <Field label="Type delete to allow it" value={typed} onChange={(e) => setTyped(e.target.value)} sx={{ width: 220 }} />}
                    <Button variant="contained" color="error" disabled={busy || data.level === "off" || keys(kind).length === 0 || (kind === "volumes" && typed !== "delete")}
                      onClick={() => run(kind, label)} sx={{ mt: kind === "volumes" ? 1 : 0.5 }}>
                      {label} {keys(kind).length || ""} picked…
                    </Button>
                  </Stack>
                </>
              )}
            </Stack>
          ))}
        </Stack>
      </DialogContent>
      <DialogActions><Button onClick={onClose}>Close</Button></DialogActions>
    </Dialog>
  );
}
