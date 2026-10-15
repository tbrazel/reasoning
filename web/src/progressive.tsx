import { ReactNode, useEffect, useState } from "react";

/** How many of `total` items to render right now. Starts at one batch
 *  and grows a batch per tick until everything is mounted, so a long
 *  KaTeX-heavy list (900 claim statements, a full dependency graph)
 *  commits in small interactive slices instead of one multi-second
 *  freeze. The count lives in the mounted component, so leaving and
 *  re-entering a tab starts small again — a remount re-renders every
 *  item from scratch whatever the previous count was. */
export function useProgressiveCount(total: number, batch = 40): number {
  const [count, setCount] = useState(Math.min(batch, total));
  useEffect(() => {
    if (count >= total) return;
    const timer = window.setTimeout(
      () => setCount((current) => Math.min(current + batch, total)),
      30,
    );
    return () => window.clearTimeout(timer);
  }, [count, total, batch]);
  return Math.min(count, total);
}

/** A list that mounts progressively (see useProgressiveCount). Place
 *  it inside the tab/pane it belongs to, so switching away unmounts it
 *  and switching back starts small again. */
export function ProgressiveList<T>({
  items,
  batch = 40,
  children,
}: {
  items: T[];
  batch?: number;
  children: (item: T, index: number) => ReactNode;
}) {
  const count = useProgressiveCount(items.length, batch);
  return <>{items.slice(0, count).map(children)}</>;
}
