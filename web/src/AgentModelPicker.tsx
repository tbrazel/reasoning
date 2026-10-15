import { useEffect, useState } from "react";
import { fetchPinnedModels, fetchRecentModels, rememberRecentModel } from "./api";
import { PriceModelPickerDialog } from "./PriceModelPickerDialog";

export interface AgentModelSource {
  executorId: string;
  label: string;
  provider: string;
  models: string[];
  ready: boolean;
  readiness: string;
  promptFidelity?: string;
}

interface ModelProfile {
  cost: string;
  context: string;
}

export function modelProfile(executorId: string, model: string): ModelProfile {
  const normalized = model.toLowerCase();
  if (executorId === "codex") {
    return { cost: "configured provider", context: "Codex-managed context" };
  }
  if (normalized.includes("auto")) {
    return { cost: "variable provider rate", context: "provider-routed context" };
  }
  return { cost: "provider-priced", context: "provider-defined context" };
}

function optionValue(executorId: string, model: string): string {
  return JSON.stringify([executorId, model]);
}

export const RECENT_OPENROUTER_MODELS_KEY = "reasoning-recent-openrouter-models";
export const OTHER_OPENROUTER_MODEL_VALUE = "browse-openrouter";
const RECENT_OPENROUTER_MODELS_EVENT = "reasoning-recent-openrouter-models-changed";
const MAX_RECENT_OPENROUTER_MODELS = 12;

export function parseRecentOpenRouterModels(raw: string | null): string[] {
  if (!raw) return [];
  try {
    const parsed = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];
    return [...new Set(
      parsed
        .filter((value): value is string => typeof value === "string")
        .map((value) => value.trim())
        .filter(Boolean),
    )].slice(0, MAX_RECENT_OPENROUTER_MODELS);
  } catch {
    return [];
  }
}

export function addRecentOpenRouterModel(models: string[], model: string): string[] {
  const selected = model.trim();
  if (!selected) return models.slice(0, MAX_RECENT_OPENROUTER_MODELS);
  return [selected, ...models.filter((candidate) => candidate !== selected)]
    .slice(0, MAX_RECENT_OPENROUTER_MODELS);
}

function readRecentOpenRouterModels(): string[] {
  if (typeof window === "undefined") return [];
  try {
    return parseRecentOpenRouterModels(window.localStorage.getItem(RECENT_OPENROUTER_MODELS_KEY));
  } catch {
    return [];
  }
}

function writeRecentOpenRouterModels(recent: string[]): void {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(RECENT_OPENROUTER_MODELS_KEY, JSON.stringify(recent));
    window.dispatchEvent(new Event(RECENT_OPENROUTER_MODELS_EVENT));
  } catch {
    // The selection still applies when private browsing or policy disables storage.
  }
}

export function AgentModelPicker({
  sources,
  executorId,
  model,
  disabled = false,
  showMetadata = true,
  priceTargetLabel = "this model selection",
  rankingContext,
  onChange,
}: {
  sources: AgentModelSource[];
  executorId: string;
  model: string;
  disabled?: boolean;
  showMetadata?: boolean;
  priceTargetLabel?: string;
  rankingContext?: { projectId: string; workflowId: string };
  onChange: (executorId: string, model: string) => void;
}) {
  const [pricePickerOpen, setPricePickerOpen] = useState(false);
  const [recentOpenRouterModels, setRecentOpenRouterModels] = useState(
    readRecentOpenRouterModels,
  );
  const [pinnedOpenRouterModels, setPinnedOpenRouterModels] = useState<string[]>([]);
  const source = sources.find((candidate) => candidate.executorId === executorId);
  const openRouterSource = sources.find(
    (candidate) => candidate.executorId === "reasoning-native",
  );
  const knownModel = (source?.models.includes(model) ?? false)
    || (executorId === "reasoning-native" && (
      recentOpenRouterModels.includes(model) || pinnedOpenRouterModels.includes(model)
    ));
  const selectedValue = knownModel ? optionValue(executorId, model) : "custom";
  const profile = modelProfile(executorId, model);
  const canBrowseOpenRouter = Boolean(openRouterSource);

  useEffect(() => {
    let cancelled = false;
    const syncRecentModels = () => setRecentOpenRouterModels(readRecentOpenRouterModels());
    window.addEventListener("storage", syncRecentModels);
    window.addEventListener(RECENT_OPENROUTER_MODELS_EVENT, syncRecentModels);
    void fetchRecentModels()
      .then(({ openrouter }) => {
        if (cancelled) return;
        const local = readRecentOpenRouterModels();
        const merged = parseRecentOpenRouterModels(JSON.stringify([...openrouter, ...local]));
        writeRecentOpenRouterModels(merged);
        setRecentOpenRouterModels(merged);
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
      window.removeEventListener("storage", syncRecentModels);
      window.removeEventListener(RECENT_OPENROUTER_MODELS_EVENT, syncRecentModels);
    };
  }, []);
  useEffect(() => {
    let cancelled = false;
    void fetchPinnedModels()
      .then(({ openrouter }) => { if (!cancelled) setPinnedOpenRouterModels(openrouter); })
      .catch(() => undefined);
    return () => { cancelled = true; };
  }, [pricePickerOpen]);

  return (
    <div className={`agent-model-picker ${showMetadata ? "" : "compact"}`}>
      <select
        className="agent-model-select"
        aria-label="Agent and model"
        title="Choose the executor and model. Setup state, pricing source, and context behavior appear below."
        value={selectedValue}
        disabled={disabled}
        onChange={(event) => {
          if (event.target.value === OTHER_OPENROUTER_MODEL_VALUE) {
            setPricePickerOpen(true);
            return;
          }
          if (event.target.value === "custom") {
            onChange(executorId, "");
            return;
          }
          const [nextExecutor, nextModel] = JSON.parse(event.target.value) as [string, string];
          onChange(nextExecutor, nextModel);
        }}
      >
        {canBrowseOpenRouter && pinnedOpenRouterModels.length > 0 && (
          <optgroup label="Starred · OpenRouter via Reasoning Native">
            {pinnedOpenRouterModels.map((pinnedModel) => (
              <option
                key={optionValue("reasoning-native", pinnedModel)}
                value={optionValue("reasoning-native", pinnedModel)}
                disabled={!openRouterSource?.ready}
              >
                {pinnedModel} · starred
              </option>
            ))}
          </optgroup>
        )}
        {canBrowseOpenRouter && recentOpenRouterModels.length > 0 && (
          <optgroup label="Recent · OpenRouter via Reasoning Native">
            {recentOpenRouterModels.filter((value) => !pinnedOpenRouterModels.includes(value)).map((recentModel) => (
              <option
                key={optionValue("reasoning-native", recentModel)}
                value={optionValue("reasoning-native", recentModel)}
                disabled={!openRouterSource?.ready}
              >
                {recentModel} · recent
              </option>
            ))}
          </optgroup>
        )}
        {sources.map((candidate) => {
          const candidateModels = candidate.executorId === "reasoning-native"
            ? candidate.models.filter((candidateModel) => (
              !recentOpenRouterModels.includes(candidateModel) && !pinnedOpenRouterModels.includes(candidateModel)
            ))
            : candidate.models;
          if (candidateModels.length === 0) return null;
          return (
            <optgroup
              key={candidate.executorId}
              label={`${candidate.provider} · ${candidate.label}${candidate.ready ? "" : " · setup required"}`}
            >
              {candidateModels.map((candidateModel) => {
                const candidateProfile = modelProfile(candidate.executorId, candidateModel);
                return (
                  <option
                    key={optionValue(candidate.executorId, candidateModel)}
                    value={optionValue(candidate.executorId, candidateModel)}
                    disabled={!candidate.ready}
                  >
                    {candidateModel} · {candidateProfile.cost}
                  </option>
                );
              })}
            </optgroup>
          );
        })}
        {canBrowseOpenRouter && (
          <optgroup label="OpenRouter · Reasoning Native">
            <option value={OTHER_OPENROUTER_MODEL_VALUE}>Other model…</option>
          </optgroup>
        )}
        <optgroup label="Advanced">
          <option value="custom">Custom model ID…</option>
        </optgroup>
      </select>
      {!knownModel && (
        <input
          className="custom-chat-model"
          value={model}
          disabled={disabled}
          onChange={(event) => onChange(executorId, event.target.value)}
          placeholder="provider/model"
          aria-label="Custom model ID"
        />
      )}
      {showMetadata && (
        <div className={`agent-picker-meta ${source?.ready ? "ready" : "needs-setup"}`}>
          <span>{source?.ready ? "ready" : "setup required"}</span>
          <span>{source?.readiness ?? "Choose an executor"}</span>
          {model && <span>{profile.cost} · {profile.context}</span>}
          {source?.promptFidelity && <span>{source.promptFidelity}</span>}
        </div>
      )}
      {pricePickerOpen && (
        <PriceModelPickerDialog
          targetLabel={priceTargetLabel}
          rankingContext={rankingContext}
          onClose={() => setPricePickerOpen(false)}
          onSelect={(modelId) => {
            const recent = addRecentOpenRouterModel(readRecentOpenRouterModels(), modelId);
            writeRecentOpenRouterModels(recent);
            setRecentOpenRouterModels(recent);
            void rememberRecentModel("openrouter", modelId)
              .then(({ openrouter }) => {
                writeRecentOpenRouterModels(openrouter);
                setRecentOpenRouterModels(openrouter);
              })
              .catch(() => undefined);
            onChange("reasoning-native", modelId);
          }}
        />
      )}
    </div>
  );
}
