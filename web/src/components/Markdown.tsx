import { Fragment, ReactNode } from "react";
import { Box, Link, Paper, Table, TableBody, TableCell, TableHead, TableRow, Typography } from "@mui/material";

// A small Markdown renderer for the user guides (headings, paragraphs, lists, tables, code, quotes, and **bold**,
// *italic*, `code` and [links]). It builds React elements, never HTML, so a guide can't inject script.

export const slug = (s: string) => s.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "");

const safeHref = (h: string) => (/^(https?:|mailto:|#|\/)/.test(h) ? h : "");

function inline(text: string, key = ""): ReactNode[] {
  const out: ReactNode[] = [];
  const re = /(`[^`]+`)|(\*\*[^*]+\*\*)|(\*[^*\s][^*]*\*)|(\[[^\]]+\]\([^)]+\))/g;
  let last = 0;
  let m: RegExpExecArray | null;
  let i = 0;
  while ((m = re.exec(text))) {
    if (m.index > last) out.push(text.slice(last, m.index));
    const t = m[0];
    const k = `${key}-${i++}`;
    if (t.startsWith("`")) out.push(<code key={k} style={{ fontSize: "0.9em" }}>{t.slice(1, -1)}</code>);
    else if (t.startsWith("**")) out.push(<b key={k}>{inline(t.slice(2, -2), k)}</b>);
    else if (t.startsWith("*")) out.push(<i key={k}>{inline(t.slice(1, -1), k)}</i>);
    else {
      const [, label, href] = /\[([^\]]+)\]\(([^)]+)\)/.exec(t)!;
      const h = safeHref(href);
      out.push(h ? <Link key={k} href={h} target={h.startsWith("http") ? "_blank" : undefined} rel="noreferrer">{label}</Link> : <Fragment key={k}>{label}</Fragment>);
    }
    last = m.index + t.length;
  }
  if (last < text.length) out.push(text.slice(last));
  return out;
}

const cells = (line: string) => line.trim().replace(/^\|/, "").replace(/\|$/, "").split("|").map((c) => c.trim());
const ITEM = /^\s*([-*]|\d+\.)\s+/;

export default function Markdown({ text }: { text: string }) {
  const lines = text.replace(/\r\n/g, "\n").split("\n");
  const blocks: ReactNode[] = [];
  let i = 0;
  let n = 0;
  while (i < lines.length) {
    const line = lines[i];
    const key = `b${n++}`;
    if (!line.trim()) { i++; continue; }
    if (/^```/.test(line)) {
      const code: string[] = [];
      i++;
      while (i < lines.length && !/^```/.test(lines[i])) code.push(lines[i++]);
      i++;
      blocks.push(<Paper key={key} variant="outlined" sx={{ p: 1, my: 1, overflow: "auto" }}><pre style={{ margin: 0, fontSize: 12 }}>{code.join("\n")}</pre></Paper>);
      continue;
    }
    const h = /^(#{1,4})\s+(.*)$/.exec(line);
    if (h) {
      const level = h[1].length;
      blocks.push(<Typography key={key} id={slug(h[2])} variant={level === 1 ? "h4" : level === 2 ? "h5" : "h6"} component={`h${level}` as "h1"}
        sx={{ mt: level <= 2 ? 3 : 2, mb: 1, scrollMarginTop: 80 }}>{inline(h[2], key)}</Typography>);
      i++;
      continue;
    }
    if (line.trim().startsWith("|") && i + 1 < lines.length && /^\s*\|?\s*:?-{2,}/.test(lines[i + 1])) {
      const head = cells(line);
      const rows: string[][] = [];
      i += 2;
      while (i < lines.length && lines[i].trim().startsWith("|")) rows.push(cells(lines[i++]));
      blocks.push(
        <Box key={key} sx={{ overflowX: "auto", my: 1 }}>
          <Table size="small">
            <TableHead><TableRow>{head.map((c, j) => <TableCell key={j}><b>{inline(c, `${key}h${j}`)}</b></TableCell>)}</TableRow></TableHead>
            <TableBody>{rows.map((r, j) => <TableRow key={j}>{r.map((c, k) => <TableCell key={k}>{inline(c, `${key}r${j}${k}`)}</TableCell>)}</TableRow>)}</TableBody>
          </Table>
        </Box>);
      continue;
    }
    if (ITEM.test(line)) {
      const ordered = /^\s*\d+\./.test(line);
      const items: string[] = [];
      while (i < lines.length && (ITEM.test(lines[i]) || (/^\s{2,}\S/.test(lines[i]) && items.length))) {
        if (ITEM.test(lines[i])) items.push(lines[i].replace(ITEM, ""));
        else items[items.length - 1] += " " + lines[i].trim();
        i++;
      }
      const List = ordered ? "ol" : "ul";
      blocks.push(<List key={key} style={{ margin: "4px 0 8px", paddingLeft: 24 }}>{items.map((t, j) => <li key={j}><Typography variant="body2" component="span">{inline(t, `${key}i${j}`)}</Typography></li>)}</List>);
      continue;
    }
    if (line.startsWith(">")) {
      const q: string[] = [];
      while (i < lines.length && lines[i].startsWith(">")) q.push(lines[i++].replace(/^>\s?/, ""));
      blocks.push(<Box key={key} sx={{ borderLeft: 3, borderColor: "divider", pl: 2, my: 1, color: "text.secondary" }}><Typography variant="body2">{inline(q.join(" "), key)}</Typography></Box>);
      continue;
    }
    const para: string[] = [];
    while (i < lines.length && lines[i].trim() && !/^(#{1,4}\s|```|>|\|)/.test(lines[i]) && !ITEM.test(lines[i])) para.push(lines[i++]);
    if (!para.length) para.push(lines[i++]);
    blocks.push(<Typography key={key} variant="body2" sx={{ my: 1 }}>{inline(para.join(" "), key)}</Typography>);
  }
  return <Box>{blocks}</Box>;
}
