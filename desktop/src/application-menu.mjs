export function macApplicationMenuTemplate({ appName, reopen }) {
  if (!appName || typeof reopen !== "function") {
    throw new TypeError("macOS application menu requires its label and actions");
  }
  const openItem = (accelerator) => ({
    label: `Open ${appName}`,
    ...(accelerator ? { accelerator } : {}),
    click: reopen,
  });
  return [
    {
      label: appName,
      submenu: [
        { role: "about" },
        { type: "separator" },
        openItem(),
        { type: "separator" },
        { role: "services" },
        { type: "separator" },
        { role: "hide" },
        { role: "hideOthers" },
        { role: "unhide" },
        { type: "separator" },
        { role: "quit" },
      ],
    },
    { label: "File", submenu: [openItem("CommandOrControl+0"), { type: "separator" }, { role: "close" }] },
    { role: "editMenu" },
    { role: "viewMenu" },
    { label: "Window", submenu: [openItem(), { type: "separator" }, { role: "minimize" }, { role: "zoom" }, { role: "front" }] },
  ];
}
