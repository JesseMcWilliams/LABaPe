// Shared list filters (design §28, O1): the values live in the page's URL, so a
// filtered view can be bookmarked or shared, and the browser's back button works.
import { createContext, ReactNode, useContext, useMemo, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { Button, MenuItem, Stack } from "@mui/material";
import FilterAltOffIcon from "@mui/icons-material/FilterAltOff";
import { Field } from "../forms";

export type FilterValues = Record<string, string>;

/** Lets a drop-down inside a FilterBar apply the filters as soon as a choice is made. */
const ApplyNow = createContext<(() => void) | null>(null);

/**
 * Filter state for `keys`, read from and written to the URL. `draft` is what the
 * fields show; `applied` is what the list uses, updated by apply() (the Filter
 * button or Enter) and clear().
 */
export function useUrlFilters(keys: string[]) {
  const [params, setParams] = useSearchParams();
  const applied: FilterValues = useMemo(
    () => Object.fromEntries(keys.map((k) => [k, params.get(k) ?? ""])),
    [params, keys.join(",")], // eslint-disable-line react-hooks/exhaustive-deps
  );
  const [draft, setDraft] = useState<FilterValues>(applied);
  const draftRef = useRef<FilterValues>(applied);         // always the latest draft, so apply() right after set() sees it
  const set = (k: string, v: string) => {
    draftRef.current = { ...draftRef.current, [k]: v };
    setDraft(draftRef.current);
  };
  const apply = (values: FilterValues = draftRef.current) => {
    const next = new URLSearchParams(params);
    for (const k of keys) {
      const v = (values[k] ?? "").trim();
      if (v) next.set(k, v); else next.delete(k);
    }
    setParams(next, { replace: true });
  };
  const clear = () => {
    const empty = Object.fromEntries(keys.map((k) => [k, ""]));
    draftRef.current = empty;
    setDraft(empty);
    apply(empty);
  };
  const active = keys.some((k) => applied[k]);
  return { draft, set, applied, apply, clear, active };
}

/** Query string for the API from applied filters; date-time fields are sent as ISO 8601 (UTC). */
export function toQuery(values: FilterValues, dateKeys: string[] = ["since", "until"]): URLSearchParams {
  const q = new URLSearchParams();
  for (const [k, v] of Object.entries(values)) {
    if (!v) continue;
    if (dateKeys.includes(k)) {
      const d = new Date(v);
      if (!Number.isNaN(d.getTime())) q.set(k, d.toISOString());
    } else {
      q.set(k, v);
    }
  }
  return q;
}

/** A row of filter fields with Filter and Clear buttons; Enter in a field applies. */
export function FilterBar({ children, onApply, onClear, active, extra }: {
  children: ReactNode;
  onApply: () => void;
  onClear: () => void;
  active: boolean;
  extra?: ReactNode;
}) {
  return (
    <Stack direction="row" spacing={2} useFlexGap sx={{ alignItems: "flex-start", flexWrap: "wrap" }}
      onKeyDown={(e) => { if (e.key === "Enter") onApply(); }}>
      <ApplyNow.Provider value={onApply}>{children}</ApplyNow.Provider>
      <Button variant="outlined" onClick={onApply} sx={{ mt: 0.25 }} title="Show only what matches these filters">
        Filter
      </Button>
      <Button onClick={onClear} disabled={!active} startIcon={<FilterAltOffIcon />} sx={{ mt: 0.25 }}
        title="Clear every filter">
        Clear
      </Button>
      {extra}
    </Stack>
  );
}

/** From/to date-time fields (browser local time). */
export function DateRangeFields({ since, until, onChange }: {
  since: string;
  until: string;
  onChange: (key: "since" | "until", value: string) => void;
}) {
  return (
    <>
      <Field label="From" type="datetime-local" value={since} sx={{ width: 220 }} help="On or after"
        onChange={(e) => onChange("since", e.target.value)} slotProps={{ inputLabel: { shrink: true } }} />
      <Field label="To" type="datetime-local" value={until} sx={{ width: 220 }} help="On or before"
        onChange={(e) => onChange("until", e.target.value)} slotProps={{ inputLabel: { shrink: true } }} />
    </>
  );
}

/** A select with an "Any" first entry. */
export function SelectFilter({ label, value, options, onChange, width = 180, help }: {
  label: string;
  value: string;
  options: string[];
  onChange: (value: string) => void;
  width?: number;
  help?: string;
}) {
  const applyNow = useContext(ApplyNow);
  return (
    <Field select label={label} value={value} sx={{ width }} help={help}
      onChange={(e) => { onChange(e.target.value); applyNow?.(); }}>
      <MenuItem value="">Any</MenuItem>
      {options.map((o) => <MenuItem key={o} value={o}>{o}</MenuItem>)}
    </Field>
  );
}
