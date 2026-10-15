import { useEffect, useMemo, useState } from "react";
import { formatModelPrice as money } from "./modelPriceFormat";
import {
  ModelPriceCatalog,
  ModelPriceCoverage,
  ModelPriceInfo,
  ModelPriceScope,
  ModelRankingConfiguration,
  ModelRankingMode,
  OpenRouterCall,
  OpenRouterCallLedger,
  WorkflowModelRankings,
  fetchPinnedModels,
  fetchOpenRouterCalls,
  fetchModelPrices,
  fetchWorkflowModelRankings,
  savePinnedModels,
} from "./api";

type PriceSort = "name" | "input-low" | "input-high" | "output-low" | "output-high" | "context";
type MoneyView = "prices" | "history";
type SourceFilter = "all" | "open_weight" | "unreported";

export interface ModelPriceSelection {
  targetLabel: string;
  onSelect: (model: ModelPriceInfo) => void;
  rankingContext?: { projectId: string; workflowId: string };
}

const PROVIDER_NAMES: Record<string, string> = {
  ai21: "AI21",
  anthropic: "Anthropic",
  cohere: "Cohere",
  deepseek: "DeepSeek",
  google: "Google",
  meta: "Meta",
  "meta-llama": "Meta Llama",
  mistralai: "Mistral AI",
  openai: "OpenAI",
  openrouter: "OpenRouter",
  qwen: "Qwen",
  xai: "xAI",
  "x-ai": "xAI",
};

const COMPANY_MARKS: Record<string, string> = {
  ai21: "21",
  anthropic: "A",
  "amazon-bedrock": "AWS",
  bedrock: "AWS",
  cerebras: "C",
  cloudflare: "CF",
  cohere: "C",
  deepseek: "DS",
  fireworks: "FW",
  google: "G",
  groq: "GQ",
  meta: "∞",
  "meta-llama": "∞",
  microsoft: "MS",
  mistralai: "M",
  nebius: "N",
  novita: "N",
  nvidia: "N",
  openai: "OA",
  qwen: "Q",
  sambanova: "SN",
  together: "TO",
  xai: "X",
  "x-ai": "X",
};

const COVERAGE_LABELS: Record<ModelPriceCoverage["pricing_kind"], string> = {
  live_catalog: "live rates",
  token_estimate: "token estimate",
  reported_after_run: "reported after run",
  provider_delegated: "provider-delegated",
};

function providerName(value: string): string {
  return PROVIDER_NAMES[value] ?? value.replace(/[-_]+/g, " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function providerSlug(value: string): string {
  return value.trim().toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "");
}

function CompanyIdentity({ value, compact = false }: { value: string; compact?: boolean }) {
  const slug = providerSlug(value);
  const name = providerName(value);
  const mark = COMPANY_MARKS[slug];
  return (
    <span className={`price-company ${compact ? "compact" : ""}`} title={`${name}${compact ? " inference provider" : " model author; the inference provider may differ"}`}>
      {mark && <i className={`price-company-mark company-${slug}`} aria-hidden="true">{mark}</i>}
      <span>{name}</span>
    </span>
  );
}

function tokens(value: number | null): string {
  if (value === null) return "—";
  if (value >= 1_000_000) return `${(value / 1_000_000).toLocaleString(undefined, { maximumFractionDigits: 1 })}M`;
  if (value >= 1_000) return `${Math.round(value / 1_000).toLocaleString()}K`;
  return value.toLocaleString();
}

function dateTime(value: string): string {
  const date = new Date(value);
  return Number.isNaN(date.valueOf()) ? value : date.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
}

function realizedMoney(value: number | null): string {
  if (value === null) return "unreported";
  if (value === 0) return "$0";
  const digits = value < 0.0001 ? 6 : value < 0.01 ? 5 : value < 1 ? 4 : 2;
  return `$${value.toLocaleString(undefined, { minimumFractionDigits: 0, maximumFractionDigits: digits })}`;
}

function elapsed(value: number | null): string {
  if (value === null) return "—";
  if (value < 1_000) return `${Math.round(value).toLocaleString()} ms`;
  return `${(value / 1_000).toLocaleString(undefined, { maximumFractionDigits: 1 })} s`;
}

function nullableNumber(left: number | null, right: number | null, direction: 1 | -1): number {
  if (left === null && right === null) return 0;
  if (left === null) return 1;
  if (right === null) return -1;
  return (left - right) * direction;
}

function sortModels(values: ModelPriceInfo[], sort: PriceSort): ModelPriceInfo[] {
  return [...values].sort((left, right) => {
    let compared = 0;
    if (sort === "input-low") compared = nullableNumber(left.input_mtok_usd, right.input_mtok_usd, 1);
    else if (sort === "input-high") compared = nullableNumber(left.input_mtok_usd, right.input_mtok_usd, -1);
    else if (sort === "output-low") compared = nullableNumber(left.output_mtok_usd, right.output_mtok_usd, 1);
    else if (sort === "output-high") compared = nullableNumber(left.output_mtok_usd, right.output_mtok_usd, -1);
    else if (sort === "context") compared = nullableNumber(left.context_length, right.context_length, -1);
    if (compared) return compared;
    return left.name.localeCompare(right.name) || left.model_id.localeCompare(right.model_id);
  });
}

function rankingPercent(value: number | null | undefined): string {
  return value == null ? "—" : `${(value * 100).toFixed(0)}%`;
}

function rankingMoney(value: number | null): string {
  if (value == null) return "unpriced";
  return `$${value.toLocaleString(undefined, { maximumFractionDigits: 6 })}`;
}

function extraPrices(model: ModelPriceInfo): { label: string; value: string }[] {
  const values = [
    ["cache read / MTok", money(model.cache_read_mtok_usd)],
    ["cache write / MTok", money(model.cache_write_mtok_usd)],
    ["reasoning / MTok", money(model.reasoning_mtok_usd)],
    ["per request", money(model.request_usd)],
    ["per image", money(model.image_usd)],
    ["per web search", money(model.web_search_usd)],
  ] as const;
  return values
    .filter(([_label, value]) => value !== "—")
    .map(([label, value]) => ({ label, value }));
}

function OpenRouterCallHistory() {
  const [ledger, setLedger] = useState<OpenRouterCallLedger | null>(null);
  const [projectId, setProjectId] = useState("");
  const [offset, setOffset] = useState(0);
  const [expanded, setExpanded] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const load = () => {
    setLoading(true);
    setError("");
    return fetchOpenRouterCalls(projectId || null, 100, offset)
      .then(setLedger)
      .catch((reason) => setError(String(reason)))
      .finally(() => setLoading(false));
  };

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError("");
    void fetchOpenRouterCalls(projectId || null, 100, offset)
      .then((payload) => { if (!cancelled) setLedger(payload); })
      .catch((reason) => { if (!cancelled) setError(String(reason)); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [offset, projectId]);

  const summary = ledger?.summary;
  return (
    <section className="price-usage" aria-labelledby="price-usage-title">
      <div className="price-section-heading price-usage-heading">
        <div><span>REALIZED USAGE</span><strong id="price-usage-title">OpenRouter call trail</strong></div>
        <small>Provider-reported cost · prompts and responses are never stored</small>
      </div>
      <div className="price-usage-toolbar">
        <label>
          <span>project</span>
          <select value={projectId} onChange={(event) => { setProjectId(event.target.value); setOffset(0); }}>
            <option value="">all projects</option>
            {(ledger?.projects ?? []).map((project) => (
              <option value={project.project_id} key={project.project_id}>{project.project_name}</option>
            ))}
          </select>
        </label>
        <span>{loading ? "reading call records…" : `${(summary?.priced_calls ?? 0).toLocaleString()} priced · ${(summary?.unpriced_calls ?? 0).toLocaleString()} unpriced`}</span>
        <button disabled={loading} onClick={() => void load()}>{loading ? "refreshing…" : "refresh calls"}</button>
      </div>
      {error && <div className="price-error"><span>{error}</span><button onClick={() => void load()}>retry</button></div>}
      <div className="price-usage-summary" aria-label="OpenRouter usage totals">
        <div><span>reported spend</span><strong>{realizedMoney(summary?.cost_usd ?? 0)}</strong></div>
        <div><span>calls</span><strong>{(summary?.calls ?? 0).toLocaleString()}</strong><small>{(summary?.failed_calls ?? 0).toLocaleString()} failed</small></div>
        <div><span>input tokens</span><strong>{tokens(summary?.prompt_tokens ?? 0)}</strong></div>
        <div><span>output tokens</span><strong>{tokens(summary?.completion_tokens ?? 0)}</strong></div>
      </div>

      {(ledger?.projects.length ?? 0) > 0 && !projectId && (
        <div className="price-usage-block">
          <div className="price-usage-label"><span>BY PROJECT</span><small>{ledger?.projects.length.toLocaleString()} projects with direct calls</small></div>
          <div className="price-usage-table" role="table" aria-label="OpenRouter usage by project">
            <div className="price-usage-project-row price-usage-table-head" role="row">
              <span role="columnheader">project</span><span role="columnheader">calls</span><span role="columnheader">priced</span><span role="columnheader">tokens</span><span role="columnheader">spend</span><span role="columnheader">latest</span>
            </div>
            {ledger?.projects.map((project) => (
              <button className="price-usage-project-row" role="row" onClick={() => { setProjectId(project.project_id); setOffset(0); }} key={project.project_id}>
                <strong role="cell">{project.project_name}</strong>
                <span role="cell" data-label="calls">{project.calls.toLocaleString()}</span>
                <span role="cell" data-label="priced">{project.priced_calls.toLocaleString()}</span>
                <span role="cell" data-label="tokens">{tokens(project.total_tokens)}</span>
                <strong role="cell" data-label="spend">{realizedMoney(project.cost_usd)}</strong>
                <span role="cell" data-label="latest">{project.last_call_at ? dateTime(project.last_call_at) : "—"}</span>
              </button>
            ))}
          </div>
        </div>
      )}

      <div className="price-usage-block">
        <div className="price-usage-label"><span>CALL RECORDS</span><small>{ledger && ledger.total > 0 ? `${(ledger.offset + 1).toLocaleString()}–${Math.min(ledger.offset + ledger.calls.length, ledger.total).toLocaleString()} of ${ledger.total.toLocaleString()}` : "0 calls"}</small></div>
        <div className="price-call-table" role="table" aria-label="Recent OpenRouter calls">
          <div className="price-call-row price-usage-table-head" role="row">
            <span role="columnheader">time</span><span role="columnheader">project</span><span role="columnheader">model</span><span role="columnheader">provider</span><span role="columnheader">input</span><span role="columnheader">output</span><span role="columnheader">cost</span><span role="columnheader">status</span>
          </div>
          {(ledger?.calls ?? []).map((call) => {
            const open = expanded === call.call_id;
            return (
              <div className={`price-call ${open ? "expanded" : ""}`} key={call.call_id}>
                <div className="price-call-row" role="row">
                  <span role="cell">{dateTime(call.started_at)}</span>
                  <span role="cell" data-label="project">{call.project_name}</span>
                  <span className="price-call-model" role="cell" data-label="model"><button aria-expanded={open} onClick={() => setExpanded(open ? null : call.call_id)}>{call.resolved_model ?? call.requested_model ?? "unknown"}<small>{call.requested_model && call.requested_model !== call.resolved_model ? `requested ${call.requested_model}` : call.run_id ?? "run unavailable"}</small></button></span>
                  <span role="cell" data-label="provider">{call.upstream_provider ?? "—"}</span>
                  <span role="cell" data-label="input">{tokens(call.prompt_tokens)}</span>
                  <span role="cell" data-label="output">{tokens(call.completion_tokens)}</span>
                  <strong role="cell" data-label="cost">{realizedMoney(call.cost_usd)}</strong>
                  <span className={`price-call-status ${call.status}`} role="cell" data-label="status">{call.status}</span>
                </div>
                {open && <CallDetail call={call} />}
              </div>
            );
          })}
          {!loading && (ledger?.calls.length ?? 0) === 0 && <div className="price-empty">No direct OpenRouter calls have been recorded{projectId ? " for this project" : ""}.</div>}
        </div>
        {(ledger?.total ?? 0) > (ledger?.limit ?? 100) && (
          <div className="price-call-pagination">
            <button disabled={loading || offset === 0} onClick={() => setOffset(Math.max(0, offset - (ledger?.limit ?? 100)))}>newer calls</button>
            <span>page {Math.floor(offset / (ledger?.limit ?? 100)) + 1}</span>
            <button disabled={loading || offset + (ledger?.limit ?? 100) >= (ledger?.total ?? 0)} onClick={() => setOffset(offset + (ledger?.limit ?? 100))}>older calls</button>
          </div>
        )}
      </div>
      <p className="price-footnote">This append-only local trail covers calls made directly by Reasoning Native. Costs come from OpenRouter&apos;s response usage; calls with no reported cost remain visible and are excluded from spend totals.</p>
    </section>
  );
}

function CallDetail({ call }: { call: OpenRouterCall }) {
  return (
    <div className="price-call-detail">
      <dl>
        <div><dt>agent run</dt><dd>{call.run_id ?? "—"}{call.sequence ? ` · call ${call.sequence}` : ""}</dd></div>
        <div><dt>response ID</dt><dd>{call.response_id ?? "—"}</dd></div>
        <div><dt>duration</dt><dd>{elapsed(call.duration_ms)}</dd></div>
        <div><dt>finish</dt><dd>{call.native_finish_reason ?? call.finish_reason ?? "—"}</dd></div>
        <div><dt>reasoning tokens</dt><dd>{tokens(call.reasoning_tokens)}</dd></div>
        <div><dt>cache read / write</dt><dd>{tokens(call.cached_tokens)} / {tokens(call.cache_write_tokens)}</dd></div>
        <div><dt>upstream inference</dt><dd>{realizedMoney(call.upstream_cost_usd)}</dd></div>
        <div><dt>record</dt><dd>{call.call_id}</dd></div>
      </dl>
      {call.error && <p>{call.error}</p>}
    </div>
  );
}

export function PriceBrowser({
  onManageKeys,
  selection,
  defaultView = "prices",
}: {
  onManageKeys?: () => void;
  selection?: ModelPriceSelection;
  defaultView?: MoneyView;
}) {
  const [view, setView] = useState<MoneyView>(selection ? "prices" : defaultView);
  const [catalog, setCatalog] = useState<ModelPriceCatalog | null>(null);
  const [scope, setScope] = useState<ModelPriceScope>("all");
  const [query, setQuery] = useState("");
  const [provider, setProvider] = useState("all");
  const [sort, setSort] = useState<PriceSort>("name");
  const [pricedOnly, setPricedOnly] = useState(false);
  const [toolsOnly, setToolsOnly] = useState(false);
  const [reasoningOnly, setReasoningOnly] = useState(false);
  const [pinnedOnly, setPinnedOnly] = useState(false);
  const [sourceFilter, setSourceFilter] = useState<SourceFilter>("all");
  const [pinnedModels, setPinnedModels] = useState<string[]>([]);
  const [rankings, setRankings] = useState<WorkflowModelRankings | null>(null);
  const [rankingsLoading, setRankingsLoading] = useState(false);
  const [rankingsProblem, setRankingsProblem] = useState("");
  const [rankingMode, setRankingMode] = useState<ModelRankingMode["mode_id"] | "catalog">("catalog");
  const [expanded, setExpanded] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const load = (refresh = false) => {
    setLoading(true);
    setError("");
    return fetchModelPrices(scope, refresh)
      .then((payload) => setCatalog(payload))
      .catch((reason) => setError(String(reason)))
      .finally(() => setLoading(false));
  };

  useEffect(() => {
    if (view !== "prices") {
      setLoading(false);
      return;
    }
    let cancelled = false;
    setLoading(true);
    setError("");
    void fetchModelPrices(scope)
      .then((payload) => { if (!cancelled) setCatalog(payload); })
      .catch((reason) => { if (!cancelled) setError(String(reason)); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [scope, view]);

  useEffect(() => {
    let cancelled = false;
    void fetchPinnedModels()
      .then(({ openrouter }) => { if (!cancelled) setPinnedModels(openrouter); })
      .catch(() => undefined);
    return () => { cancelled = true; };
  }, []);

  useEffect(() => {
    const context = selection?.rankingContext;
    if (!context) {
      setRankings(null);
      setRankingsLoading(false);
      setRankingsProblem("");
      setRankingMode("catalog");
      return;
    }
    let cancelled = false;
    setRankingsLoading(true);
    setRankingsProblem("");
    void fetchWorkflowModelRankings(context.projectId, context.workflowId)
      .then((value) => { if (!cancelled) setRankings(value); })
      .catch((reason) => {
        if (!cancelled) {
          setRankings(null);
          setRankingsProblem(String(reason));
        }
      })
      .finally(() => { if (!cancelled) setRankingsLoading(false); });
    return () => { cancelled = true; };
  }, [selection?.rankingContext?.projectId, selection?.rankingContext?.workflowId]);

  const activeRankingMode = rankings?.modes.find((mode) => mode.mode_id === rankingMode);
  const rankingConfigurations = useMemo(
    () => new Map((rankings?.configurations ?? []).map((configuration) => [configuration.configuration_id, configuration])),
    [rankings],
  );
  const directModeCounts = useMemo(() => new Map((rankings?.modes ?? []).map((mode) => [
    mode.mode_id,
    mode.configuration_ids.filter(
      (configurationId) => rankingConfigurations.get(configurationId)?.executor_id === "reasoning-native",
    ).length,
  ])), [rankingConfigurations, rankings]);
  const rankedDirectConfigurations = useMemo(() => (activeRankingMode?.configuration_ids ?? [])
    .map((configurationId) => rankingConfigurations.get(configurationId))
    .filter((configuration): configuration is ModelRankingConfiguration => configuration?.executor_id === "reasoning-native"),
  [activeRankingMode, rankingConfigurations]);
  const rankingOrder = useMemo(() => {
    const values = new Map<string, number>();
    for (const configuration of rankedDirectConfigurations) {
      if (!values.has(configuration.model)) values.set(configuration.model, values.size);
    }
    return values;
  }, [rankedDirectConfigurations]);
  const evidenceByModel = useMemo(() => {
    const values = new Map<string, ModelRankingConfiguration>();
    for (const configuration of rankedDirectConfigurations) {
      if (!values.has(configuration.model)) values.set(configuration.model, configuration);
    }
    for (const configuration of rankings?.configurations ?? []) {
      if (configuration.executor_id !== "reasoning-native") continue;
      const existing = values.get(configuration.model);
      if (!existing || (!activeRankingMode && configuration.calls > existing.calls)) {
        values.set(configuration.model, configuration);
      }
    }
    return values;
  }, [activeRankingMode, rankedDirectConfigurations, rankings]);

  const providers = useMemo(
    () => [...new Set((catalog?.models ?? []).map((model) => model.provider))]
      .sort((left, right) => providerName(left).localeCompare(providerName(right))),
    [catalog],
  );
  const visibleModels = useMemo(() => {
    const normalized = query.trim().toLowerCase();
    const filtered = (catalog?.models ?? []).filter((model) => {
      if (provider !== "all" && model.provider !== provider) return false;
      if (pricedOnly && model.input_mtok_usd === null && model.output_mtok_usd === null) return false;
      if (toolsOnly && !model.supports_tools) return false;
      if (reasoningOnly && !model.supports_reasoning) return false;
      if (pinnedOnly && !pinnedModels.includes(model.model_id)) return false;
      if (sourceFilter !== "all" && model.source_availability !== sourceFilter) return false;
      if (!normalized) return true;
      return `${model.name} ${model.model_id} ${model.provider} ${model.description}`.toLowerCase().includes(normalized);
    });
    const catalogSorted = sortModels(filtered, sort);
    if (!activeRankingMode) return catalogSorted;
    return [...catalogSorted].sort((left, right) => {
      const leftRank = rankingOrder.get(left.model_id) ?? Number.POSITIVE_INFINITY;
      const rightRank = rankingOrder.get(right.model_id) ?? Number.POSITIVE_INFINITY;
      return leftRank - rightRank || left.name.localeCompare(right.name);
    });
  }, [activeRankingMode, catalog, pinnedModels, pinnedOnly, pricedOnly, provider, query, rankingOrder, reasoningOnly, sort, sourceFilter, toolsOnly]);

  const togglePinned = (modelId: string) => {
    const next = pinnedModels.includes(modelId)
      ? pinnedModels.filter((candidate) => candidate !== modelId)
      : [...pinnedModels, modelId];
    setPinnedModels(next);
    void savePinnedModels("openrouter", next)
      .then(({ openrouter }) => setPinnedModels(openrouter))
      .catch(() => setPinnedModels(pinnedModels));
  };

  const source = catalog?.source;
  return (
    <main className={`price-browser ${selection ? "selecting" : ""}`}>
      <header className="price-browser-header">
        <div>
          <span className="price-eyebrow">{selection ? "MODEL SELECTION" : "MODEL ECONOMICS"}</span>
          <h1>{selection ? "Choose a priced model" : "Money"}</h1>
          <p>{selection
            ? `Select an OpenRouter model for ${selection.targetLabel}; Reasoning Native will run it.`
            : view === "prices"
              ? "Compare the current model catalog and token rates before planning a workflow budget."
              : "Review realized OpenRouter spend and the local call trail."}</p>
        </div>
        <div className="price-header-actions">
          {onManageKeys && <button onClick={onManageKeys}>manage API keys</button>}
          {view === "prices" && <button className="confirm" disabled={loading} onClick={() => void load(true)}>{loading ? "refreshing…" : "refresh prices"}</button>}
        </div>
      </header>

      {!selection && (
        <nav className="price-view-tabs" aria-label="Money views" role="tablist">
          <button
            role="tab"
            aria-selected={view === "prices"}
            className={view === "prices" ? "selected" : ""}
            onClick={() => setView("prices")}
          >
            Current prices
            <small>models and capabilities</small>
          </button>
          <button
            role="tab"
            aria-selected={view === "history"}
            className={view === "history" ? "selected" : ""}
            onClick={() => setView("history")}
          >
            Usage history
            <small>spend and call records</small>
          </button>
        </nav>
      )}

      {view === "prices" && source && (
        <div className={`price-source-strip ${source.status}`}>
          <i aria-hidden="true" />
          <div>
            <strong>{source.label} · {source.scope === "account" ? "account catalog" : "public catalog"}</strong>
            <span>{source.model_count.toLocaleString()} text models · {source.status} · fetched {dateTime(source.fetched_at)}</span>
          </div>
          <span>{source.detail}</span>
          <a href={source.catalog_url} target="_blank" rel="noreferrer">provider catalog ↗</a>
        </div>
      )}
      {view === "prices" && source?.status === "stale" && <div className="price-warning">Live refresh failed; showing the last successful catalog. {source.error}</div>}
      {view === "prices" && error && <div className="price-error"><span>{error}</span><button onClick={() => void load(true)}>retry</button></div>}

      {!selection && view === "history" && <OpenRouterCallHistory />}

      {!selection && view === "prices" && <section className="price-coverage" aria-labelledby="price-coverage-title">
        <div className="price-section-heading">
          <div><span>LOCAL EXECUTORS</span><strong id="price-coverage-title">How Reasoning sees cost</strong></div>
          <small>Token tariffs and realized run costs are different signals.</small>
        </div>
        <div className="price-coverage-grid">
          {(catalog?.coverage ?? []).map((item) => (
            <article className={`price-coverage-card ${item.ready ? "ready" : "needs-setup"}`} key={item.executor_id}>
              <div><i aria-hidden="true" /><strong>{item.display_name}</strong><span>{COVERAGE_LABELS[item.pricing_kind]}</span></div>
              <p>{item.detail}</p>
              <small>{item.readiness}{item.key_configured === true ? " · API key configured" : item.key_configured === false ? " · no API key" : ""}</small>
            </article>
          ))}
        </div>
      </section>}

      {view === "prices" && <section className="price-catalog" aria-labelledby="price-catalog-title">
        <div className="price-section-heading">
          <div><span>LIVE CATALOG</span><strong id="price-catalog-title">Token rates</strong></div>
          <small>USD per million tokens · OpenRouter's lowest currently advertised route</small>
        </div>
        {selection?.rankingContext && (
          <div className="price-ranking-strip">
            <div>
              <span>REASONING EVIDENCE</span>
              <strong>{rankingsLoading
                ? "Reading compatible run evidence…"
                : rankingsProblem
                  ? "Recommendations unavailable"
                  : `${rankings?.compatible_runs ?? 0} compatible runs`}</strong>
              <small>{activeRankingMode?.description ?? "Choose an observed ranking or use the live catalog order."}</small>
            </div>
            <div>
              <button className={rankingMode === "catalog" ? "selected" : ""} onClick={() => setRankingMode("catalog")}>catalog</button>
              {(rankings?.modes ?? []).map((mode) => (
                <button
                  className={rankingMode === mode.mode_id ? "selected" : ""}
                  disabled={!mode.available || (directModeCounts.get(mode.mode_id) ?? 0) === 0}
                  title={mode.description}
                  onClick={() => setRankingMode(mode.mode_id)}
                  key={mode.mode_id}
                >
                  {mode.label}
                </button>
              ))}
            </div>
          </div>
        )}
        <div className="price-controls">
          <label className="price-search"><span>⌕</span><input autoFocus={Boolean(selection)} value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search model or author…" aria-label="Search model prices" /></label>
          <label><span>scope</span><select value={scope} onChange={(event) => setScope(event.target.value as ModelPriceScope)}><option value="all">all OpenRouter models</option><option value="account" disabled={!source?.account_filter_available}>my account filters</option></select></label>
          <label><span>author</span><select value={provider} onChange={(event) => setProvider(event.target.value)}><option value="all">all model authors</option>{providers.map((value) => <option value={value} key={value}>{providerName(value)}</option>)}</select></label>
          <label><span>source</span><select value={sourceFilter} onChange={(event) => setSourceFilter(event.target.value as SourceFilter)}><option value="all">all models</option><option value="open_weight">open-weight listing</option><option value="unreported">not marked open</option></select></label>
          <label><span>sort</span><select value={sort} onChange={(event) => setSort(event.target.value as PriceSort)}><option value="name">model name</option><option value="input-low">input · low first</option><option value="input-high">input · high first</option><option value="output-low">output · low first</option><option value="output-high">output · high first</option><option value="context">largest context</option></select></label>
          <label className="price-check"><input type="checkbox" checked={pricedOnly} onChange={(event) => setPricedOnly(event.target.checked)} /><span>priced only</span></label>
          <label className="price-check"><input type="checkbox" checked={toolsOnly} onChange={(event) => setToolsOnly(event.target.checked)} /><span>tool calling</span></label>
          <label className="price-check"><input type="checkbox" checked={reasoningOnly} onChange={(event) => setReasoningOnly(event.target.checked)} /><span>reasoning</span></label>
          <label className="price-check"><input type="checkbox" checked={pinnedOnly} onChange={(event) => setPinnedOnly(event.target.checked)} /><span>starred</span></label>
        </div>

        <div className="price-result-count">{loading && !catalog ? "loading catalog…" : `${visibleModels.length.toLocaleString()} of ${(catalog?.models.length ?? 0).toLocaleString()} models`}</div>
        <div className="price-table" role="table" aria-label={selection ? "Choose an OpenRouter model by price" : "Model token prices"}>
          <div className="price-table-row price-table-head" role="row">
            <span role="columnheader">model</span><span role="columnheader">author</span><span role="columnheader">input / MTok</span><span role="columnheader">output / MTok</span><span role="columnheader">context</span><span role="columnheader">reasoning</span><span role="columnheader">tools</span>{selection && <span role="columnheader">use</span>}
          </div>
          {visibleModels.map((model) => {
            const open = expanded === model.model_id;
            const extras = extraPrices(model);
            const evidence = evidenceByModel.get(model.model_id);
            const observedRank = rankingOrder.get(model.model_id);
            const pinned = pinnedModels.includes(model.model_id);
            return (
              <div className={`price-model ${open ? "expanded" : ""}`} key={model.model_id}>
                <div className="price-table-row" role="row">
                  <span className="price-model-name" role="cell">
                    <span className="price-model-title-line">
                      <button aria-expanded={open} onClick={() => setExpanded(open ? null : model.model_id)}><strong>{model.name}</strong><small>{model.model_id}</small></button>
                      <button
                        className={`price-model-pin ${pinned ? "pinned" : ""}`}
                        aria-label={`${pinned ? "Unstar" : "Star"} ${model.name}`}
                        title={`${pinned ? "Unstar" : "Star"} this model`}
                        onClick={() => togglePinned(model.model_id)}
                      >
                        {pinned ? "★" : "☆"}
                      </button>
                    </span>
                    {observedRank != null && <em>#{observedRank + 1} · {activeRankingMode?.label}</em>}
                    {evidence && observedRank == null && <em>{evidence.calls} observed calls</em>}
                    {model.source_availability === "open_weight" && <em title="OpenRouter supplies a Hugging Face model ID; check that model's license for exact terms.">open-weight listing</em>}
                  </span>
                  <span role="cell" data-label="author"><CompanyIdentity value={model.provider} /></span>
                  <strong className="price-amount" role="cell" data-label="input / MTok">{money(model.input_mtok_usd)}</strong>
                  <strong className="price-amount" role="cell" data-label="output / MTok">{money(model.output_mtok_usd)}</strong>
                  <span className="price-context" role="cell" data-label="context">{tokens(model.context_length)}</span>
                  <span className="price-capability" role="cell" data-label="reasoning" title={model.supports_reasoning ? "OpenRouter reports reasoning parameter support" : "Reasoning parameter support is not reported"}>{model.supports_reasoning ? "yes" : "—"}</span>
                  <span className="price-tools" role="cell" data-label="tools">{model.supports_tools ? "yes" : "—"}</span>
                  {selection && <span className="price-model-action" role="cell" data-label="use"><button onClick={() => selection.onSelect(model)}>use model</button></span>}
                </div>
                {open && (
                  <div className="price-model-detail">
                    <div className="price-detail-copy">
                      <p>{model.description || "No provider description is available."}</p>
                      <dl><div><dt>canonical ID</dt><dd>{model.canonical_slug}</dd></div><div><dt>max output</dt><dd>{model.max_output_tokens === null ? "—" : `${tokens(model.max_output_tokens)} tokens`}</dd></div><div><dt>modalities</dt><dd>{model.modality ?? ([...model.input_modalities, ...model.output_modalities].join(", ") || "—")}</dd></div>{model.hugging_face_id && <div><dt>open-weight listing</dt><dd>{model.hugging_face_id}</dd></div>}{model.expiration_date && <div><dt>expires</dt><dd>{model.expiration_date}</dd></div>}</dl>
                      {evidence && (
                        <dl className="price-observed-evidence">
                          <div><dt>executor</dt><dd>{evidence.executor_id}</dd></div>
                          <div><dt>provider setting</dt><dd>{evidence.model_provider ?? "automatic"}</dd></div>
                          <div><dt>reasoning effort</dt><dd>{evidence.effort ?? "default"}</dd></div>
                          <div><dt>observed calls</dt><dd>{evidence.calls}</dd></div>
                          <div><dt>success</dt><dd>{rankingPercent(evidence.success_rate)}</dd></div>
                          <div><dt>median latency</dt><dd>{evidence.median_latency_seconds == null ? "—" : `${evidence.median_latency_seconds.toFixed(2)}s`}</dd></div>
                          <div><dt>median cost</dt><dd>{rankingMoney(evidence.median_cost_usd)}</dd></div>
                          <div><dt>benchmark cases</dt><dd>{evidence.benchmark?.cases ?? "—"}</dd></div>
                          <div><dt>false allows</dt><dd>{evidence.benchmark ? `${evidence.benchmark.false_allow_count}/${evidence.benchmark.unsafe_cases}` : "—"}</dd></div>
                          <div><dt>served by</dt><dd>{evidence.serving_providers.length ? evidence.serving_providers.map((item) => <CompanyIdentity value={item} compact key={item} />) : "unreported"}</dd></div>
                        </dl>
                      )}
                    </div>
                    <div className="price-detail-rates">
                      {extras.length ? extras.map((item) => <div key={item.label}><span>{item.label}</span><strong>{item.value}</strong></div>) : <span>No additional unit rates reported.</span>}
                    </div>
                    <div className="price-parameters"><span>supported parameters</span><p>{model.supported_parameters.length ? model.supported_parameters.join(" · ") : "not reported"}</p></div>
                  </div>
                )}
              </div>
            );
          })}
          {!loading && visibleModels.length === 0 && <div className="price-empty">No models match these filters.</div>}
        </div>
        <p className="price-footnote">Catalog rates and capabilities are provider metadata, not a quote or benchmark. Auto-routed models, provider variants, caching, reasoning tokens, tools, and account agreements can change the realized cost.{selection ? " Selecting a row switches this model choice to Reasoning Native." : ""}</p>
      </section>}
    </main>
  );
}
