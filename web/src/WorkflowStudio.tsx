import {
  PointerEvent as ReactPointerEvent,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import {
  AgentNodeConfig,
  AgentPreview,
  AgentToolInfo,
  ConnectionStatus,
  ExecutorInfo,
  McpConnectionInfo,
  PromptHead,
  PromptRevision,
  SkillHead,
  SkillRevision,
  DatasetHead,
  DatasetRevision,
  WorkflowGraph,
  WorkflowEvaluator,
  ModelPriceCatalog,
  ModelPriceInfo,
  ModelRankingConfiguration,
  PaperModerationBenchmark,
  PaperModerationCase,
  PaperModerationDecision,
  PaperModerationFlag,
  WorkflowNode,
  WorkflowNodeOutput,
  WorkflowModelRankings,
  WorkflowPreflight,
  WorkflowRunState,
  WorkflowVariant,
  createDataset,
  createDatasetFromRun,
  createAgentPrompt,
  createAgentSkill,
  cancelWorkflowRun,
  deleteAgentPrompt,
  deleteAgentSkill,
  fetchAgentCatalog,
  fetchAgentPrompt,
  fetchDataset,
  fetchDatasets,
  fetchModelPrices,
  fetchPromptRevisions,
  fetchSkillRevisions,
  fetchWorkflow,
  fetchWorkflowRun,
  fetchWorkflowRuns,
  fetchWorkflowModelRankings,
  previewAgentNode,
  preflightWorkflow,
  renderAgentPrompt,
  reviseAgentPrompt,
  reviseAgentSkill,
  saveWorkflow,
  startWorkflowRun,
} from "./api";
import { AgentModelPicker, AgentModelSource } from "./AgentModelPicker";
import { RevealArtifactButton } from "./ArtifactActions";
import { AutoRoutingControl } from "./AutoRoutingControl";
import { Markdown } from "./Markdown";
import { IconButton } from "./IconButton";

type StudioTab = "node" | "plan" | "prompts" | "skills" | "run";

interface Catalog {
  executors: ExecutorInfo[];
  tools: AgentToolInfo[];
  prompts: PromptHead[];
  skills: SkillHead[];
  connections: ConnectionStatus[];
  mcpConnections: McpConnectionInfo[];
}

const EMPTY_CATALOG: Catalog = {
  executors: [],
  tools: [],
  prompts: [],
  skills: [],
  connections: [],
  mcpConnections: [],
};

const NODE_WIDTH = 210;
const AGENT_NODE_HEIGHT = 112;
const TERMINAL_NODE_HEIGHT = 78;
const CANVAS_WIDTH = 2400;
const CANVAS_HEIGHT = 1400;
const MIN_ZOOM = 0.35;
const MAX_ZOOM = 1.5;

type WorkflowRecipe = NonNullable<WorkflowGraph["presentation"]>["recipe"];

export function workflowRecipe(graph: WorkflowGraph): WorkflowRecipe {
  const recipe = graph.presentation?.recipe;
  if (recipe && recipe !== "graph") return recipe;
  if (graph.workflow_type === "model_comparison") return "model_comparison";
  if (graph.workflow_type === "model_benchmark") return "paper_moderation";
  return recipe ?? "graph";
}

function variantFanoutAgents(
  graph: WorkflowGraph,
): (WorkflowNode & { config: AgentNodeConfig })[] {
  const agents = graph.nodes.filter(
    (node): node is WorkflowNode & { config: AgentNodeConfig } =>
      node.kind === "agent" && Boolean(node.config),
  );
  const variants = graph.variants ?? [];
  const explicit = graph.presentation?.recipe === "model_comparison"
    || graph.presentation?.recipe === "paper_moderation"
    || variants.length > 1
    || (variants.length === 1 && variants[0].variant_id !== "default");
  if (!explicit || agents.length !== 1) return agents;
  const base = agents[0];
  return variants.map((variant) => {
    const override = variant.node_overrides[base.id] ?? {};
    const config = {
      ...base.config,
      ...override,
      budget: { ...base.config.budget, ...(override.budget ?? {}) },
      sandbox: { ...base.config.sandbox, ...(override.sandbox ?? {}) },
      session: { ...base.config.session, ...(override.session ?? {}) },
      context_policy: {
        ...base.config.context_policy,
        ...(override.context_policy ?? {}),
      },
    } as AgentNodeConfig;
    return {
      ...base,
      id: variant.variant_id,
      name: variant.name,
      config,
    };
  });
}

function expandedAgentConfigurations(
  graph: WorkflowGraph,
): (WorkflowNode & { config: AgentNodeConfig; variantId: string; variantName: string })[] {
  const agents = graph.nodes.filter(
    (node): node is WorkflowNode & { config: AgentNodeConfig } =>
      node.kind === "agent" && Boolean(node.config),
  );
  const variants = graph.variants?.length
    ? graph.variants
    : [{ variant_id: "default", name: "Default", node_overrides: {} }];
  return variants.flatMap((variant) => agents.map((agent) => {
    const override = variant.node_overrides[agent.id] ?? {};
    const config = {
      ...agent.config,
      ...override,
      budget: { ...agent.config.budget, ...(override.budget ?? {}) },
      sandbox: { ...agent.config.sandbox, ...(override.sandbox ?? {}) },
      session: { ...agent.config.session, ...(override.session ?? {}) },
      context_policy: {
        ...agent.config.context_policy,
        ...(override.context_policy ?? {}),
      },
    } as AgentNodeConfig;
    return {
      ...agent,
      config,
      variantId: variant.variant_id,
      variantName: variant.name,
    };
  }));
}

function workflowCaseCount(graph: WorkflowGraph): number {
  return graph.dataset?.cases.length ?? graph.iterations;
}

function nodeHeight(kind: WorkflowNode["kind"]): number {
  return kind === "agent" ? AGENT_NODE_HEIGHT : TERMINAL_NODE_HEIGHT;
}

function modelFor(executor: string): string {
  if (executor === "codex") return "gpt-5.6-sol";
  if (executor === "claude-code") return "sonnet";
  if (executor === "hermes") return "openrouter/qwen/qwen3-coder";
  if (executor === "opencode") return "openrouter/qwen/qwen3-coder";
  return "openai/gpt-oss-20b";
}

function defaultAgentConfig(catalog: Catalog): AgentNodeConfig {
  const executor = catalog.executors[0]?.executor_id ?? "reasoning-native";
  const system = catalog.prompts.find((prompt) => prompt.kind === "system");
  const user = catalog.prompts.find((prompt) => prompt.kind === "user");
  return {
    executor_id: executor,
    model: modelFor(executor),
    model_provider: executor === "codex" ? "openai" : null,
    system_prompt: system
      ? { prompt_id: system.prompt_id, revision_id: system.revision_id }
      : undefined,
    user_prompt: user
      ? { prompt_id: user.prompt_id, revision_id: user.revision_id }
      : undefined,
    skills: [],
    tools: [],
    mcp_connections: [],
    context_policy: { mode: "none", max_tokens: 8000 },
    budget: { max_cost_usd: 1, max_tool_calls: 24, timeout_seconds: 900 },
    sandbox: { mode: "read_only", network: false },
    session: { mode: "fresh" },
  };
}

function modelSourcesFor(catalog: Catalog): AgentModelSource[] {
  return catalog.executors.map((executor) => {
    const connection = catalog.connections.find(
      (candidate) => candidate.executor_id === executor.executor_id,
    );
    return {
      executorId: executor.executor_id,
      label: executor.display_name,
      provider: connection?.provider ?? executor.display_name,
      models: executor.model_options,
      ready: connection?.ready ?? false,
      readiness: connection?.readiness ?? "Setup status unavailable",
      promptFidelity:
        executor.prompt_fidelity === "wire_exact"
          ? "exact Reasoning prompt"
          : "controlled executor prompt",
    };
  });
}

export function isAgenticHarnessOnly(description: string): boolean {
  const normalized = description.toLocaleLowerCase();
  return normalized.includes("only available for use with agentic harnesses")
    || normalized.includes("only available through agentic harnesses")
    || (normalized.includes("only available") && normalized.includes("agentic harness"));
}

function selectedPrompt(
  prompts: PromptHead[],
  kind: PromptHead["kind"],
  value: { prompt_id: string; revision_id: string } | undefined,
): string {
  if (value) return `${value.prompt_id}:${value.revision_id}`;
  const fallback = prompts.find((prompt) => prompt.kind === kind);
  return fallback ? `${fallback.prompt_id}:${fallback.revision_id}` : "";
}

function promptBinding(value: string) {
  const separator = value.indexOf(":");
  if (separator < 0) return undefined;
  return {
    prompt_id: value.slice(0, separator),
    revision_id: value.slice(separator + 1),
  };
}

function parseObject(text: string): Record<string, unknown> {
  const value = JSON.parse(text || "{}") as unknown;
  if (!value || Array.isArray(value) || typeof value !== "object") {
    throw new Error("variables must be a JSON object");
  }
  return value as Record<string, unknown>;
}

function withInitialPrompt(
  graph: WorkflowGraph,
  variables: Record<string, unknown>,
  initialPrompt: string,
): Record<string, unknown> {
  const prompt = initialPrompt.trim();
  if (!prompt) return variables;
  const merged = { ...variables };
  for (const name of ["question", "prompt", "input"]) {
    if (!(name in merged)) merged[name] = prompt;
  }
  for (const node of graph.nodes) {
    if (node.kind === "input" && !(node.id in merged)) merged[node.id] = prompt;
  }
  return merged;
}

type RunVisualState = "idle" | "ready" | "active" | "completed" | "failed";

type CanonicalHarnessEvent = {
  session_id: string;
  turn_id: string | null;
  sequence: number;
  kind: string;
  at: string;
  data: Record<string, unknown>;
};

function canonicalHarnessEvent(
  event: Record<string, unknown>,
): CanonicalHarnessEvent | null {
  if (event.type !== "harness.event") return null;
  const value = event.harness_event;
  if (!value || Array.isArray(value) || typeof value !== "object") return null;
  const candidate = value as Record<string, unknown>;
  if (
    typeof candidate.session_id !== "string"
    || (candidate.turn_id !== null && typeof candidate.turn_id !== "string")
    || typeof candidate.sequence !== "number"
    || typeof candidate.kind !== "string"
    || typeof candidate.at !== "string"
    || !candidate.data
    || Array.isArray(candidate.data)
    || typeof candidate.data !== "object"
  ) return null;
  return candidate as CanonicalHarnessEvent;
}

function workflowEventSubject(event: Record<string, unknown>): string | null {
  if (typeof event.variant_id === "string" && event.variant_id !== "default") {
    return event.variant_id;
  }
  return typeof event.node_id === "string" ? event.node_id : null;
}

export function workflowReasoningBySubject(
  run: WorkflowRunState | null,
  maxCharacters = 24_000,
): Map<string, string> {
  const reasoning = new Map<string, string>();
  for (const event of run?.events ?? []) {
    const canonical = canonicalHarnessEvent(event);
    const subject = workflowEventSubject(event);
    const text = canonical?.kind === "reasoning.delta" ? canonical.data.text : null;
    if (!subject || typeof text !== "string" || !text) continue;
    const merged = `${reasoning.get(subject) ?? ""}${text}`;
    reasoning.set(subject, merged.slice(-maxCharacters));
  }
  return reasoning;
}

function canonicalWorkflowEventLabel(
  graph: WorkflowGraph,
  event: Record<string, unknown>,
): string | null {
  const canonical = canonicalHarnessEvent(event);
  if (!canonical) return null;
  const nodeName = graph.nodes.find((node) => node.id === event.node_id)?.name
    ?? event.variant_name
    ?? event.node_id
    ?? "Agent";
  const toolName = typeof canonical.data.name === "string" ? canonical.data.name : "tool";
  if (canonical.kind === "turn.started") return `${nodeName} executor started`;
  if (canonical.kind === "message.delta") return `${nodeName} drafting…`;
  if (canonical.kind === "message.completed") return `${nodeName} response completed`;
  if (canonical.kind === "reasoning.delta") return `${nodeName} reasoning…`;
  if (canonical.kind === "tool.requested") return `${nodeName} requested ${toolName}`;
  if (canonical.kind === "tool.completed") {
    return `${nodeName} ${canonical.data.ok === false ? "failed" : "completed"} ${toolName}`;
  }
  if (canonical.kind === "progress") return `${nodeName} reported progress`;
  if (canonical.kind === "checkpoint.created") return `${nodeName} checkpoint saved`;
  if (canonical.kind === "usage") return `${nodeName} usage updated`;
  if (canonical.kind === "turn.completed") return `${nodeName} executor completed`;
  if (canonical.kind === "turn.interrupted") return `${nodeName} executor interrupted`;
  if (canonical.kind === "turn.failed") return `${nodeName} executor failed`;
  return `${nodeName} · ${canonical.kind}`;
}

function workflowRunVisuals(graph: WorkflowGraph, run: WorkflowRunState | null) {
  const nodes = new Map<string, RunVisualState>();
  const edges = new Map<string, "idle" | "flowing" | "completed" | "failed">();
  if (!run) return { nodes, edges, iteration: 0, active: false };
  const iterations = [
    ...run.events.map((event) => Number(event.iteration) || 0),
    ...(run.outputs.nodes ?? []).map((output) => output.iteration),
  ];
  const iteration = Math.max(0, ...iterations);
  const active = ["pending", "running", "cancelling"].includes(run.status);
  const lastEventByNode = new Map<string, string>();
  for (const event of run.events) {
    if (Number(event.iteration) !== iteration || typeof event.node_id !== "string") continue;
    if (event.type === "node.started" || event.type === "node.completed") {
      lastEventByNode.set(event.node_id, String(event.type));
    }
  }
  for (const node of graph.nodes) {
    if (node.kind === "input" && iteration > 0) nodes.set(node.id, "ready");
  }
  for (const output of run.outputs.nodes ?? []) {
    if (output.iteration !== iteration) continue;
    nodes.set(output.node_id, output.status === "completed" ? "completed" : "failed");
  }
  for (const [nodeId, eventType] of lastEventByNode) {
    if (eventType === "node.started") nodes.set(nodeId, "active");
  }
  for (const node of graph.nodes.filter((candidate) => candidate.kind === "output")) {
    const sources = graph.edges
      .filter((edge) => edge.target === node.id)
      .map((edge) => nodes.get(edge.source));
    if (sources.some((state) => state === "failed")) nodes.set(node.id, "failed");
    else if (
      run.status === "completed"
      || (sources.length > 0 && sources.every((state) => state === "completed"))
    ) nodes.set(node.id, "completed");
  }
  for (const edge of graph.edges) {
    const source = nodes.get(edge.source) ?? "idle";
    const target = nodes.get(edge.target) ?? "idle";
    if (source === "failed" || target === "failed") edges.set(edge.id, "failed");
    else if (target === "active") edges.set(edge.id, "flowing");
    else if (
      (source === "completed" || source === "ready")
      && target === "completed"
    ) edges.set(edge.id, "completed");
  }
  return { nodes, edges, iteration, active };
}

function WorkflowCanvas({
  graph,
  run,
  selectedNode,
  selectedEdge,
  linkSource,
  paletteDragging,
  onSelectNode,
  onSelectEdge,
  onBeginNodeMove,
  onMoveNode,
  onDeleteNode,
  onStartLink,
  onFinishLink,
}: {
  graph: WorkflowGraph;
  run: WorkflowRunState | null;
  selectedNode: string | null;
  selectedEdge: string | null;
  linkSource: string | null;
  paletteDragging: boolean;
  onSelectNode: (id: string) => void;
  onSelectEdge: (id: string) => void;
  onBeginNodeMove: () => void;
  onMoveNode: (id: string, position: { x: number; y: number }) => void;
  onDeleteNode: (id: string) => void;
  onStartLink: (id: string) => void;
  onFinishLink: (id: string) => void;
}) {
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const canvasRef = useRef<HTMLDivElement | null>(null);
  const [scale, setScale] = useState(1);
  const [viewport, setViewport] = useState({ x: 0, y: 0, width: 0, height: 0 });
  const runVisuals = useMemo(() => workflowRunVisuals(graph, run), [graph, run]);

  const syncViewport = useCallback((atScale = scale) => {
    const scroll = scrollRef.current;
    if (!scroll) return;
    setViewport({
      x: scroll.scrollLeft / atScale,
      y: scroll.scrollTop / atScale,
      width: scroll.clientWidth / atScale,
      height: scroll.clientHeight / atScale,
    });
  }, [scale]);

  const applyScale = useCallback((next: number) => {
    const scroll = scrollRef.current;
    const clamped = Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, next));
    if (!scroll) {
      setScale(clamped);
      return;
    }
    const worldCenter = {
      x: (scroll.scrollLeft + scroll.clientWidth / 2) / scale,
      y: (scroll.scrollTop + scroll.clientHeight / 2) / scale,
    };
    setScale(clamped);
    window.requestAnimationFrame(() => {
      scroll.scrollLeft = worldCenter.x * clamped - scroll.clientWidth / 2;
      scroll.scrollTop = worldCenter.y * clamped - scroll.clientHeight / 2;
      syncViewport(clamped);
    });
  }, [scale, syncViewport]);

  const fitToView = useCallback(() => {
    const scroll = scrollRef.current;
    if (!scroll) return;
    if (graph.nodes.length === 0) {
      setScale(1);
      scroll.scrollTo({ left: 0, top: 0 });
      syncViewport(1);
      return;
    }
    const minX = Math.min(...graph.nodes.map((node) => node.position.x));
    const minY = Math.min(...graph.nodes.map((node) => node.position.y));
    const maxX = Math.max(...graph.nodes.map((node) => node.position.x + NODE_WIDTH));
    const maxY = Math.max(...graph.nodes.map((node) => node.position.y + nodeHeight(node.kind)));
    const padding = 72;
    const fitted = Math.min(
      1,
      (scroll.clientWidth - padding * 2) / Math.max(1, maxX - minX),
      (scroll.clientHeight - padding * 2) / Math.max(1, maxY - minY),
    );
    const next = Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, fitted));
    setScale(next);
    window.requestAnimationFrame(() => {
      scroll.scrollLeft = Math.max(0, (minX - padding / next) * next);
      scroll.scrollTop = Math.max(0, (minY - padding / next) * next);
      syncViewport(next);
    });
  }, [graph.nodes, syncViewport]);

  useEffect(() => {
    const frame = window.requestAnimationFrame(fitToView);
    return () => window.cancelAnimationFrame(frame);
  }, [graph.workflow_id]);

  useEffect(() => {
    const onResize = () => fitToView();
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, [fitToView]);

  useEffect(() => {
    const scroll = scrollRef.current;
    if (!scroll || typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(() => fitToView());
    observer.observe(scroll);
    return () => observer.disconnect();
  }, [fitToView]);

  const startDrag = (event: ReactPointerEvent, node: WorkflowNode) => {
    if (runVisuals.active) return;
    if ((event.target as HTMLElement).closest("button,input,select,textarea")) return;
    const target = event.currentTarget as HTMLElement;
    target.setPointerCapture(event.pointerId);
    const start = { x: event.clientX, y: event.clientY };
    const origin = node.position;
    onBeginNodeMove();
    const move = (next: PointerEvent) => {
      onMoveNode(node.id, {
        x: Math.max(0, origin.x + (next.clientX - start.x) / scale),
        y: Math.max(0, origin.y + (next.clientY - start.y) / scale),
      });
    };
    const finish = () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", finish);
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", finish, { once: true });
  };

  return (
    <div className="workflow-canvas-shell">
      <div className="workflow-canvas-controls" aria-label="Canvas view controls">
        <button aria-label="Zoom out" title="Zoom out" onClick={() => applyScale(scale - 0.15)}>−</button>
        <button className="canvas-scale" aria-label="Reset zoom" title="Reset zoom" onClick={() => applyScale(1)}>{Math.round(scale * 100)}%</button>
        <button aria-label="Zoom in" title="Zoom in" onClick={() => applyScale(scale + 0.15)}>+</button>
        <button onClick={fitToView}>fit</button>
      </div>
      <div className={`workflow-canvas-scroll ${paletteDragging ? "palette-drop-active" : ""}`} ref={scrollRef} onScroll={() => syncViewport()}>
        <div className="workflow-canvas-stage" style={{ width: CANVAS_WIDTH * scale, height: CANVAS_HEIGHT * scale }}>
        <div
          className="workflow-canvas"
          data-scale={scale}
          ref={canvasRef}
          style={{ transform: `scale(${scale})` }}
          onClick={() => onSelectEdge("")}
        >
        <svg className="workflow-edges" width={CANVAS_WIDTH} height={CANVAS_HEIGHT}>
          {graph.edges.map((edge) => {
            const source = graph.nodes.find((node) => node.id === edge.source);
            const target = graph.nodes.find((node) => node.id === edge.target);
            if (!source || !target) return null;
            const x1 = source.position.x + NODE_WIDTH;
            const y1 = source.position.y + nodeHeight(source.kind) / 2;
            const x2 = target.position.x;
            const y2 = target.position.y + nodeHeight(target.kind) / 2;
            const bend = Math.max(45, Math.abs(x2 - x1) / 2);
            const path = `M ${x1} ${y1} C ${x1 + bend} ${y1}, ${x2 - bend} ${y2}, ${x2} ${y2}`;
            const runState = runVisuals.edges.get(edge.id) ?? "idle";
            return (
              <g key={edge.id} className={`workflow-edge edge-run-${runState}`}>
                <path
                className={`workflow-edge-base ${edge.id === selectedEdge ? "selected" : ""}`}
                d={path}
                onClick={(event) => {
                  event.stopPropagation();
                  onSelectEdge(edge.id);
                }}
                />
                {runState === "flowing" && <path className="workflow-edge-flow" d={path} />}
              </g>
            );
          })}
        </svg>
        {graph.nodes.map((node) => {
          const runState = runVisuals.nodes.get(node.id) ?? "idle";
          return (
          <div
            key={node.id}
            className={`workflow-node workflow-node-${node.kind} ${
              selectedNode === node.id ? "selected" : ""
            } ${linkSource === node.id ? "linking" : ""} node-run-${runState}`}
            style={{
              left: node.position.x,
              top: node.position.y,
              width: NODE_WIDTH,
              height: nodeHeight(node.kind),
            }}
            onPointerDown={(event) => startDrag(event, node)}
            onClick={(event) => {
              event.stopPropagation();
              onSelectNode(node.id);
            }}
            onKeyDown={(event) => {
              if (runVisuals.active) return;
              const step = event.shiftKey ? 50 : 10;
              const delta = event.key === "ArrowLeft" ? { x: -step, y: 0 }
                : event.key === "ArrowRight" ? { x: step, y: 0 }
                  : event.key === "ArrowUp" ? { x: 0, y: -step }
                    : event.key === "ArrowDown" ? { x: 0, y: step }
                      : null;
              if (delta) {
                event.preventDefault();
                onBeginNodeMove();
                onMoveNode(node.id, {
                  x: Math.max(0, node.position.x + delta.x),
                  y: Math.max(0, node.position.y + delta.y),
                });
              } else if (event.key === "Delete" || event.key === "Backspace") {
                event.preventDefault();
                onDeleteNode(node.id);
              } else if (event.key === "Enter" || event.key === " ") {
                event.preventDefault();
                onSelectNode(node.id);
              }
            }}
            role="button"
            tabIndex={0}
            aria-label={`${node.kind} node: ${node.name}${runState !== "idle" ? `, ${runState}` : ""}`}
          >
            <button
              className="workflow-port workflow-port-in"
              title="connect here"
              disabled={runVisuals.active}
              onClick={(event) => {
                event.stopPropagation();
                onFinishLink(node.id);
              }}
            />
            <div className="workflow-node-kind">{node.kind}</div>
            {runState !== "idle" && (
              <div className="workflow-node-run-state">
                <span />
                {runState === "ready" ? `week ${runVisuals.iteration}` : runState}
              </div>
            )}
            <div className="workflow-node-name">{node.name}</div>
            {node.kind === "agent" && node.config && (
              <>
                <div className="workflow-node-model">{node.config.model || "choose a model"}</div>
                <div className="workflow-node-meta">
                  {node.config.executor_id}
                  {node.config.model_provider ? ` / ${node.config.model_provider}` : ""}
                  {` · ${node.config.context_policy.mode}`}
                </div>
              </>
            )}
            <button
              className="workflow-port workflow-port-out"
              title="start connection"
              disabled={runVisuals.active}
              onClick={(event) => {
                event.stopPropagation();
                onStartLink(node.id);
              }}
            />
          </div>
          );
        })}
        </div>
      </div>
      </div>
      <svg className="workflow-minimap" viewBox={`0 0 ${CANVAS_WIDTH} ${CANVAS_HEIGHT}`} aria-label="Workflow minimap">
        {graph.edges.map((edge) => {
          const source = graph.nodes.find((node) => node.id === edge.source);
          const target = graph.nodes.find((node) => node.id === edge.target);
          if (!source || !target) return null;
          return <line key={edge.id} x1={source.position.x + NODE_WIDTH} y1={source.position.y + nodeHeight(source.kind) / 2} x2={target.position.x} y2={target.position.y + nodeHeight(target.kind) / 2} />;
        })}
        {graph.nodes.map((node) => <rect key={node.id} className={`minimap-node minimap-node-${node.kind} node-run-${runVisuals.nodes.get(node.id) ?? "idle"}`} x={node.position.x} y={node.position.y} width={NODE_WIDTH} height={nodeHeight(node.kind)} rx="20" />)}
        <rect className="minimap-viewport" x={viewport.x} y={viewport.y} width={viewport.width} height={viewport.height} />
      </svg>
    </div>
  );
}

function NodeInspector({
  node,
  edgeSelected,
  projectId,
  graph,
  catalog,
  preview,
  previewVariables,
  onPreviewVariables,
  onChange,
  onDeleteNode,
  onDeleteEdge,
  onPreview,
}: {
  node: WorkflowNode | null;
  edgeSelected: boolean;
  projectId: string | null;
  graph: WorkflowGraph;
  catalog: Catalog;
  preview: AgentPreview | null;
  previewVariables: string;
  onPreviewVariables: (value: string) => void;
  onChange: (node: WorkflowNode) => void;
  onDeleteNode: () => void;
  onDeleteEdge: () => void;
  onPreview: () => void;
}) {
  if (!node) {
    return edgeSelected ? (
      <div className="studio-panel-body">
        <p className="studio-help">Selected connection.</p>
        <button className="danger" onClick={onDeleteEdge}>delete connection</button>
      </div>
    ) : (
      <div className="studio-panel-body studio-help">
        Drop a node onto the canvas, then select it to configure its executor, exact prompts,
        skills, tools, context, and budget.
      </div>
    );
  }

  const changeConfig = (change: Partial<AgentNodeConfig>) => {
    if (!node.config) return;
    onChange({ ...node, config: { ...node.config, ...change } });
  };
  const config = node.config;
  const modelSources = modelSourcesFor(catalog);
  const autoRoutable = graph.nodes.filter((item) => item.kind === "agent").length === 1;
  return (
    <div className="studio-panel-body studio-form">
      <label>
        name
        <input value={node.name} onChange={(event) => onChange({ ...node, name: event.target.value })} />
      </label>
      {node.kind === "agent" && config ? (
        <>
          <label>
            agent and model
            <AgentModelPicker
              sources={modelSources}
              executorId={config.executor_id}
              model={config.model}
              priceTargetLabel={`workflow node “${node.name}”`}
              rankingContext={projectId ? { projectId, workflowId: graph.workflow_id } : undefined}
              onChange={(executor_id, model) => changeConfig({
                executor_id,
                model,
                model_provider: executor_id === "codex"
                  ? (config.executor_id === "codex" ? config.model_provider || "openai" : "openai")
                  : null,
              })}
            />
          </label>
          {projectId && autoRoutable && (
            <AutoRoutingControl
              source="workflow"
              projectId={projectId}
              resourceId={graph.workflow_id}
              onSelect={(selection) => changeConfig({
                executor_id: selection.harness_id,
                model: selection.model,
                model_provider: selection.harness_id === "codex"
                  ? selection.provider
                  : null,
                effort: selection.reasoning_effort,
              })}
            />
          )}
          {config.executor_id === "codex" && (
            <label>
              Codex model provider
              <input
                list="codex-workflow-model-providers"
                value={config.model_provider ?? ""}
                onChange={(event) => changeConfig({ model_provider: event.target.value })}
                placeholder="openai or openrouter"
              />
              <datalist id="codex-workflow-model-providers">
                <option value="openai" />
                <option value="openrouter" />
              </datalist>
              <small>Persisted in the workflow revision and passed explicitly when Codex starts the node.</small>
            </label>
          )}
          <label>
            effort
            <select value={config.effort ?? ""} onChange={(event) => changeConfig({ effort: event.target.value || null })}>
              <option value="">executor default</option>
              {['low', 'medium', 'high', 'xhigh', 'max'].map((effort) => <option key={effort}>{effort}</option>)}
            </select>
          </label>
          <label>
            system prompt
            <select
              value={selectedPrompt(catalog.prompts, "system", config.system_prompt)}
              onChange={(event) => changeConfig({ system_prompt: promptBinding(event.target.value) })}
            >
              <option value="">choose a prompt</option>
              {catalog.prompts.filter((prompt) => prompt.kind === "system").map((prompt) => (
                <option key={prompt.prompt_id} value={`${prompt.prompt_id}:${prompt.revision_id}`}>{prompt.name}</option>
              ))}
            </select>
          </label>
          <label>
            user prompt
            <select
              value={selectedPrompt(catalog.prompts, "user", config.user_prompt)}
              onChange={(event) => changeConfig({ user_prompt: promptBinding(event.target.value) })}
            >
              <option value="">choose a prompt</option>
              {catalog.prompts.filter((prompt) => prompt.kind === "user").map((prompt) => (
                <option key={prompt.prompt_id} value={`${prompt.prompt_id}:${prompt.revision_id}`}>{prompt.name}</option>
              ))}
            </select>
          </label>
          <fieldset>
            <legend>tools</legend>
            {catalog.tools.map((tool) => (
              <label className="check-row" key={tool.name} title={tool.description}>
                <input
                  type="checkbox"
                  checked={config.tools.includes(tool.name)}
                  onChange={(event) => changeConfig({ tools: event.target.checked ? [...config.tools, tool.name] : config.tools.filter((name) => name !== tool.name) })}
                />
                <span>{tool.name}</span><small>{tool.risk}</small>
              </label>
            ))}
          </fieldset>
          {catalog.executors.find((executor) => executor.executor_id === config.executor_id)?.supports_mcp && (
            <fieldset>
              <legend>external MCP connections</legend>
              {catalog.mcpConnections.length === 0 && (
                <span className="studio-help">Add connections in Settings, then grant them to this agent.</span>
              )}
              {catalog.mcpConnections.map((connection) => (
                <label className="check-row" key={connection.connection_id} title={connection.enabled ? `${connection.transport} connection` : "Enable this connection in Settings first"}>
                  <input
                    type="checkbox"
                    disabled={!connection.enabled}
                    checked={(config.mcp_connections ?? []).includes(connection.connection_id)}
                    onChange={(event) => {
                      const current = config.mcp_connections ?? [];
                      changeConfig({ mcp_connections: event.target.checked ? [...current, connection.connection_id] : current.filter((id) => id !== connection.connection_id) });
                    }}
                  />
                  <span>{connection.name}</span>
                  <small>{connection.enabled ? connection.transport : "disabled"}</small>
                </label>
              ))}
            </fieldset>
          )}
          <fieldset>
            <legend>skills</legend>
            {catalog.skills.length === 0 && <span className="studio-help">No skills yet.</span>}
            {catalog.skills.map((skill) => (
              <label className="check-row" key={skill.skill_id} title={skill.description}>
                <input
                  type="checkbox"
                  checked={config.skills.some((binding) => binding.skill_id === skill.skill_id)}
                  onChange={(event) => changeConfig({ skills: event.target.checked ? [...config.skills, { skill_id: skill.skill_id, revision_id: skill.revision_id }] : config.skills.filter((binding) => binding.skill_id !== skill.skill_id) })}
                />
                <span>{skill.name}</span>
              </label>
            ))}
          </fieldset>
          <label>
            context
            <select value={config.context_policy.mode} onChange={(event) => changeConfig({ context_policy: { ...config.context_policy, mode: event.target.value as AgentNodeConfig["context_policy"]["mode"] } })}>
              <option value="none">none</option>
              <option value="predecessors">direct predecessors</option>
              <option value="all">all prior agent outputs</option>
              <option value="selected">selected agents</option>
              <option value="rolling">rolling latest output</option>
            </select>
          </label>
          {config.context_policy.mode === "selected" && (
            <fieldset>
              <legend>context agents</legend>
              {graph.nodes.filter((candidate) => candidate.kind === "agent" && candidate.id !== node.id).map((candidate) => (
                <label className="check-row" key={candidate.id}>
                  <input
                    type="checkbox"
                    checked={(config.context_policy.include_agent_outputs ?? []).includes(candidate.id)}
                    onChange={(event) => {
                      const current = config.context_policy.include_agent_outputs ?? [];
                      changeConfig({ context_policy: { ...config.context_policy, include_agent_outputs: event.target.checked ? [...current, candidate.id] : current.filter((id) => id !== candidate.id) } });
                    }}
                  />
                  <span>{candidate.name}</span>
                </label>
              ))}
            </fieldset>
          )}
          <label>
            context token ceiling
            <input type="number" min="1" value={config.context_policy.max_tokens ?? ""} onChange={(event) => changeConfig({ context_policy: { ...config.context_policy, max_tokens: event.target.value ? Number(event.target.value) : null } })} />
          </label>
          <div className="form-row">
            <label>
              max cost $
              <input type="number" min="0" step="0.01" value={config.budget.max_cost_usd ?? ""} onChange={(event) => changeConfig({ budget: { ...config.budget, max_cost_usd: event.target.value ? Number(event.target.value) : null } })} />
            </label>
            <label>
              timeout sec
              <input type="number" min="1" value={config.budget.timeout_seconds ?? 900} onChange={(event) => changeConfig({ budget: { ...config.budget, timeout_seconds: Number(event.target.value) } })} />
            </label>
          </div>
          <label className="check-row">
            <input type="checkbox" checked={Boolean(config.sandbox.network)} onChange={(event) => changeConfig({ sandbox: { ...config.sandbox, network: event.target.checked } })} />
            <span>allow network tools</span>
          </label>
          <label>
            prompt-preview variables (JSON)
            <textarea rows={4} value={previewVariables} onChange={(event) => onPreviewVariables(event.target.value)} />
          </label>
          <button onClick={onPreview}>preview exact composed prompts</button>
          {preview && (
            <div className="prompt-preview">
              <div className="prompt-preview-head">{preview.prompt_fidelity} · {preview.prompt_hashes.effective_system_sha256?.slice(0, 12)}</div>
              <strong>system</strong>
              <pre>{preview.system_prompt}</pre>
              <strong>user</strong>
              <pre>{preview.user_prompt}</pre>
              {preview.mcp_connections.length > 0 && (
                <><strong>external MCP</strong><pre>{preview.mcp_connections.join("\n")}</pre></>
              )}
            </div>
          )}
        </>
      ) : (
        <p className="studio-help">This node labels an external input or final output in the graph.</p>
      )}
      <button className="danger" onClick={onDeleteNode}>delete node</button>
    </div>
  );
}

function PromptStudio({
  prompts,
  onChanged,
  onDraft,
}: {
  prompts: PromptHead[];
  onChanged: () => void;
  onDraft: (description: string) => void;
}) {
  const [selected, setSelected] = useState<string>("");
  const [revisions, setRevisions] = useState<PromptRevision[]>([]);
  const [parent, setParent] = useState<string | undefined>();
  const [name, setName] = useState("");
  const [kind, setKind] = useState<PromptHead["kind"]>("system");
  const [description, setDescription] = useState("");
  const [template, setTemplate] = useState("");
  const [message, setMessage] = useState("");
  const [deleteArmed, setDeleteArmed] = useState(false);
  const [baseline, setBaseline] = useState(() => JSON.stringify({ name: "", kind: "system", description: "", template: "" }));
  const [pendingSelection, setPendingSelection] = useState<string | null>(null);
  const [testVariables, setTestVariables] = useState("{}");
  const [rendered, setRendered] = useState("");

  const snapshot = (values = { name, kind, description, template }) => JSON.stringify(values);
  const dirty = snapshot() !== baseline;
  const humanTime = (value: string) => {
    const date = new Date(value);
    return Number.isNaN(date.valueOf()) ? value : date.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
  };

  const loadRevision = (revision: PromptRevision) => {
    setParent(revision.revision_id);
    setName(revision.name);
    setKind(revision.kind);
    setDescription(revision.description);
    setTemplate(revision.template);
    setBaseline(snapshot({ name: revision.name, kind: revision.kind, description: revision.description, template: revision.template }));
    setRendered("");
  };
  const selectImmediately = (promptId: string) => {
    setDeleteArmed(false);
    setPendingSelection(null);
    setSelected(promptId);
    if (!promptId) {
      setRevisions([]); setParent(undefined); setName(""); setKind("system"); setDescription(""); setTemplate(""); setBaseline(snapshot({ name: "", kind: "system", description: "", template: "" }));
      return;
    }
    void fetchPromptRevisions(promptId).then(({ revisions: values }) => {
      setRevisions(values);
      if (values.length) loadRevision(values[values.length - 1]);
    });
  };
  const select = (promptId: string) => {
    if (dirty) {
      setPendingSelection(promptId);
      setMessage("You have unsaved prompt changes. Save them or discard them before switching.");
      return;
    }
    selectImmediately(promptId);
  };
  const save = () => {
    setDeleteArmed(false);
    setMessage("saving…");
    const action = selected
      ? reviseAgentPrompt(selected, { name, kind, description, template, parent_revision_id: parent })
      : createAgentPrompt({ name, kind, description, template });
    void action.then((revision) => {
      setMessage("Saved a new immutable revision.");
      setSelected(revision.prompt_id);
      loadRevision(revision);
      onChanged();
      return fetchPromptRevisions(revision.prompt_id);
    }).then(({ revisions: values }) => setRevisions(values)).catch((error) => setMessage(String(error)));
  };
  const remove = () => {
    if (!selected) return;
    if (!deleteArmed) {
      setDeleteArmed(true);
      setMessage("Click confirm delete to remove this prompt from the library. Pinned revisions remain reproducible.");
      return;
    }
    setMessage("deleting…");
    void deleteAgentPrompt(selected).then(() => {
      setSelected(""); setRevisions([]); setParent(undefined); setName("");
      setDescription(""); setTemplate(""); setDeleteArmed(false);
      setMessage("prompt deleted; immutable revisions remain available to saved workflows");
      onChanged();
    }).catch((error) => setMessage(String(error)));
  };
  const duplicate = () => {
    setSelected(""); setRevisions([]); setParent(undefined); setDeleteArmed(false);
    setName(`${name} copy`); setBaseline("");
    setMessage("Editing a copy. Create it to add a separate prompt to the library.");
  };
  const testRender = () => {
    if (!selected || !parent) {
      setMessage("Save the prompt before testing its variables.");
      return;
    }
    try {
      const values = JSON.parse(testVariables) as Record<string, unknown>;
      void renderAgentPrompt(selected, parent, values).then(({ text }) => {
        setRendered(text); setMessage("Rendered this exact saved revision.");
      }).catch((error) => setMessage(String(error)));
    } catch {
      setMessage("Test values must be valid JSON.");
    }
  };
  const activeRevision = revisions.find((revision) => revision.revision_id === parent);

  return (
    <div className="studio-panel-body studio-form">
      <label>
        prompt document
        <select value={selected} onChange={(event) => select(event.target.value)}>
          <option value="">new prompt…</option>
          {prompts.map((prompt) => <option key={prompt.prompt_id} value={prompt.prompt_id}>{prompt.name} · {prompt.kind}</option>)}
        </select>
      </label>
      {pendingSelection !== null && <div className="unsaved-guard"><span>Discard unsaved changes?</span><button onClick={() => { setPendingSelection(null); setMessage(""); }}>keep editing</button><button className="danger" onClick={() => selectImmediately(pendingSelection)}>discard &amp; switch</button></div>}
      {selected && activeRevision && <div className="revision-context"><strong>{activeRevision.revision_id === prompts.find((prompt) => prompt.prompt_id === selected)?.revision_id ? "Current revision" : "Earlier revision"}</strong><span>{humanTime(activeRevision.created_at)} · saving creates a new revision from this point</span></div>}
      <div className="form-row">
        <label>name<input value={name} onChange={(event) => setName(event.target.value)} /></label>
        <label>kind<select value={kind} onChange={(event) => setKind(event.target.value as PromptHead["kind"])}><option>system</option><option>user</option><option>context</option><option>evaluator</option></select></label>
      </div>
      <label>purpose<input value={description} onChange={(event) => setDescription(event.target.value)} placeholder="What this prompt must accomplish" /></label>
      <label>exact template<textarea rows={14} value={template} onChange={(event) => setTemplate(event.target.value)} placeholder="Use {{variable}} for explicit slots." /></label>
      {activeRevision?.slots.length ? <div className="slot-list"><strong>Required inputs</strong>{activeRevision.slots.map((slot) => <code key={slot}>{`{{${slot}}}`}</code>)}</div> : <p className="studio-help">Add {"{{variable}}"} placeholders for values supplied by each workflow run.</p>}
      <details className="prompt-test"><summary>Test this saved revision</summary><label>test values (JSON)<textarea rows={4} value={testVariables} onChange={(event) => setTestVariables(event.target.value)} /></label><button disabled={!selected || !parent} onClick={testRender}>render exact prompt</button>{rendered && <pre>{rendered}</pre>}</details>
      <div className="studio-actions">
        {selected && <button className="danger" onClick={remove}>{deleteArmed ? "confirm delete" : "delete prompt"}</button>}
        {selected && <button onClick={duplicate}>duplicate</button>}
        <button onClick={() => onDraft(`Draft a ${kind} prompt named “${name || 'untitled'}”. Purpose: ${description || 'help me define it'}. Use explicit {{variable}} slots where useful. Return the proposed exact prompt and briefly explain its design; do not save it yet.`)}>draft with Reasoning</button>
        <button className="confirm" disabled={!dirty || !name.trim() || !template.trim()} onClick={save}>{selected ? "save new revision" : "create prompt"}</button>
      </div>
      {message && <div className="studio-message">{message}</div>}
      {revisions.length > 0 && (
        <div className="revision-list">
          {revisions.map((revision, index) => <button key={revision.revision_id} className={revision.revision_id === parent ? "selected" : ""} onClick={() => dirty ? (setMessage("Save or discard your changes before opening another revision.")) : loadRevision(revision)}><span>Revision {index + 1}{index === revisions.length - 1 ? " · current" : ""}</span><small>{humanTime(revision.created_at)}</small></button>)}
        </div>
      )}
    </div>
  );
}

function SkillStudio({
  skills,
  tools,
  onChanged,
  onDraft,
}: {
  skills: SkillHead[];
  tools: AgentToolInfo[];
  onChanged: () => void;
  onDraft: (description: string) => void;
}) {
  const [selected, setSelected] = useState("");
  const [revisions, setRevisions] = useState<SkillRevision[]>([]);
  const [parent, setParent] = useState<string | undefined>();
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [instructions, setInstructions] = useState("");
  const [selectedTools, setSelectedTools] = useState<string[]>([]);
  const [message, setMessage] = useState("");
  const [baseline, setBaseline] = useState(() => JSON.stringify({ description: "", instructions: "", selectedTools: [] }));
  const [pendingSelection, setPendingSelection] = useState<string | null>(null);
  const [deleteArmed, setDeleteArmed] = useState(false);
  const snapshot = (values = { description, instructions, selectedTools }) => JSON.stringify({ ...values, selectedTools: [...values.selectedTools].sort() });
  const dirty = snapshot() !== baseline;
  const toolsForRevision = (revision: SkillRevision) => {
    const reasoning = revision.metadata?.reasoning;
    if (!reasoning || typeof reasoning !== "object") return [];
    const values = (reasoning as { tools?: unknown }).tools;
    return Array.isArray(values) ? values.filter((value): value is string => typeof value === "string") : [];
  };
  const loadRevision = (revision: SkillRevision) => {
    const revisionTools = toolsForRevision(revision);
    setSelected(revision.skill_id); setParent(revision.revision_id); setName(revision.name);
    setDescription(revision.description); setInstructions(revision.instructions); setSelectedTools(revisionTools);
    setBaseline(snapshot({ description: revision.description, instructions: revision.instructions, selectedTools: revisionTools }));
  };
  const selectImmediately = (skillId: string) => {
    setPendingSelection(null); setDeleteArmed(false); setSelected(skillId);
    if (!skillId) {
      setRevisions([]); setParent(undefined); setName(""); setDescription(""); setInstructions(""); setSelectedTools([]);
      setBaseline(snapshot({ description: "", instructions: "", selectedTools: [] }));
      return;
    }
    void fetchSkillRevisions(skillId).then(({ revisions: values }) => {
      setRevisions(values); if (values.length) loadRevision(values[values.length - 1]);
    }).catch((error) => setMessage(String(error)));
  };
  const select = (skillId: string) => {
    if (dirty) { setPendingSelection(skillId); setMessage("You have unsaved skill changes."); return; }
    selectImmediately(skillId);
  };
  const save = () => {
    const capabilities = tools.filter((tool) => selectedTools.includes(tool.name)).flatMap((tool) => tool.capabilities);
    const values = { description, instructions, metadata: { reasoning: { tools: selectedTools, capabilities: [...new Set(capabilities)] } } };
    const action = selected
      ? reviseAgentSkill(selected, { ...values, parent_revision_id: parent })
      : createAgentSkill({ name, ...values });
    void action.then((skill) => {
      setMessage("Saved a new immutable skill revision."); loadRevision(skill); onChanged();
      return fetchSkillRevisions(skill.skill_id);
    }).then(({ revisions: values }) => setRevisions(values)).catch((error) => setMessage(String(error)));
  };
  const duplicate = () => {
    setSelected(""); setRevisions([]); setParent(undefined); setName(`${name}-copy`); setBaseline("");
    setMessage("Editing a copy. Skill names use lowercase letters and hyphens.");
  };
  const remove = () => {
    if (!selected) return;
    if (!deleteArmed) { setDeleteArmed(true); setMessage("Click confirm delete to remove this skill from the library. Saved workflows keep their pinned revisions."); return; }
    void deleteAgentSkill(selected).then(() => {
      selectImmediately(""); setMessage("Skill removed; pinned revisions remain reproducible."); onChanged();
    }).catch((error) => setMessage(String(error)));
  };
  const activeRevision = revisions.find((revision) => revision.revision_id === parent);
  return (
    <div className="studio-panel-body studio-form">
      <p className="studio-help">Skills package reusable agent instructions and tool access. Every save creates a reproducible revision.</p>
      <label>skill<select value={selected} onChange={(event) => select(event.target.value)}><option value="">new skill…</option>{skills.map((skill) => <option key={skill.skill_id} value={skill.skill_id}>{skill.name}</option>)}</select></label>
      {pendingSelection !== null && <div className="unsaved-guard"><span>Discard unsaved changes?</span><button onClick={() => setPendingSelection(null)}>keep editing</button><button className="danger" onClick={() => selectImmediately(pendingSelection)}>discard &amp; switch</button></div>}
      {activeRevision && <div className="revision-context"><strong>{activeRevision.revision_id === skills.find((skill) => skill.skill_id === selected)?.revision_id ? "Current revision" : "Earlier revision"}</strong><span>{new Date(activeRevision.created_at).toLocaleString()} · saving branches from this point</span></div>}
      <label>skill name<input disabled={Boolean(selected)} value={name} onChange={(event) => setName(event.target.value.toLowerCase().replace(/[^a-z0-9-]/g, "-").replace(/-+/g, "-"))} placeholder="weekly-advisor" /></label>
      <label>description<input value={description} onChange={(event) => setDescription(event.target.value)} /></label>
      <label>agent instructions<textarea rows={12} value={instructions} onChange={(event) => setInstructions(event.target.value)} placeholder="Explain when to use this skill and the steps the agent should follow." /></label>
      <fieldset><legend>required tools</legend>{tools.map((tool) => <label key={tool.name} className="check-row"><input type="checkbox" checked={selectedTools.includes(tool.name)} onChange={(event) => setSelectedTools(event.target.checked ? [...selectedTools, tool.name] : selectedTools.filter((name) => name !== tool.name))} /><span>{tool.name}</span><small>{tool.risk}</small></label>)}</fieldset>
      <div className="studio-actions">{selected && <button className="danger" onClick={remove}>{deleteArmed ? "confirm delete" : "delete skill"}</button>}{selected && <button onClick={duplicate}>duplicate</button>}<button onClick={() => onDraft(`Draft an Agent Skill named “${name || 'untitled'}”. Purpose: ${description || 'help me define it'}. Write clear agent instructions and recommend only essential tools. Do not save it yet.`)}>draft with Reasoning</button><button className="confirm" disabled={!dirty || !name || !description || !instructions} onClick={save}>{selected ? "save new revision" : "create skill"}</button></div>
      {message && <div className="studio-message">{message}</div>}
      {revisions.length > 0 && <div className="revision-list">{revisions.map((revision, index) => <button key={revision.revision_id} className={revision.revision_id === parent ? "selected" : ""} onClick={() => dirty ? setMessage("Save or discard your changes before opening another revision.") : loadRevision(revision)}><span>Revision {index + 1}{index === revisions.length - 1 ? " · current" : ""}</span><small>{new Date(revision.created_at).toLocaleString()}</small></button>)}</div>}
      {activeRevision && <details className="registry-details"><summary>Revision details</summary><dl><dt>revision</dt><dd><code>{activeRevision.revision_id}</code></dd><dt>format</dt><dd>Open Agent Skills / SKILL.md</dd><dt>parent</dt><dd><code>{activeRevision.parent_revision_id || "first revision"}</code></dd></dl></details>}
    </div>
  );
}

export function PlanStudio({
  graph,
  projectId,
  disabled,
  onChange,
}: {
  graph: WorkflowGraph;
  projectId: string;
  disabled: boolean;
  onChange: (change: (current: WorkflowGraph) => WorkflowGraph) => void;
}) {
  const [datasets, setDatasets] = useState<DatasetHead[]>([]);
  const [casesText, setCasesText] = useState("[]");
  const [variantsText, setVariantsText] = useState("[]");
  const [evaluatorsText, setEvaluatorsText] = useState("[]");
  const [message, setMessage] = useState("");
  const cases = graph.dataset?.cases ?? [];
  const variants = graph.variants?.length
    ? graph.variants
    : [{ variant_id: "default", name: "Default", node_overrides: {} }];
  const baseAgentCount = graph.nodes.filter((node) => node.kind === "agent").length;
  const caseCount = workflowCaseCount(graph);
  const scheduledCalls = baseAgentCount * caseCount * variants.length;
  const datasetSelection = graph.dataset_ref
    ? `${graph.dataset_ref.dataset_id}@${graph.dataset_ref.revision_id}`
    : graph.dataset ? "__inline__" : "__iterations__";

  const reloadDatasets = useCallback(() => {
    void fetchDatasets(projectId)
      .then(({ datasets: values }) => setDatasets(values))
      .catch((error) => setMessage(String(error)));
  }, [projectId]);
  useEffect(reloadDatasets, [reloadDatasets]);
  useEffect(() => {
    setCasesText(JSON.stringify(cases, null, 2));
  }, [graph.dataset_ref?.revision_id, JSON.stringify(cases)]);
  useEffect(() => {
    setVariantsText(JSON.stringify(variants, null, 2));
  }, [JSON.stringify(variants)]);
  useEffect(() => {
    setEvaluatorsText(JSON.stringify(graph.evaluators ?? [], null, 2));
  }, [JSON.stringify(graph.evaluators ?? [])]);

  const chooseDataset = (value: string) => {
    setMessage("");
    if (value === "__iterations__") {
      onChange((current) => ({ ...current, dataset: undefined, dataset_ref: null }));
      return;
    }
    if (value === "__inline__") return;
    const [datasetId, revisionId] = value.split("@", 2);
    void fetchDataset(projectId, datasetId, revisionId)
      .then((dataset) => onChange((current) => ({
        ...current,
        dataset: {
          kind: "case_dataset_v1",
          schema_version: 1,
          name: dataset.name,
          description: dataset.description,
          cases: dataset.cases,
        },
        dataset_ref: { dataset_id: dataset.dataset_id, revision_id: dataset.revision_id },
        iterations: dataset.cases.length,
      })))
      .catch((error) => setMessage(String(error)));
  };

  const applyCases = () => {
    try {
      const parsed = JSON.parse(casesText) as unknown;
      if (!Array.isArray(parsed) || parsed.length < 1 || parsed.length > 1000) {
        throw new Error("Cases must be a JSON array with 1–1000 entries.");
      }
      onChange((current) => ({
        ...current,
        dataset: {
          kind: "case_dataset_v1",
          schema_version: 1,
          name: current.dataset?.name ?? `${current.name} cases`,
          description: current.dataset?.description ?? "Cases embedded in this workflow revision.",
          cases: parsed,
        },
        dataset_ref: null,
        iterations: parsed.length,
      }));
      setMessage(`${parsed.length} cases attached to this workflow revision.`);
    } catch (error) {
      setMessage(String(error));
    }
  };

  const applyVariants = () => {
    try {
      const parsed = JSON.parse(variantsText) as unknown;
      if (!Array.isArray(parsed) || parsed.length < 1 || parsed.length > 64) {
        throw new Error("Variants must be a JSON array with 1–64 entries.");
      }
      onChange((current) => ({ ...current, variants: parsed as WorkflowVariant[] }));
      setMessage(`${parsed.length} variants attached to this workflow revision.`);
    } catch (error) {
      setMessage(String(error));
    }
  };

  const applyEvaluators = () => {
    try {
      const parsed = JSON.parse(evaluatorsText) as unknown;
      if (!Array.isArray(parsed) || parsed.length > 32) {
        throw new Error("Evaluators must be a JSON array with at most 32 entries.");
      }
      onChange((current) => ({ ...current, evaluators: parsed as WorkflowEvaluator[] }));
      setMessage(`${parsed.length} evaluator${parsed.length === 1 ? "" : "s"} attached to this workflow revision.`);
    } catch (error) {
      setMessage(String(error));
    }
  };

  const saveCases = () => {
    if (!graph.dataset) return;
    setMessage("saving reusable dataset…");
    void createDataset(projectId, graph.dataset)
      .then((dataset) => {
        reloadDatasets();
        onChange((current) => ({
          ...current,
          dataset_ref: { dataset_id: dataset.dataset_id, revision_id: dataset.revision_id },
        }));
        setMessage(`Saved ${dataset.name} to the reusable dataset library.`);
      })
      .catch((error) => setMessage(String(error)));
  };

  const setEvaluator = (kind: "paper_moderation_v1" | "exact_match_v1", enabled: boolean) => {
    onChange((current) => {
      const remaining = (current.evaluators ?? []).filter((item) => item.kind !== kind);
      return {
        ...current,
        evaluators: enabled ? [...remaining, {
          evaluator_id: kind === "paper_moderation_v1" ? "paper-moderation" : "exact-match",
          kind,
          config: {},
        }] : remaining,
      };
    });
  };

  return (
    <div className="studio-panel-body studio-form workflow-plan">
      <p className="studio-help">Every workflow uses the same plan: cases × variants run through a graph of small harnesses, then local evaluators score the saved outputs. A model judge is just another downstream harness; focused studios are views of this contract.</p>
      <div className="workflow-plan-summary">
        <div><strong>{caseCount}</strong><span>cases</span></div>
        <div><strong>{variants.length}</strong><span>variants</span></div>
        <div><strong>{baseAgentCount}</strong><span>harnesses</span></div>
        <div><strong>{scheduledCalls}</strong><span>scheduled calls</span></div>
      </div>
      <label>focused view<select disabled={disabled} value={graph.presentation?.recipe ?? "graph"} onChange={(event) => onChange((current) => ({ ...current, presentation: { recipe: event.target.value as NonNullable<WorkflowGraph["presentation"]>["recipe"] } }))}><option value="graph">Organization graph</option><option value="model_comparison">Model comparison</option><option value="paper_moderation">Paper moderation</option><option value="dataset_generator">Dataset generator</option></select><small>The execution contract stays the same when the view changes.</small></label>
      <fieldset>
        <legend>cases</legend>
        <label>source<select disabled={disabled} value={datasetSelection} onChange={(event) => chooseDataset(event.target.value)}><option value="__iterations__">Repeated graph iterations</option>{graph.dataset && !graph.dataset_ref && <option value="__inline__">Cases embedded in this workflow</option>}{datasets.map((dataset) => <option key={dataset.revision_id} value={`${dataset.dataset_id}@${dataset.revision_id}`}>{dataset.name} · {dataset.revision_id.slice(0, 12)}</option>)}</select></label>
        {!graph.dataset && <label>iterations<input type="number" min="1" max="1000" disabled={disabled} value={graph.iterations} onChange={(event) => onChange((current) => ({ ...current, iterations: Math.max(1, Number(event.target.value)) }))} /></label>}
        {graph.dataset && <>
          <label>case array (JSON)<textarea rows={12} disabled={disabled} value={casesText} onChange={(event) => setCasesText(event.target.value)} /><small>Each case has case_id, name, inputs, and optional expected/metadata. Expected values are never exposed to agents.</small></label>
          <div className="studio-actions"><button disabled={disabled} onClick={applyCases}>apply cases</button><button disabled={disabled} onClick={saveCases}>save to dataset library</button><button disabled={disabled} onClick={() => onChange((current) => ({ ...current, dataset: undefined, dataset_ref: null }))}>use iterations instead</button></div>
        </>}
      </fieldset>
      <fieldset>
        <legend>variants</legend>
        <label>variant array (JSON)<textarea rows={12} disabled={disabled} value={variantsText} onChange={(event) => setVariantsText(event.target.value)} /><small>Each variant has variant_id, name, and partial node_overrides keyed by agent node ID.</small></label>
        <div className="studio-actions"><button disabled={disabled} onClick={applyVariants}>apply variants</button></div>
      </fieldset>
      <fieldset>
        <legend>local evaluators</legend>
        <label className="check-row"><input type="checkbox" disabled={disabled} checked={(graph.evaluators ?? []).some((item) => item.kind === "exact_match_v1")} onChange={(event) => setEvaluator("exact_match_v1", event.target.checked)} /><span>Exact match</span><small>Compare structured/text output with each case’s expected value.</small></label>
        <label className="check-row"><input type="checkbox" disabled={disabled} checked={(graph.evaluators ?? []).some((item) => item.kind === "paper_moderation_v1")} onChange={(event) => setEvaluator("paper_moderation_v1", event.target.checked)} /><span>Paper moderation scorecard</span><small>Safety decisions, flags, false-allow rate, schema, speed, and cost.</small></label>
        <details>
          <summary>advanced evaluator configuration</summary>
          <label>evaluator array (JSON)<textarea rows={9} disabled={disabled} value={evaluatorsText} onChange={(event) => setEvaluatorsText(event.target.value)} /><small>Optional config.node_ids selects the agent nodes to score. Without it, evaluators use agents connected to output nodes.</small></label>
          <div className="studio-actions"><button disabled={disabled} onClick={applyEvaluators}>apply evaluators</button></div>
        </details>
      </fieldset>
      <fieldset>
        <legend>execution guardrails</legend>
        <label>parallel calls<input type="number" min="1" max="8" disabled={disabled} value={graph.execution_policy?.max_concurrency ?? 1} onChange={(event) => onChange((current) => ({ ...current, execution_policy: { ...current.execution_policy, max_concurrency: Math.max(1, Math.min(8, Number(event.target.value))), failure_policy: current.execution_policy?.failure_policy ?? "fail_fast" } }))} /></label>
        <label>failure policy<select disabled={disabled} value={graph.execution_policy?.failure_policy ?? "fail_fast"} onChange={(event) => onChange((current) => ({ ...current, execution_policy: { ...current.execution_policy, max_concurrency: current.execution_policy?.max_concurrency ?? 1, failure_policy: event.target.value as "fail_fast" | "continue" } }))}><option value="fail_fast">Stop on first failure</option><option value="continue">Keep partial results</option></select></label>
        <label>maximum scheduled calls<input type="number" min="1" disabled={disabled} value={graph.execution_policy?.max_calls ?? scheduledCalls} onChange={(event) => onChange((current) => ({ ...current, execution_policy: { ...current.execution_policy, max_concurrency: current.execution_policy?.max_concurrency ?? 1, failure_policy: current.execution_policy?.failure_policy ?? "fail_fast", max_calls: Math.max(1, Number(event.target.value)) } }))} /><small>The backend refuses to start if the expanded plan exceeds this limit.</small></label>
        <label>run cost limit (USD)<input type="number" min="0.000001" step="0.01" disabled={disabled} placeholder="no run-wide limit" value={graph.execution_policy?.max_total_cost_usd ?? ""} onChange={(event) => onChange((current) => ({ ...current, execution_policy: { ...current.execution_policy, max_concurrency: current.execution_policy?.max_concurrency ?? 1, failure_policy: current.execution_policy?.failure_policy ?? "fail_fast", max_total_cost_usd: event.target.value ? Math.max(0.000001, Number(event.target.value)) : null } }))} /><small>Stops scheduling new calls once recorded spend reaches the limit; active concurrent calls may finish.</small></label>
      </fieldset>
      {message && <div className="studio-message">{message}</div>}
    </div>
  );
}

function RunStudio({
  graph,
  catalog,
  projectId,
  projectName,
  initialPrompt,
  onInitialPrompt,
  variables,
  onVariables,
  run,
  history,
  onRun,
  onCancel,
  onSelectRun,
  onPrepareAnother,
}: {
  graph: WorkflowGraph;
  catalog: Catalog;
  projectId: string;
  projectName: string | null;
  initialPrompt: string;
  onInitialPrompt: (value: string) => void;
  variables: string;
  onVariables: (value: string) => void;
  run: WorkflowRunState | null;
  history: WorkflowRunState[];
  onRun: () => void;
  onCancel: () => void;
  onSelectRun: (run: WorkflowRunState) => void;
  onPrepareAnother: () => void;
}) {
  const [confirmArmed, setConfirmArmed] = useState(false);
  const [datasetSaving, setDatasetSaving] = useState(false);
  const [datasetMessage, setDatasetMessage] = useState("");
  const [modelPreflight, setModelPreflight] = useState<WorkflowPreflight | null>(null);
  const [preflightFor, setPreflightFor] = useState("");
  const [preflightChecking, setPreflightChecking] = useState(false);
  const [preflightProblem, setPreflightProblem] = useState<string | null>(null);
  const preflightRequest = useRef(0);
  const preflightKey = useMemo(() => JSON.stringify(graph), [graph]);
  const agents = expandedAgentConfigurations(graph);
  const caseCount = workflowCaseCount(graph);
  const variantCount = graph.variants?.length || 1;
  const scheduledRuns = agents.length * caseCount;
  const maximumCost = agents.reduce(
    (total, node) => total + (node.config?.budget.max_cost_usd ?? 0),
    0,
  ) * caseCount;
  const runCostLimit = graph.execution_policy?.max_total_cost_usd ?? maximumCost;
  const issues: { level: "error" | "warning" | "ready"; text: string }[] = [];
  if (!projectName) issues.push({ level: "error", text: "Choose a target project." });
  if (agents.length === 0) issues.push({ level: "error", text: "Add at least one agent node." });
  if (graph.nodes.some((node) => node.kind === "input") && !initialPrompt.trim()) {
    issues.push({ level: "error", text: "Add the initial research prompt." });
  }
  try {
    parseObject(variables);
  } catch (error) {
    issues.push({ level: "error", text: String(error) });
  }
  for (const agent of agents) {
    const config = agent.config;
    const agentLabel = variantCount > 1 ? `${agent.name} / ${agent.variantName}` : agent.name;
    if (!config?.model.trim()) {
      issues.push({ level: "error", text: `${agentLabel} needs a model.` });
      continue;
    }
    if (config.executor_id === "codex" && !config.model_provider?.trim()) {
      issues.push({ level: "error", text: `${agentLabel} needs an explicit Codex model provider.` });
    }
    if (!config.system_prompt || !config.user_prompt) {
      issues.push({ level: "error", text: `${agentLabel} needs both system and user prompts.` });
    }
    const connection = catalog.connections.find(
      (candidate) => candidate.executor_id === config.executor_id,
    );
    if (!connection?.ready) {
      issues.push({
        level: "error",
        text: `${agentLabel}: ${connection?.readiness ?? "executor setup is unavailable"}.`,
      });
    }
  }
  if (!graph.nodes.some((node) => node.kind === "output")) {
    issues.push({ level: "warning", text: "Add an output node to make the organization’s result explicit." });
  }
  const capabilityPending = Boolean(projectId) && (preflightChecking || preflightFor !== preflightKey);
  if (capabilityPending) {
    issues.push({ level: "warning", text: "Checking model routes and capabilities…" });
  } else if (preflightProblem) {
    issues.push({ level: "error", text: `Capability preflight failed: ${preflightProblem}` });
  } else if (modelPreflight) {
    modelPreflight.issues.forEach((text) => issues.push({ level: "error", text }));
    modelPreflight.warnings.forEach((text) => issues.push({ level: "warning", text }));
    if (modelPreflight.ready) {
      issues.push({
        level: "ready",
        text: `Capability preflight checked ${modelPreflight.checks.length} effective model route${modelPreflight.checks.length === 1 ? "" : "s"}.`,
      });
    }
  }
  if (!issues.some((issue) => issue.level === "error")) {
    issues.push({ level: "ready", text: "Prompts, models, variables, and local connections are ready." });
  }
  const blocked = capabilityPending || issues.some((issue) => issue.level === "error");
  const active = run && ["pending", "running", "cancelling"].includes(run.status);
  const runCost = run?.cost ?? {
    total_usd: 0,
    priced_calls: 0,
    estimated_calls: 0,
    unpriced_calls: run?.outputs.nodes?.length ?? 0,
    by_executor: [],
  };
  const needsConfirmation = scheduledRuns >= 20 || runCostLimit >= 20;
  const completedCalls = run?.events.filter((event) => event.type === "node.completed").length ?? 0;
  const progress = scheduledRuns ? Math.min(100, (completedCalls / scheduledRuns) * 100) : 0;
  const outputs = run?.outputs.nodes ?? [];
  const terminalNodeIds = new Set(
    graph.nodes.filter((node) => node.kind === "output").map((node) => node.id),
  );
  const terminalSourceIds = new Set(
    graph.edges.filter((edge) => terminalNodeIds.has(edge.target)).map((edge) => edge.source),
  );
  const latestTerminalOutputs = [...outputs].reverse().filter((output, index, reversed) => {
    const key = `${output.work_item_id ?? output.iteration}:${output.node_id}`;
    return terminalSourceIds.has(output.node_id)
      && reversed.findIndex((candidate) => `${candidate.work_item_id ?? candidate.iteration}:${candidate.node_id}` === key) === index;
  }).reverse().slice(-12);
  const highlightedOutputs = latestTerminalOutputs.length
    ? latestTerminalOutputs
    : outputs.slice(-1);

  const runPreflight = useCallback((refresh = false) => {
    if (!projectId) {
      setModelPreflight(null);
      setPreflightProblem(null);
      return;
    }
    const request = ++preflightRequest.current;
    const targetKey = preflightKey;
    setPreflightChecking(true);
    setPreflightProblem(null);
    void preflightWorkflow(projectId, graph, refresh)
      .then((value) => {
        if (preflightRequest.current !== request) return;
        setModelPreflight(value);
        setPreflightFor(targetKey);
      })
      .catch((error) => {
        if (preflightRequest.current !== request) return;
        setModelPreflight(null);
        setPreflightFor(targetKey);
        setPreflightProblem(String(error));
      })
      .finally(() => {
        if (preflightRequest.current === request) setPreflightChecking(false);
      });
  }, [graph, preflightKey, projectId]);

  useEffect(() => runPreflight(false), [runPreflight]);
  useEffect(() => setConfirmArmed(false), [graph.workflow_id, graph.iterations, initialPrompt, variables]);

  const eventLabel = (event: Record<string, unknown>) => {
    const canonical = canonicalWorkflowEventLabel(graph, event);
    if (canonical) return canonical;
    const nodeName = graph.nodes.find((node) => node.id === event.node_id)?.name;
    const iteration = event.case_name
      ? ` · ${String(event.case_name)}`
      : event.iteration ? ` · iteration ${event.iteration}` : "";
    if (event.type === "workflow.started") return "Organization started";
    if (event.type === "iteration.started") return `Week ${event.iteration} started`;
    if (event.type === "iteration.completed") return `Week ${event.iteration} completed`;
    if (event.type === "work_item.started") return `${event.case_id} / ${event.variant_id} started`;
    if (event.type === "work_item.completed") return `${event.case_id} / ${event.variant_id} completed`;
    if (event.type === "node.started") return `${nodeName ?? event.node_id} started${iteration}`;
    if (event.type === "node.completed") return `${nodeName ?? event.node_id} completed${iteration}`;
    if (event.type === "workflow.completed") return "Organization completed";
    if (event.type === "workflow.partial") return "Organization completed with partial results";
    if (event.type === "workflow.cancelled") return "Run cancelled";
    if (event.type === "workflow.failed") return `Run failed · ${event.error ?? "unknown error"}`;
    return String(event.type ?? "run event");
  };

  const outputCard = (output: WorkflowNodeOutput, emphasized = false) => {
    const nodeName = graph.nodes.find((node) => node.id === output.node_id)?.name ?? output.node_id;
    const reportedFailure = output.status !== "completed" || output.usage?.failed === true;
    const error = output.error || (reportedFailure ? output.text : null);
    const text = error === output.text ? "" : output.text;
    const scope = output.case_name
      ? `${output.case_name}${output.variant_name && output.variant_id !== "default" ? ` · ${output.variant_name}` : ""}`
      : `iteration ${output.iteration}`;
    return (
      <article
        className={`run-output-card ${reportedFailure ? "failed" : "completed"} ${emphasized ? "emphasized" : ""}`}
        key={`${output.agent_run_id ?? `${output.node_id}-${output.iteration}`}-${emphasized ? "featured" : "history"}`}
      >
        <header>
          <strong>{nodeName}</strong>
          <span>{scope}</span>
          <small>{reportedFailure ? "failed" : "completed"}</small>
        </header>
        {error && <div className="run-output-error">{error}</div>}
        {text && <div className="run-output-text"><Markdown text={text} /></div>}
        {output.structured_output != null && (
          <pre>{JSON.stringify(output.structured_output, null, 2)}</pre>
        )}
        {!error && !text && output.structured_output == null && (
          <div className="run-output-empty">The agent returned no content.</div>
        )}
      </article>
    );
  };

  const begin = () => {
    if (needsConfirmation && !confirmArmed) {
      setConfirmArmed(true);
      return;
    }
    setConfirmArmed(false);
    onRun();
  };

  const saveGeneratedDataset = () => {
    if (!run) return;
    setDatasetSaving(true);
    setDatasetMessage("");
    void createDatasetFromRun(projectId, run.run_id)
      .then((dataset) => setDatasetMessage(`Saved ${dataset.name} · ${dataset.cases.length} cases`))
      .catch((error) => setDatasetMessage(String(error)))
      .finally(() => setDatasetSaving(false));
  };

  return (
    <div className="studio-panel-body studio-form">
      <div className="run-summary"><strong>{scheduledRuns}</strong><span>agent calls · {graph.nodes.filter((node) => node.kind === "agent").length} agents × {caseCount} cases × {variantCount} variants</span></div>
      <div className="run-target"><span>target project</span><strong>{projectName ?? "none selected"}</strong></div>
      <div className="run-explanation">
        Reasoning saves the current graph, then follows its dependencies for every iteration.
        {` Ready sibling branches run with up to ${graph.execution_policy?.max_concurrency ?? 1} concurrent call${(graph.execution_policy?.max_concurrency ?? 1) === 1 ? "" : "s"}. `}
        Each node receives its exact pinned prompts plus the context policy you configured.
        This run can spend up to ${runCostLimit.toFixed(2)} across {graph.execution_policy?.max_total_cost_usd != null ? "the run-wide limit" : "node budgets"}. Cancellation
        finishes active agent calls, then stops before the next dependency layer.
      </div>
      <div className="run-agent-breakdown">
        {agents.map((agent) => {
          const connection = catalog.connections.find((candidate) => candidate.executor_id === agent.config?.executor_id);
          return <div key={`${agent.id}:${agent.variantId}`}><strong>{agent.name}{variantCount > 1 ? ` / ${agent.variantName}` : ""}</strong><span>{connection?.provider ?? agent.config?.executor_id}{agent.config?.model_provider ? ` / ${agent.config.model_provider}` : ""} · {agent.config?.model}</span><small>up to ${(agent.config?.budget.max_cost_usd ?? 0).toFixed(2)} / call · {connection?.ready ? "ready" : "setup required"}</small></div>;
        })}
      </div>
      <label>
        initial research prompt
        <textarea
          rows={7}
          value={initialPrompt}
          onChange={(event) => onInitialPrompt(event.target.value)}
          placeholder="State the research question, conjecture, or brief this organization should work on."
        />
        <small>
          Available to prompt templates as {"{{question}}"}, {"{{prompt}}"}, {"{{input}}"},
          and by the ID of each input node.
        </small>
      </label>
      <details className="run-advanced">
        <summary>advanced variables (JSON)</summary>
        <label><textarea rows={7} value={variables} onChange={(event) => onVariables(event.target.value)} /></label>
      </details>
      <div className="run-preflight-heading">
        <span>preflight</span>
        <button type="button" disabled={preflightChecking || !projectId} onClick={() => runPreflight(true)}>
          {preflightChecking ? "checking…" : "refresh capabilities"}
        </button>
      </div>
      <div className="run-preflight" aria-live="polite">
        {issues.map((issue, index) => <div className={`preflight-${issue.level}`} key={`${issue.text}-${index}`}><span>{issue.level === "ready" ? "✓" : issue.level === "warning" ? "!" : "×"}</span>{issue.text}</div>)}
      </div>
      <button
        className={`confirm ${confirmArmed ? "confirm-armed" : ""}`}
        disabled={Boolean(active) || blocked}
        onClick={begin}
      >
        {active
          ? run?.status === "cancelling" ? "stopping after current call…" : "running…"
          : confirmArmed
            ? `confirm ${scheduledRuns} calls · up to $${runCostLimit.toFixed(2)}`
            : "review and start run"}
      </button>
      {run && <>
        <div className={`run-status run-${run.status}`}><span>{run.status}{run.error ? ` · ${run.error}` : ""}</span><strong>{completedCalls} / {scheduledRuns} calls</strong></div>
        <div className="run-progress"><span style={{ width: `${progress}%` }} /></div>
        <div className="run-cost-summary">
          <div>
            <span>recorded price</span>
            <strong>${runCost.total_usd.toFixed(6)}</strong>
          </div>
          <small>
            {runCost.priced_calls} priced call{runCost.priced_calls === 1 ? "" : "s"}
            {runCost.estimated_calls ? ` · ${runCost.estimated_calls} estimated` : ""}
            {runCost.unpriced_calls ? ` · ${runCost.unpriced_calls} not reported` : ""}
          </small>
          {runCost.by_executor.length > 0 && (
            <details>
              <summary>price breakdown</summary>
              {runCost.by_executor.map((item) => (
                <div key={`${item.executor_id}:${item.model_provider ?? "default"}:${item.model}`}>
                  <span>{item.executor_id}{item.model_provider ? ` / ${item.model_provider}` : ""} · {item.model}</span>
                  <strong>${item.total_usd.toFixed(6)}</strong>
                </div>
              ))}
            </details>
          )}
        </div>
        <section className="run-output" aria-live="polite">
          <div className="run-output-heading">
            <strong>{latestTerminalOutputs.length ? "workflow result" : "latest agent output"}</strong>
            <span>{outputs.length ? `${outputs.length} result${outputs.length === 1 ? "" : "s"} saved` : "waiting for the first agent"}</span>
          </div>
          {highlightedOutputs.map((output) => outputCard(output, true))}
          {outputs.length > highlightedOutputs.length && (
            <details className="run-output-history">
              <summary>all agent outputs</summary>
              <div>{[...outputs].reverse().map((output) => outputCard(output))}</div>
            </details>
          )}
        </section>
        <div className="run-actions">
          {active && <button className="danger" disabled={run.status === "cancelling"} onClick={onCancel}>{run.status === "cancelling" ? "stopping…" : "stop run"}</button>}
          {run.artifact_path && <><button onClick={() => window.dispatchEvent(new CustomEvent("artifact-open", { detail: run.artifact_path }))}>open saved report</button><RevealArtifactButton projectId={projectId} name={run.artifact_path} /></>}
          {!active && graph.presentation?.recipe === "dataset_generator" && <button disabled={datasetSaving || run.status !== "completed"} onClick={saveGeneratedDataset}>{datasetSaving ? "saving dataset…" : "save cases as reusable dataset"}</button>}
          {!active && <button onClick={onPrepareAnother}>prepare another run</button>}
        </div>
        {datasetMessage && <div className="studio-message">{datasetMessage}</div>}
        <div className="run-events">{run.events.slice(-12).reverse().map((event, index) => <div key={String(event.sequence ?? index)}><span>{eventLabel(event)}</span><time>{event.at ? new Date(String(event.at)).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" }) : ""}</time></div>)}</div>
      </>}
      {history.length > 0 && <details className="run-history"><summary>recent runs</summary><div>{history.map((item) => <button key={item.run_id} className={item.run_id === run?.run_id ? "selected" : ""} onClick={() => onSelectRun(item)}><span>{item.status} · {item.iterations} iterations{item.cost ? ` · $${item.cost.total_usd.toFixed(6)}` : ""}</span><time>{item.created_at ? new Date(item.created_at).toLocaleString() : item.run_id.slice(0, 16)}</time></button>)}</div></details>}
    </div>
  );
}

export function ComparisonStudio({
  graph,
  catalog,
  projectId,
  projectName,
  initialPrompt,
  onInitialPrompt,
  run,
  history,
  onRun,
  onCancel,
  onSelectRun,
  onPrepareAnother,
  onChangeAgent,
  onAddAgent,
  onRemoveAgent,
  onSetConfigurations,
  onEditGraph,
  starting = false,
}: {
  graph: WorkflowGraph;
  catalog: Catalog;
  projectId: string;
  projectName: string;
  initialPrompt: string;
  onInitialPrompt: (value: string) => void;
  run: WorkflowRunState | null;
  history: WorkflowRunState[];
  onRun: () => void;
  onCancel: () => void;
  onSelectRun: (run: WorkflowRunState) => void;
  onPrepareAnother: () => void;
  onChangeAgent: (node: WorkflowNode) => void;
  onAddAgent: () => void;
  onRemoveAgent: (nodeId: string) => void;
  onSetConfigurations: (configurations: ModelRankingConfiguration[]) => void;
  onEditGraph: () => void;
  starting?: boolean;
}) {
  const agents = variantFanoutAgents(graph);
  const sources = modelSourcesFor(catalog);
  const active = starting || Boolean(run && ["pending", "running", "cancelling"].includes(run.status));
  const [modelCatalog, setModelCatalog] = useState<ModelPriceCatalog | null>(null);
  const [catalogChecking, setCatalogChecking] = useState(false);
  const [catalogProblem, setCatalogProblem] = useState<string | null>(null);
  const [rankings, setRankings] = useState<WorkflowModelRankings | null>(null);
  const [rankingsLoading, setRankingsLoading] = useState(true);
  const [rankingsProblem, setRankingsProblem] = useState<string | null>(null);
  const [recommendationCount, setRecommendationCount] = useState(4);
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    let cancelled = false;
    setCatalogChecking(true);
    setCatalogProblem(null);
    fetchModelPrices("account")
      .catch(() => fetchModelPrices("all"))
      .then((value) => {
        if (!cancelled) setModelCatalog(value);
      })
      .catch((error) => {
        if (!cancelled) setCatalogProblem(String(error));
      })
      .finally(() => {
        if (!cancelled) setCatalogChecking(false);
      });
    return () => { cancelled = true; };
  }, []);
  useEffect(() => {
    let cancelled = false;
    setRankingsLoading(true);
    setRankingsProblem(null);
    void fetchWorkflowModelRankings(projectId, graph.workflow_id)
      .then((value) => { if (!cancelled) setRankings(value); })
      .catch((error) => {
        if (!cancelled) {
          setRankings(null);
          setRankingsProblem(String(error));
        }
      })
      .finally(() => { if (!cancelled) setRankingsLoading(false); });
    return () => { cancelled = true; };
  }, [graph.workflow_id, history.length, projectId, run?.updated_at]);
  const rankingConfigurations = useMemo(
    () => new Map((rankings?.configurations ?? []).map((configuration) => [configuration.configuration_id, configuration])),
    [rankings],
  );
  const eligibleModeConfigurations = useMemo(() => new Map(
    (rankings?.modes ?? []).map((mode) => [mode.mode_id, mode.configuration_ids
      .map((configurationId) => rankingConfigurations.get(configurationId))
      .filter((configuration): configuration is ModelRankingConfiguration => {
        if (!configuration) return false;
        const source = sources.find((candidate) => candidate.executorId === configuration.executor_id);
        if (!source?.ready) return false;
        if (configuration.executor_id === "codex" && !configuration.model_provider?.trim()) return false;
        if (configuration.executor_id !== "reasoning-native") return true;
        const model = modelCatalog?.models.find((candidate) => candidate.model_id === configuration.model);
        return Boolean(model && !isAgenticHarnessOnly(model.description));
      })],
    ),
  ), [modelCatalog, rankingConfigurations, rankings, sources]);
  useEffect(() => {
    if (!active) return;
    setNow(Date.now());
    const timer = window.setInterval(() => setNow(Date.now()), 1_000);
    return () => window.clearInterval(timer);
  }, [active]);
  const issues: string[] = [];
  if (agents.length < 2) issues.push("Choose at least two models.");
  if (!initialPrompt.trim()) issues.push("Enter the question to compare.");
  for (const agent of agents) {
    const source = sources.find((candidate) => candidate.executorId === agent.config.executor_id);
    const modelRecord = modelCatalog?.models.find(
      (candidate) => candidate.model_id === agent.config.model,
    );
    if (!agent.config.model.trim()) issues.push(`${agent.name} needs a model.`);
    if (!source?.ready) issues.push(`${agent.name}: ${source?.readiness ?? "setup is unavailable"}.`);
    if (agent.config.executor_id === "codex" && !agent.config.model_provider?.trim()) {
      issues.push(`${agent.name} needs an explicit Codex model provider.`);
    }
    if (
      agent.config.executor_id === "reasoning-native" &&
      modelCatalog &&
      modelCatalog.source.status !== "stale" &&
      !modelCatalog.models.some((candidate) => candidate.model_id === agent.config.model)
    ) {
      issues.push(
        `${agent.name}: ${agent.config.model} is not in the current OpenRouter catalog; it may have been retired or renamed.`,
      );
    }
    if (
      agent.config.executor_id === "reasoning-native"
      && modelRecord
      && isAgenticHarnessOnly(modelRecord.description)
    ) {
      issues.push(
        `${agent.name}: ${agent.config.model} is restricted to agentic harnesses; use Codex or choose an unrestricted model.`,
      );
    }
  }
  const scheduledCalls = agents.length * graph.iterations;
  const maximumCost = agents.reduce(
    (total, agent) => total + (agent.config.budget.max_cost_usd ?? 0),
    0,
  ) * graph.iterations;
  const completedCalls = run?.events.filter((event) => event.type === "node.completed").length ?? 0;
  const progress = scheduledCalls ? Math.min(100, completedCalls / scheduledCalls * 100) : 0;
  const outputs = run?.outputs.nodes ?? [];
  const reasoningByAgent = useMemo(() => workflowReasoningBySubject(run), [run]);
  const latestOutputs = new Map<string, WorkflowNodeOutput>();
  for (const output of outputs) latestOutputs.set(
    output.variant_id && output.variant_id !== "default" ? output.variant_id : output.node_id,
    output,
  );
  const runningNodes = new Set(
    run?.events
      .filter((event) => event.type === "node.started")
      .map((event) => String(event.variant_id && event.variant_id !== "default" ? event.variant_id : event.node_id)) ?? [],
  );
  for (const output of outputs) runningNodes.delete(output.node_id);
  const startedAt = new Map<string, number>();
  for (const event of run?.events ?? []) {
    if (event.type !== "node.started" || typeof event.node_id !== "string") continue;
    const subjectId = typeof event.variant_id === "string" && event.variant_id !== "default"
      ? event.variant_id
      : event.node_id;
    const timestamp = typeof event.at === "string" ? Date.parse(event.at) : Number.NaN;
    if (Number.isFinite(timestamp)) startedAt.set(subjectId, timestamp);
  }

  const tokensFor = (output: WorkflowNodeOutput | undefined) => {
    if (!output?.usage) return null;
    for (const key of ["total_tokens", "totalTokens", "tokens"]) {
      const value = output.usage[key];
      if (typeof value === "number") return value;
    }
    const input = output.usage.input_tokens ?? output.usage.prompt_tokens;
    const generated = output.usage.output_tokens ?? output.usage.completion_tokens;
    return typeof input === "number" && typeof generated === "number" ? input + generated : null;
  };
  const reasoningTokensFor = (output: WorkflowNodeOutput | undefined) => {
    if (!output?.usage) return null;
    for (const key of ["reasoning_tokens", "reasoningTokens"]) {
      const value = output.usage[key];
      if (typeof value === "number") return value;
    }
    for (const key of ["completion_tokens_details", "output_tokens_details"]) {
      const details = output.usage[key];
      if (details && typeof details === "object") {
        const value = (details as Record<string, unknown>).reasoning_tokens;
        if (typeof value === "number") return value;
      }
    }
    return null;
  };

  return (
    <main className="comparison-studio">
      <header className="comparison-header">
        <div>
          <span className="comparison-eyebrow">model comparison · {projectName}</span>
          <h2>{graph.name}</h2>
          <p>One pinned prompt goes to every model independently. Results survive sibling failures and are saved with cost and provenance.</p>
        </div>
        <button onClick={onEditGraph}>edit graph</button>
      </header>

      <section className="comparison-setup" aria-labelledby="comparison-question-title">
        <div className="comparison-section-heading">
          <div>
            <span>01</span>
            <strong id="comparison-question-title">Question</strong>
          </div>
          <small>identical input for every model</small>
        </div>
        <textarea
          rows={6}
          value={initialPrompt}
          disabled={active}
          onChange={(event) => onInitialPrompt(event.target.value)}
          placeholder="Ask the question you want each model to answer independently."
        />
      </section>

      <section className="comparison-setup" aria-labelledby="comparison-models-title">
        <div className="comparison-section-heading">
          <div>
            <span>02</span>
            <strong id="comparison-models-title">Models</strong>
          </div>
          <small>{agents.length} selected · up to ${maximumCost.toFixed(2)}</small>
        </div>
        <div className="comparison-recommendations">
          <div>
            <strong>Observed recommendations</strong>
            <span>{rankingsLoading
              ? "Reading compatible run evidence…"
              : rankingsProblem
                ? "Recommendations unavailable"
                : `${rankings?.compatible_runs ?? 0} compatible runs · task-specific evidence`}</span>
          </div>
          <label>
            configurations
            <select
              value={recommendationCount}
              disabled={active}
              onChange={(event) => setRecommendationCount(Number(event.target.value))}
            >
              {[2, 3, 4, 5, 6].map((value) => <option value={value} key={value}>{value}</option>)}
            </select>
          </label>
          <div className="comparison-recommendation-actions">
            {(rankings?.modes ?? []).map((mode) => {
              const configurations = eligibleModeConfigurations.get(mode.mode_id) ?? [];
              return (
                <button
                  disabled={active || configurations.length < 2}
                  title={mode.description}
                  onClick={() => onSetConfigurations(configurations.slice(0, recommendationCount))}
                  key={mode.mode_id}
                >
                  {mode.label}
                </button>
              );
            })}
          </div>
          <small>Recommendations preserve the tested executor, provider, model, and reasoning effort. Quality and value require evaluator output.</small>
        </div>
        <div className="comparison-model-list">
          {agents.map((agent, index) => {
            const source = sources.find((candidate) => candidate.executorId === agent.config.executor_id);
            return (
              <div className="comparison-model-row" key={agent.id}>
                <span className="comparison-model-index">{String.fromCharCode(65 + index)}</span>
                <div>
                  <AgentModelPicker
                    sources={sources}
                    executorId={agent.config.executor_id}
                    model={agent.config.model}
                    disabled={active}
                    showMetadata={false}
                    priceTargetLabel={`comparison model ${String.fromCharCode(65 + index)}`}
                    rankingContext={{ projectId, workflowId: graph.workflow_id }}
                    onChange={(executor_id, model) => onChangeAgent({
                      ...agent,
                      config: {
                        ...agent.config,
                        executor_id,
                        model,
                        model_provider: executor_id === "codex"
                          ? (agent.config.executor_id === "codex" ? agent.config.model_provider || "openai" : "openai")
                          : null,
                      },
                    })}
                  />
                  <small className={source?.ready ? "comparison-ready" : "comparison-setup-needed"}>
                    {source?.ready ? "ready" : "setup required"} · {source?.provider ?? agent.config.executor_id}
                    {source?.promptFidelity ? ` · ${source.promptFidelity}` : ""}
                  </small>
                  {agent.config.executor_id === "codex" && (
                    <label className="comparison-provider">
                      Codex provider
                      <input
                        list={`comparison-codex-providers-${agent.id}`}
                        value={agent.config.model_provider ?? ""}
                        disabled={active}
                        onChange={(event) => onChangeAgent({
                          ...agent,
                          config: { ...agent.config, model_provider: event.target.value },
                        })}
                        placeholder="openai or openrouter"
                      />
                      <datalist id={`comparison-codex-providers-${agent.id}`}>
                        <option value="openai" />
                        <option value="openrouter" />
                      </datalist>
                    </label>
                  )}
                </div>
                <button
                  className="comparison-remove"
                  disabled={active || agents.length <= 2}
                  onClick={() => onRemoveAgent(agent.id)}
                  aria-label={`Remove ${agent.name}`}
                >
                  remove
                </button>
              </div>
            );
          })}
        </div>
        <button className="comparison-add" disabled={active || agents.length >= 6} onClick={onAddAgent}>
          + add another model
        </button>
      </section>

      <section className="comparison-launch">
        <div>
          {issues.length > 0
            ? issues.map((issue) => <span className="comparison-issue" key={issue}>× {issue}</span>)
            : <span className="comparison-ok">✓ {scheduledCalls} calls ready · branches run concurrently</span>}
          {catalogChecking && <span className="comparison-catalog-note">checking the live OpenRouter catalog…</span>}
          {catalogProblem && <span className="comparison-catalog-note">catalog check unavailable · provider will decide at run time</span>}
          {modelCatalog?.source.status === "stale" && <span className="comparison-catalog-note">using a stale model catalog · missing models are not blocked</span>}
        </div>
        <button className="confirm" disabled={active || catalogChecking || issues.length > 0} onClick={onRun}>
          {starting ? "checking models…" : active ? (run?.status === "cancelling" ? "stopping…" : "comparing…") : "compare models"}
        </button>
      </section>

      {run && (
        <section className="comparison-results" aria-live="polite">
          <div className="comparison-section-heading">
            <div><span>03</span><strong>Results</strong></div>
            <small>{completedCalls} / {scheduledCalls} complete · ${run.cost?.total_usd.toFixed(6) ?? "0.000000"}</small>
          </div>
          <div className={`run-status run-${run.status}`}>
            <span>{run.status}{run.error ? ` · ${run.error}` : ""}</span>
            <strong>{completedCalls} / {scheduledCalls} calls</strong>
          </div>
          <div className="run-progress"><span style={{ width: `${progress}%` }} /></div>
          <div className="comparison-result-grid">
            {agents.map((agent, index) => {
              const output = latestOutputs.get(agent.id);
              const reasoning = reasoningByAgent.get(agent.id);
              const failed = output && output.status !== "completed";
              const status = output ? (failed ? "failed" : "completed") : runningNodes.has(agent.id) ? "running" : "queued";
              const tokens = tokensFor(output);
              const reasoningTokens = reasoningTokensFor(output);
              const nodeStartedAt = startedAt.get(agent.id);
              const elapsed = nodeStartedAt == null ? null : Math.max(0, (now - nodeStartedAt) / 1_000);
              return (
                <article className={`comparison-result-card ${status}`} key={`${run.run_id}-${agent.id}`}>
                  <header>
                    <span>{String.fromCharCode(65 + index)}</span>
                    <div>
                      <strong>{agent.config.model}</strong>
                      <small>{agent.config.executor_id}{agent.config.model_provider ? ` / ${agent.config.model_provider}` : ""}</small>
                    </div>
                    <em>{status}</em>
                  </header>
                  {output ? (
                    <>
                      <dl className="comparison-provenance">
                        <div><dt>time</dt><dd>{output.duration_seconds != null ? `${output.duration_seconds.toFixed(2)}s` : "—"}</dd></div>
                        <div><dt>tokens</dt><dd>{tokens?.toLocaleString() ?? "—"}</dd></div>
                        <div><dt>reasoning</dt><dd>{reasoningTokens?.toLocaleString() ?? "—"}</dd></div>
                        <div><dt>cost</dt><dd>{output.cost ? `$${output.cost.amount_usd.toFixed(6)}` : failed ? "failed before usage" : "not reported"}</dd></div>
                        <div><dt>resolved</dt><dd>{output.resolved_model ?? "—"}</dd></div>
                        <div><dt>served by</dt><dd>{output.serving_provider ?? "—"}</dd></div>
                        <div><dt>finish</dt><dd>{output.finish_reason ?? "—"}</dd></div>
                        <div><dt>prompt</dt><dd>{output.prompt_fidelity?.replaceAll("_", " ") ?? "—"}</dd></div>
                        <div><dt>price</dt><dd>{output.cost?.basis.replaceAll("_", " ") ?? "—"}</dd></div>
                      </dl>
                      {output.error && <div className="run-output-error">{output.error}</div>}
                      {reasoning && (
                        <details className="comparison-reasoning">
                          <summary>provider reasoning</summary>
                          <div><Markdown text={reasoning} /></div>
                        </details>
                      )}
                      {output.text && <div className="run-output-text"><Markdown text={output.text} /></div>}
                      {!output.error && !output.text && <div className="run-output-empty">The model returned no content.</div>}
                    </>
                  ) : (
                    <>
                      {reasoning && (
                        <details className="comparison-reasoning" open={status === "running"}>
                          <summary>provider reasoning</summary>
                          <div><Markdown text={reasoning} /></div>
                        </details>
                      )}
                      <div className="comparison-waiting">
                        <i />{status === "running" ? `Waiting for this model${elapsed == null ? "" : ` · ${elapsed.toFixed(0)}s`}…` : "Queued for execution"}
                      </div>
                    </>
                  )}
                </article>
              );
            })}
          </div>
          <div className="run-actions">
            {active && <button className="danger" disabled={run.status === "cancelling"} onClick={onCancel}>{run.status === "cancelling" ? "stopping…" : "stop comparison"}</button>}
            {run.artifact_path && <><button onClick={() => window.dispatchEvent(new CustomEvent("artifact-open", { detail: run.artifact_path }))}>open saved report</button><RevealArtifactButton projectId={projectId} name={run.artifact_path} /></>}
            {!active && <button onClick={onPrepareAnother}>compare another question</button>}
          </div>
        </section>
      )}

      {history.length > 0 && (
        <details className="run-history comparison-history">
          <summary>recent comparisons</summary>
          <div>{history.map((item) => <button key={item.run_id} className={item.run_id === run?.run_id ? "selected" : ""} onClick={() => onSelectRun(item)}><span>{item.status} · {item.cost ? `$${item.cost.total_usd.toFixed(6)}` : "price pending"}</span><time>{item.created_at ? new Date(item.created_at).toLocaleString() : item.run_id.slice(0, 16)}</time></button>)}</div>
        </details>
      )}
    </main>
  );
}

const MODERATION_FLAGS: { value: PaperModerationFlag; label: string }[] = [
  { value: "malicious_code", label: "malicious code" },
  { value: "prompt_injection", label: "prompt injection" },
  { value: "slop", label: "slop" },
  { value: "offensive_content", label: "offensive content" },
];

function modelRate(model: ModelPriceInfo): number {
  if (model.input_mtok_usd == null || model.output_mtok_usd == null) return Number.POSITIVE_INFINITY;
  return model.input_mtok_usd + model.output_mtok_usd;
}

function benchmarkPercent(value: number | null): string {
  return value == null ? "—" : `${(value * 100).toFixed(1)}%`;
}

export function BenchmarkStudio({
  graph,
  projectId,
  projectName,
  run,
  history,
  onRun,
  onCancel,
  onSelectRun,
  onPrepareAnother,
  onChangeBenchmark,
  onSetModels,
  onAttachDataset,
  onEditGraph,
  starting = false,
}: {
  graph: WorkflowGraph;
  projectId: string;
  projectName: string;
  run: WorkflowRunState | null;
  history: WorkflowRunState[];
  onRun: () => void;
  onCancel: () => void;
  onSelectRun: (run: WorkflowRunState) => void;
  onPrepareAnother: () => void;
  onChangeBenchmark: (benchmark: PaperModerationBenchmark) => void;
  onSetModels: (models: string[]) => void;
  onAttachDataset: (dataset: DatasetRevision) => void;
  onEditGraph: () => void;
  starting?: boolean;
}) {
  const benchmark = graph.benchmark ?? (graph.dataset ? {
    kind: "paper_moderation_v1" as const,
    schema_version: 1 as const,
    cases: graph.dataset.cases.map((item) => ({
      case_id: item.case_id,
      name: item.name,
      paper_text: String(item.inputs.paper_text ?? ""),
      expected: item.expected as PaperModerationCase["expected"],
    })),
  } : undefined);
  const agents = variantFanoutAgents(graph);
  const selectedModels = agents.map((agent) => agent.config.model);
  const active = starting || Boolean(run && ["pending", "running", "cancelling"].includes(run.status));
  const [catalog, setCatalog] = useState<ModelPriceCatalog | null>(null);
  const [catalogProblem, setCatalogProblem] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const [datasets, setDatasets] = useState<DatasetHead[]>([]);
  const [selectedDataset, setSelectedDataset] = useState("");
  const [datasetMessage, setDatasetMessage] = useState("");
  const [rankings, setRankings] = useState<WorkflowModelRankings | null>(null);
  const [rankingsLoading, setRankingsLoading] = useState(true);
  const [rankingsProblem, setRankingsProblem] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setCatalogProblem(null);
    fetchModelPrices("account")
      .catch(() => fetchModelPrices("all"))
      .then((value) => { if (!cancelled) setCatalog(value); })
      .catch((error) => { if (!cancelled) setCatalogProblem(String(error)); });
    return () => { cancelled = true; };
  }, []);

  useEffect(() => {
    void fetchDatasets(projectId)
      .then(({ datasets: values }) => setDatasets(values))
      .catch(() => setDatasets([]));
  }, [projectId]);

  useEffect(() => {
    let cancelled = false;
    setRankingsLoading(true);
    setRankingsProblem(null);
    void fetchWorkflowModelRankings(projectId, graph.workflow_id)
      .then((value) => { if (!cancelled) setRankings(value); })
      .catch((error) => {
        if (!cancelled) {
          setRankings(null);
          setRankingsProblem(String(error));
        }
      })
      .finally(() => { if (!cancelled) setRankingsLoading(false); });
    return () => { cancelled = true; };
  }, [graph.workflow_id, history.length, projectId, run?.updated_at]);

  const testedModeModels = useMemo(() => {
    const configurations = new Map(
      (rankings?.configurations ?? []).map((configuration) => [configuration.configuration_id, configuration]),
    );
    const eligibleModelIds = new Set((catalog?.models ?? [])
      .filter((model) => model.input_modalities.includes("text")
        && model.output_modalities.includes("text")
        && (!model.expiration_date || Date.parse(model.expiration_date) > Date.now())
        && !isAgenticHarnessOnly(model.description))
      .map((model) => model.model_id));
    return new Map((rankings?.modes ?? []).map((mode) => {
      const modelIds = mode.configuration_ids
        .map((configurationId) => configurations.get(configurationId))
        .filter((configuration): configuration is ModelRankingConfiguration => (
          configuration?.executor_id === "reasoning-native"
          && eligibleModelIds.has(configuration.model)
        ))
        .map((configuration) => configuration.model);
      return [mode.mode_id, [...new Set(modelIds)].slice(0, 12)];
    }));
  }, [catalog, rankings]);

  const eligibleModels = useMemo(() => (catalog?.models ?? [])
    .filter((model) => {
      const textIn = model.input_modalities.includes("text");
      const textOut = model.output_modalities.includes("text");
      const current = !model.expiration_date || Date.parse(model.expiration_date) > Date.now();
      return textIn && textOut && current && !isAgenticHarnessOnly(model.description);
    })
    .sort((left, right) => modelRate(left) - modelRate(right) || left.model_id.localeCompare(right.model_id)), [catalog]);
  const matchingModels = useMemo(() => {
    const query = search.trim().toLocaleLowerCase();
    if (!query) return eligibleModels.slice(0, 24);
    return eligibleModels
      .filter((model) => `${model.model_id} ${model.name} ${model.provider}`.toLocaleLowerCase().includes(query))
      .slice(0, 36);
  }, [eligibleModels, search]);

  const chooseCheapest = () => {
    onSetModels(eligibleModels.filter((model) => Number.isFinite(modelRate(model))).slice(0, 12).map((model) => model.model_id));
  };
  const chooseProviderSpread = () => {
    const choices = new Map<string, string>();
    for (const model of eligibleModels) {
      if (!choices.has(model.provider)) choices.set(model.provider, model.model_id);
      if (choices.size >= 12) break;
    }
    onSetModels([...choices.values()]);
  };
  const toggleModel = (modelId: string) => {
    if (selectedModels.includes(modelId)) onSetModels(selectedModels.filter((value) => value !== modelId));
    else if (selectedModels.length < 24) onSetModels([...selectedModels, modelId]);
  };

  const updateCase = (index: number, change: Partial<PaperModerationCase>) => {
    if (!benchmark) return;
    onChangeBenchmark({
      ...benchmark,
      cases: benchmark.cases.map((item, candidate) => candidate === index ? { ...item, ...change } : item),
    });
  };
  const toggleFlag = (index: number, flag: PaperModerationFlag) => {
    if (!benchmark) return;
    const current = benchmark.cases[index];
    const flags = current.expected.flags.includes(flag)
      ? current.expected.flags.filter((value) => value !== flag)
      : [...current.expected.flags, flag];
    updateCase(index, { expected: { ...current.expected, flags } });
  };
  const addCase = () => {
    if (!benchmark || benchmark.cases.length >= 200) return;
    onChangeBenchmark({
      ...benchmark,
      cases: [...benchmark.cases, {
        case_id: `case-${crypto.randomUUID().slice(0, 8)}`,
        name: "New moderation case",
        paper_text: "Paste or write the untrusted paper excerpt here.",
        expected: { decision: "review", flags: [] },
      }],
    });
  };
  const removeCase = (index: number) => {
    if (!benchmark || benchmark.cases.length <= 1) return;
    onChangeBenchmark({ ...benchmark, cases: benchmark.cases.filter((_item, candidate) => candidate !== index) });
  };
  const saveCorpus = () => {
    if (!benchmark) return;
    setDatasetMessage("saving corpus…");
    void createDataset(projectId, {
      name: `${graph.name} corpus`,
      description: "Reusable paper moderation cases.",
      cases: benchmark.cases.map((item) => ({
        case_id: item.case_id,
        name: item.name,
        inputs: { paper_text: item.paper_text },
        expected: item.expected,
      })),
    }).then((dataset) => {
      onAttachDataset(dataset);
      setDatasets((current) => [{
        dataset_id: dataset.dataset_id,
        revision_id: dataset.revision_id,
        name: dataset.name,
        created_at: dataset.created_at,
        updated_at: dataset.created_at,
      }, ...current.filter((item) => item.dataset_id !== dataset.dataset_id)]);
      setSelectedDataset(dataset.dataset_id);
      setDatasetMessage(`saved ${dataset.revision_id.slice(0, 16)}`);
    }).catch((error) => setDatasetMessage(String(error)));
  };
  const loadCorpus = () => {
    if (!selectedDataset) return;
    setDatasetMessage("loading corpus…");
    void fetchDataset(projectId, selectedDataset).then((dataset) => {
      onAttachDataset(dataset);
      setDatasetMessage(`loaded ${dataset.revision_id.slice(0, 16)}`);
    }).catch((error) => setDatasetMessage(String(error)));
  };

  const caseCount = benchmark?.cases.length ?? 0;
  const scheduledCalls = caseCount * agents.length;
  const completedCalls = run?.outputs.nodes?.length ?? 0;
  const progress = scheduledCalls ? Math.min(100, completedCalls / scheduledCalls * 100) : 0;
  const maximumCost = agents.reduce((sum, agent) => sum + (agent.config.budget.max_cost_usd ?? 0), 0) * caseCount;
  const issues: string[] = [];
  if (!benchmark || benchmark.kind !== "paper_moderation_v1") issues.push("This workflow needs a paper moderation corpus.");
  if (caseCount < 2) issues.push("Add at least two benchmark cases.");
  if (agents.length < 2) issues.push("Select at least two models.");
  if (agents.length > 24) issues.push("Benchmarks support at most 24 models at once.");
  const scorecard = run?.outputs.benchmark;

  return (
    <main className="comparison-studio benchmark-studio">
      <header className="comparison-header">
        <div>
          <span className="comparison-eyebrow">paper moderation benchmark · {projectName}</span>
          <h2>{graph.name}</h2>
          <p>Every model sees the same versioned, untrusted cases with no tools or network. Expected answers remain hidden; results are scored with safety errors, format reliability, latency, and realized cost.</p>
        </div>
        <button onClick={onEditGraph}>edit graph</button>
      </header>

      <section className="comparison-setup" aria-labelledby="benchmark-corpus-title">
        <div className="comparison-section-heading">
          <div><span>01</span><strong id="benchmark-corpus-title">Moderation corpus</strong></div>
          <small>{caseCount} versioned cases · answer key hidden at run time</small>
        </div>
        <div className="benchmark-case-list">
          {benchmark?.cases.map((item, index) => (
            <details className="benchmark-case" key={item.case_id} open={index === 0}>
              <summary>
                <span>{String(index + 1).padStart(2, "0")}</span>
                <strong>{item.name}</strong>
                <em>{item.expected.decision} · {item.expected.flags.length ? item.expected.flags.join(", ") : "clean"}</em>
              </summary>
              <div className="benchmark-case-editor">
                <label>Case name<input disabled={active} value={item.name} onChange={(event) => updateCase(index, { name: event.target.value })} /></label>
                <label>Stable ID<input disabled={active} value={item.case_id} onChange={(event) => updateCase(index, { case_id: event.target.value })} /></label>
                <label className="benchmark-paper-text">Untrusted TeX, Markdown, abstract, or excerpt<textarea rows={7} disabled={active} value={item.paper_text} onChange={(event) => updateCase(index, { paper_text: event.target.value })} /></label>
                <label>Expected decision<select disabled={active} value={item.expected.decision} onChange={(event) => updateCase(index, { expected: { ...item.expected, decision: event.target.value as PaperModerationDecision } })}><option value="allow">allow</option><option value="review">review</option><option value="reject">reject</option></select></label>
                <fieldset><legend>Expected flags</legend>{MODERATION_FLAGS.map(({ value, label }) => <label key={value}><input type="checkbox" disabled={active} checked={item.expected.flags.includes(value)} onChange={() => toggleFlag(index, value)} />{label}</label>)}</fieldset>
                <button className="danger" disabled={active || caseCount <= 1} onClick={() => removeCase(index)}>remove case</button>
              </div>
            </details>
          ))}
        </div>
        <button className="comparison-add" disabled={active || caseCount >= 200} onClick={addCase}>+ add moderation case</button>
        <div className="benchmark-dataset-actions">
          <button disabled={active || !benchmark} onClick={saveCorpus}>save corpus as reusable dataset</button>
          <select disabled={active || datasets.length === 0} value={selectedDataset} onChange={(event) => setSelectedDataset(event.target.value)}>
            <option value="">choose a saved dataset…</option>
            {datasets.map((dataset) => <option value={dataset.dataset_id} key={dataset.dataset_id}>{dataset.name}</option>)}
          </select>
          <button disabled={active || !selectedDataset} onClick={loadCorpus}>load dataset</button>
          {datasetMessage && <small>{datasetMessage}</small>}
        </div>
      </section>

      <section className="comparison-setup" aria-labelledby="benchmark-models-title">
        <div className="comparison-section-heading">
          <div><span>02</span><strong id="benchmark-models-title">OpenRouter models</strong></div>
          <small>{agents.length} selected · {scheduledCalls} calls · up to ${maximumCost.toFixed(2)}</small>
        </div>
        <div className="benchmark-presets">
          <button disabled={active || !catalog} onClick={chooseCheapest}>cheapest 12</button>
          <button disabled={active || !catalog} onClick={chooseProviderSpread}>provider spread</button>
          {(rankings?.modes ?? []).map((mode) => {
            const modelIds = testedModeModels.get(mode.mode_id) ?? [];
            return (
              <button
                disabled={active || modelIds.length < 2}
                title={mode.description}
                onClick={() => onSetModels(modelIds)}
                key={mode.mode_id}
              >
                tested · {mode.label.toLowerCase()}
              </button>
            );
          })}
          <button disabled={active} onClick={() => onSetModels([])}>clear</button>
          <span>{rankingsLoading
            ? "reading compatible runs…"
            : rankingsProblem
              ? "tested presets unavailable"
              : `${rankings?.compatible_runs ?? 0} compatible runs`} · at most eight concurrent calls; failures do not stop siblings.</span>
        </div>
        <input className="benchmark-model-search" disabled={active || !catalog} value={search} onChange={(event) => setSearch(event.target.value)} placeholder={catalog ? "Search model, publisher, or model ID" : "Loading the OpenRouter catalog…"} />
        {catalogProblem && <span className="comparison-issue">× OpenRouter catalog unavailable · {catalogProblem}</span>}
        {catalog?.source.status === "stale" && <span className="comparison-catalog-note">The catalog is stale; refresh Prices before a consequential sweep.</span>}
        <div className="benchmark-model-picker">
          {matchingModels.map((model) => {
            const selected = selectedModels.includes(model.model_id);
            return <button key={model.model_id} disabled={active || (!selected && selectedModels.length >= 24)} className={selected ? "selected" : ""} onClick={() => toggleModel(model.model_id)}><span>{selected ? "✓" : "+"}</span><strong>{model.name}</strong><small>{model.provider} · {Number.isFinite(modelRate(model)) ? `$${modelRate(model).toFixed(3)}/M combined` : "price incomplete"}</small></button>;
          })}
        </div>
        <div className="benchmark-selected-models">{selectedModels.map((model) => <button key={model} disabled={active} onClick={() => toggleModel(model)}>{model}<span>×</span></button>)}</div>
      </section>

      <section className="comparison-launch">
        <div>
          {issues.length > 0 ? issues.map((issue) => <span className="comparison-issue" key={issue}>× {issue}</span>) : <span className="comparison-ok">✓ {scheduledCalls} isolated calls ready · deterministic local scoring</span>}
        </div>
        <button className="confirm" disabled={active || issues.length > 0} onClick={onRun}>{starting ? "saving…" : active ? "benchmarking…" : "run benchmark"}</button>
      </section>

      {run && <section className="comparison-results benchmark-results" aria-live="polite">
        <div className="comparison-section-heading"><div><span>03</span><strong>Scorecard</strong></div><small>{completedCalls} / {scheduledCalls} calls · ${run.cost?.total_usd.toFixed(6) ?? "0.000000"}</small></div>
        <div className={`run-status run-${run.status}`}><span>{run.status}{run.error ? ` · ${run.error}` : ""}</span><strong>{completedCalls} / {scheduledCalls}</strong></div>
        <div className="run-progress"><span style={{ width: `${progress}%` }} /></div>
        {scorecard?.models.length ? <div className="benchmark-table-wrap"><table className="benchmark-score-table"><thead><tr><th>rank</th><th>model</th><th>decision</th><th>flag F1</th><th>false allow</th><th>schema</th><th>latency</th><th>cost</th></tr></thead><tbody>{scorecard.models.map((score) => <tr key={score.node_id}><td>{score.rank}</td><td><strong>{score.model}</strong><small>{score.completed_calls}/{score.cases} calls</small></td><td>{benchmarkPercent(score.decision_accuracy)}</td><td>{benchmarkPercent(score.flag_f1)}</td><td className={score.false_allow_count ? "benchmark-danger" : ""}>{benchmarkPercent(score.false_allow_rate)}</td><td>{benchmarkPercent(score.schema_valid_rate)}</td><td>{score.average_latency_seconds == null ? "—" : `${score.average_latency_seconds.toFixed(2)}s`}</td><td>{score.unpriced_calls ? (score.priced_calls ? `$${score.total_cost_usd.toFixed(6)} + ${score.unpriced_calls} not reported` : "not reported") : `$${score.total_cost_usd.toFixed(6)}`}</td></tr>)}</tbody></table></div> : <div className="comparison-waiting"><i />Waiting for the first scored verdict…</div>}
        {scorecard?.models.map((score) => <details className="benchmark-case-results" key={`cases-${score.node_id}`}><summary>{score.model} · case details</summary><div>{score.case_results.map((item) => <div className={item.decision_correct && item.flags_exact ? "correct" : "incorrect"} key={item.case_id}><span>{item.decision_correct && item.flags_exact ? "✓" : "×"}</span><strong>{item.case_name}</strong><small>expected {item.expected.decision} [{item.expected.flags.join(", ") || "clean"}] · received {item.predicted?.decision ?? item.status} [{item.predicted?.flags.join(", ") || "—"}]</small>{item.error && <em>{item.error}</em>}</div>)}</div></details>)}
        <div className="run-actions">{active && <button className="danger" disabled={run.status === "cancelling"} onClick={onCancel}>{run.status === "cancelling" ? "stopping…" : "stop benchmark"}</button>}{run.artifact_path && <><button onClick={() => window.dispatchEvent(new CustomEvent("artifact-open", { detail: run.artifact_path }))}>open saved report</button><RevealArtifactButton projectId={projectId} name={run.artifact_path} /></>}{!active && <button onClick={onPrepareAnother}>run again</button>}</div>
      </section>}

      {history.length > 0 && <details className="run-history comparison-history"><summary>recent benchmark runs</summary><div>{history.map((item) => <button key={item.run_id} className={item.run_id === run?.run_id ? "selected" : ""} onClick={() => onSelectRun(item)}><span>{item.status} · {item.iterations} cases{item.cost ? ` · $${item.cost.total_usd.toFixed(6)}` : ""}</span><time>{item.created_at ? new Date(item.created_at).toLocaleString() : item.run_id.slice(0, 16)}</time></button>)}</div></details>}
    </main>
  );
}

function orderedWorkflowNodes(graph: WorkflowGraph): WorkflowNode[] {
  const nodes = new Map(graph.nodes.map((node) => [node.id, node]));
  const incoming = new Map(graph.nodes.map((node) => [node.id, 0]));
  const outgoing = new Map(graph.nodes.map((node) => [node.id, [] as string[]]));
  for (const edge of graph.edges) {
    if (!nodes.has(edge.source) || !nodes.has(edge.target)) continue;
    incoming.set(edge.target, (incoming.get(edge.target) ?? 0) + 1);
    outgoing.get(edge.source)?.push(edge.target);
  }
  const ready = graph.nodes.filter((node) => incoming.get(node.id) === 0);
  const ordered: WorkflowNode[] = [];
  while (ready.length) {
    const node = ready.shift()!;
    ordered.push(node);
    for (const target of outgoing.get(node.id) ?? []) {
      const next = (incoming.get(target) ?? 1) - 1;
      incoming.set(target, next);
      if (next === 0) ready.push(nodes.get(target)!);
    }
  }
  return ordered.length === graph.nodes.length ? ordered : graph.nodes;
}

function PinnedPrompt({
  label,
  binding,
}: {
  label: string;
  binding?: { prompt_id: string; revision_id: string };
}) {
  const [prompt, setPrompt] = useState<PromptRevision | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  useEffect(() => {
    let cancelled = false;
    setPrompt(null);
    setProblem(null);
    if (!binding) return () => { cancelled = true; };
    void fetchAgentPrompt(binding.prompt_id, binding.revision_id)
      .then((value) => { if (!cancelled) setPrompt(value); })
      .catch((error) => { if (!cancelled) setProblem(String(error)); });
    return () => { cancelled = true; };
  }, [binding?.prompt_id, binding?.revision_id]);

  if (!binding) {
    return <div className="workflow-prompt-missing"><strong>{label}</strong><span>not configured</span></div>;
  }
  return (
    <details className="workflow-prompt-reader">
      <summary>
        <strong>{label}</strong>
        <span>{prompt?.name ?? (problem ? "unavailable" : "loading exact revision…")}</span>
        <code>{binding.revision_id.slice(0, 16)}</code>
      </summary>
      {problem ? <div className="studio-message">{problem}</div> : prompt ? (
        <div>
          {prompt.description && <p>{prompt.description}</p>}
          <pre>{prompt.template}</pre>
          {prompt.slots.length > 0 && <small>variables · {prompt.slots.join(", ")}</small>}
        </div>
      ) : <div className="studio-help">Loading the immutable prompt body…</div>}
    </details>
  );
}

export function WorkflowOverviewStudio({
  graph,
  projectName,
  onEditGraph,
  onReviewRun,
}: {
  graph: WorkflowGraph;
  projectName: string;
  onEditGraph: () => void;
  onReviewRun: () => void;
}) {
  const recipe = workflowRecipe(graph);
  const recipeLabel: Record<WorkflowRecipe, string> = {
    graph: "reasoning workflow",
    model_comparison: "model comparison",
    paper_moderation: "paper moderation",
    dataset_generator: "dataset generator",
  };
  const agents = graph.nodes.filter((node) => node.kind === "agent" && node.config);
  const caseCount = graph.dataset?.cases.length ?? graph.benchmark?.cases.length ?? graph.iterations;
  const variantCount = Math.max(1, graph.variants?.length ?? 1);
  const scheduledCalls = caseCount * variantCount * agents.length;
  const ordered = orderedWorkflowNodes(graph);
  const predecessors = new Map<string, string[]>();
  for (const edge of graph.edges) {
    const source = graph.nodes.find((node) => node.id === edge.source);
    if (!source) continue;
    predecessors.set(edge.target, [...(predecessors.get(edge.target) ?? []), source.name]);
  }
  const evaluatorNames = (graph.evaluators ?? []).map((evaluator) => (
    evaluator.kind === "paper_moderation_v1" ? "paper moderation scorecard" : "exact match"
  ));

  return (
    <div className="workflow-studio comparison-workflow-studio">
      <main className="comparison-studio workflow-overview-studio">
        <header className="comparison-header">
          <div>
            <span className="comparison-eyebrow">{recipeLabel[recipe]} · {projectName}</span>
            <h2>{graph.name}</h2>
            <p>{graph.description || "A reviewable, immutable workflow authored in this project. Read its exact organization and prompts below before running it."}</p>
          </div>
          <div className="workflow-overview-actions">
            <button onClick={onEditGraph}>edit graph</button>
            <button className="confirm" onClick={onReviewRun}>review run</button>
          </div>
        </header>

        <section className="workflow-overview-summary" aria-label="Workflow execution summary">
          <div><strong>{caseCount}</strong><span>{graph.dataset || graph.benchmark ? "cases" : "iterations"}</span></div>
          <div><strong>{variantCount}</strong><span>variants</span></div>
          <div><strong>{agents.length}</strong><span>harnesses</span></div>
          <div><strong>{scheduledCalls}</strong><span>scheduled calls</span></div>
        </section>

        <section className="comparison-setup workflow-overview-contract">
          <div className="comparison-section-heading">
            <div><span>01</span><strong>What this workflow will do</strong></div>
            <small>revision {graph.revision_id.slice(0, 16)}</small>
          </div>
          <dl>
            <div><dt>Cases</dt><dd>{graph.dataset?.name ?? graph.benchmark?.kind ?? `${graph.iterations} repeated graph iteration${graph.iterations === 1 ? "" : "s"}`}</dd></div>
            <div><dt>Variants</dt><dd>{(graph.variants ?? []).map((variant) => variant.name).join(", ") || "Default"}</dd></div>
            <div><dt>Evaluation</dt><dd>{evaluatorNames.join(", ") || "No deterministic evaluator; outputs are preserved for review."}</dd></div>
            <div><dt>Guardrails</dt><dd>{graph.execution_policy?.max_concurrency ?? 1} parallel · {graph.execution_policy?.failure_policy ?? "fail_fast"}{graph.execution_policy?.max_calls ? ` · at most ${graph.execution_policy.max_calls} calls` : ""}{graph.execution_policy?.max_total_cost_usd ? ` · $${graph.execution_policy.max_total_cost_usd.toFixed(2)} run limit` : ""}</dd></div>
          </dl>
        </section>

        <section className="comparison-setup workflow-overview-flow">
          <div className="comparison-section-heading">
            <div><span>02</span><strong>Organization and exact instructions</strong></div>
            <small>{ordered.length} nodes · prompts pinned by revision</small>
          </div>
          {ordered.length === 0 && <div className="workflow-overview-empty">This draft has no nodes yet. Open the graph to finish its organization.</div>}
          <div className="workflow-overview-node-list">
            {ordered.map((node, index) => {
              const config = node.config;
              const incoming = predecessors.get(node.id) ?? [];
              return (
                <article className={`workflow-overview-node workflow-overview-node-${node.kind}`} key={node.id}>
                  <header>
                    <span>{String(index + 1).padStart(2, "0")}</span>
                    <div><small>{node.kind}</small><strong>{node.name}</strong></div>
                    {incoming.length > 0 && <em>receives {incoming.join(", ")}</em>}
                  </header>
                  {node.kind === "agent" && config && (
                    <div className="workflow-overview-agent">
                      <div className="workflow-overview-agent-meta">
                        <span>{config.executor_id}{config.model_provider ? ` / ${config.model_provider}` : ""}</span>
                        <strong>{config.model}</strong>
                        <small>reasoning {config.effort ?? "executor default"} · context {config.context_policy.mode} · sandbox {config.sandbox.mode}{config.sandbox.network ? " + network" : ""}</small>
                        <small>{config.tools.length ? `tools · ${config.tools.join(", ")}` : "no tools"}{config.skills.length ? ` · ${config.skills.length} pinned skill${config.skills.length === 1 ? "" : "s"}` : ""}</small>
                      </div>
                      <PinnedPrompt label="system prompt" binding={config.system_prompt} />
                      <PinnedPrompt label="task prompt" binding={config.user_prompt} />
                    </div>
                  )}
                </article>
              );
            })}
          </div>
        </section>
      </main>
    </div>
  );
}

export function WorkflowStudio({
  projectId,
  projectName,
  workflowId,
  workflowRevisionId,
  runId,
  onChanged,
  onOpenRun,
  onDraftPrompt,
  draftInitialPrompt,
  onDraftInitialPromptConsumed,
}: {
  projectId: string;
  projectName: string;
  workflowId: string;
  workflowRevisionId: string;
  runId: string | null;
  onChanged: () => Promise<unknown>;
  onOpenRun: (runId: string | null) => void;
  onDraftPrompt: (message: string) => void;
  draftInitialPrompt: string | null;
  onDraftInitialPromptConsumed: () => void;
}) {
  const [catalog, setCatalog] = useState<Catalog>(EMPTY_CATALOG);
  const [graph, setGraph] = useState<WorkflowGraph | null>(null);
  const [selectedNode, setSelectedNode] = useState<string | null>(null);
  const [selectedEdge, setSelectedEdge] = useState<string | null>(null);
  const [linkSource, setLinkSource] = useState<string | null>(null);
  const [tab, setTab] = useState<StudioTab>("node");
  const [inspectorOpen, setInspectorOpen] = useState(false);
  const [dirty, setDirty] = useState(false);
  const [undoStack, setUndoStack] = useState<WorkflowGraph[]>([]);
  const [redoStack, setRedoStack] = useState<WorkflowGraph[]>([]);
  const [message, setMessage] = useState("");
  const [preview, setPreview] = useState<AgentPreview | null>(null);
  const [variables, setVariables] = useState(
    "{}",
  );
  const [initialPrompt, setInitialPrompt] = useState("");
  const [run, setRun] = useState<WorkflowRunState | null>(null);
  const [runHistory, setRunHistory] = useState<WorkflowRunState[]>([]);
  const [startingRun, setStartingRun] = useState(false);
  const [comparisonGraphOpen, setComparisonGraphOpen] = useState(false);
  const [benchmarkGraphOpen, setBenchmarkGraphOpen] = useState(false);
  const [graphEditorOpen, setGraphEditorOpen] = useState(false);
  const startedRunRef = useRef<string | null>(null);
  const [paletteDrag, setPaletteDrag] = useState<{
    kind: WorkflowNode["kind"];
    x: number;
    y: number;
  } | null>(null);
  const workflowActive = Boolean(
    run && ["pending", "running", "cancelling"].includes(run.status),
  );

  const reloadCatalog = useCallback(() => {
    void fetchAgentCatalog(projectId).then(setCatalog);
  }, [projectId]);
  useEffect(reloadCatalog, [reloadCatalog]);

  useEffect(() => {
    void fetchWorkflow(projectId, workflowId).then((value) => {
      setGraph(value); setSelectedNode(null); setSelectedEdge(null); setDirty(false);
      setComparisonGraphOpen(false);
      setBenchmarkGraphOpen(false);
      setGraphEditorOpen(false);
      setUndoStack([]); setRedoStack([]);
      if (draftInitialPrompt) {
        setInitialPrompt(draftInitialPrompt);
        setTab("run");
        setInspectorOpen(true);
        onDraftInitialPromptConsumed();
      } else {
        if (!runId) setInspectorOpen(false);
        setInitialPrompt("");
      }
    }).catch((error) => setMessage(String(error)));
  }, [projectId, runId, workflowId, workflowRevisionId]);

  useEffect(() => {
    if (!runId) {
      setRun(null);
      return;
    }
    void fetchWorkflowRun(projectId, runId).then((state) => {
      const keepCanvasForeground = startedRunRef.current === runId;
      if (keepCanvasForeground) startedRunRef.current = null;
      setRun(state); setTab("run"); setInspectorOpen(!keepCanvasForeground);
    }).catch((error) => setMessage(String(error)));
  }, [projectId, runId]);

  const reloadRunHistory = useCallback(() => {
    if (!projectId || !graph) {
      setRunHistory([]);
      return;
    }
    void fetchWorkflowRuns(projectId, graph.workflow_id)
      .then(({ runs }) => setRunHistory(runs))
      .catch(() => setRunHistory([]));
  }, [graph?.workflow_id, projectId]);

  useEffect(reloadRunHistory, [reloadRunHistory]);

  useEffect(() => {
    if (!projectId || !run || !["pending", "running", "cancelling"].includes(run.status)) return;
    const timer = window.setInterval(() => {
      void fetchWorkflowRun(projectId, run.run_id).then((state) => {
        setRun(state);
        if (!["pending", "running", "cancelling"].includes(state.status)) {
          reloadRunHistory();
          void onChanged();
          setInspectorOpen(true);
        }
      });
    }, 800);
    return () => window.clearInterval(timer);
  }, [onChanged, projectId, reloadRunHistory, run]);

  const recordHistory = () => {
    if (!graph || workflowActive) return;
    setUndoStack((current) => [...current.slice(-49), graph]);
    setRedoStack([]);
  };
  const edit = (
    change: (current: WorkflowGraph) => WorkflowGraph,
    record = true,
  ) => {
    if (workflowActive) {
      setMessage("The graph is locked to its saved revision while this run is active.");
      return;
    }
    if (record) recordHistory();
    setGraph((current) => current ? change(current) : current);
    setDirty(true);
    setPreview(null);
  };
  const undo = useCallback(() => {
    if (workflowActive) return;
    const previous = undoStack[undoStack.length - 1];
    if (!graph || !previous) return;
    setUndoStack((current) => current.slice(0, -1));
    setRedoStack((current) => [...current.slice(-49), graph]);
    setGraph(previous);
    setDirty(true);
    setPreview(null);
  }, [graph, undoStack, workflowActive]);
  const redo = useCallback(() => {
    if (workflowActive) return;
    const next = redoStack[redoStack.length - 1];
    if (!graph || !next) return;
    setRedoStack((current) => current.slice(0, -1));
    setUndoStack((current) => [...current.slice(-49), graph]);
    setGraph(next);
    setDirty(true);
    setPreview(null);
  }, [graph, redoStack, workflowActive]);

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement | null;
      if (target?.closest("input,textarea,select,[contenteditable=true]")) return;
      if (!(event.metaKey || event.ctrlKey) || event.key.toLowerCase() !== "z") return;
      event.preventDefault();
      if (event.shiftKey) redo();
      else undo();
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [redo, undo]);
  const save = useCallback(async () => {
    if (!graph) throw new Error("choose a workflow");
    setMessage("saving…");
    const saved = await saveWorkflow(projectId, graph);
    setGraph(saved); setDirty(false); setMessage(`saved ${saved.revision_id.slice(0, 16)}`);
    void onChanged();
    return saved;
  }, [graph, onChanged, projectId]);
  const addNode = (kind: WorkflowNode["kind"], position: { x: number; y: number }) => {
    if (!graph) return;
    const id = `${kind}-${crypto.randomUUID().slice(0, 8)}`;
    const node: WorkflowNode = { id, kind, name: kind === "agent" ? "New agent" : kind === "input" ? "Input" : "Output", position };
    if (kind === "agent") node.config = defaultAgentConfig(catalog);
    edit((current) => ({ ...current, nodes: [...current.nodes, node] }));
    setSelectedNode(id); setSelectedEdge(null); setTab("node"); setInspectorOpen(true);
  };
  const beginPaletteDrag = (
    event: ReactPointerEvent<HTMLButtonElement>,
    kind: WorkflowNode["kind"],
  ) => {
    if (event.button !== 0) return;
    event.preventDefault();
    event.currentTarget.setPointerCapture(event.pointerId);
    setPaletteDrag({ kind, x: event.clientX, y: event.clientY });
    const move = (next: PointerEvent) => {
      setPaletteDrag({ kind, x: next.clientX, y: next.clientY });
    };
    const finish = (next: PointerEvent) => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", finish);
      window.removeEventListener("pointercancel", cancel);
      setPaletteDrag(null);
      const viewport = document.querySelector<HTMLElement>(".workflow-canvas-scroll");
      const canvas = document.querySelector<HTMLElement>(".workflow-canvas");
      if (!viewport || !canvas) return;
      const visible = viewport.getBoundingClientRect();
      if (
        next.clientX < visible.left || next.clientX > visible.right ||
        next.clientY < visible.top || next.clientY > visible.bottom
      ) return;
      const bounds = canvas.getBoundingClientRect();
      const scale = Number(canvas.dataset.scale || "1");
      addNode(kind, {
        x: Math.max(20, (next.clientX - bounds.left) / scale - NODE_WIDTH / 2),
        y: Math.max(20, (next.clientY - bounds.top) / scale - nodeHeight(kind) / 2),
      });
    };
    const cancel = () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", finish);
      window.removeEventListener("pointercancel", cancel);
      setPaletteDrag(null);
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", finish, { once: true });
    window.addEventListener("pointercancel", cancel, { once: true });
  };
  const finishLink = (target: string) => {
    if (!linkSource || !graph || linkSource === target) return;
    if (!graph.edges.some((edge) => edge.source === linkSource && edge.target === target)) {
      edit((current) => ({ ...current, edges: [...current.edges, { id: `edge-${crypto.randomUUID().slice(0, 8)}`, source: linkSource, target }] }));
    }
    setLinkSource(null);
  };
  const node = graph?.nodes.find((candidate) => candidate.id === selectedNode) ?? null;
  const deleteNode = (id: string) => {
    if (workflowActive) {
      setMessage("The graph is locked to its saved revision while this run is active.");
      return;
    }
    edit((current) => ({
      ...current,
      nodes: current.nodes.filter((item) => item.id !== id),
      edges: current.edges.filter((edge) => edge.source !== id && edge.target !== id),
    }));
    setSelectedNode(null);
    setMessage("Node deleted · use Undo to restore it.");
  };
  const runWorkflow = () => {
    if (!graph) {
      setMessage("Choose a workflow before starting a run.");
      return;
    }
    if (!projectId) {
      setMessage("Select a project to run this workflow against its ledger and artifacts.");
      return;
    }
    let parsed: Record<string, unknown>;
    try { parsed = parseObject(variables); } catch (error) { setMessage(String(error)); return; }
    if (graph.nodes.some((candidate) => candidate.kind === "input") && !initialPrompt.trim()) {
      setMessage("Add the initial research prompt in the Run panel before starting.");
      setTab("run");
      return;
    }
    const supplied = withInitialPrompt(graph, parsed, initialPrompt);
    setStartingRun(true);
    setMessage("");
    void save()
      .then((saved) => startWorkflowRun(projectId, saved.workflow_id, saved.iterations, supplied, initialPrompt, saved.revision_id))
      .then(({ run_id }) => fetchWorkflowRun(projectId, run_id))
      .then((state) => {
        startedRunRef.current = state.run_id;
        setRun(state);
        setTab("run");
        setInspectorOpen(false);
        onOpenRun(state.run_id);
        reloadRunHistory();
      })
      .catch((error) => setMessage(String(error)))
      .finally(() => setStartingRun(false));
  };
  const cancelRun = () => {
    if (!projectId || !run) return;
    void cancelWorkflowRun(projectId, run.run_id)
      .then(setRun)
      .catch((error) => setMessage(String(error)));
  };

  const changeComparisonAgent = (changed: WorkflowNode) => {
    if (!graph) return;
    const baseAgents = graph.nodes.filter(
      (candidate): candidate is WorkflowNode & { config: AgentNodeConfig } =>
        candidate.kind === "agent" && Boolean(candidate.config),
    );
    const variant = graph.variants?.find((candidate) => candidate.variant_id === changed.id);
    if (baseAgents.length === 1 && variant && changed.config) {
      const base = baseAgents[0];
      edit((current) => ({
        ...current,
        variants: (current.variants ?? []).map((item) => item.variant_id === changed.id ? {
          ...item,
          name: changed.name,
          node_overrides: { ...item.node_overrides, [base.id]: changed.config! },
        } : item),
      }));
      return;
    }
    edit((current) => ({
      ...current,
      nodes: current.nodes.map((item) => item.id === changed.id ? changed : item),
    }));
  };
  const addComparisonAgent = () => {
    if (!graph || workflowActive) return;
    const variants = graph.variants ?? [];
    const agents = graph.nodes.filter((candidate) => candidate.kind === "agent");
    const explicitVariants = graph.presentation?.recipe === "model_comparison"
      || variants.length > 1
      || (variants.length === 1 && variants[0].variant_id !== "default");
    if (agents.length === 1 && explicitVariants) {
      if (variants.length >= 6) return;
      const base = agents[0];
      const source = variantFanoutAgents(graph).at(-1)?.config ?? base.config ?? defaultAgentConfig(catalog);
      const id = `model-${crypto.randomUUID().slice(0, 8)}`;
      edit((current) => ({
        ...current,
        variants: [...(current.variants ?? []), {
          variant_id: id,
          name: `Model ${String.fromCharCode(65 + variants.length)}`,
          node_overrides: { [base.id]: JSON.parse(JSON.stringify(source)) as AgentNodeConfig },
        }],
        execution_policy: {
          ...current.execution_policy,
          max_concurrency: Math.min(8, Math.max(2, current.execution_policy?.max_concurrency ?? 4)),
          failure_policy: "continue",
        },
      }));
      return;
    }
    if (agents.length >= 6) return;
    const source = agents[agents.length - 1];
    const id = `model-${crypto.randomUUID().slice(0, 8)}`;
    const input = graph.nodes.find((candidate) => candidate.kind === "input");
    const output = graph.nodes.find((candidate) => candidate.kind === "output");
    const config = source?.config
      ? JSON.parse(JSON.stringify(source.config)) as AgentNodeConfig
      : defaultAgentConfig(catalog);
    const node: WorkflowNode = {
      id,
      kind: "agent",
      name: `Model ${String.fromCharCode(65 + agents.length)}`,
      position: { x: 320, y: 120 + agents.length * 180 },
      config,
    };
    edit((current) => ({
      ...current,
      nodes: [...current.nodes, node],
      edges: [
        ...current.edges,
        ...(input ? [{ id: `edge-${crypto.randomUUID().slice(0, 8)}`, source: input.id, target: id }] : []),
        ...(output ? [{ id: `edge-${crypto.randomUUID().slice(0, 8)}`, source: id, target: output.id }] : []),
      ],
      execution_policy: {
        max_concurrency: Math.min(8, Math.max(2, current.execution_policy?.max_concurrency ?? 4)),
        failure_policy: "continue",
      },
    }));
  };
  const removeComparisonAgent = (nodeId: string) => {
    if (!graph || workflowActive) return;
    const variants = graph.variants ?? [];
    const agents = graph.nodes.filter((candidate) => candidate.kind === "agent");
    if (agents.length === 1 && variants.some((variant) => variant.variant_id === nodeId)) {
      if (variants.length <= 2) return;
      edit((current) => ({
        ...current,
        variants: (current.variants ?? []).filter((variant) => variant.variant_id !== nodeId),
      }));
      return;
    }
    if (agents.length <= 2) return;
    edit((current) => ({
      ...current,
      nodes: current.nodes.filter((candidate) => candidate.id !== nodeId),
      edges: current.edges.filter((edge) => edge.source !== nodeId && edge.target !== nodeId),
    }));
  };

  const setComparisonConfigurations = (configurations: ModelRankingConfiguration[]) => {
    if (!graph || workflowActive) return;
    const seenConfigurations = new Set<string>();
    const uniqueConfigurations = configurations.filter((configuration) => {
      if (seenConfigurations.has(configuration.configuration_id)) return false;
      seenConfigurations.add(configuration.configuration_id);
      return true;
    }).slice(0, 6);
    if (uniqueConfigurations.length < 2) return;
    const existingAgents = graph.nodes.filter(
      (candidate): candidate is WorkflowNode & { config: AgentNodeConfig } =>
        candidate.kind === "agent" && Boolean(candidate.config),
    );
    const configurationKey = ({ executor_id, model_provider, model, effort }: {
      executor_id: string;
      model_provider?: string | null;
      model: string;
      effort?: string | null;
    }) => JSON.stringify([executor_id, model_provider ?? null, model, effort ?? null]);
    const applyConfiguration = (
      source: AgentNodeConfig,
      configuration: ModelRankingConfiguration,
    ): AgentNodeConfig => ({
      ...(JSON.parse(JSON.stringify(source)) as AgentNodeConfig),
      executor_id: configuration.executor_id,
      model_provider: configuration.model_provider ?? null,
      model: configuration.model,
      effort: configuration.effort ?? null,
    });
    const variants = graph.variants ?? [];
    const explicitVariants = graph.presentation?.recipe === "model_comparison"
      || variants.length > 1
      || (variants.length === 1 && variants[0].variant_id !== "default");
    if (existingAgents.length === 1 && explicitVariants) {
      const base = existingAgents[0];
      const existingByConfiguration = new Map(
        variantFanoutAgents(graph).map((agent) => [configurationKey(agent.config), agent]),
      );
      edit((current) => ({
        ...current,
        variants: uniqueConfigurations.map((configuration, index) => {
          const existing = existingByConfiguration.get(configurationKey(configuration));
          return {
            variant_id: existing?.id ?? `model-${crypto.randomUUID().slice(0, 8)}`,
            name: existing?.name ?? `Model ${String.fromCharCode(65 + index)}`,
            node_overrides: {
              [base.id]: applyConfiguration(existing?.config ?? base.config, configuration),
            },
          };
        }),
        execution_policy: {
          ...current.execution_policy,
          max_concurrency: Math.min(8, Math.max(2, uniqueConfigurations.length)),
          failure_policy: "continue",
        },
      }));
      return;
    }
    const existingByConfiguration = new Map(
      existingAgents.map((agent) => [configurationKey(agent.config), agent]),
    );
    const input = graph.nodes.find((candidate) => candidate.kind === "input");
    const output = graph.nodes.find((candidate) => candidate.kind === "output");
    const source = existingAgents[0]?.config ?? defaultAgentConfig(catalog);
    const selectedAgents: WorkflowNode[] = uniqueConfigurations.map((configuration, index) => {
      const existing = existingByConfiguration.get(configurationKey(configuration));
      if (existing) return existing;
      return {
        id: `model-${crypto.randomUUID().slice(0, 8)}`,
        kind: "agent" as const,
        name: `Model ${String.fromCharCode(65 + index)}`,
        position: { x: 320, y: 120 + index * 180 },
        config: applyConfiguration(source, configuration),
      };
    });
    edit((current) => {
      const nonAgents = current.nodes.filter((candidate) => candidate.kind !== "agent");
      const nonAgentIds = new Set(nonAgents.map((candidate) => candidate.id));
      const preservedEdges = current.edges.filter(
        (edge) => nonAgentIds.has(edge.source) && nonAgentIds.has(edge.target),
      );
      return {
        ...current,
        nodes: [...nonAgents, ...selectedAgents],
        edges: [
          ...preservedEdges,
          ...(input ? selectedAgents.map((agent) => ({
            id: `edge-${crypto.randomUUID().slice(0, 8)}`,
            source: input.id,
            target: agent.id,
          })) : []),
          ...(output ? selectedAgents.map((agent) => ({
            id: `edge-${crypto.randomUUID().slice(0, 8)}`,
            source: agent.id,
            target: output.id,
          })) : []),
        ],
        execution_policy: {
          ...current.execution_policy,
          max_concurrency: Math.min(8, Math.max(2, uniqueConfigurations.length)),
          failure_policy: "continue",
        },
      };
    });
  };

  const changeBenchmark = (benchmark: PaperModerationBenchmark) => {
    edit((current) => ({
      ...current,
      benchmark,
      dataset: {
        kind: "case_dataset_v1",
        schema_version: 1,
        name: current.dataset?.name ?? "Paper moderation corpus",
        description: current.dataset?.description ?? "Reusable paper moderation cases.",
        cases: benchmark.cases.map((item) => ({
          case_id: item.case_id,
          name: item.name,
          inputs: { paper_text: item.paper_text },
          expected: item.expected,
        })),
      },
      dataset_ref: null,
      iterations: benchmark.cases.length,
    }));
  };
  const attachBenchmarkDataset = (dataset: DatasetRevision) => {
    const benchmark: PaperModerationBenchmark = {
      kind: "paper_moderation_v1",
      schema_version: 1,
      cases: dataset.cases.map((item) => ({
        case_id: item.case_id,
        name: item.name,
        paper_text: String(item.inputs.paper_text ?? ""),
        expected: item.expected as PaperModerationCase["expected"],
      })),
    };
    edit((current) => ({
      ...current,
      benchmark,
      dataset: {
        kind: "case_dataset_v1",
        schema_version: 1,
        name: dataset.name,
        description: dataset.description,
        cases: dataset.cases,
      },
      dataset_ref: { dataset_id: dataset.dataset_id, revision_id: dataset.revision_id },
      iterations: dataset.cases.length,
    }));
  };
  const setBenchmarkModels = (models: string[]) => {
    if (!graph || workflowActive) return;
    const uniqueModels = [...new Set(models)].slice(0, 24);
    const existingAgents = graph.nodes.filter(
      (candidate): candidate is WorkflowNode & { config: AgentNodeConfig } =>
        candidate.kind === "agent" && Boolean(candidate.config),
    );
    const variants = graph.variants ?? [];
    const explicitVariants = graph.presentation?.recipe === "paper_moderation"
      || variants.length > 1
      || (variants.length === 1 && variants[0].variant_id !== "default");
    if (existingAgents.length === 1 && explicitVariants) {
      const base = existingAgents[0];
      const existingByModel = new Map(
        variantFanoutAgents(graph).map((agent) => [agent.config.model, agent]),
      );
      edit((current) => ({
        ...current,
        variants: uniqueModels.map((model, index) => {
          const existing = existingByModel.get(model);
          return {
            variant_id: existing?.id ?? `moderator-${crypto.randomUUID().slice(0, 8)}`,
            name: existing?.name ?? `Moderator ${String(index + 1).padStart(2, "0")}`,
            node_overrides: {
              [base.id]: {
                ...(existing?.config ?? base.config),
                executor_id: "reasoning-native",
                model,
                model_provider: null,
              },
            },
          };
        }),
        execution_policy: {
          ...current.execution_policy,
          max_concurrency: 8,
          failure_policy: "continue",
          max_calls: Math.max(1, (current.dataset?.cases.length ?? current.benchmark?.cases.length ?? 1) * uniqueModels.length),
        },
      }));
      return;
    }
    const existingByModel = new Map(existingAgents.map((agent) => [agent.config.model, agent]));
    const system = catalog.prompts.find((prompt) => prompt.prompt_id === "paper-moderation-role");
    const user = catalog.prompts.find((prompt) => prompt.prompt_id === "paper-moderation-case");
    const output = graph.nodes.find((candidate) => candidate.kind === "output");
    const newAgents: WorkflowNode[] = uniqueModels.map((model, index) => {
      const existing = existingByModel.get(model);
      if (existing) return existing;
      const source = existingAgents[0]?.config ?? defaultAgentConfig(catalog);
      return {
        id: `moderator-${crypto.randomUUID().slice(0, 8)}`,
        kind: "agent" as const,
        name: `Moderator ${String(index + 1).padStart(2, "0")}`,
        position: { x: 300, y: 80 + index * 150 },
        config: {
          ...(JSON.parse(JSON.stringify(source)) as AgentNodeConfig),
          executor_id: "reasoning-native",
          model,
          model_provider: null,
          system_prompt: system
            ? { prompt_id: system.prompt_id, revision_id: system.revision_id }
            : source.system_prompt,
          user_prompt: user
            ? { prompt_id: user.prompt_id, revision_id: user.revision_id }
            : source.user_prompt,
          skills: [],
          tools: [],
          mcp_connections: [],
          context_policy: { mode: "none", max_tokens: 8000 },
          budget: { max_cost_usd: 0.25, max_tool_calls: 0, timeout_seconds: 180 },
          sandbox: { mode: "read_only", network: false },
          session: { mode: "fresh" },
        },
      };
    });
    edit((current) => {
      const nonAgents = current.nodes.filter((candidate) => candidate.kind !== "agent");
      const nonAgentIds = new Set(nonAgents.map((candidate) => candidate.id));
      const preservedEdges = current.edges.filter(
        (edge) => nonAgentIds.has(edge.source) && nonAgentIds.has(edge.target),
      );
      return {
        ...current,
        nodes: [...newAgents, ...nonAgents],
        edges: [
          ...preservedEdges,
          ...(output ? newAgents.map((agent) => ({
            id: `edge-${crypto.randomUUID().slice(0, 8)}`,
            source: agent.id,
            target: output.id,
          })) : []),
        ],
        execution_policy: { max_concurrency: 8, failure_policy: "continue" },
      };
    });
  };

  const recipe = graph ? workflowRecipe(graph) : "graph";

  if (graph && recipe === "paper_moderation" && !benchmarkGraphOpen) {
    return (
      <div className="workflow-studio comparison-workflow-studio">
        <BenchmarkStudio
          graph={graph}
          projectId={projectId}
          projectName={projectName}
          run={run}
          history={runHistory}
          onRun={runWorkflow}
          onCancel={cancelRun}
          onSelectRun={(state) => { setRun(state); onOpenRun(state.run_id); }}
          onPrepareAnother={() => { setRun(null); onOpenRun(null); }}
          onChangeBenchmark={changeBenchmark}
          onSetModels={setBenchmarkModels}
          onAttachDataset={attachBenchmarkDataset}
          onEditGraph={() => setBenchmarkGraphOpen(true)}
          starting={startingRun}
        />
        {message && <div className="workflow-toast" onClick={() => setMessage("")}>{message}</div>}
      </div>
    );
  }

  if (graph && recipe === "model_comparison" && !comparisonGraphOpen) {
    return (
      <div className="workflow-studio comparison-workflow-studio">
        <ComparisonStudio
          graph={graph}
          catalog={catalog}
          projectId={projectId}
          projectName={projectName}
          initialPrompt={initialPrompt}
          onInitialPrompt={setInitialPrompt}
          run={run}
          history={runHistory}
          onRun={runWorkflow}
          onCancel={cancelRun}
          onSelectRun={(state) => { setRun(state); onOpenRun(state.run_id); }}
          onPrepareAnother={() => { setRun(null); onOpenRun(null); }}
          onChangeAgent={changeComparisonAgent}
          onAddAgent={addComparisonAgent}
          onRemoveAgent={removeComparisonAgent}
          onSetConfigurations={setComparisonConfigurations}
          onEditGraph={() => setComparisonGraphOpen(true)}
          starting={startingRun}
        />
        {message && <div className="workflow-toast" onClick={() => setMessage("")}>{message}</div>}
      </div>
    );
  }

  if (
    graph
    && graph.nodes.length > 0
    && (recipe === "graph" || recipe === "dataset_generator")
    && !graphEditorOpen
  ) {
    return (
      <WorkflowOverviewStudio
        graph={graph}
        projectName={projectName}
        onEditGraph={() => setGraphEditorOpen(true)}
        onReviewRun={() => {
          setGraphEditorOpen(true);
          setTab("run");
          setInspectorOpen(true);
        }}
      />
    );
  }

  const palette = (["input", "agent", "output"] as const);
  return (
    <div className={`workflow-studio ${inspectorOpen ? "inspector-visible" : ""}`}>
      <aside className="workflow-library">
        <div className="studio-section-title">nodes · drag to canvas</div>
        <div className="node-palette">{palette.map((kind) => <button key={kind} disabled={workflowActive} onPointerDown={(event) => beginPaletteDrag(event, kind)} onDoubleClick={() => addNode(kind, { x: 80 + Math.random() * 260, y: 80 + Math.random() * 260 })}><span>{kind === "agent" ? "◉" : kind === "input" ? "→" : "□"}</span>{kind}</button>)}</div>
        <button className="workflow-inspector-toggle" onClick={() => setInspectorOpen(true)}>prompts, skills &amp; run</button>
        <div className="studio-help library-help">Connect nodes from the right port to the left port. Repetition belongs to the schedule, so the graph stays acyclic and inspectable.</div>
      </aside>
      <main className="workflow-main">
        {graph ? <>
          <div className="workflow-toolbar">
            <div className="workflow-toolbar-primary">
              <input className="workflow-name-input" aria-label="Workflow name" disabled={workflowActive} value={graph.name} onChange={(event) => edit((current) => ({ ...current, name: event.target.value }))} />
              <span className={dirty ? "dirty" : "saved"}>{dirty ? "unsaved" : graph.revision_id.slice(0, 16)}</span>
            </div>
            <div className="workflow-toolbar-meta">
              <label>{graph.dataset ? "cases" : "iterations"}<input type="number" min="1" max="1000" disabled={workflowActive || Boolean(graph.dataset)} value={workflowCaseCount(graph)} onChange={(event) => edit((current) => ({ ...current, iterations: Math.max(1, Number(event.target.value)) }))} /></label>
              <div className="workflow-history-controls"><button disabled={workflowActive || !undoStack.length} aria-label="Undo workflow edit" title="Undo" onClick={undo}>↶</button><button disabled={workflowActive || !redoStack.length} aria-label="Redo" title="Redo" onClick={redo}>↷</button></div>
              {workflowActive && <span className="workflow-live-status"><i />case {Math.max(1, ...run!.events.map((event) => Number(event.iteration) || 0))} · live</span>}
            </div>
            <div className="workflow-toolbar-actions">
              {recipe === "model_comparison" && <button onClick={() => setComparisonGraphOpen(false)}>setup</button>}
              {recipe === "paper_moderation" && <button onClick={() => setBenchmarkGraphOpen(false)}>setup</button>}
              {(recipe === "graph" || recipe === "dataset_generator") && <button onClick={() => setGraphEditorOpen(false)}>overview</button>}
              <button disabled={workflowActive} onClick={() => void save().catch((error) => setMessage(String(error)))}>save</button>
              <button className="confirm" onClick={() => { setTab("run"); setInspectorOpen(true); }}>run…</button>
              <button className="workflow-inspector-toggle" onClick={() => setInspectorOpen(true)}>inspector</button>
            </div>
          </div>
          <WorkflowCanvas key={graph.workflow_id} graph={graph} run={run} selectedNode={selectedNode} selectedEdge={selectedEdge} linkSource={linkSource} paletteDragging={Boolean(paletteDrag)} onSelectNode={(id) => { setSelectedNode(id); setSelectedEdge(null); setTab("node"); setInspectorOpen(true); }} onSelectEdge={(id) => { setSelectedEdge(id || null); if (id) setSelectedNode(null); setInspectorOpen(Boolean(id)); }} onBeginNodeMove={recordHistory} onMoveNode={(id, position) => edit((current) => ({ ...current, nodes: current.nodes.map((item) => item.id === id ? { ...item, position } : item) }), false)} onDeleteNode={deleteNode} onStartLink={setLinkSource} onFinishLink={finishLink} />
        </> : <div className="workflow-welcome"><strong>Design a scientific organization.</strong><p>Create a workflow, author exact prompts and skills, then drag agents onto the canvas.</p></div>}
        {message && <div className="workflow-toast" onClick={() => setMessage("")}>{message}</div>}
      </main>
      {paletteDrag && <div className="palette-drag-ghost" style={{ left: paletteDrag.x, top: paletteDrag.y }}><span>{paletteDrag.kind === "agent" ? "◉" : paletteDrag.kind === "input" ? "→" : "□"}</span>{paletteDrag.kind}</div>}
      {inspectorOpen && <button className="workflow-inspector-backdrop" aria-label="Close workflow inspector" onClick={() => setInspectorOpen(false)} />}
      <aside className={`workflow-inspector ${inspectorOpen ? "open" : ""}`}>
        <div className="studio-tab-header"><div className="studio-tabs" role="tablist" aria-label="Workflow inspector">{(["node", "plan", "prompts", "skills", "run"] as StudioTab[]).map((value) => <button role="tab" aria-selected={tab === value} key={value} className={tab === value ? "selected" : ""} onClick={() => setTab(value)}>{value}</button>)}</div><IconButton icon="close" className="workflow-inspector-close" aria-label="Close workflow inspector" onClick={() => setInspectorOpen(false)} /></div>
        {graph && tab === "node" && <NodeInspector node={node} edgeSelected={Boolean(selectedEdge)} projectId={projectId} graph={graph} catalog={catalog} preview={preview} previewVariables={variables} onPreviewVariables={setVariables} onChange={(changed) => edit((current) => ({ ...current, nodes: current.nodes.map((item) => item.id === changed.id ? changed : item) }))} onDeleteNode={() => { if (!node) return; deleteNode(node.id); }} onDeleteEdge={() => { if (!selectedEdge) return; edit((current) => ({ ...current, edges: current.edges.filter((edge) => edge.id !== selectedEdge) })); setSelectedEdge(null); setMessage("Connection deleted · use Undo to restore it."); }} onPreview={() => { if (!node?.config) return; if (!projectId) { setMessage("Select a project to preview project tools and context."); return; } try { void previewAgentNode(projectId, node.config, withInitialPrompt(graph, parseObject(variables), initialPrompt)).then(setPreview).catch((error) => setMessage(String(error))); } catch (error) { setMessage(String(error)); } }} />}
        {graph && tab === "plan" && <PlanStudio graph={graph} projectId={projectId} disabled={workflowActive} onChange={(change) => edit(change)} />}
        <div className={tab === "prompts" ? "studio-tab-content" : "studio-tab-content studio-tab-hidden"}><PromptStudio prompts={catalog.prompts} onChanged={reloadCatalog} onDraft={onDraftPrompt} /></div>
        <div className={tab === "skills" ? "studio-tab-content" : "studio-tab-content studio-tab-hidden"}><SkillStudio skills={catalog.skills} tools={catalog.tools} onChanged={reloadCatalog} onDraft={onDraftPrompt} /></div>
        {graph && tab === "run" && <RunStudio graph={graph} catalog={catalog} projectId={projectId} projectName={projectName} initialPrompt={initialPrompt} onInitialPrompt={setInitialPrompt} variables={variables} onVariables={setVariables} run={run} history={runHistory} onRun={runWorkflow} onCancel={cancelRun} onSelectRun={(state) => { setRun(state); onOpenRun(state.run_id); }} onPrepareAnother={() => { setRun(null); onOpenRun(null); }} />}
      </aside>
    </div>
  );
}
