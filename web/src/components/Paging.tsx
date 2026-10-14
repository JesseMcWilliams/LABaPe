// Paged lists and exports (L1, Planning_Lists-Secrets-Settings.md; questions 159-160): every paged
// list shows the user's own page size (50 by default, kept in their profile), and an export asks
// for this page, a range of rows, or all of the (filtered) list.
import { useEffect, useState } from "react";
import {
  Button, Dialog, DialogActions, DialogContent, DialogTitle, FormControlLabel, IconButton, MenuItem, Radio, RadioGroup,
  Stack, TextField, Typography,
} from "@mui/material";
import ChevronLeftIcon from "@mui/icons-material/ChevronLeft";
import ChevronRightIcon from "@mui/icons-material/ChevronRight";
import DownloadIcon from "@mui/icons-material/Download";
import { api } from "../api";

const SIZES = [25, 50, 100, 250];
let cached: number | null = null;
let loading: Promise<number> | null = null;
const listeners = new Set<(n: number) => void>();

/** The signed-in user's rows per page, shared by every paged list; setting it saves it to their profile. */
export function usePageSize(): [number, (n: number) => void] {
  const [size, setSize] = useState<number>(cached ?? 50);
  useEffect(() => {
    listeners.add(setSize);
    if (cached === null) {
      loading = loading ?? api.get<{ page_size: number }>("/api/auth/me/preferences").then((p) => p.page_size, () => 50);
      loading.then((n) => { cached = n; listeners.forEach((l) => l(n)); });
    }
    return () => { listeners.delete(setSize); };
  }, []);
  const set = (n: number) => {
    cached = n;
    listeners.forEach((l) => l(n));
    api.put("/api/auth/me/preferences", { page_size: n }).catch(() => undefined);
  };
  return [size, set];
}

/** The rows of `page` (0-based) of a list held in the browser. */
export const pageOf = <T,>(rows: T[], page: number, size: number) => rows.slice(page * size, (page + 1) * size);

/** "Rows 51-100 of 230", previous/next, and the rows-per-page choice (25, 50, 100, 250 or the user's own). */
export function Pager({ total, page, onPage, more }: {
  total: number | null; page: number; onPage: (page: number) => void; more?: boolean;
}) {
  const [size, setSize] = usePageSize();
  const [custom, setCustom] = useState("");
  const [asking, setAsking] = useState(false);
  const first = total === 0 ? 0 : page * size + 1;
  const last = total === null ? page * size + size : Math.min(total, (page + 1) * size);
  const hasNext = total === null ? Boolean(more) : (page + 1) * size < total;
  return (
    <Stack direction="row" spacing={1} sx={{ alignItems: "center", justifyContent: "flex-end", flexWrap: "wrap" }}>
      <Typography variant="body2" color="text.secondary">
        {total === null ? `Rows ${first}-${last}` : `Rows ${first}-${last} of ${total}`}
      </Typography>
      <IconButton size="small" disabled={page === 0} onClick={() => onPage(page - 1)} title="Previous page"><ChevronLeftIcon /></IconButton>
      <IconButton size="small" disabled={!hasNext} onClick={() => onPage(page + 1)} title="Next page"><ChevronRightIcon /></IconButton>
      {!asking ? (
        <TextField select size="small" label="Rows per page" value={SIZES.includes(size) ? size : "own"} sx={{ width: 140 }}
          onChange={(e) => {
            if (e.target.value === "other") { setAsking(true); setCustom(String(size)); return; }
            if (e.target.value !== "own") { setSize(Number(e.target.value)); onPage(0); }
          }}>
          {SIZES.map((n) => <MenuItem key={n} value={n}>{n}</MenuItem>)}
          {!SIZES.includes(size) && <MenuItem value="own">{size}</MenuItem>}
          <MenuItem value="other">Other…</MenuItem>
        </TextField>
      ) : (
        <TextField size="small" label="Rows per page" value={custom} sx={{ width: 140 }} autoFocus
          helperText="5-1000; Enter to keep"
          onChange={(e) => setCustom(e.target.value.replace(/[^0-9]/g, ""))}
          onKeyDown={(e) => {
            if (e.key === "Enter") {
              const n = Math.min(1000, Math.max(5, Number(custom) || 50));
              setSize(n); setAsking(false); onPage(0);
            } else if (e.key === "Escape") setAsking(false);
          }} />
      )}
    </Stack>
  );
}

export interface Column<T> { header: string; value: (row: T) => unknown }

function csv<T>(rows: T[], columns: Column<T>[]): string {
  const cell = (v: unknown) => {
    const s = v === null || v === undefined ? "" : Array.isArray(v) ? v.join("; ") : typeof v === "object" ? JSON.stringify(v) : String(v);
    return /[",\r\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
  };
  return [columns.map((c) => cell(c.header)).join(","), ...rows.map((r) => columns.map((c) => cell(c.value(r))).join(","))].join("\r\n") + "\r\n";
}

function save(name: string, type: string, text: string) {
  const url = URL.createObjectURL(new Blob([text], { type }));
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

/**
 * Export the list (question 159): this page, rows from-to (1-based, of the filtered list), or all.
 * `fetchRows(offset, limit)` returns those rows, from the browser's list or the API.
 */
export function ExportButton<T>({ name, total, page, columns, fetchRows, formats = ["csv"] }: {
  name: string; total: number; page: number; columns: Column<T>[];
  fetchRows: (offset: number, limit: number) => Promise<T[]>; formats?: ("csv" | "json")[];
}) {
  const [size] = usePageSize();
  const [open, setOpen] = useState(false);
  const [scope, setScope] = useState<"page" | "range" | "all">("page");
  const [format, setFormat] = useState(formats[0]);
  const [from, setFrom] = useState("1");
  const [to, setTo] = useState(String(Math.min(total, 100)));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const go = async () => {
    setError("");
    let offset = 0, limit = total;
    if (scope === "page") { offset = page * size; limit = size; }
    if (scope === "range") {
      const a = Number(from), b = Number(to);
      if (!a || !b || a > b || a < 1) { setError("Give rows from and to, first row 1, from not after to."); return; }
      offset = a - 1; limit = b - a + 1;
    }
    setBusy(true);
    try {
      const rows = await fetchRows(offset, limit);
      const stamp = new Date().toISOString().slice(0, 19).replace(/[:T]/g, "-");
      if (format === "json") save(`${name}-${stamp}.json`, "application/json", JSON.stringify(rows, null, 2) + "\n");
      else save(`${name}-${stamp}.csv`, "text/csv", csv(rows, columns));
      setOpen(false);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <>
      <Button size="small" startIcon={<DownloadIcon />} disabled={total === 0} onClick={() => setOpen(true)}
        title={`Download the list as ${formats.map((f) => f.toUpperCase()).join(" or ")} (asks which rows)`}>
        Export
      </Button>
      <Dialog open={open} onClose={() => setOpen(false)}>
        <DialogTitle>Export {name}</DialogTitle>
        <DialogContent>
          <Stack spacing={1}>
            {formats.length > 1 && (
              <RadioGroup row value={format} onChange={(e) => setFormat(e.target.value as "csv" | "json")}>
                {formats.map((f) => <FormControlLabel key={f} value={f} control={<Radio />} label={f.toUpperCase()} />)}
              </RadioGroup>
            )}
            <RadioGroup value={scope} onChange={(e) => setScope(e.target.value as "page" | "range" | "all")}>
              <FormControlLabel value="page" control={<Radio />} label={`This page (rows ${page * size + 1}-${Math.min(total, (page + 1) * size)})`} />
              <FormControlLabel value="range" control={<Radio />} label="Rows from - to" />
              {scope === "range" && (
                <Stack direction="row" spacing={1} sx={{ pl: 4 }}>
                  <TextField size="small" label="From" value={from} sx={{ width: 110 }} onChange={(e) => setFrom(e.target.value.replace(/[^0-9]/g, ""))} />
                  <TextField size="small" label="To" value={to} sx={{ width: 110 }} onChange={(e) => setTo(e.target.value.replace(/[^0-9]/g, ""))} />
                </Stack>
              )}
              <FormControlLabel value="all" control={<Radio />} label={`All ${total} rows`} />
            </RadioGroup>
            <Typography variant="caption" color="text.secondary">With the filters shown; in the list's order.</Typography>
            {error && <Typography color="error" variant="body2">{error}</Typography>}
          </Stack>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setOpen(false)}>Cancel</Button>
          <Button variant="contained" disabled={busy} onClick={go}>Download</Button>
        </DialogActions>
      </Dialog>
    </>
  );
}
