import { createHash, randomUUID } from "node:crypto";
import { constants } from "node:fs";
import { lstat, open, rename, unlink } from "node:fs/promises";
import path from "node:path";

export const PICKER_URL = "reasoning-app://connection/index.html";

export function serverOrigin(value) {
  const error = () =>
    new TypeError(
      "Enter an HTTPS server URL without a path, credentials, query, or fragment. HTTP is allowed only on localhost.",
    );
  if (
    typeof value !== "string" ||
    value.length > 2048 ||
    /[\u0000-\u001f\u007f\\]/u.test(value)
  )
    throw error();
  let url;
  try {
    url = new URL(value.trim());
  } catch {
    throw error();
  }
  if (
    url.username ||
    url.password ||
    url.pathname !== "/" ||
    url.search ||
    url.hash
  )
    throw error();
  const loopback = ["localhost", "127.0.0.1", "[::1]"].includes(url.hostname);
  if (url.protocol !== "https:" && !(url.protocol === "http:" && loopback))
    throw error();
  return url.origin;
}

export function serverPartition(origin) {
  return (
    "persist:reasoning-server-" +
    createHash("sha256").update(serverOrigin(origin)).digest("hex")
  );
}

export function sameServer(url, origin) {
  try {
    const target = new URL(url);
    return (
      !target.username &&
      !target.password &&
      ["http:", "https:"].includes(target.protocol) &&
      target.origin === origin
    );
  } catch {
    return false;
  }
}

export function externalLink(value) {
  if (
    typeof value !== "string" ||
    value.length > 4096 ||
    /[\u0000-\u001f\u007f\\]/u.test(value)
  )
    return null;
  try {
    const url = new URL(value);
    return ["http:", "https:"].includes(url.protocol) &&
      !url.username &&
      !url.password
      ? url.href
      : null;
  } catch {
    return null;
  }
}

export function trustedPicker(event, contents) {
  return (
    !!contents &&
    !contents.isDestroyed() &&
    event.sender === contents &&
    event.senderFrame === contents.mainFrame &&
    event.senderFrame?.url === PICKER_URL
  );
}

export function remotePreferences(origin) {
  return {
    partition: serverPartition(origin),
    contextIsolation: true,
    nodeIntegration: false,
    nodeIntegrationInWorker: false,
    nodeIntegrationInSubFrames: false,
    sandbox: true,
    webSecurity: true,
    allowRunningInsecureContent: false,
    webviewTag: false,
    navigateOnDragDrop: false,
  };
}

export async function readConnection(directory) {
  const filename = path.join(directory, "connection.json");
  try {
    const metadata = await lstat(filename);
    if (
      !metadata.isFile() ||
      metadata.size > 4096 ||
      metadata.mode & 0o077 ||
      (process.getuid && metadata.uid !== process.getuid())
    )
      return null;
    const handle = await open(
      filename,
      constants.O_RDONLY | constants.O_NOFOLLOW,
    );
    try {
      return serverOrigin(JSON.parse(await handle.readFile("utf8")).origin);
    } finally {
      await handle.close();
    }
  } catch {
    return null;
  }
}

export async function saveConnection(directory, origin) {
  const filename = path.join(directory, "connection.json");
  const temporary = path.join(directory, ".connection-" + randomUUID());
  try {
    const handle = await open(temporary, "wx", 0o600);
    try {
      await handle.writeFile(
        JSON.stringify({ origin: serverOrigin(origin) }) + "\n",
      );
      await handle.sync();
    } finally {
      await handle.close();
    }
    await rename(temporary, filename);
  } finally {
    await unlink(temporary).catch((error) => {
      if (error.code !== "ENOENT") throw error;
    });
  }
}
