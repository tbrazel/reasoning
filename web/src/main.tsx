import React from "react";
import { createRoot } from "react-dom/client";
import "@fontsource/ibm-plex-sans/latin-400.css";
import "@fontsource/ibm-plex-sans/latin-400-italic.css";
import "@fontsource/ibm-plex-sans/latin-500.css";
import "@fontsource/ibm-plex-sans/latin-600.css";
import "@fontsource/ibm-plex-sans/latin-700.css";
import "@fontsource/ibm-plex-mono/latin-400.css";
import "@fontsource/ibm-plex-mono/latin-500.css";
import "@fontsource/ibm-plex-mono/latin-600.css";
import "@fontsource/ibm-plex-mono/latin-700.css";
import { App } from "./App";
import { applyAppearance, readAppearance, initializeColorMode } from "./appearance";
import { warmRichText } from "./Markdown";
import { isUxLabPath } from "./uxLabRoute";
import "./styles.css";

applyAppearance(readAppearance());
initializeColorMode();

// Platform-specific chrome (the macOS translucent rail and Explorer) keys
// off this attribute; in a browser there is no desktop bridge and it stays
// unset, so every such rule is inert.
const desktopPlatform = window.reasoningDesktop?.platform;
if (desktopPlatform) {
  document.documentElement.dataset.platform = desktopPlatform;
}

const uxLab = isUxLabPath(window.location.pathname);
// UX Lab renders no transcript, so it does not pay for the renderer chunk.
if (!uxLab) warmRichText();
const UxLab = React.lazy(() => import("./UxLab"));

createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    {uxLab ? (
      <React.Suspense fallback={<div className="app-loading">Opening UX Lab…</div>}>
        <UxLab />
      </React.Suspense>
    ) : <App />}
  </React.StrictMode>,
);
