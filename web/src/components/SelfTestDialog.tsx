// The SMART self-test schedule (plan: Planning_SMART-Self-Test-Schedule.md): the types the page and the host panel
// share, and the dialog that edits the stack default or one host's override.
import { useState } from "react";
import {
  Alert, Button, Checkbox, Dialog, DialogActions, DialogContent, DialogTitle, FormControlLabel, MenuItem, Stack, Typography,
} from "@mui/material";
import { api } from "../api";
import { Field } from "../forms";
import { useConfirm } from "./Confirm";

export interface Schedule {
  run: boolean; short_on: boolean; short_weekday: number; short_hour: number; long_on: boolean; long_day: number; long_hour: number; warn: boolean;
}
export interface Drive {
  device: string; model: string; serial: string; power_on_hours: number | null; left_out: boolean; applied: string; warnings: string[];
  tests: Record<string, { hours: number | null; passed: boolean | null; status: string } | null> | null;
}
export interface SelfTestHost {
  id: number; name: string; ready: boolean; described: string; expression: string; in_sync: boolean | null;
  policy: Schedule & { mode: "default" | "custom" | "off"; skip: string[] };
  next: { short: string | null; long: string | null };
  smartd: { conf: string; active: boolean; tz: string; now: string; described: string } | null;
  drives: Drive[]; override: { warn?: boolean | null };
}

export const WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];
const hh = (h: number) => `${String(h).padStart(2, "0")}:00`;
const ord = (n: number) => (n % 100 >= 11 && n % 100 <= 13 ? "th" : ({ 1: "st", 2: "nd", 3: "rd" } as Record<number, string>)[n % 10] ?? "th");

/** The same words the server uses, for the preview line while editing. */
export const describe = (s: Schedule): string => {
  if (!s.run) return "no scheduled self-tests";
  const bits = [];
  if (s.short_on) bits.push(`short: every ${WEEKDAYS[s.short_weekday - 1]} at ${hh(s.short_hour)}`);
  if (s.long_on) bits.push(`long: the ${s.long_day}${ord(s.long_day)} of the month at ${hh(s.long_hour)}`);
  return bits.join(", ") || "no scheduled self-tests";
};
export const hostTime = (t: string | null, tz?: string) => (t ? `${t.replace("T", " ")}${tz ? ` ${tz}` : ""}` : "—");

type Warn = "default" | "yes" | "no";

export default function SelfTestDialog({ host, def, following, onClose, onSaved }: {
  host: SelfTestHost | null;            // null: the stack default
  def: Schedule;
  following: string[];                  // names of the ready hosts that follow the default (told before saving it)
  onClose: () => void; onSaved: (note: string) => void;
}) {
  const confirm = useConfirm();
  const start = host ? host.policy : def;
  const [mode, setMode] = useState<"default" | "custom" | "off">(host?.policy.mode ?? "default");
  const [s, setS] = useState<Schedule>({ ...start });
  const [skip, setSkip] = useState<string[]>(host?.policy.skip ?? []);
  const [warn, setWarn] = useState<Warn>(host?.override.warn == null ? "default" : host.override.warn ? "yes" : "no");
  const [error, setError] = useState("");
  const set = (p: Partial<Schedule>) => setS({ ...s, ...p });
  const showSchedule = host ? mode === "custom" : s.run;
  const effective: Schedule = host ? (mode === "custom" ? s : mode === "off" ? { ...def, run: false } : def) : s;
  const tz = host?.smartd?.tz;
  const save = async () => {
    setError("");
    try {
      if (!host) {
        if (!(await confirm({
          title: "Change the default schedule?",
          body: following.length
            ? `It is applied on ${following.length} host${following.length === 1 ? "" : "s"} that follow the default (${following.join(", ")}): a short setup job runs on each, and smartd restarts there.`
            : "No host follows the default at the moment, so nothing is changed on a host.",
          confirmLabel: "Save and apply",
        }))) return;
        const r = await api.put<{ queued: string[] }>("/api/admin/selftests/default", s);
        onSaved(r.queued.length ? `Saved; applying on ${r.queued.join(", ")}.` : "Saved.");
      } else {
        const r = await api.put<{ queued: boolean; note: string }>(`/api/admin/selftests/hosts/${host.id}`, {
          mode, ...s, skip, warn: warn === "default" ? null : warn === "yes",
        });
        onSaved(r.queued ? `Saved; applying on ${host.name}.` : r.note);
      }
    } catch (e) {
      setError((e as Error).message);
    }
  };
  return (
    <Dialog open onClose={onClose} maxWidth="sm" fullWidth>
      <DialogTitle>{host ? `Self-tests on ${host.name}` : "Default self-test schedule"}</DialogTitle>
      <DialogContent>
        <Stack spacing={1.5} sx={{ pt: 1 }}>
          {error && <Alert severity="error">{error}</Alert>}
          {host && (
            <Field select label="Schedule" value={mode} onChange={(e) => setMode(e.target.value as typeof mode)}
              help="Use the stack's default, set this host's own, or run no scheduled self-tests on it">
              <MenuItem value="default">Use the stack default ({describe(def)})</MenuItem>
              <MenuItem value="custom">This host's own schedule</MenuItem>
              <MenuItem value="off">No scheduled self-tests</MenuItem>
            </Field>
          )}
          {!host && <FormControlLabel label="Schedule self-tests" control={<Checkbox checked={s.run} onChange={(e) => set({ run: e.target.checked })} />} />}
          {showSchedule && (
            <>
              <Stack direction="row" spacing={1} sx={{ alignItems: "flex-start" }}>
                <FormControlLabel sx={{ minWidth: 120 }} label="Short test" control={<Checkbox checked={s.short_on} onChange={(e) => set({ short_on: e.target.checked })} />} />
                <Field select label="Every" size="small" disabled={!s.short_on} value={s.short_weekday} onChange={(e) => set({ short_weekday: Number(e.target.value) })}>
                  {WEEKDAYS.map((d, i) => <MenuItem key={d} value={i + 1}>{d}</MenuItem>)}
                </Field>
                <Field select label="At" size="small" disabled={!s.short_on} value={s.short_hour} onChange={(e) => set({ short_hour: Number(e.target.value) })}>
                  {Array.from({ length: 24 }, (_, h) => <MenuItem key={h} value={h}>{hh(h)}</MenuItem>)}
                </Field>
              </Stack>
              <Stack direction="row" spacing={1} sx={{ alignItems: "flex-start" }}>
                <FormControlLabel sx={{ minWidth: 120 }} label="Long test" control={<Checkbox checked={s.long_on} onChange={(e) => set({ long_on: e.target.checked })} />} />
                <Field select label="On day" size="small" disabled={!s.long_on} value={s.long_day} onChange={(e) => set({ long_day: Number(e.target.value) })}>
                  {Array.from({ length: 28 }, (_, i) => <MenuItem key={i + 1} value={i + 1}>{i + 1}{ord(i + 1)} of the month</MenuItem>)}
                </Field>
                <Field select label="At" size="small" disabled={!s.long_on} value={s.long_hour} onChange={(e) => set({ long_hour: Number(e.target.value) })}>
                  {Array.from({ length: 24 }, (_, h) => <MenuItem key={h} value={h}>{hh(h)}</MenuItem>)}
                </Field>
              </Stack>
            </>
          )}
          <Alert severity="info" icon={false}>
            {describe(effective)}. Times are the host&apos;s own clock{tz ? ` (${tz})` : ""}; a long test of a big drive takes hours and may slow it down.
          </Alert>
          {host && (
            <>
              <Typography variant="subtitle2">Drives to leave out of the tests</Typography>
              {host.drives.length === 0 ? <Typography variant="body2" color="text.secondary">No drives were read from this host yet.</Typography> : host.drives.map((d) => (
                <FormControlLabel key={d.device} label={`${d.device} · ${d.model || "?"}`}
                  control={<Checkbox size="small" checked={skip.includes(d.device)}
                    onChange={(e) => setSkip(e.target.checked ? [...skip, d.device] : skip.filter((x) => x !== d.device))} />} />
              ))}
              <Typography variant="caption" color="text.secondary">
                For a drive smartd cannot test (behind a hardware RAID controller, or a USB bridge). It stays monitored for health and temperature.
                Left-out drives get no &quot;never tested&quot; warning.
              </Typography>
            </>
          )}
          {host ? (
            <Field select label="Warn when no self-test ran" value={warn} onChange={(e) => setWarn(e.target.value as Warn)}
              help="A failed self-test is always reported, whatever this says">
              <MenuItem value="default">Follow the stack default ({def.warn ? "warn" : "don't warn"})</MenuItem>
              <MenuItem value="yes">Warn</MenuItem>
              <MenuItem value="no">Don&apos;t warn</MenuItem>
            </Field>
          ) : (
            <FormControlLabel label="Warn when a drive has had no self-test (a failed one is always reported)"
              control={<Checkbox checked={s.warn} onChange={(e) => set({ warn: e.target.checked })} />} />
          )}
        </Stack>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>Cancel</Button>
        <Button variant="contained" onClick={save}>{host ? "Save and apply" : "Save"}</Button>
      </DialogActions>
    </Dialog>
  );
}
