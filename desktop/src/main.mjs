// Public server chat is the default. Local filesystem/execution capabilities
// belong only to the explicitly selected, separate workbench process.
if (process.env.REASONING_DESKTOP_MODE === "local-workbench") {
  await import("./local-main.mjs");
} else if (
  process.env.REASONING_DESKTOP_MODE &&
  process.env.REASONING_DESKTOP_MODE !== "organization"
) {
  throw new Error(
    "REASONING_DESKTOP_MODE must be organization or local-workbench",
  );
} else {
  await import("./organization-host.mjs");
}
