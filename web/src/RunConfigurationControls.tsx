import {
  ChatExecutionOptions,
  ExecutionPreflight,
  SelectedRunConfiguration,
} from "./api";

const EFFORT_LABELS: Record<string, string> = {
  none: "Reasoning off",
  low: "Low reasoning",
  medium: "Medium reasoning",
  high: "High reasoning",
  xhigh: "Extra-high reasoning",
  max: "Maximum reasoning",
};

function unique(values: string[]): string[] {
  return [...new Set(values.filter(Boolean))];
}

/**
 * Provider-neutral run controls. The server supplies every relationship and
 * capability used here; the client does not infer behavior from a harness,
 * provider, model, or target name.
 */
export function RunConfigurationControls({
  options,
  selection,
  preflight,
  disabled,
  onChange,
}: {
  options: ChatExecutionOptions;
  selection: SelectedRunConfiguration;
  preflight: ExecutionPreflight | null;
  disabled: boolean;
  onChange: (selection: SelectedRunConfiguration) => void;
}) {
  const harness = options.harnesses.find(
    (candidate) => candidate.harness_id === selection.harness_id,
  );
  const route = options.inference_routes.find(
    (candidate) => candidate.route_id === selection.inference_route_id,
  );
  const modelOptions = unique([
    ...(route?.model_options ?? []),
    ...(harness?.model_options ?? []),
  ]);
  const routeEfforts = route?.reasoning_efforts ?? [];
  const reasoningEfforts = (harness?.reasoning_efforts ?? []).filter(
    (effort) => routeEfforts.length === 0 || routeEfforts.includes(effort),
  );
  const sessionPinned = Boolean(options.session_binding);
  const pinnedTitle = sessionPinned
    ? "This choice is pinned by the persistent session. Start a new chat to change it."
    : undefined;

  const chooseHarness = (harnessId: string) => {
    const nextHarness = options.harnesses.find(
      (candidate) => candidate.harness_id === harnessId,
    );
    const nextRoute = options.inference_routes.find(
      (candidate) =>
        candidate.route_id === selection.inference_route_id
        && candidate.harness_ids.includes(harnessId),
    ) ?? options.inference_routes.find((candidate) =>
      candidate.harness_ids.includes(harnessId));
    const nextEffort = selection.reasoning_effort
      && nextHarness?.reasoning_efforts.includes(selection.reasoning_effort)
      ? selection.reasoning_effort
      : nextHarness?.reasoning_efforts[0] ?? null;
    onChange({
      ...selection,
      harness_id: harnessId,
      inference_route_id: nextRoute?.route_id ?? "",
      model: selection.model || nextRoute?.model_options[0] || "",
      reasoning_effort: nextEffort,
    });
  };

  const chooseRoute = (routeId: string) => {
    const nextRoute = options.inference_routes.find(
      (candidate) => candidate.route_id === routeId,
    );
    const nextHarness = nextRoute?.harness_ids.includes(selection.harness_id)
      ? selection.harness_id
      : nextRoute?.harness_ids[0] ?? selection.harness_id;
    onChange({
      ...selection,
      harness_id: nextHarness,
      inference_route_id: routeId,
      model: selection.model || nextRoute?.model_options[0] || "",
    });
  };

  return (
    <div className="run-configuration-controls">
      <label>
        <span>Harness</span>
        <select
          aria-label="Harness"
          value={selection.harness_id}
          disabled={disabled || sessionPinned}
          title={pinnedTitle}
          onChange={(event) => chooseHarness(event.target.value)}
        >
          {options.harnesses.map((candidate) => (
            <option key={candidate.harness_id} value={candidate.harness_id}>
              {candidate.display_name}
            </option>
          ))}
        </select>
      </label>
      <label>
        <span>Provider route</span>
        <select
          aria-label="Provider route"
          value={selection.inference_route_id}
          disabled={disabled}
          onChange={(event) => chooseRoute(event.target.value)}
        >
          {options.inference_routes.map((candidate) => (
            <option
              key={candidate.route_id}
              value={candidate.route_id}
              disabled={!candidate.harness_ids.includes(selection.harness_id)}
            >
              {candidate.display_name}{candidate.ready ? "" : " · setup required"}
            </option>
          ))}
        </select>
      </label>
      <label className="run-model-control">
        <span>Model</span>
        <input
          aria-label="Model"
          list="run-configuration-models"
          value={selection.model}
          disabled={disabled || sessionPinned}
          title={pinnedTitle}
          onChange={(event) => onChange({ ...selection, model: event.target.value })}
        />
        <datalist id="run-configuration-models">
          {modelOptions.map((model) => <option key={model} value={model} />)}
        </datalist>
      </label>
      <label>
        <span>Target</span>
        <select
          aria-label="Execution target"
          value={selection.target_id}
          disabled={disabled || sessionPinned}
          title={pinnedTitle}
          onChange={(event) => onChange({
            ...selection,
            target_id: event.target.value,
          })}
        >
          {options.execution_targets.map((candidate) => (
            <option key={candidate.target_id} value={candidate.target_id}>
              {candidate.display_name}
            </option>
          ))}
        </select>
      </label>
      <label>
        <span>Reasoning</span>
        <select
          aria-label="Reasoning effort"
          value={selection.reasoning_effort ?? ""}
          disabled={disabled}
          onChange={(event) => onChange({
            ...selection,
            reasoning_effort: event.target.value || null,
          })}
        >
          <option value="">Harness default</option>
          {reasoningEfforts.map((effort) => (
            <option key={effort} value={effort}>
              {EFFORT_LABELS[effort] ?? effort}
            </option>
          ))}
        </select>
      </label>
      <span
        className={`chat-run-preflight ${preflight?.compatible ? "ready" : "needs-setup"}`}
        title={preflight?.issues.map((issue) => issue.detail).join("\n")}
        aria-live="polite"
      >
        {!preflight
          ? "checking…"
          : preflight.compatible
            ? "compatible"
            : `${preflight.issues.length} issue${preflight.issues.length === 1 ? "" : "s"}`}
      </span>
      {preflight && !preflight.compatible && (
        <div className="chat-run-preflight-issues" role="alert">
          {preflight.issues.map((issue) => (
            <span key={`${issue.scope}:${issue.code}`}>{issue.detail}</span>
          ))}
        </div>
      )}
    </div>
  );
}
