// Picks what goes in a catalog package: any number of services, applications and (one level deep)
// other packages, from the catalog, optionally narrowed to an OS family.
import { useState } from "react";
import { Autocomplete, Chip, Stack, TextField, ToggleButton, ToggleButtonGroup, Typography } from "@mui/material";
import type { CatalogItem } from "../pages/Catalog";

export interface Member { kind: string; name: string }
const KIND_LABEL: Record<string, string> = { service: "Services", application: "Applications", package: "Packages" };
const KIND_ORDER = ["service", "application", "package"];
const key = (m: Member) => `${m.kind}:${m.name}`;

export default function PackageMembers({ value, onChange, items, selfName, error }: {
  value: Member[]; onChange: (v: Member[]) => void; items: CatalogItem[]; selfName?: string; error?: string;
}) {
  const [family, setFamily] = useState<"" | "windows" | "linux">("");
  // What can go in: not retired, not this package, and not a package that already holds packages (one level only).
  const options = items
    .filter((i) => i.status !== "retired" && !(i.kind === "package" && i.name === selfName)
      && !(i.kind === "package" && ((i.definition.items as Member[] | undefined) ?? []).some((m) => m.kind === "package")))
    .filter((i) => !family || i.platforms.includes(family))
    .sort((a, b) => KIND_ORDER.indexOf(a.kind) - KIND_ORDER.indexOf(b.kind) || a.display_name.localeCompare(b.display_name));
  const byKey = new Map(items.map((i) => [key(i), i]));
  const picked = value.map((m) => byKey.get(key(m)) ?? ({ kind: m.kind, name: m.name, display_name: m.name, platforms: [], status: "draft" } as unknown as CatalogItem));
  const label = (i: CatalogItem) => `${i.display_name}${i.status === "draft" ? " (draft)" : ""}`;
  return (
    <Stack spacing={1}>
      <Stack direction="row" spacing={2} sx={{ alignItems: "center", flexWrap: "wrap" }}>
        <Typography variant="body2">Choose the services, applications and packages to include</Typography>
        <ToggleButtonGroup size="small" exclusive value={family} onChange={(_, v) => setFamily(v ?? "")}
          aria-label="Show only items for an OS">
          <ToggleButton value="" title="Every item">All OS</ToggleButton>
          <ToggleButton value="windows" title="Only items that install on Windows">Windows</ToggleButton>
          <ToggleButton value="linux" title="Only items that install on Linux">Linux</ToggleButton>
        </ToggleButtonGroup>
      </Stack>
      <Autocomplete multiple fullWidth size="small" options={options} value={picked} filterSelectedOptions disableCloseOnSelect
        groupBy={(i) => KIND_LABEL[i.kind] ?? i.kind} getOptionLabel={label}
        isOptionEqualToValue={(a, b) => key(a) === key(b)}
        onChange={(_, v) => onChange(v.map((i) => ({ kind: i.kind, name: i.name })))}
        renderValue={(vals, getItemProps) => vals.map((v, k) => {
          const { key: chipKey, ...chipProps } = getItemProps({ index: k });
          return <Chip key={chipKey} size="small" label={`${v.kind}: ${label(v)}`} {...chipProps} />;
        })}
        renderOption={(props, i) => (
          <li {...props} key={key(i)}>
            <Stack direction="row" spacing={1} sx={{ alignItems: "baseline", width: "100%" }}>
              <span>{label(i)}</span>
              <Typography variant="caption" color="text.secondary">{i.platforms.join(", ") || "no platform"}</Typography>
            </Stack>
          </li>
        )}
        renderInput={(params) => (
          <TextField {...params} label="Contents" error={Boolean(error)}
            helperText={error ?? (options.length ? "Type to search. Members for another OS are skipped on a host group of the other OS."
              : "Nothing in the catalog for this OS")} />
        )} />
    </Stack>
  );
}
