import { useState } from "react";
import {
  Box, Button, Dialog, DialogActions, DialogContent, DialogTitle, IconButton, Paper, Stack, Tooltip, Typography,
} from "@mui/material";
import ContentCopyIcon from "@mui/icons-material/ContentCopy";
import VisibilityIcon from "@mui/icons-material/Visibility";
import VisibilityOffIcon from "@mui/icons-material/VisibilityOff";
import ZoomInIcon from "@mui/icons-material/ZoomIn";
import { api, CredentialSection } from "../api";

// Tester credentials: each secret masked, with Show, Show large and Copy.
// Every reveal and copy is reported for the audit log.
export default function Credentials({ envId, sections }: { envId: number | string; sections: CredentialSection[] }) {
  const [shown, setShown] = useState<Record<string, boolean>>({});
  const [large, setLarge] = useState<{ label: string; value: string } | null>(null);
  const [copied, setCopied] = useState("");

  const report = (action: string, section: string, label: string) =>
    api.post(`/api/environments/${envId}/credentials/reveal`, { action, section, label }).catch(() => undefined);

  const copy = async (key: string, section: string, label: string, value: string) => {
    try {
      await navigator.clipboard.writeText(value);
    } catch {
      // Clipboard API needs HTTPS (or localhost); fall back to a hidden textarea.
      const ta = document.createElement("textarea");
      ta.value = value;
      document.body.appendChild(ta);
      ta.select();
      document.execCommand("copy");
      ta.remove();
    }
    setCopied(key);
    setTimeout(() => setCopied(""), 1500);
    report("copy", section, label);
  };

  if (!sections.length) return <Typography variant="body2">None stored.</Typography>;

  return (
    <Stack spacing={1.5}>
      {sections.map((s) => (
        <Paper key={s.title} variant="outlined" sx={{ p: 1.5 }}>
          <Typography variant="subtitle2" gutterBottom>{s.title}</Typography>
          {s.items.map((it) => {
            const key = `${s.title}/${it.label}`;
            const visible = !it.secret || shown[key];
            return (
              <Stack key={key} direction="row" spacing={1} sx={{ alignItems: "center", minHeight: 36 }}>
                <Typography variant="body2" color="text.secondary" sx={{ minWidth: 170 }}>{it.label}</Typography>
                <Typography variant="body2" sx={{ fontFamily: "monospace", flexGrow: 1, wordBreak: "break-all" }}>
                  {visible ? it.value : "••••••••••••"}
                </Typography>
                {it.secret && (
                  <Tooltip title={shown[key] ? "Hide" : "Show"}>
                    <IconButton size="small" aria-label={shown[key] ? "Hide" : "Show"} onClick={() => {
                      setShown({ ...shown, [key]: !shown[key] });
                      if (!shown[key]) report("show", s.title, it.label);
                    }}>
                      {shown[key] ? <VisibilityOffIcon fontSize="small" /> : <VisibilityIcon fontSize="small" />}
                    </IconButton>
                  </Tooltip>
                )}
                {it.secret && (
                  <Tooltip title="Show large">
                    <IconButton size="small" aria-label="Show large" onClick={() => {
                      setLarge({ label: `${s.title}: ${it.label}`, value: it.value });
                      report("show_large", s.title, it.label);
                    }}>
                      <ZoomInIcon fontSize="small" />
                    </IconButton>
                  </Tooltip>
                )}
                <Tooltip title={copied === key ? "Copied" : "Copy"}>
                  <IconButton size="small" aria-label="Copy" onClick={() => copy(key, s.title, it.label, it.value)}>
                    <ContentCopyIcon fontSize="small" color={copied === key ? "success" : undefined} />
                  </IconButton>
                </Tooltip>
              </Stack>
            );
          })}
          {s.notes.map((n, k) => (
            <Typography key={k} variant="caption" component="div" color="text.secondary" sx={{ fontFamily: "monospace" }}>
              {n}
            </Typography>
          ))}
        </Paper>
      ))}
      <Dialog open={Boolean(large)} onClose={() => setLarge(null)} maxWidth="md" fullWidth>
        <DialogTitle>{large?.label}</DialogTitle>
        <DialogContent>
          {/* Large, spaced, monospace: easy to read across a room or type by hand. */}
          <Box sx={{ fontFamily: "monospace", fontSize: "2.4rem", letterSpacing: "0.12em", textAlign: "center",
            wordBreak: "break-all", py: 3 }}>
            {large?.value}
          </Box>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setLarge(null)}>Close</Button>
        </DialogActions>
      </Dialog>
    </Stack>
  );
}
