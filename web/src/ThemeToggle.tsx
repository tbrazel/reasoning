import { useEffect, useState } from "react";
import { applyColorMode } from "./appearance";

/** System-derived initial theme; explicit toggles persist across reloads. */
export function ThemeToggle() {
  const [dark, setDark] = useState(() => document.documentElement.dataset.theme === "dark");
  useEffect(() => {
    const observer = new MutationObserver(() => setDark(document.documentElement.dataset.theme === "dark"));
    observer.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
    return () => observer.disconnect();
  }, []);
  const label = dark ? "Switch to light mode" : "Switch to dark mode";
  return <button type="button" className="org-icon-button org-theme-toggle" aria-label={label} title={label}
    onClick={() => applyColorMode(dark ? "light" : "dark")}>
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" aria-hidden="true">
      <circle cx="12" cy="12" r="4" /><path d="M12 2v2m0 16v2M2 12h2m16 0h2M5 5l1.5 1.5m11 11L19 19M5 19l1.5-1.5m11-11L19 5" />
    </svg>
  </button>;
}
