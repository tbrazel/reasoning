import { useState } from "react";
import {
  AutoRoutingDecision,
  AutoSelectedRunConfiguration,
  RoutingObjective,
  autoRouteChat,
  autoRouteWorkflow,
} from "./api";

const OBJECTIVES: { value: RoutingObjective; label: string }[] = [
  { value: "balanced", label: "balanced" },
  { value: "quality", label: "quality" },
  { value: "reliability", label: "reliable" },
  { value: "cost", label: "lowest cost" },
  { value: "latency", label: "fastest" },
];

function formatSelection(selection: AutoSelectedRunConfiguration): string {
  return [
    selection.harness_id,
    selection.provider,
    selection.model,
    selection.reasoning_effort ? `${selection.reasoning_effort} reasoning` : "executor-default reasoning",
  ].filter(Boolean).join(" · ");
}

export function AutoRoutingControl({
  source,
  projectId,
  resourceId,
  prompt = "",
  reasoningEffort = null,
  disabled = false,
  onSelect,
}: {
  source: "chat" | "workflow";
  projectId: string;
  resourceId: string;
  prompt?: string;
  reasoningEffort?: string | null;
  disabled?: boolean;
  onSelect: (selection: AutoSelectedRunConfiguration) => void;
}) {
  const [objective, setObjective] = useState<RoutingObjective>("balanced");
  const [decision, setDecision] = useState<AutoRoutingDecision | null>(null);
  const [loading, setLoading] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const [expanded, setExpanded] = useState(false);
  const promptReady = source === "workflow" || Boolean(prompt.trim());

  const choose = () => {
    if (!promptReady || loading || disabled) return;
    setLoading(true);
    setProblem(null);
    const request = source === "chat"
      ? autoRouteChat(projectId, resourceId, prompt, objective, reasoningEffort)
      : autoRouteWorkflow(projectId, resourceId, objective);
    void request
      .then((result) => {
        setDecision(result);
        setExpanded(true);
        if (result.selected) onSelect(result.selected);
      })
      .catch((error) => {
        setDecision(null);
        setProblem(error instanceof Error ? error.message : String(error));
        setExpanded(true);
      })
      .finally(() => setLoading(false));
  };

  const selectedAssessment = decision?.assessments.find((item) => (
    item.selection.harness_id === decision.selected?.harness_id
    && item.selection.inference_route_id === decision.selected?.inference_route_id
    && item.selection.target_id === decision.selected?.target_id
    && item.selection.model === decision.selected?.model
    && item.selection.reasoning_effort === decision.selected?.reasoning_effort
  ));

  return (
    <div className="auto-routing-control">
      <div className="auto-routing-actions">
        <select
          aria-label="Auto routing objective"
          value={objective}
          disabled={disabled || loading}
          onChange={(event) => setObjective(event.target.value as RoutingObjective)}
        >
          {OBJECTIVES.map((item) => <option value={item.value} key={item.value}>{item.label}</option>)}
        </select>
        <button
          type="button"
          disabled={disabled || loading || !promptReady}
          title={promptReady
            ? "Choose a concrete configuration from live compatibility and task-specific evidence"
            : "Type a prompt before asking Auto to route it"}
          onClick={choose}
        >
          {loading ? "routing…" : "Auto"}
        </button>
        {(decision || problem) && (
          <button
            type="button"
            className="auto-routing-disclosure"
            aria-expanded={expanded}
            title={expanded ? "Hide routing evidence" : "Show routing evidence"}
            onClick={() => setExpanded((value) => !value)}
          >
            {decision?.selected ? "evidence" : "why not"} {expanded ? "▴" : "▾"}
          </button>
        )}
      </div>
      {expanded && (
        <div className={`auto-routing-result ${decision?.selected ? "selected" : "unavailable"}`}>
          {problem ? (
            <strong>{problem}</strong>
          ) : decision?.selected ? (
            <>
              <strong>{formatSelection(decision.selected)}</strong>
              <span>
                {decision.eligible_count}/{decision.candidate_count} compatible candidates · {decision.scope}
              </span>
              {selectedAssessment && (
                <span>
                  {selectedAssessment.components.map((item) => (
                    `${item.name} ${item.value.toFixed(2)} (${item.source})`
                  )).join(" · ")}
                </span>
              )}
              <span>{decision.notes.join(" ")}</span>
            </>
          ) : decision ? (
            <>
              <strong>No configuration met the {decision.objective} evidence policy.</strong>
              <span>
                {Object.entries(decision.rejection_counts)
                  .sort((left, right) => right[1] - left[1])
                  .slice(0, 5)
                  .map(([code, count]) => `${code.replaceAll("_", " ")} ×${count}`)
                  .join(" · ") || "No candidates were advertised."}
              </span>
              <span>{decision.notes.join(" ")}</span>
            </>
          ) : null}
        </div>
      )}
    </div>
  );
}
