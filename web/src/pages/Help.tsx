import { useEffect, useMemo, useState } from "react";
import { Link as RouterLink, useSearchParams } from "react-router-dom";
import { Alert, Box, Link, List, ListItemButton, ListItemText, Paper, Stack, Tab, Tabs, Typography } from "@mui/material";
import { api } from "../api";
import Markdown, { slug } from "../components/Markdown";

// Help (backlog row 24): the user guides, served by the stack. ?doc=guide|admin|cli picks the guide;
// ?find=<words> opens the first section whose heading contains them.
interface DocInfo { id: string; title: string }
interface Doc { id: string; title: string; markdown: string }

export default function Help() {
  const [params, setParams] = useSearchParams();
  const [docs, setDocs] = useState<DocInfo[]>([]);
  const [doc, setDoc] = useState<Doc | null>(null);
  const [error, setError] = useState("");
  useEffect(() => { api.get<DocInfo[]>("/api/help").then(setDocs, (e) => setError(e.message)); }, []);
  const id = params.get("doc") && docs.some((d) => d.id === params.get("doc")) ? params.get("doc")! : docs[0]?.id;
  const find = params.get("find") ?? "";
  useEffect(() => {
    if (!id) return;
    setDoc(null);
    api.get<Doc>(`/api/help/${id}`).then(setDoc, (e) => setError(e.message));
  }, [id]);
  const sections = useMemo(() => (doc?.markdown ?? "").split("\n").filter((l) => /^## /.test(l)).map((l) => l.replace(/^## /, "")), [doc]);
  // Scroll to the section asked for (by words in its heading) once the guide is on the page.
  useEffect(() => {
    if (!doc) return;
    const hit = find ? sections.find((s) => s.toLowerCase().includes(find.toLowerCase())) : undefined;
    if (hit) setTimeout(() => document.getElementById(slug(hit))?.scrollIntoView({ block: "start" }), 50);
    else window.scrollTo(0, 0);
  }, [doc, find, sections]);
  const go = (s: string) => { setParams({ doc: id!, find: s.replace(/^\d+\.\s*/, "") }, { replace: true }); };
  return (
    <Stack spacing={2}>
      <Typography variant="h5">Help</Typography>
      {error && <Alert severity="error">{error}</Alert>}
      {docs.length > 1 && (
        <Tabs value={id ?? false} onChange={(_, v) => setParams({ doc: v })}>
          {docs.map((d) => <Tab key={d.id} value={d.id} label={d.title} />)}
        </Tabs>
      )}
      <Stack direction="row" spacing={3} sx={{ alignItems: "flex-start" }}>
        <Paper variant="outlined" sx={{ width: 280, flexShrink: 0, position: "sticky", top: 72, maxHeight: "calc(100vh - 100px)", overflow: "auto", display: { xs: "none", md: "block" } }}>
          <List dense>
            {sections.map((s) => (
              <ListItemButton key={s} onClick={() => go(s)} selected={!!find && s.toLowerCase().includes(find.toLowerCase())}>
                <ListItemText primary={s} />
              </ListItemButton>
            ))}
          </List>
        </Paper>
        <Box sx={{ flexGrow: 1, minWidth: 0, maxWidth: 980 }}>
          {doc ? <Markdown text={doc.markdown} /> : !error && <Typography color="text.secondary">Loading…</Typography>}
          <Typography variant="caption" color="text.secondary" component="div" sx={{ mt: 3 }}>
            These guides ship with LABaPe and match this release. <Link component={RouterLink} to="/">Back to the dashboard</Link>.
          </Typography>
        </Box>
      </Stack>
    </Stack>
  );
}
