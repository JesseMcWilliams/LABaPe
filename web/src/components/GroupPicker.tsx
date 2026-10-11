import { useEffect, useState } from "react";
import { Autocomplete, Chip, TextField } from "@mui/material";
import { api, FieldProblem } from "../api";

interface GroupSearch { source: "authentik" | "known"; groups: string[]; warning: string }

/**
 * Pick Authentik groups by type-ahead search (design §21.6). Suggestions
 * come from Authentik when a directory token is set, else from groups seen
 * at sign-in; any name can still be typed and added with Enter.
 */
export default function GroupPicker({ label, value, onChange, help, error }: {
  label: string;
  value: string[];
  onChange: (groups: string[]) => void;
  help?: string;
  error?: FieldProblem | string;
}) {
  const [input, setInput] = useState("");
  const [options, setOptions] = useState<string[]>([]);
  const [note, setNote] = useState("");
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    let live = true;
    setLoading(true);
    const t = setTimeout(() => {
      api.get<GroupSearch>(`/api/directory/groups?q=${encodeURIComponent(input.trim())}`).then((r) => {
        if (!live) return;
        setOptions(r.groups);
        setNote(r.warning || (r.source === "known" ? "Suggestions: groups seen at sign-in" : "Suggestions from Authentik"));
      }, () => live && setNote("Group search unavailable; type names and press Enter")).finally(() => live && setLoading(false));
    }, 250);
    return () => {
      live = false;
      clearTimeout(t);
    };
  }, [input]);

  const message = typeof error === "string" ? error : error?.message;
  return (
    <Autocomplete multiple freeSolo size="small" sx={{ minWidth: 300, flex: 1 }} filterSelectedOptions
      options={options} value={value} loading={loading} inputValue={input}
      onInputChange={(_, v) => setInput(v)}
      onChange={(_, v) => onChange([...new Set(v.map((x) => x.trim()).filter(Boolean))])}
      renderValue={(vals, getItemProps) => vals.map((v, k) => {
        const { key, ...chipProps } = getItemProps({ index: k });
        return <Chip key={key} size="small" label={v} {...chipProps} />;
      })}
      renderInput={(params) => (
        <TextField {...params} label={label} error={Boolean(message)}
          helperText={message ?? ([help, note].filter(Boolean).join(". ") || " ")} />
      )} />
  );
}
