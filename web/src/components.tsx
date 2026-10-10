import { Chip } from "@mui/material";

const colors: Record<string, "default" | "primary" | "success" | "error" | "warning" | "info"> = {
  queued: "info",
  running: "primary",
  deploying: "primary",
  destroying: "warning",
  succeeded: "success",
  deployed: "success",
  failed: "error",
  cancelled: "default",
  destroyed: "default",
  new: "default",
};

export function StateChip({ state }: { state: string }) {
  return <Chip size="small" label={state} color={colors[state] ?? "default"} />;
}
