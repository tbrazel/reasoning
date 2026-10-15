// Copyright Benjamin Antieau and Deven Manam. SPDX-License-Identifier: Apache-2.0
import { useEffect, useState } from "react";
import { BotRun } from "./organization-api";

export function canStopBot(run: BotRun, userId: string, canWrite: boolean, canManage: boolean) {
  return canWrite && ["queued", "running", "waiting_approval"].includes(run.status)
    && (canManage || run.requested_by === userId || run.owner_user_id === userId);
}
export function publishBotActivity(topicId: string, active: boolean) {
  window.dispatchEvent(new CustomEvent("reasoning-topic-bots", { detail: { topicId, active } }));
}
export function useBotActivity(topicId: string) {
  const [active, setActive] = useState(false);
  useEffect(() => {
    setActive(false);
    const receive = (event: Event) => {
      const detail = (event as CustomEvent).detail;
      if (detail?.topicId === topicId) setActive(Boolean(detail.active));
    };
    window.addEventListener("reasoning-topic-bots", receive);
    return () => window.removeEventListener("reasoning-topic-bots", receive);
  }, [topicId]);
  return active;
}
