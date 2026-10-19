// Shared form behavior: aligned fields, clear field-level errors, and
// reporting blocked submits so admins can see where users struggle.
import { ReactNode } from "react";
import { Alert, AlertTitle, Box, TextField, TextFieldProps } from "@mui/material";
import ErrorOutlineIcon from "@mui/icons-material/ErrorOutline";
import WarningAmberIcon from "@mui/icons-material/WarningAmber";
import { api, ApiError, FieldProblem } from "./api";

export type FieldErrors = Record<string, FieldProblem>;

export const toFieldErrors = (problems: FieldProblem[]): FieldErrors =>
  Object.fromEntries(problems.map((p) => [p.field, p]));

/** Server error -> {summary, fields}. */
export function fromApiError(err: unknown): { summary: string; fields: FieldErrors } {
  if (err instanceof ApiError) return { summary: err.message, fields: toFieldErrors(err.errors) };
  return { summary: (err as Error).message ?? String(err), fields: {} };
}

/** Tell the server which fields the browser refused (admin Errors page). */
export function reportBlocked(page: string, problems: FieldProblem[]) {
  if (problems.length) api.post("/api/errors/report", { page, fields: problems }).catch(() => undefined);
}

function HelperLine({ icon, text, color }: { icon: ReactNode; text: string; color: string }) {
  return (
    <Box component="span" sx={{ display: "inline-flex", alignItems: "flex-start", gap: 0.5, color }}>
      {icon}
      <span>{text}</span>
    </Box>
  );
}

/**
 * A TextField that always reserves its helper line (so a row of fields
 * stays aligned whether or not any of them has help or an error) and shows
 * an error in red with an icon, or a warning in amber.
 */
export function Field({ error, warning, help, ...props }: Omit<TextFieldProps, "error"> & {
  error?: FieldProblem | string;
  warning?: string;
  help?: string;
}) {
  const message = typeof error === "string" ? error : error?.message;
  let helper: ReactNode = help || " ";
  if (message) {
    helper = <HelperLine icon={<ErrorOutlineIcon sx={{ fontSize: 16, mt: "1px" }} />} text={message} color="error.main" />;
  } else if (warning) {
    helper = <HelperLine icon={<WarningAmberIcon sx={{ fontSize: 16, mt: "1px" }} />} text={warning} color="warning.main" />;
  }
  return (
    <TextField size="small" {...props} error={Boolean(message)}
      color={!message && warning ? "warning" : props.color} focused={!message && warning ? true : props.focused}
      helperText={helper}
      slotProps={{ ...props.slotProps, formHelperText: { sx: { mx: 0.5, maxWidth: 360, minHeight: "1.25em" } } }} />
  );
}

/** Summary at the top of a form: the server's message plus each field's problem. */
export function ErrorSummary({ summary, fields }: { summary: string; fields: FieldErrors }) {
  if (!summary && Object.keys(fields).length === 0) return null;
  const list = Object.values(fields);
  return (
    <Alert severity="error" icon={<ErrorOutlineIcon />}>
      <AlertTitle>{list.length > 1 ? "Some fields need fixing" : "This couldn't be saved"}</AlertTitle>
      {list.length === 0 && summary}
      {list.length > 0 && (
        <Box component="ul" sx={{ m: 0, pl: 2 }}>
          {list.map((p) => <li key={p.field}><b>{p.label}</b>: {p.message}</li>)}
        </Box>
      )}
    </Alert>
  );
}

// --- client-side rules (the same rules the server applies) -------------------

export const rules = {
  envName: (v: string) =>
    /^[a-z][a-z0-9-]{2,39}$/.test(v) && !v.startsWith("test-") ? "" :
      "Use 3-40 lower-case letters, digits and hyphens, starting with a letter. Names starting with test- are reserved.",
  groupName: (v: string) => /^[A-Za-z][A-Za-z0-9]{0,11}$/.test(v) ? "" :
    "Use 1-12 letters and digits, starting with a letter.",
  hostName: (v: string) => !v || /^[A-Za-z][A-Za-z0-9-]{0,14}$/.test(v) ? "" :
    "Use 1-15 letters, digits and hyphens, starting with a letter (Windows limits names to 15).",
  intRange: (lo: number, hi: number, unit = "") => (v: number) =>
    Number.isInteger(v) && v >= lo && v <= hi ? "" : `Use a whole number${unit ? ` of ${unit}` : ""} from ${lo} to ${hi}.`,
  netName: (v: string) => /^[a-z][a-z0-9-]{0,62}$/.test(v) ? "" :
    "Use 1-63 lower-case letters, digits and hyphens, starting with a letter.",
  cidr: (v: string) => /^(\d{1,3}\.){3}\d{1,3}\/\d{1,2}$/.test(v.trim()) ? "" :
    "Use an IPv4 network such as 172.21.48.0/22.",
  ip: (v: string) => !v || /^(\d{1,3}\.){3}\d{1,3}$/.test(v.trim()) ? "" : "Use an IPv4 address such as 172.21.48.1.",
  ranges: (list: string[]) => list.every((r) => /^(\d{1,3}\.){3}\d{1,3}(-(\d{1,3}\.){3}\d{1,3})?$/.test(r)) ? "" :
    "Use addresses or ranges such as 172.21.50.1-172.21.51.254, separated by commas.",
  bridge: (v: string) => /^[A-Za-z0-9_.-]{1,15}$/.test(v) ? "" :
    "Use 1-15 letters, digits, dots, underscores or hyphens (a Linux interface name).",
};

/** Collect non-empty rule results into FieldProblems. */
export function check(entries: [string, string, string][]): FieldProblem[] {
  return entries.filter(([, , msg]) => msg).map(([field, label, message]) => ({ field, label, message }));
}
