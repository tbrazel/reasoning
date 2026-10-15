import { contextBridge, ipcRenderer } from "electron";

contextBridge.exposeInMainWorld("reasoningDesktop", Object.freeze({
  platform: process.platform,
  revealArtifact(projectId, artifactPath) {
    return ipcRenderer.invoke("reasoning:reveal-artifact", projectId, artifactPath);
  },
}));
