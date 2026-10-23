import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import { CssBaseline, ThemeProvider, createTheme } from "@mui/material";
import App from "./App";
import { ConfirmProvider } from "./components/Confirm";

// A bare <a> would use the browser's default blue, unreadable on the dark theme (about 1.7:1): give it the theme's
// primary colour, which has the contrast in both schemes. MUI's own Link carries a class and is left alone.
const theme = createTheme({
  colorSchemes: { dark: true },
  components: {
    MuiCssBaseline: {
      styleOverrides: (t) => {
        const color = (t.vars ?? t).palette.primary.main;
        return { "a:not([class]), a:not([class]):visited": { color } };
      },
    },
  },
});

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <ThemeProvider theme={theme}>
      <CssBaseline />
      <BrowserRouter>
        <ConfirmProvider>
          <App />
        </ConfirmProvider>
      </BrowserRouter>
    </ThemeProvider>
  </StrictMode>,
);
