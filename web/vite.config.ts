import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { readFileSync } from "node:fs";

// The offline recording player (linked from Remote sessions): guac-player.template.html with guacamole-common-js
// inlined, so one file plays a downloaded .guac recording on any computer.
function guacPlayer() {
  const lib = () => readFileSync("node_modules/guacamole-common-js/dist/cjs/guacamole-common.min.js", "utf8");
  const page = () => readFileSync("guac-player.template.html", "utf8").replace("/*GUAC_LIB*/", () => lib().replace(/<\/script/gi, "<\\/script"));
  return {
    name: "labape-guac-player",
    generateBundle() { this.emitFile({ type: "asset", fileName: "guac-player.html", source: page() }); },
    configureServer(server: { middlewares: { use: (f: (req: { url?: string }, res: { setHeader: (k: string, v: string) => void; end: (b: string) => void }, next: () => void) => void) => void } }) {
      server.middlewares.use((req, res, next) => {
        if (req.url?.startsWith("/guac-player.html")) { res.setHeader("Content-Type", "text/html"); res.end(page()); } else next();
      });
    },
  };
}

// `npm run dev` proxies the API to a locally running `labape serve`.
export default defineConfig({
  plugins: [react(), guacPlayer()],
  // One bundle (~540 kB, ~170 kB gzipped) is fine for a LAN admin tool.
  build: { chunkSizeWarningLimit: 1000 },
  server: { proxy: { "/api": "http://localhost:8000" } },
});
