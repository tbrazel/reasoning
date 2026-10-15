import assert from "node:assert/strict";
import { test } from "node:test";

import { macApplicationMenuTemplate } from "../src/application-menu.mjs";

test("macOS menu can restore a closed Reasoning window", () => {
  const calls = [];
  const reopen = () => calls.push("reopen");
  const menu = macApplicationMenuTemplate({ appName: "Reasoning", reopen });

  const appOpen = menu[0].submenu.find((item) => item.label === "Open Reasoning");
  const fileOpen = menu[1].submenu[0];
  const windowOpen = menu[4].submenu[0];
  assert.equal(fileOpen.accelerator, "CommandOrControl+0");
  assert.equal(appOpen.click, reopen);
  assert.equal(windowOpen.click, reopen);
  windowOpen.click();
  assert.deepEqual(calls, ["reopen"]);
});
