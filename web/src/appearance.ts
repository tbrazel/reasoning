export const APPEARANCE_STORAGE_KEY = "reasoning.appearance";
export const COLOR_MODE_STORAGE_KEY = "reasoning.color-mode";
export type ColorMode = "system" | "light" | "dark";
export function readColorMode(): ColorMode {
  try { const mode = localStorage.getItem(COLOR_MODE_STORAGE_KEY); return mode === "light" || mode === "dark" ? mode : "system"; }
  catch { return "system"; }
}
export function applyColorMode(mode: ColorMode): void {
  document.documentElement.dataset.colorMode = mode;
  document.documentElement.dataset.theme = mode === "system" ? (window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light") : mode;
  try { localStorage.setItem(COLOR_MODE_STORAGE_KEY, mode); } catch { /* Storage is optional. */ }
}
export function initializeColorMode(): void {
  applyColorMode(readColorMode());
  window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => applyColorMode(readColorMode()));
  window.addEventListener("storage", event => { if (event.key === COLOR_MODE_STORAGE_KEY) applyColorMode(readColorMode()); });
}

export const APPEARANCE_PRESETS = [
  {
    id: "studio",
    name: "Studio",
    description: "Neutral, restrained, and focused on the work in front of you.",
    swatches: ["#101112", "#1a1c1e", "#70c5ad"],
  },
  {
    id: "ink",
    name: "Ink",
    description: "A warmer, bookish dark palette for long research sessions.",
    swatches: ["#171411", "#231f1a", "#d6ad73"],
  },
  {
    id: "teal",
    name: "Teal",
    description: "Deep teal surfaces with restrained research accents.",
    swatches: ["#041c1c", "#0a3030", "#34d399"],
  },
] as const;

export type AppearancePreset = (typeof APPEARANCE_PRESETS)[number]["id"];

export const DEFAULT_APPEARANCE: AppearancePreset = "studio";

export function isAppearancePreset(value: unknown): value is AppearancePreset {
  return APPEARANCE_PRESETS.some((preset) => preset.id === value);
}

export function readAppearance(): AppearancePreset {
  try {
    const saved = localStorage.getItem(APPEARANCE_STORAGE_KEY);
    return isAppearancePreset(saved) ? saved : DEFAULT_APPEARANCE;
  } catch {
    return DEFAULT_APPEARANCE;
  }
}

export function applyAppearance(preset: AppearancePreset): void {
  document.documentElement.dataset.appearance = preset;
  try {
    localStorage.setItem(APPEARANCE_STORAGE_KEY, preset);
  } catch {
    // A restricted webview may disable storage; the current document still
    // receives the selected appearance for the remainder of the session.
  }
}
