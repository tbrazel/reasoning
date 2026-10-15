import { useEffect, useMemo, useRef, useState } from "react";
import { GraphPayload, fetchGraph } from "./api";
import { Markdown, TeX } from "./Markdown";
import { useProgressiveCount } from "./progressive";
import "./ledger-graph.css";

const NODE_W = 230;
const NODE_H = 84;
const COL_GAP = 90;
const ROW_GAP = 22;
const PAD = 24;

interface Placed {
  x: number;
  y: number;
}

/** Longest-path layering: premises (no outgoing dependencies) at the left,
 *  dependents to the right. Cycles fall back to depth 0. */
function layout(graph: GraphPayload, heights: Record<string, number>): Record<string, Placed> {
  const premisesOf: Record<string, string[]> = {};
  for (const edge of graph.edges) {
    (premisesOf[edge.from_claim] ??= []).push(edge.to_claim);
  }
  const depth: Record<string, number> = {};
  const visiting = new Set<string>();
  const computeDepth = (id: string): number => {
    if (id in depth) return depth[id];
    if (visiting.has(id)) return 0; // cycle guard
    visiting.add(id);
    const premises = premisesOf[id] ?? [];
    depth[id] = premises.length
      ? 1 + Math.max(...premises.map(computeDepth))
      : 0;
    visiting.delete(id);
    return depth[id];
  };
  for (const node of graph.nodes) computeDepth(node.claim_id);

  const columns: Record<number, string[]> = {};
  for (const node of graph.nodes) {
    (columns[depth[node.claim_id]] ??= []).push(node.claim_id);
  }
  const placed: Record<string, Placed> = {};
  for (const [column, ids] of Object.entries(columns)) {
    let y = PAD;
    ids.forEach((id) => {
      placed[id] = {
        x: PAD + Number(column) * (NODE_W + COL_GAP),
        y,
      };
      y += (heights[id] ?? NODE_H) + ROW_GAP;
    });
  }
  return placed;
}

const EDGE_COLORS: Record<string, string> = {
  current: "#34d399",
  stale: "#fbbf24",
  broken: "#f87171",
};

export function GraphView({
  projectId,
  version,
  onOpen,
  loadGraph,
}: {
  projectId: string;
  version: string;
  onOpen: (claimId: string) => void;
  loadGraph?: () => Promise<GraphPayload>;
}) {
  const [graph, setGraph] = useState<GraphPayload | null>(null);
  const [zoom, setZoom] = useState(1);
  const [error, setError] = useState("");
  const [expanded, setExpanded] = useState<Set<string>>(() => new Set());
  const [heights, setHeights] = useState<Record<string, number>>({});
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    let active = true; let timer: ReturnType<typeof setTimeout>;
    setGraph(null); setError("");
    const refresh = async () => {
      try { const value = await (loadGraph ? loadGraph() : fetchGraph(projectId));
        if (active) { setGraph(value); setError(""); }
      } catch { if (active) setError("Graph unavailable. Check your access and retry."); }
      if (active && loadGraph) timer = setTimeout(refresh, 5000);
    };
    void refresh();
    return () => { active = false; clearTimeout(timer); };
  }, [projectId, version, loadGraph]);

  const placed = useMemo(() => (graph ? layout(graph, heights) : {}), [graph, heights]);
  // Nodes carry KaTeX statements: mount them in slices so opening the
  // graph never freezes the app (the edges are cheap and render whole).
  const nodeCount = useProgressiveCount(graph?.nodes.length ?? 0);

  useEffect(() => {
    const viewport = scrollRef.current;
    if (!viewport || !graph) return;
    const observer = new ResizeObserver(entries => {
      setHeights(previous => {
        const next = { ...previous }; let changed = false;
        for (const entry of entries) {
          const node = entry.target as HTMLElement, id = node.dataset.claimId;
          const height = Math.ceil(node.offsetHeight);
          if (id && height > 0 && next[id] !== height) { next[id] = height; changed = true; }
        }
        return changed ? next : previous;
      });
    });
    viewport.querySelectorAll("[data-claim-id]").forEach(node => observer.observe(node));
    return () => observer.disconnect();
  }, [graph, nodeCount, expanded]);

  if (error) return <p role="alert">{error}</p>;
  if (!graph) return <div className="empty">loading…</div>;
  if (graph.nodes.length === 0) return <div className="empty">The claim graph will appear as the ledger grows.</div>;

  const width =
    Math.max(...Object.values(placed).map((p) => p.x)) + NODE_W + PAD;
  const height =
    Math.max(...Object.entries(placed).map(([id, p]) => p.y + (heights[id] ?? NODE_H))) + PAD;
  const fit = () => {
    const viewport = scrollRef.current;
    if (!viewport) return;
    const next = Math.min(
      1,
      (viewport.clientWidth - 32) / width,
      (viewport.clientHeight - 54) / height,
    );
    setZoom(Math.max(0.35, next));
    viewport.scrollTo({ left: 0, top: 0 });
  };

  return (
    <div className="graph-scroll" ref={scrollRef}>
      <div className="graph-controls" aria-label="Ledger graph view controls">
        <button aria-label="Zoom out" onClick={() => setZoom((value) => Math.max(0.35, value - 0.1))}>−</button>
        <button aria-label="Reset zoom" onClick={() => setZoom(1)}>{Math.round(zoom * 100)}%</button>
        <button aria-label="Zoom in" onClick={() => setZoom((value) => Math.min(1.5, value + 0.1))}>+</button>
        <button onClick={fit}>fit</button>
      </div>
      <div className="graph-stage" style={{ width: width * zoom, height: height * zoom }}>
        <div className="graph-canvas" style={{ width, height, transform: `scale(${zoom})` }}>
        <svg className="graph-edges" width={width} height={height}>
          {graph.edges.map((edge, i) => {
            const from = placed[edge.from_claim];
            const to = placed[edge.to_claim];
            if (!from || !to) return null;
            // dependent's left edge -> premise's right edge
            const x1 = from.x;
            const y1 = from.y + (heights[edge.from_claim] ?? NODE_H) / 2;
            const x2 = to.x + NODE_W;
            const y2 = to.y + (heights[edge.to_claim] ?? NODE_H) / 2;
            const dx = Math.max(30, Math.abs(x1 - x2) / 2);
            return (
              <path
                key={i}
                d={`M ${x1} ${y1} C ${x1 - dx} ${y1}, ${x2 + dx} ${y2}, ${x2} ${y2}`}
                stroke={EDGE_COLORS[edge.state] ?? "#7dd3fc"}
                strokeWidth={edge.state === "current" ? 1.4 : 2}
                strokeDasharray={edge.state === "stale" ? "5 4" : undefined}
                fill="none"
                opacity={0.75}
              />
            );
          })}
        </svg>
        {graph.nodes.slice(0, nodeCount).map((node) => {
          const p = placed[node.claim_id];
          if (!p) return null;
          const missing = graph.unresolved[node.claim_id];
          return (
            <article
              key={node.claim_id}
              data-claim-id={node.claim_id}
              className={"graph-node ledger-expandable-node" + (expanded.has(node.claim_id) ? " ledger-node-expanded" : "")}
              style={{ left: p.x, top: p.y, width: NODE_W, minHeight: NODE_H }}
            >
              <div className="claim-head-row">
                <span className={`status-chip status-${node.status}`}>
                  {node.status}
                </span>
                {missing ? (
                  <span className="status-chip status-pending">
                    {missing} unresolved
                  </span>
                ) : null}
              </div>
              <div className="graph-node-statement">
                {expanded.has(node.claim_id) ? <Markdown text={node.statement_text} /> : <TeX text={node.statement_text} />}
              </div>
              <div className="ledger-node-actions"><button type="button" aria-expanded={expanded.has(node.claim_id)} aria-label={`${expanded.has(node.claim_id) ? "Collapse" : "Expand"} statement ${node.claim_id}`}
                onClick={() => setExpanded(current => { const next = new Set(current); if (next.has(node.claim_id)) next.delete(node.claim_id); else next.add(node.claim_id); return next; })}>
                {expanded.has(node.claim_id) ? "Collapse" : "Expand"}</button>
                <button type="button" onClick={() => onOpen(node.claim_id)} aria-label={`Inspect proof ${node.claim_id}`}>Inspect proof</button></div>
            </article>
          );
        })}
        </div>
      </div>
    </div>
  );
}
