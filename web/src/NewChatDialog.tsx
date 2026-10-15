import { useEffect, useState } from "react";
import { DevelopmentWorkspaceInfo, createChat, fetchDevelopmentWorkspaces } from "./api";
import { useModalDialog } from "./useModalDialog";

export function NewChatDialog({
  projectId,
  projectName,
  onClose,
  onCreated,
  onManageWorkspaces,
}: {
  projectId: string;
  projectName: string;
  onClose: () => void;
  onCreated: (chatId: string) => void;
  onManageWorkspaces: () => void;
}) {
  const dialogRef = useModalDialog(onClose);
  const [title, setTitle] = useState("");
  const [workspaceId, setWorkspaceId] = useState("");
  const [workspaces, setWorkspaces] = useState<DevelopmentWorkspaceInfo[]>([]);
  const [message, setMessage] = useState("loading workspaces…");
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    void fetchDevelopmentWorkspaces()
      .then(({ workspaces: values }) => {
        setWorkspaces(values);
        setMessage("");
      })
      .catch((error) => setMessage(String(error)));
  }, []);

  const submit = () => {
    if (saving) return;
    setSaving(true);
    setMessage(workspaceId ? "opening repository workspace…" : "creating chat…");
    void createChat(projectId, title.trim() || undefined, workspaceId || undefined)
      .then((chat) => {
        onClose();
        onCreated(chat.id);
      })
      .catch((error) => {
        setSaving(false);
        setMessage(String(error));
      });
  };

  const selected = workspaces.find((item) => item.workspace_id === workspaceId);
  return (
    <div className="name-overlay" onClick={onClose}>
      <div ref={dialogRef} className="name-dialog new-chat-dialog" role="dialog" aria-modal="true" aria-labelledby="new-chat-title" onClick={(event) => event.stopPropagation()}>
        <div>
          <div className="name-dialog-title" id="new-chat-title">New chat</div>
          <p>Start inside {projectName}.</p>
        </div>
        <label>
          <span className="new-chat-field-label"><span>title</span><small>optional</small></span>
          <input autoFocus value={title} placeholder="What are you working on?" onChange={(event) => setTitle(event.target.value)} onKeyDown={(event) => { if (event.key === "Enter") submit(); }} />
        </label>
        <label>
          workspace
          <select value={workspaceId} onChange={(event) => setWorkspaceId(event.target.value)}>
            <option value="">Research scratchpad</option>
            {workspaces.map((workspace) => (
              <option value={workspace.workspace_id} disabled={!workspace.available} key={workspace.workspace_id}>
                {workspace.name}{workspace.available ? "" : " · unavailable"}
              </option>
            ))}
          </select>
        </label>
        <div className={`workspace-choice ${workspaceId ? "development" : "research"}`}>
          <strong>{workspaceId ? "Development chat · Codex 5.6 Sol" : "Research chat"}</strong>
          <span>{selected ? selected.path : "A private scratchpad with project artifacts, bibliography, workflows, and ledger tools."}</span>
          {workspaceId && <small>Codex may read and edit this repository. Commits and pushes still require an explicit request.</small>}
        </div>
        <button className="manage-workspaces" onClick={() => { onClose(); onManageWorkspaces(); }}>manage development workspaces…</button>
        {message && <div className="new-chat-message">{message}</div>}
        <div className="name-dialog-actions">
          <button className="icon-btn" onClick={onClose}>cancel</button>
          <button className="confirm" disabled={saving || (workspaceId !== "" && !selected?.available)} onClick={submit}>{saving ? "creating…" : "create"}</button>
        </div>
      </div>
    </div>
  );
}
