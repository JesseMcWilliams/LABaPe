// Installer OS targets (design §28.2, question 112): generic (Any, Windows,
// Linux), families and specific OSes, from GET /api/files/targets.
import { useEffect, useState } from "react";
import { Autocomplete, Chip, Stack, TextField } from "@mui/material";
import { api } from "../api";

export interface TargetOption { value: string; label: string; group: string }

let cache: TargetOption[] | null = null;

export function useTargetVocabulary(): TargetOption[] {
  const [vocab, setVocab] = useState<TargetOption[]>(cache ?? []);
  useEffect(() => {
    if (cache) return;
    api.get<TargetOption[]>("/api/files/targets").then((v) => { cache = v; setVocab(v); }, () => undefined);
  }, []);
  return vocab;
}

const label = (vocab: TargetOption[], value: string) => vocab.find((t) => t.value === value)?.label ?? value;

export function TargetChips({ targets, vocab }: { targets: string[]; vocab: TargetOption[] }) {
  if (!targets.length) return <Chip size="small" color="warning" label="not set" title="Deploys won't use this installer until its targets are set" />;
  return (
    <Stack direction="row" spacing={0.5} useFlexGap sx={{ flexWrap: "wrap" }}>
      {targets.map((t) => <Chip key={t} size="small" label={label(vocab, t)} variant={t === "any" ? "outlined" : "filled"} />)}
    </Stack>
  );
}

/** Pick any mix of generic and specific targets. */
export function TargetPicker({ value, onChange, error, disabled }: {
  value: string[];
  onChange: (value: string[]) => void;
  error?: string;
  disabled?: boolean;
}) {
  const vocab = useTargetVocabulary();
  const selected = vocab.filter((t) => value.includes(t.value));
  return (
    <Autocomplete multiple disabled={disabled} options={vocab} value={selected} groupBy={(o) => o.group}
      getOptionLabel={(o) => o.label} isOptionEqualToValue={(a, b) => a.value === b.value}
      onChange={(_, v) => onChange(v.map((o) => o.value))}
      renderInput={(params) => (
        <TextField {...params} size="small" label="Targets" error={Boolean(error)}
          helperText={error || "Where deploys may use it: Any OS, Windows or Linux, a family, or specific OSes"} />
      )} />
  );
}
