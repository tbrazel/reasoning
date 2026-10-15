const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld(
  "reasoningConnection",
  Object.freeze({
    status: () => ipcRenderer.invoke("reasoning:connection-status"),
    connect: (origin) => ipcRenderer.invoke("reasoning:connect", origin),
  }),
);
