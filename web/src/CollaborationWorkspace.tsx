import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  collaborationBootstrap,
  collaborationCancelInference,
  collaborationConfigureRunner,
  collaborationCreateTopic,
  collaborationInviteMember,
  collaborationMembers,
  collaborationMessages,
  collaborationOperations,
  collaborationPostMessage,
  collaborationQueueInference,
  collaborationRunnerStatus,
  collaborationTopics,
  type CollaborationBootstrap,
  type CollaborationMessage,
  type CollaborationMember,
  type CollaborationOperation,
  type CollaborationRunnerStatus,
  type CollaborationTopic,
} from "./desktopCloudApi";

function message(reason: unknown): string {
  return reason instanceof Error ? reason.message : String(reason);
}

export function CollaborationWorkspace() {
  const [bootstrap, setBootstrap] = useState<CollaborationBootstrap | null>(null);
  const [organizationId, setOrganizationId] = useState("");
  const [workspaceId, setWorkspaceId] = useState("");
  const [topics, setTopics] = useState<CollaborationTopic[]>([]);
  const [topicId, setTopicId] = useState("");
  const [messages, setMessages] = useState<CollaborationMessage[]>([]);
  const [operations, setOperations] = useState<CollaborationOperation[]>([]);
  const [members, setMembers] = useState<CollaborationMember[]>([]);
  const [membersOpen, setMembersOpen] = useState(false);
  const [inviteEmails, setInviteEmails] = useState("");
  const [inviteRole, setInviteRole] = useState("member");
  const [runner, setRunner] = useState<CollaborationRunnerStatus | null>(null);
  const [newTopic, setNewTopic] = useState("");
  const [draft, setDraft] = useState("");
  const [route, setRoute] = useState<"contributed_codex" | "metered_openrouter">("contributed_codex");
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const active = useRef(true);

  const organization = useMemo(
    () => bootstrap?.organizations.find((item) => item.organization_id === organizationId) ?? null,
    [bootstrap, organizationId],
  );
  const workspace = organization?.workspaces.find((item) => item.workspace_id === workspaceId) ?? null;
  const canContribute = organization?.permissions.includes("compute.contribute") ?? false;
  const canReadMembers = organization?.permissions.includes("members.read") ?? false;
  const canInviteMembers = organization?.permissions.includes("members.invite") ?? false;
  const runnerEnabled = runner?.enabled_organizations.includes(organizationId) ?? false;

  const loadMessages = useCallback(async () => {
    if (!organizationId || !workspaceId || !topicId) {
      setMessages([]);
      return;
    }
    const page = await collaborationMessages(organizationId, workspaceId, topicId);
    if (active.current) setMessages(page.messages);
  }, [organizationId, workspaceId, topicId]);

  const loadOperations = useCallback(async () => {
    if (!organizationId || !workspaceId || !topicId) {
      setOperations([]);
      return;
    }
    const page = await collaborationOperations(organizationId, workspaceId, topicId);
    if (active.current) setOperations(page.operations);
  }, [organizationId, workspaceId, topicId]);

  const loadTopics = useCallback(async () => {
    if (!organizationId || !workspaceId) return;
    const response = await collaborationTopics(organizationId, workspaceId);
    if (!active.current) return;
    setTopics(response.topics);
    setTopicId((current) => response.topics.some((item) => item.topic_id === current)
      ? current
      : response.topics[0]?.topic_id ?? "");
  }, [organizationId, workspaceId]);

  const loadMembers = useCallback(async () => {
    if (!organizationId || !canReadMembers) {
      setMembers([]);
      return;
    }
    const response = await collaborationMembers(organizationId);
    if (active.current) setMembers(response.members);
  }, [organizationId, canReadMembers]);

  useEffect(() => {
    active.current = true;
    void Promise.all([collaborationBootstrap(), collaborationRunnerStatus()])
      .then(([next, nextRunner]) => {
        if (!active.current) return;
        setBootstrap(next);
        setRunner(nextRunner);
        const savedOrganization = window.localStorage.getItem("reasoning.activeOrganization");
        const selectedOrganization = next.organizations.find((item) => item.organization_id === savedOrganization)
          ?? next.organizations.find((item) => item.organization_id === next.default_organization_id)
          ?? next.organizations[0];
        setOrganizationId(selectedOrganization.organization_id);
        const savedWorkspace = window.localStorage.getItem(`reasoning.activeWorkspace.${selectedOrganization.organization_id}`);
        setWorkspaceId(selectedOrganization.workspaces.find((item) => item.workspace_id === savedWorkspace)?.workspace_id
          ?? (selectedOrganization.organization_id === next.default_organization_id
            ? next.default_workspace_id
            : selectedOrganization.workspaces[0]?.workspace_id ?? ""));
        setError("");
      })
      .catch((reason) => setError(message(reason)))
      .finally(() => setLoading(false));
    return () => { active.current = false; };
  }, []);

  useEffect(() => {
    if (!organizationId || !workspaceId) return;
    window.localStorage.setItem("reasoning.activeOrganization", organizationId);
    window.localStorage.setItem(`reasoning.activeWorkspace.${organizationId}`, workspaceId);
    setTopics([]);
    setTopicId("");
    setMessages([]);
    setOperations([]);
    void loadTopics().catch((reason) => setError(message(reason)));
    void loadMembers().catch((reason) => setError(message(reason)));
  }, [organizationId, workspaceId, loadTopics, loadMembers]);

  useEffect(() => {
    void Promise.all([loadMessages(), loadOperations()]).catch((reason) => setError(message(reason)));
    if (!topicId) return;
    const interval = window.setInterval(() => {
      void Promise.all([loadMessages(), loadOperations(), collaborationRunnerStatus().then(setRunner)])
        .catch((reason) => setError(message(reason)));
    }, 3000);
    return () => window.clearInterval(interval);
  }, [topicId, loadMessages, loadOperations]);

  const chooseOrganization = (nextId: string) => {
    const next = bootstrap?.organizations.find((item) => item.organization_id === nextId);
    setOrganizationId(nextId);
    setWorkspaceId(next?.workspaces[0]?.workspace_id ?? "");
  };

  const createTopic = async () => {
    const title = newTopic.trim();
    if (!title) return;
    setBusy(true);
    setError("");
    try {
      const created = await collaborationCreateTopic(organizationId, workspaceId, title);
      setNewTopic("");
      await loadTopics();
      setTopicId(created.topic_id);
    } catch (reason) {
      setError(message(reason));
    } finally {
      setBusy(false);
    }
  };

  const send = async (pooled: boolean) => {
    const content = draft.trim();
    if (!content || !topicId) return;
    setBusy(true);
    setError("");
    try {
      await collaborationPostMessage(organizationId, workspaceId, topicId, content);
      if (pooled) await collaborationQueueInference(organizationId, workspaceId, topicId, content, route);
      setDraft("");
      await loadMessages();
      if (pooled) await loadOperations();
    } catch (reason) {
      setError(message(reason));
    } finally {
      setBusy(false);
    }
  };

  const cancelOperation = async (operationId: string) => {
    setBusy(true);
    setError("");
    try {
      await collaborationCancelInference(organizationId, workspaceId, topicId, operationId);
      await loadOperations();
    } catch (reason) {
      setError(message(reason));
    } finally {
      setBusy(false);
    }
  };

  const inviteMembers = async () => {
    const emails = [...new Set(inviteEmails.split(/[\s,;]+/).map((item) => item.trim()).filter(Boolean))];
    if (!emails.length) return;
    setBusy(true);
    setError("");
    try {
      for (const email of emails) await collaborationInviteMember(organizationId, email, inviteRole);
      setInviteEmails("");
      await loadMembers();
    } catch (reason) {
      setError(message(reason));
    } finally {
      setBusy(false);
    }
  };

  const toggleRunner = async () => {
    if (!organization) return;
    setBusy(true);
    setError("");
    try {
      setRunner(await collaborationConfigureRunner(
        organization.organization_id,
        organization.workspaces.filter((item) => item.status === "active").map((item) => item.workspace_id),
        !runnerEnabled,
      ));
    } catch (reason) {
      setError(message(reason));
    } finally {
      setBusy(false);
    }
  };

  if (loading) return <main className="collaboration-empty"><strong>Opening shared workspaces…</strong></main>;
  if (!bootstrap || !organization || !workspace) return (
    <main className="collaboration-empty">
      <strong>Sign in to open your workspaces</strong>
      <p>Use Settings → Private Cloud. Your personal organization and shared organizations will appear here.</p>
      {error && <p className="collaboration-error">{error}</p>}
    </main>
  );

  return (
    <main className="collaboration-workspace">
      <header className="collaboration-header">
        <div className="collaboration-selectors">
          <label>organization<select value={organizationId} onChange={(event) => chooseOrganization(event.target.value)}>
            {bootstrap.organizations.map((item) => <option key={item.organization_id} value={item.organization_id}>
              {item.name}{item.kind === "personal" ? " · personal" : ""}
            </option>)}
          </select></label>
          <label>workspace<select value={workspaceId} onChange={(event) => setWorkspaceId(event.target.value)}>
            {organization.workspaces.filter((item) => item.status === "active").map((item) => (
              <option key={item.workspace_id} value={item.workspace_id}>{item.name}</option>
            ))}
          </select></label>
        </div>
        <div className="collaboration-membership">
          {organization.role_ids.map((role) => <span key={role}>{role}</span>)}
          {canReadMembers && <button disabled={busy} onClick={() => setMembersOpen((value) => !value)}>
            {membersOpen ? "Close members" : `Members · ${members.length}`}
          </button>}
          {canContribute && <button disabled={busy} className={runnerEnabled ? "runner-enabled" : ""} onClick={toggleRunner}>
            {runnerEnabled ? "Local Codex contributing" : "Contribute local Codex"}
          </button>}
        </div>
      </header>
      {runner?.active_job && <section className="collaboration-local-activity">
        <header><strong>This Desktop is contributing</strong><span>{runner.active_job.route === "metered_openrouter" ? "metered API" : "local Codex"}</span></header>
        <p>{runner.active_job.prompt}</p>
        <small>{runner.active_job.progress} · {runner.active_job.model}</small>
      </section>}
      {runner?.last_error && runnerEnabled && <div className="collaboration-warning">Runner retrying: {runner.last_error}</div>}
      {error && <div className="collaboration-error" role="alert">{error}</div>}
      {membersOpen && <section className="collaboration-members-panel">
        <header><strong>{organization.name} members</strong><span>Roles limit compute, swarms, deployment, and billing independently.</span></header>
        <div className="collaboration-member-list">
          {members.map((member) => <article key={member.account_id}>
            <strong>{member.email ?? member.user_id}{member.self ? " · you" : ""}</strong>
            <span>{member.role_ids.join(" · ")}</span>
          </article>)}
        </div>
        {canInviteMembers && organization.kind === "shared" && <div className="collaboration-invite">
          <textarea value={inviteEmails} onChange={(event) => setInviteEmails(event.target.value)} placeholder="Email addresses, one per line" />
          <select value={inviteRole} onChange={(event) => setInviteRole(event.target.value)}>
            <option value="member">Member · chat, workflows, use compute</option>
            <option value="viewer">Viewer · read only</option>
            <option value="compute_contributor">Compute contributor · donate local capacity</option>
            <option value="admin">Admin · members and workspaces</option>
            <option value="swarm_operator">Swarm operator</option>
            <option value="deployer">Deployer</option>
            <option value="billing_admin">Billing administrator</option>
            <option value="role_admin">Role administrator</option>
          </select>
          <button disabled={busy || !inviteEmails.trim()} onClick={() => void inviteMembers()}>Invite</button>
        </div>}
      </section>}
      <div className="collaboration-columns">
        <aside className="collaboration-topics">
          <div className="collaboration-section-title"><strong>Topics</strong><span>{topics.length}</span></div>
          <div className="collaboration-new-topic">
            <input value={newTopic} placeholder="New topic" onChange={(event) => setNewTopic(event.target.value)} onKeyDown={(event) => { if (event.key === "Enter") void createTopic(); }} />
            <button disabled={busy || !newTopic.trim()} onClick={() => void createTopic()}>＋</button>
          </div>
          <div className="collaboration-topic-list">
            {topics.map((topic) => <button key={topic.topic_id} className={topic.topic_id === topicId ? "selected" : ""} onClick={() => setTopicId(topic.topic_id)}>
              <strong>{topic.title}</strong><small>{topic.state}</small>
            </button>)}
            {!topics.length && <p>Create the first topic in {workspace.name}.</p>}
          </div>
        </aside>
        <section className="collaboration-conversation">
          {topicId ? <>
            <div className="collaboration-messages">
              {messages.map((item) => <article key={item.message_id} className={`collaboration-message ${item.kind}`}>
                <header><strong>{item.actor.id}</strong><span>{item.kind}</span></header>
                <p>{item.content}</p>
              </article>)}
              {!messages.length && <div className="collaboration-conversation-empty">Start the shared conversation.</div>}
            </div>
            {!!operations.length && <section className="collaboration-operations">
              <header><strong>Shared prompt activity</strong><span>{operations.length}</span></header>
              {operations.map((operation) => <article key={operation.operation_id}>
                <div><strong>{operation.route === "metered_openrouter" ? "Metered API" : "Local Codex"}</strong><span className={`operation-${operation.state}`}>{operation.state}</span></div>
                <p>{operation.prompt}</p>
                <small>
                  {operation.progress}
                  {operation.runner ? ` · ${operation.runner.display_name} (${operation.runner.owner.id})` : " · waiting for a contributor"}
                  {operation.usage?.cost_usd != null ? ` · $${operation.usage.cost_usd.toFixed(6)}` : ""}
                  {operation.usage?.total_tokens != null ? ` · ${operation.usage.total_tokens} tokens` : ""}
                </small>
                {["queued", "offered", "accepted", "running"].includes(operation.state) && <button disabled={busy} onClick={() => void cancelOperation(operation.operation_id)}>Cancel</button>}
              </article>)}
            </section>}
            <div className="collaboration-composer">
              <textarea value={draft} placeholder="Message this workspace…" onChange={(event) => setDraft(event.target.value)} onKeyDown={(event) => {
                if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); void send(false); }
              }} />
              <div className="collaboration-route-picker">
                <label><input type="radio" checked={route === "contributed_codex"} onChange={() => setRoute("contributed_codex")} /> Local Codex · contributed, no API charge</label>
                <label title={runner?.api_route_ready ? "Uses the assigned Desktop's configured OpenRouter key" : "A contributor with OpenRouter configured must be online"}><input type="radio" checked={route === "metered_openrouter"} onChange={() => setRoute("metered_openrouter")} /> API · metered and reported</label>
              </div>
              <div><span>Enter to send · Shift Enter for a new line</span><button disabled={busy || !draft.trim()} onClick={() => void send(false)}>Send</button><button className="pooled" disabled={busy || !draft.trim()} onClick={() => void send(true)}>{route === "metered_openrouter" ? "Ask metered API" : "Ask pooled Codex"}</button></div>
            </div>
          </> : <div className="collaboration-conversation-empty">Choose or create a topic.</div>}
        </section>
      </div>
    </main>
  );
}
