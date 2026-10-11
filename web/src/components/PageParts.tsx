import { ReactNode } from "react";
import { Link as RouterLink } from "react-router-dom";
import { Box, Button, Link, Paper, Stack, Table, TableBody, TableCell, TableRow, Typography } from "@mui/material";
import ArrowBackIcon from "@mui/icons-material/ArrowBack";

// The list-and-detail pattern shared by Environments, Hosts, Networks, the
// catalog, files and templates (design §22.2): a list with Add at the top
// right, and the name opening the entry's own page.

export function ListHeader({ title, intro, addTo, addLabel, canAdd, children }: {
  title: string; intro?: ReactNode; addTo?: string; addLabel?: string; canAdd?: boolean; children?: ReactNode;
}) {
  return (
    <Stack spacing={1}>
      <Stack direction="row" spacing={1} sx={{ alignItems: "center" }}>
        <Typography variant="h5">{title}</Typography>
        <Box sx={{ flexGrow: 1 }} />
        {children}
        {canAdd && addTo && (
          <Button variant="contained" component={RouterLink} to={addTo}
            title={`${addLabel ?? "Add"}: opens the form; nothing is saved until you confirm there`}>{addLabel ?? "Add"}</Button>
        )}
      </Stack>
      {intro && <Typography variant="body2" color="text.secondary">{intro}</Typography>}
    </Stack>
  );
}

export function DetailHeader({ title, back, backLabel, actions }: {
  title: ReactNode; back: string; backLabel: string; actions?: ReactNode;
}) {
  return (
    <Stack spacing={0.5}>
      <Link component={RouterLink} to={back} underline="hover" variant="body2" title={`Back to ${backLabel}`}
        sx={{ display: "inline-flex", alignItems: "center", gap: 0.5 }}>
        <ArrowBackIcon sx={{ fontSize: 16 }} /> {backLabel}
      </Link>
      <Stack direction="row" spacing={1} sx={{ alignItems: "center" }}>
        <Typography variant="h5">{title}</Typography>
        <Box sx={{ flexGrow: 1 }} />
        {actions}
      </Stack>
    </Stack>
  );
}

/** Read-only label/value rows, for people who may see an entry but not edit it. */
export function Facts({ rows }: { rows: [string, ReactNode][] }) {
  return (
    <Paper variant="outlined">
      <Table size="small">
        <TableBody>
          {rows.map(([label, value]) => (
            <TableRow key={label}>
              <TableCell sx={{ width: 220, color: "text.secondary" }}>{label}</TableCell>
              <TableCell>{value === "" || value === null || value === undefined ? "—" : value}</TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </Paper>
  );
}
