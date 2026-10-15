import { useEffect, useState } from "react";
import {
  ApiKeyStatus,
  ConnectionStatus,
  DevelopmentWorkspaceEnvironmentDraft,
  DevelopmentWorkspaceInfo,
  DevelopmentWorkspaceSuggestion,
  McpConnectionInfo,
  addDevelopmentWorkspace,
  clearDevelopmentWorkspaceEnvironment,
  deleteApiKey,
  deleteDevelopmentWorkspace,
  deleteMcpConnection,
  fetchAgentConnections,
  fetchApiKeyStatuses,
  fetchCodexSetupPrompt,
  fetchDevelopmentWorkspaces,
  fetchMcpConnections,
  saveApiKey,
  saveMcpConnection,
  runDevelopmentWorkspaceEnvironment,
  setDevelopmentWorkspaceEnvironment,
  setDevelopmentWorkspacePython,
  testCodexConfiguration,
  validateOpenRouterConnection,
} from "./api";
import {
  ConfirmationDialog,
  type ConfirmationRequest,
} from "./ConfirmationDialog";
import { useModalDialog } from "./useModalDialog";
import { IconButton } from "./IconButton";
import { DesktopCloudSettings } from "./DesktopCloudSettings";
import { desktopAuth } from "./desktopCloudApi";
import {
  APPEARANCE_PRESETS,
  applyAppearance,
  readAppearance,
  type AppearancePreset,
} from "./appearance";

export type SettingsSection = "appearance" | "agents" | "workspaces" | "tools" | "cloud";

const SECTION_COPY: Record<SettingsSection, string> = {
  appearance: "Choose how Reasoning feels without changing how your research works.",
  agents: "Connect a provider now; add local agent harnesses when you need them.",
  workspaces: "Repositories available to explicitly selected development chats.",
  tools: "External MCP servers granted to individual workflow agents.",
  cloud: "Sign in and explicitly connect this Desktop to the private Cloud.",
};

export function SettingsDialog({
  onClose,
  onWorkspacesChanged,
  initialSection = "agents",
}: {
  onClose: () => void;
  onWorkspacesChanged?: () => void | Promise<unknown>;
  initialSection?: SettingsSection;
}) {
  const dialogRef = useModalDialog(onClose);
  const [section, setSection] = useState<SettingsSection>(initialSection);
  const [providers, setProviders] = useState<ApiKeyStatus[]>([]);
  const [connections, setConnections] = useState<ConnectionStatus[]>([]);
  const [mcpConnections, setMcpConnections] = useState<McpConnectionInfo[]>([]);
  const [developmentWorkspaces, setDevelopmentWorkspaces] = useState<DevelopmentWorkspaceInfo[]>([]);
  const [workspaceSuggestions, setWorkspaceSuggestions] = useState<DevelopmentWorkspaceSuggestion[]>([]);
  const [workspaceName, setWorkspaceName] = useState("");
  const [workspacePath, setWorkspacePath] = useState("");
  const [editingWorkspacePython, setEditingWorkspacePython] = useState<string | null>(null);
  const [workspacePythonPath, setWorkspacePythonPath] = useState("");
  const [editingWorkspaceEnvironment, setEditingWorkspaceEnvironment] = useState<string | null>(null);
  const [workspaceEnvironmentDraft, setWorkspaceEnvironmentDraft] = useState<DevelopmentWorkspaceEnvironmentDraft>({
    label: "Workspace environment",
    check_command: "",
    setup_command: null,
    check_timeout_seconds: 120,
    setup_timeout_seconds: 1800,
  });
  const [workspaceEnvironmentAction, setWorkspaceEnvironmentAction] = useState<string | null>(null);
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const [mcpId, setMcpId] = useState("");
  const [mcpName, setMcpName] = useState("");
  const [mcpTransport, setMcpTransport] = useState<McpConnectionInfo["transport"]>("stdio");
  const [mcpEnabled, setMcpEnabled] = useState(true);
  const [mcpCommand, setMcpCommand] = useState("");
  const [mcpArgs, setMcpArgs] = useState("");
  const [mcpUrl, setMcpUrl] = useState("");
  const [mcpEnvironment, setMcpEnvironment] = useState("");
  const [mcpHeaders, setMcpHeaders] = useState("");
  const [editingMcp, setEditingMcp] = useState<string | null>(null);
  const [deleteArmed, setDeleteArmed] = useState<string | null>(null);
  const [message, setMessage] = useState("loading…");
  const [codexModel, setCodexModel] = useState("openrouter/auto");
  const [codexTesting, setCodexTesting] = useState(false);
  const [codexSetupPrompt, setCodexSetupPrompt] = useState("");
  const [confirmation, setConfirmation] = useState<ConfirmationRequest | null>(null);
  const [appearance, setAppearance] = useState<AppearancePreset>(readAppearance);
  const [cloudAvailable, setCloudAvailable] = useState(false);

  const chooseAppearance = (preset: AppearancePreset) => {
    setAppearance(preset);
    applyAppearance(preset);
  };

  const refresh = () => {
    setMessage("checking local setup…");
    void Promise.all([fetchApiKeyStatuses(), fetchAgentConnections(), fetchMcpConnections(), fetchDevelopmentWorkspaces()])
      .then(([{ providers: values }, { connections: statuses }, { connections: mcpValues }, workspacePayload]) => {
        setProviders(values);
        setConnections(statuses);
        setMcpConnections(mcpValues);
        setDevelopmentWorkspaces(workspacePayload.workspaces);
        setWorkspaceSuggestions(workspacePayload.suggestions);
        setMessage("");
      })
      .catch((error) => setMessage(String(error)));
  };

  useEffect(() => {
    refresh();
    void desktopAuth().then(() => setCloudAvailable(true)).catch(() => setCloudAvailable(false));
  }, []);

  const save = (provider: string) => {
    const apiKey = drafts[provider]?.trim();
    if (!apiKey) return;
    setMessage("saving locally…");
    void saveApiKey(provider, apiKey)
      .then(({ providers: values }) => {
        setProviders(values);
        setDrafts((current) => ({ ...current, [provider]: "" }));
        if (provider !== "openrouter") {
          setMessage("API key saved. New agent runs will use it immediately.");
          void fetchAgentConnections().then(({ connections: statuses }) => setConnections(statuses));
          return undefined;
        }
        setMessage("OpenRouter key saved locally · verifying connection…");
        return validateOpenRouterConnection()
          .then((validation) => {
            setMessage(`${validation.validation_detail}. OpenRouter chats are ready.`);
          })
          .catch((error) => {
            setMessage(`The key was saved, but verification failed: ${String(error)}`);
          })
          .finally(() => {
            void fetchAgentConnections().then(({ connections: statuses }) => setConnections(statuses));
            window.dispatchEvent(new Event("agent-connections-changed"));
          });
      })
      .catch((error) => setMessage(String(error)));
  };

  const remove = (provider: string) => {
    setMessage("removing local key…");
    void deleteApiKey(provider)
      .then(({ providers: values }) => {
        setProviders(values);
        setMessage("Local API key removed.");
        void fetchAgentConnections().then(({ connections: statuses }) => setConnections(statuses));
        window.dispatchEvent(new Event("agent-connections-changed"));
      })
      .catch((error) => setMessage(String(error)));
  };

  const buildCodexSetupPrompt = () => {
    const selectedModel = codexModel.trim();
    if (!selectedModel) return;
    setMessage("preparing native Codex setup instructions…");
    void fetchCodexSetupPrompt(selectedModel)
      .then(({ prompt }) => {
        setCodexSetupPrompt(prompt);
        setMessage("Paste this prompt into a Reasoning development chat running Codex.");
      })
      .catch((error) => setMessage(String(error)));
  };

  const testCodex = () => {
    const selectedModel = codexModel.trim();
    if (!selectedModel || codexTesting) return;
    setCodexTesting(true);
    setMessage(`Testing ${selectedModel} through native Codex configuration…`);
    void testCodexConfiguration(selectedModel)
      .then((result) => {
        setMessage(`${result.model}: ${result.detail}.`);
      })
      .catch((error) => setMessage(String(error)))
      .finally(() => {
        setCodexTesting(false);
        void fetchAgentConnections().then(({ connections: statuses }) => setConnections(statuses));
        window.dispatchEvent(new Event("agent-connections-changed"));
      });
  };

  const clearMcpDraft = () => {
    setEditingMcp(null);
    setMcpId("");
    setMcpName("");
    setMcpTransport("stdio");
    setMcpEnabled(true);
    setMcpCommand("");
    setMcpArgs("");
    setMcpUrl("");
    setMcpEnvironment("");
    setMcpHeaders("");
  };

  const secretPairs = (text: string, label: string): Record<string, string> => {
    const values: Record<string, string> = {};
    for (const line of text.split("\n").map((value) => value.trim()).filter(Boolean)) {
      const separator = line.indexOf("=");
      if (separator < 1 || !line.slice(separator + 1)) {
        throw new Error(`${label} must use NAME=value, one per line.`);
      }
      values[line.slice(0, separator).trim()] = line.slice(separator + 1);
    }
    return values;
  };

  const saveMcp = () => {
    const connectionId = mcpId.trim();
    if (!connectionId || !mcpName.trim()) return;
    try {
      const environment = mcpEnvironment.trim()
        ? secretPairs(mcpEnvironment, "Environment secrets")
        : undefined;
      const headers = mcpHeaders.trim()
        ? secretPairs(mcpHeaders, "Header secrets")
        : undefined;
      setMessage("saving MCP connection locally…");
      void saveMcpConnection(connectionId, {
        name: mcpName.trim(),
        transport: mcpTransport,
        enabled: mcpEnabled,
        command: mcpTransport === "stdio" ? mcpCommand.trim() : undefined,
        args: mcpTransport === "stdio" ? mcpArgs.split("\n").map((value) => value.trim()).filter(Boolean) : [],
        url: mcpTransport === "stdio" ? undefined : mcpUrl.trim(),
        ...(environment ? { environment } : {}),
        ...(headers ? { headers } : {}),
      })
        .then(({ connections: values }) => {
          setMcpConnections(values);
          clearMcpDraft();
          setMessage("MCP connection saved. Grant it explicitly on a workflow agent node.");
        })
        .catch((error) => setMessage(String(error)));
    } catch (error) {
      setMessage(String(error));
    }
  };

  const editMcp = (connection: McpConnectionInfo) => {
    setEditingMcp(connection.connection_id);
    setMcpId(connection.connection_id);
    setMcpName(connection.name);
    setMcpTransport(connection.transport);
    setMcpEnabled(connection.enabled);
    setMcpCommand(connection.command ?? "");
    setMcpArgs(connection.args.join("\n"));
    setMcpUrl(connection.url ?? "");
    setMcpEnvironment("");
    setMcpHeaders("");
    setDeleteArmed(null);
  };

  const toggleMcp = (connection: McpConnectionInfo) => {
    setMessage(`${connection.enabled ? "disabling" : "enabling"} MCP connection…`);
    void saveMcpConnection(connection.connection_id, {
      name: connection.name,
      transport: connection.transport,
      enabled: !connection.enabled,
      command: connection.command ?? undefined,
      args: connection.args,
      url: connection.url ?? undefined,
    })
      .then(({ connections: values }) => {
        setMcpConnections(values);
        setMessage(`MCP connection ${connection.enabled ? "disabled" : "enabled"}.`);
      })
      .catch((error) => setMessage(String(error)));
  };

  const removeMcp = (connectionId: string) => {
    if (deleteArmed !== connectionId) {
      setDeleteArmed(connectionId);
      return;
    }
    setMessage("deleting MCP connection…");
    void deleteMcpConnection(connectionId)
      .then(({ connections: values }) => {
        setMcpConnections(values);
        if (editingMcp === connectionId) clearMcpDraft();
        setDeleteArmed(null);
        setMessage("MCP connection deleted. Existing workflow revisions remain auditable but cannot run until edited.");
      })
      .catch((error) => setMessage(String(error)));
  };

  const addWorkspace = (name = workspaceName, path = workspacePath) => {
    if (!name.trim() || !path.trim()) return;
    setMessage("registering development workspace…");
    void addDevelopmentWorkspace(name.trim(), path.trim())
      .then(({ workspaces }) => {
        setDevelopmentWorkspaces(workspaces);
        setWorkspaceSuggestions((current) => current.filter((item) => item.path !== path.trim()));
        setWorkspaceName("");
        setWorkspacePath("");
        setMessage("Development workspace registered. Select it when creating a chat.");
        void onWorkspacesChanged?.();
      })
      .catch((error) => setMessage(String(error)));
  };

  const removeWorkspace = (workspaceId: string) => {
    const armedId = `workspace:${workspaceId}`;
    if (deleteArmed !== armedId) {
      setDeleteArmed(armedId);
      return;
    }
    setMessage("removing development workspace grant…");
    void deleteDevelopmentWorkspace(workspaceId)
      .then(({ workspaces }) => {
        setDevelopmentWorkspaces(workspaces);
        setDeleteArmed(null);
        setMessage("Development workspace grant removed. The repository was not changed.");
        void onWorkspacesChanged?.();
        void fetchDevelopmentWorkspaces().then((payload) => setWorkspaceSuggestions(payload.suggestions));
      })
      .catch((error) => setMessage(String(error)));
  };

  const configureWorkspacePython = (workspace: DevelopmentWorkspaceInfo) => {
    setEditingWorkspacePython(workspace.workspace_id);
    setWorkspacePythonPath(
      workspace.python_environment.mode === "configured"
        ? workspace.python_environment.path ?? ""
        : workspace.python_environment.mode === "bundled"
          ? "bundled"
          : "",
    );
    setDeleteArmed(null);
  };

  const saveWorkspacePython = (workspaceId: string, value: string | null) => {
    setMessage("saving workspace Python environment…");
    void setDevelopmentWorkspacePython(workspaceId, value)
      .then(({ workspaces }) => {
        setDevelopmentWorkspaces(workspaces);
        setEditingWorkspacePython(null);
        setWorkspacePythonPath("");
        setMessage("Workspace Python environment saved.");
        void onWorkspacesChanged?.();
      })
      .catch((error) => setMessage(String(error)));
  };

  const configureWorkspaceEnvironment = (workspace: DevelopmentWorkspaceInfo) => {
    const environment = workspace.workspace_environment;
    setEditingWorkspaceEnvironment(workspace.workspace_id);
    setWorkspaceEnvironmentDraft({
      label: environment.managed ? environment.label : "Workspace environment",
      check_command: environment.check_command,
      setup_command: environment.setup_command || null,
      check_timeout_seconds: environment.check_timeout_seconds,
      setup_timeout_seconds: environment.setup_timeout_seconds,
    });
    setDeleteArmed(null);
  };

  const saveWorkspaceEnvironment = (workspaceId: string) => {
    setMessage("saving private workspace setup commands…");
    void setDevelopmentWorkspaceEnvironment(workspaceId, workspaceEnvironmentDraft)
      .then(({ workspaces }) => {
        setDevelopmentWorkspaces(workspaces);
        setEditingWorkspaceEnvironment(null);
        setMessage("Workspace setup commands saved in Reasoning. Run the readiness check when you are ready.");
        void onWorkspacesChanged?.();
      })
      .catch((error) => setMessage(String(error)));
  };

  const clearWorkspaceEnvironment = (workspaceId: string) => {
    setMessage("removing private workspace setup commands…");
    void clearDevelopmentWorkspaceEnvironment(workspaceId)
      .then(({ workspaces }) => {
        setDevelopmentWorkspaces(workspaces);
        setEditingWorkspaceEnvironment(null);
        setMessage("Workspace setup commands removed from Reasoning. The repository was not changed.");
        void onWorkspacesChanged?.();
      })
      .catch((error) => setMessage(String(error)));
  };

  const performWorkspaceEnvironmentAction = (workspace: DevelopmentWorkspaceInfo, action: "check" | "setup") => {
    setWorkspaceEnvironmentAction(`${workspace.workspace_id}:${action}`);
    setMessage(action === "setup" ? `setting up ${workspace.name}…` : `checking ${workspace.name}…`);
    void runDevelopmentWorkspaceEnvironment(workspace.workspace_id, action)
      .then(({ workspaces }) => {
        setDevelopmentWorkspaces(workspaces);
        setMessage(action === "setup" ? `${workspace.name} setup finished.` : `${workspace.name} check finished.`);
        void onWorkspacesChanged?.();
      })
      .catch((error) => setMessage(String(error)))
      .finally(() => setWorkspaceEnvironmentAction(null));
  };

  const runWorkspaceEnvironment = (workspace: DevelopmentWorkspaceInfo, action: "check" | "setup") => {
    if (action === "setup") {
      setConfirmation({
        title: `Run setup for ${workspace.name}?`,
        message: `This executes the command saved in your Reasoning settings inside ${workspace.path}.`,
        confirmLabel: "run setup",
        tone: "confirm",
        onConfirm: () => performWorkspaceEnvironmentAction(workspace, action),
      });
      return;
    }
    performWorkspaceEnvironmentAction(workspace, action);
  };

  return (
    <>
      <div className="name-overlay settings-overlay" onClick={onClose}>
        <div ref={dialogRef} className="settings-dialog" role="dialog" aria-modal="true" aria-labelledby="settings-title" onClick={(event) => event.stopPropagation()}>
        <header>
          <div>
            <strong id="settings-title">Settings</strong>
            <span>{SECTION_COPY[section]}</span>
          </div>
          <IconButton icon="close" onClick={onClose} aria-label="Close settings" />
        </header>
        <nav className="settings-tabs" role="tablist" aria-label="Settings sections">
          {(["appearance", "agents", "workspaces", "tools", ...(cloudAvailable ? ["cloud" as const] : [])] as SettingsSection[]).map((value) => (
            <button role="tab" aria-selected={section === value} className={section === value ? "selected" : ""} onClick={() => setSection(value)} key={value}>
              {value === "appearance" ? "Appearance" : value === "agents" ? "Connections" : value === "workspaces" ? "Repositories" : value === "cloud" ? "Cloud" : "MCP tools"}
            </button>
          ))}
        </nav>
        <div className="settings-content">
          {section === "cloud" && <DesktopCloudSettings />}
          {section === "appearance" && (
            <section className="appearance-settings" role="tabpanel">
              <div className="settings-section-heading">
                <div>
                  <strong>Visual style</strong>
                  <span>Applied immediately and saved on this computer.</span>
                </div>
                <span className="appearance-mode-pill">Dark</span>
              </div>
              <div className="appearance-grid" role="radiogroup" aria-label="Visual style">
                {APPEARANCE_PRESETS.map((preset) => (
                  <button
                    type="button"
                    role="radio"
                    aria-checked={appearance === preset.id}
                    className={`appearance-card ${appearance === preset.id ? "selected" : ""}`}
                    onClick={() => chooseAppearance(preset.id)}
                    key={preset.id}
                  >
                    <span className="appearance-preview" aria-hidden="true" style={{ background: preset.swatches[0] }}>
                      <i style={{ background: preset.swatches[1] }} />
                      <b style={{ background: preset.swatches[2] }} />
                    </span>
                    <span className="appearance-card-copy">
                      <strong>{preset.name}</strong>
                      <small>{preset.description}</small>
                    </span>
                    <span className="appearance-check" aria-hidden="true">{appearance === preset.id ? "✓" : ""}</span>
                  </button>
                ))}
              </div>
              <div className="appearance-light-note">
                <strong>Color theme</strong>
                <p>Reasoning follows your system's light or dark theme. In an organization, choose an explicit override from the account menu.</p>
              </div>
            </section>
          )}
          {section === "agents" && (
            <section role="tabpanel">
              <p className="settings-note">
                OpenRouter is the shortest path to an ordinary research chat: paste one key and Reasoning
                verifies it without terminal setup. Codex is optional and remains isolated in Reasoning's
                private Codex home. Keys stay on this computer in an owner-readable file and are never
                returned to the browser after saving.
              </p>
              <div className="connection-heading">
                <strong>Connection readiness</strong>
                <button onClick={refresh}>check setup</button>
              </div>
              <div className="connection-grid">
                {connections.map((connection) => (
                  <div className={`connection-card ${connection.ready ? "ready" : "needs-setup"}`} key={connection.executor_id}>
                    <div className="connection-card-head"><strong>{connection.display_name}</strong><span>{connection.ready ? "ready" : "setup required"}</span></div>
                    <div>{connection.provider} · {connection.kind === "cli" ? "local CLI" : "API"}</div>
                    <p>{connection.readiness}</p>
                    {connection.validation_status === "invalid" && <small className="connection-validation-error">{connection.validation_detail}</small>}
                    {connection.validation_status === "valid" && connection.validated_at && <small className="connection-validation-valid">verified {new Date(connection.validated_at).toLocaleString()}</small>}
                    {!connection.ready && <small>{connection.setup}</small>}
                    {connection.executor_id === "codex" && (
                      <div className="codex-native-setup">
                        <label htmlFor="codex-native-model">Native Codex model</label>
                        <div>
                          <input
                            id="codex-native-model"
                            value={codexModel}
                            onChange={(event) => {
                              setCodexModel(event.target.value);
                              setCodexSetupPrompt("");
                            }}
                            onKeyDown={(event) => { if (event.key === "Enter") testCodex(); }}
                            placeholder="provider/model"
                            spellCheck={false}
                          />
                          <button
                            disabled={!codexModel.trim()}
                            onClick={buildCodexSetupPrompt}
                          >
                            setup prompt
                          </button>
                          <button
                            className="confirm"
                            disabled={!codexModel.trim() || codexTesting || !connection.installed}
                            onClick={testCodex}
                          >
                            {codexTesting ? "testing…" : "test"}
                          </button>
                        </div>
                        {codexSetupPrompt && (
                          <div className="codex-setup-prompt">
                            <textarea readOnly value={codexSetupPrompt} aria-label="Codex setup prompt" />
                            <button onClick={() => void navigator.clipboard.writeText(codexSetupPrompt).then(() => setMessage("Codex setup prompt copied."))}>
                              copy prompt
                            </button>
                          </div>
                        )}
                      </div>
                    )}
                  </div>
                ))}
              </div>
              <div className="connection-heading api-key-heading"><strong>Provider credentials</strong></div>
              <div className="api-key-list">
                {providers.map((provider) => (
                  <div className="api-key-row" key={provider.provider}>
                    <div className="api-key-label"><strong>{provider.label}</strong><span>{provider.configured ? `${provider.hint} · ${provider.source === "user" ? "saved in Reasoning" : "from environment"}` : "not configured"}</span></div>
                    <input type="password" autoComplete="off" aria-label={`${provider.label} API key`} value={drafts[provider.provider] ?? ""} onCopy={(event) => event.preventDefault()} onCut={(event) => event.preventDefault()} onDragStart={(event) => event.preventDefault()} onChange={(event) => setDrafts((current) => ({ ...current, [provider.provider]: event.target.value }))} onKeyDown={(event) => { if (event.key === "Enter") save(provider.provider); }} placeholder={provider.configured ? "replace key…" : "paste API key…"} />
                    {provider.source === "user" && <button className="danger" onClick={() => remove(provider.provider)}>remove</button>}
                    <button className="confirm" disabled={!drafts[provider.provider]?.trim()} onClick={() => save(provider.provider)}>{provider.provider === "openrouter" ? "save & verify" : "save"}</button>
                  </div>
                ))}
              </div>
            </section>
          )}
          {section === "workspaces" && (
            <section role="tabpanel">
              <div className="connection-heading development-heading"><strong>Development workspaces</strong><span>Repository access · selected per chat</span></div>
              <p className="settings-note">A registered repository remains inaccessible to agents until you explicitly select it for a new development chat. Reasoning grants that chat write access to this repository; the repository itself is never copied, moved, or deleted here.</p>
              <div className="development-workspace-list">
                {developmentWorkspaces.length === 0 && <div className="settings-empty">No repositories registered yet.</div>}
                {developmentWorkspaces.map((workspace) => (
                  <div
                    className={`development-workspace-row ${workspace.available && (!workspace.workspace_environment.managed || workspace.workspace_environment.ready) ? "" : "unavailable"}`}
                    key={workspace.workspace_id}
                  >
                    <div className="development-workspace-summary">
                      <strong>{workspace.name}</strong><code>{workspace.path}</code>
                      <small className={workspace.python_environment.available ? "ready" : "needs-setup"}>
                        Python · {workspace.python_environment.label} · {workspace.python_environment.available ? "ready" : "setup required"}
                      </small>
                      {workspace.workspace_environment.managed && (
                        <small className={workspace.workspace_environment.ready ? "ready" : "needs-setup"}>
                          {workspace.workspace_environment.label} · {workspace.workspace_environment.ready ? "ready" : "setup required"}
                        </small>
                      )}
                    </div>
                    <span>{workspace.available && (!workspace.workspace_environment.managed || workspace.workspace_environment.ready) ? "ready" : "attention"}</span>
                    <div className="development-workspace-actions">
                      {workspace.workspace_environment.check_available && (
                        <button
                          disabled={workspaceEnvironmentAction !== null}
                          onClick={() => runWorkspaceEnvironment(workspace, "check")}
                        >
                          {workspaceEnvironmentAction === `${workspace.workspace_id}:check` ? "checking…" : "check"}
                        </button>
                      )}
                      {workspace.workspace_environment.setup_available && !workspace.workspace_environment.ready && (
                        <button
                          className="confirm"
                          disabled={workspaceEnvironmentAction !== null}
                          onClick={() => runWorkspaceEnvironment(workspace, "setup")}
                        >
                          {workspaceEnvironmentAction === `${workspace.workspace_id}:setup` ? "setting up…" : "set up"}
                        </button>
                      )}
                      <button onClick={() => configureWorkspaceEnvironment(workspace)}>Setup…</button>
                      <button onClick={() => configureWorkspacePython(workspace)}>Python…</button>
                      <button className="danger" onClick={() => removeWorkspace(workspace.workspace_id)}>{deleteArmed === `workspace:${workspace.workspace_id}` ? "confirm" : "remove"}</button>
                    </div>
                    {workspace.workspace_environment.managed && (
                      <details className="workspace-environment-detail">
                        <summary>{workspace.workspace_environment.detail}</summary>
                        {workspace.workspace_environment.output && <pre>{workspace.workspace_environment.output}</pre>}
                      </details>
                    )}
                    {editingWorkspacePython === workspace.workspace_id && (
                      <div className="workspace-python-editor">
                        <p>{workspace.python_environment.detail}</p>
                        <label>Python interpreter<input autoFocus value={workspacePythonPath} onChange={(event) => setWorkspacePythonPath(event.target.value)} placeholder="/path/to/.venv/bin/python" /></label>
                        <div>
                          <button onClick={() => saveWorkspacePython(workspace.workspace_id, null)}>auto-detect</button>
                          <button onClick={() => saveWorkspacePython(workspace.workspace_id, "bundled")}>use Reasoning Python</button>
                          <button className="confirm" disabled={!workspacePythonPath.trim() || workspacePythonPath.trim() === "bundled"} onClick={() => saveWorkspacePython(workspace.workspace_id, workspacePythonPath.trim())}>use path</button>
                          <button onClick={() => setEditingWorkspacePython(null)}>cancel</button>
                        </div>
                      </div>
                    )}
                    {editingWorkspaceEnvironment === workspace.workspace_id && (
                      <div className="workspace-environment-editor">
                        <p>These commands are private Reasoning settings. They run from the repository root without a shell.</p>
                        <label>Label<input value={workspaceEnvironmentDraft.label} onChange={(event) => setWorkspaceEnvironmentDraft((current) => ({ ...current, label: event.target.value }))} placeholder="Workspace environment" /></label>
                        <label>Readiness check<input value={workspaceEnvironmentDraft.check_command} onChange={(event) => setWorkspaceEnvironmentDraft((current) => ({ ...current, check_command: event.target.value }))} placeholder="make check" /></label>
                        <label>Setup command<input value={workspaceEnvironmentDraft.setup_command ?? ""} onChange={(event) => setWorkspaceEnvironmentDraft((current) => ({ ...current, setup_command: event.target.value || null }))} placeholder="make bootstrap" /></label>
                        <div>
                          {workspace.workspace_environment.managed && <button className="danger" onClick={() => clearWorkspaceEnvironment(workspace.workspace_id)}>remove setup</button>}
                          <button className="confirm" disabled={!workspaceEnvironmentDraft.label.trim() || !workspaceEnvironmentDraft.check_command.trim()} onClick={() => saveWorkspaceEnvironment(workspace.workspace_id)}>save</button>
                          <button onClick={() => setEditingWorkspaceEnvironment(null)}>cancel</button>
                        </div>
                      </div>
                    )}
                  </div>
                ))}
              </div>
              {workspaceSuggestions.map((suggestion) => (
                <div className="workspace-suggestion" key={suggestion.path}><div><strong>{suggestion.name} detected</strong><code>{suggestion.path}</code></div><button onClick={() => addWorkspace(suggestion.name, suggestion.path)}>register</button></div>
              ))}
              <div className="workspace-editor">
                <label>name<input value={workspaceName} onChange={(event) => setWorkspaceName(event.target.value)} placeholder="My research software" /></label>
                <label>absolute repository path<input value={workspacePath} onChange={(event) => setWorkspacePath(event.target.value)} placeholder="/home/me/src/project" /></label>
                <button className="confirm" disabled={!workspaceName.trim() || !workspacePath.trim()} onClick={() => addWorkspace()}>register repository</button>
              </div>
            </section>
          )}
          {section === "tools" && (
            <section role="tabpanel">
              <div className="connection-heading mcp-heading"><strong>MCP connections</strong><span>Reusable tools · granted per workflow agent</span><button onClick={clearMcpDraft}>new connection</button></div>
              <p className="settings-note">Commands and remote servers can act with your account permissions. Reasoning stores environment values and authorization headers locally, never returns them to this window, and does not attach a connection to a run until you select it on that node.</p>
              <div className="mcp-connection-list">
                {mcpConnections.length === 0 && <div className="settings-empty">No external MCP connections yet.</div>}
                {mcpConnections.map((connection) => (
                  <div className={`mcp-connection-row ${connection.enabled ? "enabled" : "disabled"}`} key={connection.connection_id}>
                    <div><strong>{connection.name}</strong><code>{connection.connection_id}</code></div>
                    <span>{connection.transport} · {connection.command || connection.url}</span>
                    <small>{[...connection.environment_names, ...connection.header_names].length ? `private values: ${[...connection.environment_names, ...connection.header_names].join(", ")}` : "no private values"}</small>
                    <div className="mcp-row-actions"><button onClick={() => toggleMcp(connection)}>{connection.enabled ? "disable" : "enable"}</button><button onClick={() => editMcp(connection)}>edit</button><button className="danger" onClick={() => removeMcp(connection.connection_id)}>{deleteArmed === connection.connection_id ? "confirm" : "delete"}</button></div>
                  </div>
                ))}
              </div>
              <div className="mcp-editor">
                <div className="mcp-editor-heading"><strong>{editingMcp ? `Edit ${editingMcp}` : "Add an MCP connection"}</strong><span>{editingMcp ? "Blank secret fields preserve the saved private values." : "Use a short stable ID; workflow revisions store it."}</span></div>
                <div className="mcp-editor-grid">
                  <label>connection id<input disabled={Boolean(editingMcp)} value={mcpId} onChange={(event) => setMcpId(event.target.value)} placeholder="oeis-local" /></label>
                  <label>display name<input value={mcpName} onChange={(event) => setMcpName(event.target.value)} placeholder="OEIS research tools" /></label>
                  <label>transport<select value={mcpTransport} onChange={(event) => setMcpTransport(event.target.value as McpConnectionInfo["transport"])}><option value="stdio">local command (stdio)</option><option value="http">remote HTTP</option><option value="sse">remote SSE</option></select></label>
                  <label className="mcp-enabled"><input type="checkbox" checked={mcpEnabled} onChange={(event) => setMcpEnabled(event.target.checked)} />enabled for new grants</label>
                </div>
                {mcpTransport === "stdio" ? (
                  <div className="mcp-editor-grid"><label>command<input value={mcpCommand} onChange={(event) => setMcpCommand(event.target.value)} placeholder="uvx" /></label><label>arguments · one per line<textarea rows={3} value={mcpArgs} onChange={(event) => setMcpArgs(event.target.value)} placeholder={"mcp-server-package\n--read-only"} /></label><label className="mcp-secret-field">environment · NAME=value, one per line<textarea rows={3} value={mcpEnvironment} onChange={(event) => setMcpEnvironment(event.target.value)} onCopy={(event) => event.preventDefault()} onCut={(event) => event.preventDefault()} placeholder="API_TOKEN=…" /></label></div>
                ) : (
                  <div className="mcp-editor-grid"><label>server URL<input value={mcpUrl} onChange={(event) => setMcpUrl(event.target.value)} placeholder="https://example.org/mcp" /></label><label className="mcp-secret-field">headers · NAME=value, one per line<textarea rows={3} value={mcpHeaders} onChange={(event) => setMcpHeaders(event.target.value)} onCopy={(event) => event.preventDefault()} onCut={(event) => event.preventDefault()} placeholder="Authorization=Bearer …" /></label></div>
                )}
                <div className="mcp-editor-actions">{editingMcp && <button onClick={clearMcpDraft}>cancel</button>}<button className="confirm" disabled={!mcpId.trim() || !mcpName.trim() || (mcpTransport === "stdio" ? !mcpCommand.trim() : !mcpUrl.trim())} onClick={saveMcp}>save connection</button></div>
              </div>
            </section>
          )}
        </div>
        {message && <div className="studio-message">{message}</div>}
        </div>
      </div>
      {confirmation && (
        <ConfirmationDialog
          request={confirmation}
          onClose={() => setConfirmation(null)}
        />
      )}
    </>
  );
}
