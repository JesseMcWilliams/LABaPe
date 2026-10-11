import { createContext, ReactNode, useCallback, useContext, useRef, useState } from "react";
import {
  Box, Button, ButtonProps, Dialog, DialogActions, DialogContent, DialogContentText, DialogTitle, IconButton,
  IconButtonProps, Tooltip,
} from "@mui/material";
import WarningAmberIcon from "@mui/icons-material/WarningAmber";
import { useMaintenance } from "./Maintenance";

// Confirmations and tooltips for every action (design §23): hovering an
// action shows a short summary (and says when it asks to confirm); an
// action that changes something first opens a dialog with a fuller note
// and Continue / Cancel.

export interface ConfirmOptions {
  title: string;
  body: ReactNode;
  confirmLabel?: string;       // default "Continue"
  danger?: boolean;            // red Continue button and a warning icon
}

type ConfirmFn = (o: ConfirmOptions) => Promise<boolean>;
const ConfirmContext = createContext<ConfirmFn>(async () => true);
export const useConfirm = () => useContext(ConfirmContext);

export function ConfirmProvider({ children }: { children: ReactNode }) {
  const [open, setOpen] = useState<ConfirmOptions | null>(null);
  const resolver = useRef<((ok: boolean) => void) | undefined>(undefined);
  const confirm = useCallback<ConfirmFn>((o) => new Promise((resolve) => {
    resolver.current = resolve;
    setOpen(o);
  }), []);
  const close = (ok: boolean) => {
    resolver.current?.(ok);
    setOpen(null);
  };
  return (
    <ConfirmContext.Provider value={confirm}>
      {children}
      <Dialog open={Boolean(open)} onClose={() => close(false)} maxWidth="sm" fullWidth>
        {open && (
          <>
            <DialogTitle sx={{ display: "flex", alignItems: "center", gap: 1 }}>
              {open.danger && <WarningAmberIcon color="error" />}{open.title}
            </DialogTitle>
            <DialogContent>
              {typeof open.body === "string" ? <DialogContentText>{open.body}</DialogContentText> : open.body}
            </DialogContent>
            <DialogActions>
              <Button onClick={() => close(false)} autoFocus>Cancel</Button>
              <Button variant="contained" color={open.danger ? "error" : "primary"} onClick={() => close(true)}>
                {open.confirmLabel ?? "Continue"}
              </Button>
            </DialogActions>
          </>
        )}
      </Dialog>
    </ConfirmContext.Provider>
  );
}

/** The tooltip text: the summary, plus a note when a confirmation follows. */
const tipText = (tip: string, confirm?: ConfirmOptions) => (confirm ? `${tip} (asks to confirm)` : tip);

/**
 * A button with a short tooltip. With `confirm`, clicking opens the
 * confirmation dialog first and runs `onClick` only after Continue.
 */
export function ActionButton({ tip, confirm, onClick, children, allowInMaintenance, ...props }: Omit<ButtonProps, "onClick"> & {
  tip: string;
  confirm?: ConfirmOptions;
  onClick?: () => unknown;
  allowInMaintenance?: boolean;
}) {
  const ask = useConfirm();
  const paused = Boolean(useMaintenance()?.active) && Boolean(confirm || props.type === "submit") && !allowInMaintenance;
  if (paused) {
    tip = "Unavailable during maintenance";
    props = { ...props, disabled: true };
  }
  const run = async () => {
    if (confirm && !(await ask(confirm))) return;
    await onClick?.();
  };
  return (
    <Tooltip title={tipText(tip, confirm)} arrow>
      {/* A span keeps the tooltip working on a disabled button. */}
      <Box component="span" sx={{ display: "inline-flex" }}>
        <Button {...props} onClick={onClick || confirm ? run : undefined}>{children}</Button>
      </Box>
    </Tooltip>
  );
}

export function ActionIconButton({ tip, confirm, onClick, children, ...props }: Omit<IconButtonProps, "onClick"> & {
  tip: string;
  confirm?: ConfirmOptions;
  onClick?: () => unknown;
}) {
  const ask = useConfirm();
  if (useMaintenance()?.active && confirm) {
    tip = "Unavailable during maintenance";
    props = { ...props, disabled: true };
  }
  const run = async () => {
    if (confirm && !(await ask(confirm))) return;
    await onClick?.();
  };
  return (
    <Tooltip title={tipText(tip, confirm)} arrow>
      <Box component="span" sx={{ display: "inline-flex" }}>
        <IconButton aria-label={tip} {...props} onClick={run}>{children}</IconButton>
      </Box>
    </Tooltip>
  );
}
