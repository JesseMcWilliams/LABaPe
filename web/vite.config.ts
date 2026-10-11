import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// `npm run dev` proxies the API to a locally running `labape serve`.
export default defineConfig({
  plugins: [react()],
  // One bundle (~540 kB, ~170 kB gzipped) is fine for a LAN admin tool.
  build: { chunkSizeWarningLimit: 1000 },
  server: { proxy: { "/api": "http://localhost:8000" } },
});
