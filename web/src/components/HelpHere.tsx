import { Link as RouterLink, useLocation } from "react-router-dom";
import { Box, Link } from "@mui/material";
import HelpOutlineIcon from "@mui/icons-material/HelpOutline";

// The Help section for each page (backlog row 24): the doc and a word or two of the heading to open, matched
// against the guide's headings on the Help page. The first prefix that matches the path wins.
const HELP_FOR: [string, "guide" | "admin", string][] = [
  ["/environment-templates", "guide", "Environment templates"],
  ["/environments/new", "guide", "Creating an environment"],
  ["/environments", "guide", "Working with an environment"],
  ["/jobs", "guide", "Jobs"],
  ["/files", "guide", "Files and templates"],
  ["/templates", "guide", "Files and templates"],
  ["/catalog", "guide", "The catalog"],
  ["/certificates", "guide", "Certificates"],
  ["/tokens", "guide", "API tokens"],
  ["/syslog", "admin", "The syslog collector"],
  ["/admin/hosts", "admin", "KVM hosts"],
  ["/admin/networks", "admin", "Networks"],
  ["/admin/settings", "admin", "Settings"],
  ["/admin/mail", "admin", "The stack's mail server"],
  ["/admin/secrets", "admin", "Secrets and rotation"],
  ["/admin/audit", "admin", "Audit log"],
  ["/admin/errors", "admin", "Audit log"],
  ["/admin/sessions", "admin", "Remote access"],
  ["/admin/links", "admin", "Linked stacks"],
  ["/admin/quotas", "admin", "Quotas, leases"],
  ["/", "guide", "The dashboard"],
];

export function helpTarget(path: string): { doc: string; find: string } | null {
  if (path === "/help" || path === "/login" || path === "/breakglass" || path.startsWith("/remote/")) return null;
  const hit = HELP_FOR.find(([prefix]) => (prefix === "/" ? path === "/" : path === prefix || path.startsWith(prefix + "/")));
  return hit ? { doc: hit[1], find: hit[2] } : { doc: "guide", find: "" };
}

/** A small "Help for this page" link above the page; it opens the Help page at the relevant section. */
export default function HelpHere() {
  const { pathname } = useLocation();
  const t = helpTarget(pathname);
  if (!t) return null;
  return (
    <Box sx={{ position: "absolute", top: 4, right: 24, zIndex: 1 }}>
      <Link component={RouterLink} to={`/help?doc=${t.doc}${t.find ? `&find=${encodeURIComponent(t.find)}` : ""}`} variant="body2" underline="hover"
        title="Open the Help section for this page" sx={{ display: "inline-flex", alignItems: "center", gap: 0.5 }}>
        <HelpOutlineIcon sx={{ fontSize: 16 }} /> Help for this page
      </Link>
    </Box>
  );
}
