import { FormEvent, ReactNode, useEffect, useMemo, useState } from "react";
import "./ux-lab.css";

export type UxDirectionId = "native" | "studio" | "canvas" | "command" | "editorial";
export type UxViewportId = "desktop" | "web" | "compact";

interface UxDirection {
  id: UxDirectionId;
  number: string;
  name: string;
  summary: string;
  emphasis: string;
}

export const UX_DIRECTIONS: readonly UxDirection[] = [
  {
    id: "native",
    number: "01",
    name: "Quiet native",
    summary: "Calm conversation, familiar controls, almost no visible machinery.",
    emphasis: "Ease and restraint",
  },
  {
    id: "studio",
    number: "02",
    name: "Research studio",
    summary: "Dense navigation and provenance for serious, long-running work.",
    emphasis: "Control and legibility",
  },
  {
    id: "canvas",
    number: "03",
    name: "Floating canvas",
    summary: "Layered work surfaces with more depth, tactility, and motion.",
    emphasis: "Presence and polish",
  },
  {
    id: "command",
    number: "04",
    name: "Command first",
    summary: "A nearly invisible shell organized around search and keyboard flow.",
    emphasis: "Speed and focus",
  },
  {
    id: "editorial",
    number: "05",
    name: "Editorial workspace",
    summary: "A warm, document-led workspace that makes research feel publishable.",
    emphasis: "Reading and synthesis",
  },
] as const;

export const UX_VIEWPORTS: readonly { id: UxViewportId; label: string; detail: string }[] = [
  { id: "desktop", label: "Desktop", detail: "1440 shell" },
  { id: "web", label: "Web", detail: "1180 shell" },
  { id: "compact", label: "Compact", detail: "760 shell" },
] as const;

const directionIds = new Set(UX_DIRECTIONS.map((item) => item.id));
const viewportIds = new Set(UX_VIEWPORTS.map((item) => item.id));

export function parseUxLabState(search: string): {
  direction: UxDirectionId;
  viewport: UxViewportId;
} {
  const params = new URLSearchParams(search);
  const direction = params.get("direction") as UxDirectionId | null;
  const viewport = params.get("viewport") as UxViewportId | null;
  return {
    direction: direction && directionIds.has(direction) ? direction : "native",
    viewport: viewport && viewportIds.has(viewport) ? viewport : "desktop",
  };
}

function Icon({ name }: { name: "chat" | "projects" | "search" | "money" | "settings" | "plus" | "send" | "panel" | "spark" | "paperclip" | "check" }) {
  const paths: Record<string, ReactNode> = {
    chat: <><path d="M3 3.5h14v10H9l-4 3v-3H3z" /></>,
    projects: <><path d="M2.5 5h6l1.5 2h7.5v8.5h-15z" /><path d="M2.5 5V3.5h5l1.5 1.5" /></>,
    search: <><circle cx="8.5" cy="8.5" r="5" /><path d="m12.3 12.3 4.2 4.2" /></>,
    money: <><circle cx="10" cy="10" r="7" /><path d="M12.5 7.5c-.6-.5-1.4-.8-2.4-.8-1.4 0-2.3.7-2.3 1.7 0 2.6 4.8 1.1 4.8 3.6 0 1-.9 1.8-2.5 1.8-1.1 0-2.1-.4-2.8-1M10 5.3v9.4" /></>,
    settings: <><circle cx="10" cy="10" r="2.5" /><path d="M10 2.5v2M10 15.5v2M17.5 10h-2M4.5 10h-2M15.3 4.7l-1.4 1.4M6.1 13.9l-1.4 1.4M15.3 15.3l-1.4-1.4M6.1 6.1 4.7 4.7" /></>,
    plus: <><path d="M10 4v12M4 10h12" /></>,
    send: <><path d="m3 10 14-7-4.8 14-2.6-5.4zM9.6 11.6 17 3" /></>,
    panel: <><rect x="3" y="3" width="14" height="14" rx="2" /><path d="M12 3v14" /></>,
    spark: <><path d="m10 2 1.2 4.1L15 8l-3.8 1.9L10 14l-1.2-4.1L5 8l3.8-1.9zM16 13l.6 1.9 1.9.6-1.9.6L16 18l-.6-1.9-1.9-.6 1.9-.6z" /></>,
    paperclip: <><path d="m7.3 10.8 5.9-5.9a2.5 2.5 0 0 1 3.5 3.5l-7.4 7.4a4 4 0 0 1-5.7-5.7l7-7" /></>,
    check: <><path d="m4 10 4 4 8-8" /></>,
  };
  return <svg className="ux-icon" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.45" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{paths[name]}</svg>;
}

const conversations = [
  { id: "spectral", title: "Spectral sequence intuition", meta: "2m", unread: false },
  { id: "moderation", title: "Moderation benchmark", meta: "18m", unread: true },
  { id: "protein", title: "Protein folding survey", meta: "Yesterday", unread: false },
  { id: "grant", title: "Grant evidence map", meta: "Mon", unread: false },
];

const models = [
  { name: "DeepSeek R1 0528", author: "DeepSeek", input: "$0.45", output: "$2.15", context: "164K", source: "Open weights" },
  { name: "Qwen3 235B A22B", author: "Qwen", input: "$0.22", output: "$0.88", context: "131K", source: "Open weights" },
  { name: "Gemini 2.5 Flash", author: "Google", input: "$0.30", output: "$2.50", context: "1M", source: "Unreported" },
  { name: "Claude Sonnet 4.5", author: "Anthropic", input: "$3.00", output: "$15.00", context: "200K", source: "Unreported" },
];

function RailButton({ active, label, icon, onClick }: { active: boolean; label: string; icon: "chat" | "projects" | "search" | "money" | "settings"; onClick?: () => void }) {
  return <button type="button" className={active ? "active" : ""} aria-label={label} title={label} onClick={onClick}><Icon name={icon} /><span>{label}</span></button>;
}

function ConversationView({
  conversation,
  direction,
  contextOpen,
  onToggleContext,
}: {
  conversation: string;
  direction: UxDirection;
  contextOpen: boolean;
  onToggleContext: () => void;
}) {
  const [draft, setDraft] = useState("");
  const [sent, setSent] = useState<string[]>([]);
  const submit = (event: FormEvent) => {
    event.preventDefault();
    const value = draft.trim();
    if (!value) return;
    setSent((current) => [...current, value]);
    setDraft("");
  };

  return (
    <main className="ux-workspace">
      <header className="ux-workspace-header">
        <div className="ux-command-prompt"><Icon name="search" /><span>Ask, open, or run anything…</span><kbd>⌘ K</kbd></div>
        <div className="ux-title-block">
          <small>Topology · Research chat</small>
          <strong>{conversation}</strong>
        </div>
        <div className="ux-header-actions">
          <button type="button" className="ux-model-button"><span className="ux-model-dot" />Qwen3 235B <b>⌄</b></button>
          <button type="button" className={contextOpen ? "active" : ""} aria-label="Toggle research context" title="Toggle research context" onClick={onToggleContext}><Icon name="panel" /></button>
        </div>
      </header>

      <div className="ux-thread">
        <div className="ux-thread-date"><span />Today<span /></div>
        <article className="ux-message ux-message-user">
          <div className="ux-avatar">BA</div>
          <div><small>You · 10:42 AM</small><p>Can you give me an intuitive account of why the differentials in the Serre spectral sequence have the bidegrees they do?</p></div>
        </article>
        <article className="ux-message ux-message-agent">
          <div className="ux-avatar"><Icon name="spark" /></div>
          <div className="ux-message-body">
            <small>Reasoning · Qwen3 235B · 14.2s</small>
            <p>The cleanest intuition is to think of each page as what remains after resolving interactions at one geometric scale.</p>
            <p>Start with the bigraded approximation <span className="ux-math">E²<sub>p,q</sub> ≅ H<sub>p</sub>(B; H<sub>q</sub>(F))</span>. A class has a horizontal base degree and a vertical fiber degree. The differential must lower total homological degree by one, but on page <em>r</em> it is allowed to travel <em>r</em> columns through the filtration. Those two constraints force</p>
            <div className="ux-equation">d<sub>r</sub>: E<sup>r</sup><sub>p,q</sub> → E<sup>r</sup><sub>p−r, q+r−1</sub>.</div>
            <details className="ux-reasoning">
              <summary><span>Reasoning trace</span><small>3 steps · 1 source checked</small></summary>
              <ol><li>Relate the filtration shift to base degree.</li><li>Preserve the total degree −1 condition.</li><li>Check against the transgression edge case.</li></ol>
            </details>
            <div className="ux-artifact-card">
              <div className="ux-artifact-icon">𝑑</div>
              <div><strong>Bidegree sketch</strong><small>Computation · 3 artifacts</small></div>
              <button type="button">Open</button>
            </div>
          </div>
        </article>
        {sent.map((message, index) => (
          <article className="ux-message ux-message-user" key={`${message}:${index}`}>
            <div className="ux-avatar">BA</div><div><small>You · just now</small><p>{message}</p></div>
          </article>
        ))}
      </div>

      <form className="ux-composer" onSubmit={submit}>
        <div className="ux-composer-topline"><span>{direction.emphasis}</span><small>Reasoning Native · automatic route</small></div>
        <textarea value={draft} onChange={(event) => setDraft(event.target.value)} placeholder="Continue the research…" aria-label="Mock message" rows={2} />
        <div className="ux-composer-actions">
          <div><button type="button" aria-label="Attach"><Icon name="paperclip" /></button><button type="button" className="ux-tools-button">Tools <span>4</span></button></div>
          <div><span>High reasoning</span><button type="submit" className="ux-send" aria-label="Send mock message"><Icon name="send" /></button></div>
        </div>
      </form>
    </main>
  );
}

function MoneyView() {
  const [history, setHistory] = useState(false);
  const [openWeight, setOpenWeight] = useState(false);
  return (
    <main className="ux-workspace ux-money">
      <header className="ux-workspace-header">
        <div className="ux-title-block"><small>MODEL ECONOMICS</small><strong>Money</strong></div>
        <div className="ux-header-actions"><button type="button">Manage keys</button><button type="button" className="primary">Refresh prices</button></div>
      </header>
      <div className="ux-money-scroll">
        <nav className="ux-money-tabs" aria-label="Money mockup views">
          <button type="button" className={!history ? "active" : ""} onClick={() => setHistory(false)}><strong>Current prices</strong><small>Models and capabilities</small></button>
          <button type="button" className={history ? "active" : ""} onClick={() => setHistory(true)}><strong>Usage history</strong><small>Spend and call records</small></button>
        </nav>
        {!history ? <>
          <section className="ux-money-summary"><div><small>CATALOGUE</small><strong>327</strong><span>text models</span></div><div><small>STARRED</small><strong>12</strong><span>ready to compare</span></div><div><small>UPDATED</small><strong>now</strong><span>OpenRouter public catalogue</span></div></section>
          <div className="ux-money-controls"><label><Icon name="search" /><input placeholder="Search model or author…" /></label><button type="button" className={openWeight ? "active" : ""} onClick={() => setOpenWeight((value) => !value)}>{openWeight ? "Open weights only" : "All sources"}</button></div>
          <div className="ux-model-table" role="table" aria-label="Synthetic model prices">
            <div className="ux-model-row head" role="row"><span>Model</span><span>Author</span><span>Input / MTok</span><span>Output / MTok</span><span>Context</span></div>
            {models.filter((model) => !openWeight || model.source === "Open weights").map((model) => <div className="ux-model-row" role="row" key={model.name}><span><b>☆</b><strong>{model.name}</strong><small>{model.source}</small></span><span>{model.author}</span><span>{model.input}</span><span>{model.output}</span><span>{model.context}</span></div>)}
          </div>
        </> : <>
          <section className="ux-history-total"><div><small>REPORTED SPEND · 30 DAYS</small><strong>$18.42</strong></div><div className="ux-bars" aria-label="Synthetic daily spend chart">{[20, 38, 25, 62, 48, 76, 44, 88, 58, 71, 92, 66].map((height, index) => <i style={{ height: `${height}%` }} key={index} />)}</div></section>
          <div className="ux-call-list"><div><span className="ux-provider-mark">Q</span><p><strong>Qwen3 235B A22B</strong><small>Topology · Spectral sequence intuition</small></p><b>$0.084</b><time>Today, 10:42</time></div><div><span className="ux-provider-mark">DS</span><p><strong>DeepSeek R1 0528</strong><small>Moderation benchmark · 24 calls</small></p><b>$1.92</b><time>Yesterday</time></div><div><span className="ux-provider-mark">G</span><p><strong>Gemini 2.5 Flash</strong><small>Protein folding survey</small></p><b>$0.31</b><time>Monday</time></div></div>
        </>}
      </div>
    </main>
  );
}

function ProjectsView({ onOpenChat }: { onOpenChat: () => void }) {
  return <main className="ux-workspace ux-projects-view">
    <header className="ux-workspace-header">
      <div className="ux-title-block"><small>PROJECT</small><strong>Topology</strong></div>
      <div className="ux-header-actions"><button type="button">Import document</button><button type="button" className="primary"><Icon name="plus" />New research</button></div>
    </header>
    <div className="ux-project-scroll">
      <section className="ux-project-hero"><div><small>ACTIVE RESEARCH SPACE</small><h2>Topology</h2><p>Homotopy theory, spectral sequences, and reusable computation notes.</p></div><dl><div><dt>Conversations</dt><dd>4</dd></div><div><dt>Workflows</dt><dd>3</dd></div><div><dt>Artifacts</dt><dd>18</dd></div></dl></section>
      <section className="ux-project-grid">
        <div className="ux-project-panel"><header><div><small>RECENT</small><strong>Conversations</strong></div><button type="button">View all</button></header>{conversations.slice(0, 3).map((item, index) => <button type="button" className="ux-project-conversation" onClick={onOpenChat} key={item.id}><span>{index === 0 ? "↗" : "·"}</span><div><strong>{item.title}</strong><small>{index === 0 ? "Qwen3 235B · 14.2s" : `${item.meta} · research chat`}</small></div><time>{item.meta}</time></button>)}</div>
        <div className="ux-project-panel"><header><div><small>ACTIVE</small><strong>Workflows</strong></div><button type="button">＋</button></header><div className="ux-workflow-card"><div><span className="running" />Moderation sweep<small>18 / 24 calls</small></div><progress value="18" max="24" /></div><div className="ux-workflow-card"><div><span />Compare proof strategies<small>Ready · 3 variants</small></div><progress value="0" max="3" /></div><div className="ux-workflow-card"><div><span className="complete" />Literature triage<small>Complete · 42 papers</small></div><progress value="3" max="3" /></div></div>
      </section>
      <section className="ux-project-artifacts"><header><div><small>OUTPUTS</small><strong>Recent artifacts</strong></div></header><div><button type="button"><span>TXT</span><strong>serre-examples.txt</strong><small>Text · updated today</small></button><button type="button"><span>MD</span><strong>spectral-sequence-notes.md</strong><small>Document · updated today</small></button><button type="button"><span>PDF</span><strong>moderation-report.pdf</strong><small>Report · 2.4 MB</small></button></div></section>
    </div>
  </main>;
}

function ContextPanel({ open, onClose }: { open: boolean; onClose: () => void }) {
  const [taskOpen, setTaskOpen] = useState(true);
  return <aside className={`ux-context ${open ? "open" : ""}`}>
    <header><div><small>RESEARCH CONTEXT</small><strong>Current thread</strong></div><button type="button" aria-label="Close context" onClick={onClose}>×</button></header>
    <section><h3>Working set</h3><button type="button" className="ux-context-file"><span>PDF</span><div><strong>McCleary, 2nd ed.</strong><small>pp. 25–41 · attached</small></div></button><button type="button" className="ux-context-file"><span>TXT</span><div><strong>serre-examples.txt</strong><small>3 cells · current project</small></div></button></section>
    <section><h3>Run</h3><button type="button" className="ux-task-heading" aria-expanded={taskOpen} onClick={() => setTaskOpen((value) => !value)}><span><i className="complete"><Icon name="check" /></i>Build intuition</span><small>3 / 3</small></button>{taskOpen && <ol className="ux-task-list"><li className="done"><i /><span>Inspect attached reference</span></li><li className="done"><i /><span>Check bidegree convention</span></li><li className="done"><i /><span>Produce visual example</span></li></ol>}</section>
    <section className="ux-run-facts"><h3>Provenance</h3><dl><div><dt>Model</dt><dd>Qwen3 235B</dd></div><div><dt>Provider</dt><dd>OpenRouter · auto</dd></div><div><dt>Tokens</dt><dd>4,812</dd></div><div><dt>Cost</dt><dd>$0.084</dd></div></dl></section>
  </aside>;
}

function UxShell({ direction, viewport }: { direction: UxDirection; viewport: UxViewportId }) {
  const [surface, setSurface] = useState<"chat" | "projects" | "money">("chat");
  const [selectedConversation, setSelectedConversation] = useState(conversations[0].id);
  const [contextOpen, setContextOpen] = useState(true);
  const selected = conversations.find((item) => item.id === selectedConversation) ?? conversations[0];
  return (
    <div className="ux-preview-stage" data-viewport={viewport}>
      <section className="ux-shell" data-direction={direction.id} data-viewport={viewport} aria-label={`${direction.name} interactive Reasoning mockup`}>
        <div className="ux-window-bar">
          <div className="ux-traffic"><i /><i /><i /></div>
          <span>{viewport === "web" ? "localhost/workbench" : "Reasoning"}</span>
          <small>{viewport === "desktop" ? "Desktop preview" : viewport === "web" ? "Browser preview" : "Compact preview"}</small>
        </div>
        <div className="ux-app-frame">
          <nav className="ux-rail" aria-label="Mock application navigation">
            <div className="ux-logo">T</div>
            <div><RailButton active={surface === "chat"} label="Chat" icon="chat" onClick={() => setSurface("chat")} /><RailButton active={surface === "projects"} label="Projects" icon="projects" onClick={() => setSurface("projects")} /><RailButton active={false} label="Search" icon="search" /><RailButton active={surface === "money"} label="Money" icon="money" onClick={() => setSurface("money")} /></div>
            <RailButton active={false} label="Settings" icon="settings" />
          </nav>
          <aside className="ux-sidebar">
            <div className="ux-sidebar-brand"><strong>Reasoning</strong><small>REASONING WORKBENCH</small></div>
            <button type="button" className="ux-new-chat"><Icon name="plus" />New research</button>
            <label className="ux-sidebar-search"><Icon name="search" /><input placeholder="Search" /></label>
            <div className="ux-sidebar-section"><header><span>PROJECTS</span><button type="button">＋</button></header><button type="button" className="ux-project active"><span className="ux-project-glyph">T</span><span><strong>Topology</strong><small>4 conversations</small></span></button><button type="button" className="ux-project"><span className="ux-project-glyph">B</span><span><strong>Benchmark lab</strong><small>2 workflows running</small></span></button></div>
            <div className="ux-sidebar-section ux-conversations"><header><span>CONVERSATIONS</span><small>drag to reorder</small></header>{conversations.map((item) => <button type="button" className={selectedConversation === item.id ? "active" : ""} onClick={() => { setSelectedConversation(item.id); setSurface("chat"); }} key={item.id}><i className="ux-grip">⠿</i><span><strong>{item.title}</strong><small>{item.meta}</small></span>{item.unread && <b />}</button>)}</div>
            <div className="ux-sidebar-footer"><span className="ux-avatar">BA</span><span><strong>Ben</strong><small>Research workspace</small></span><button type="button">•••</button></div>
          </aside>
          {surface === "chat" ? <ConversationView conversation={selected.title} direction={direction} contextOpen={contextOpen} onToggleContext={() => setContextOpen((value) => !value)} /> : surface === "projects" ? <ProjectsView onOpenChat={() => setSurface("chat")} /> : <MoneyView />}
          <ContextPanel open={surface === "chat" && contextOpen} onClose={() => setContextOpen(false)} />
        </div>
      </section>
    </div>
  );
}

export function UxLab() {
  const initial = useMemo(() => parseUxLabState(typeof window === "undefined" ? "" : window.location.search), []);
  const [directionId, setDirectionId] = useState<UxDirectionId>(initial.direction);
  const [viewport, setViewport] = useState<UxViewportId>(initial.viewport);
  const [copied, setCopied] = useState(false);
  const direction = UX_DIRECTIONS.find((item) => item.id === directionId) ?? UX_DIRECTIONS[0];

  useEffect(() => {
    const priorTitle = document.title;
    document.title = "UX Lab · Reasoning";
    return () => { document.title = priorTitle; };
  }, []);

  useEffect(() => {
    const url = new URL(window.location.href);
    url.searchParams.set("direction", directionId);
    url.searchParams.set("viewport", viewport);
    window.history.replaceState({}, "", url);
  }, [directionId, viewport]);

  const copyLink = () => {
    if (!navigator.clipboard) return;
    void navigator.clipboard.writeText(window.location.href).then(() => {
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1500);
    });
  };

  return <div className="ux-lab">
    <header className="ux-lab-header">
      <div><span>REASONING / INTERNAL</span><h1>UX Lab</h1><p>Five directions, one realistic research state. Switch freely; nothing here touches your projects.</p></div>
      <div className="ux-lab-actions"><a href="/">Exit lab</a><button type="button" onClick={copyLink}>{copied ? "Copied" : "Copy state URL"}</button></div>
    </header>
    <section className="ux-lab-controls" aria-label="UX Lab controls">
      <nav className="ux-direction-tabs" aria-label="Design directions" role="tablist">{UX_DIRECTIONS.map((item) => <button type="button" role="tab" aria-selected={item.id === directionId} className={item.id === directionId ? "active" : ""} onClick={() => setDirectionId(item.id)} key={item.id}><small>{item.number}</small><span><strong>{item.name}</strong><em>{item.summary}</em></span></button>)}</nav>
      <div className="ux-viewport-tabs" aria-label="Preview size">{UX_VIEWPORTS.map((item) => <button type="button" aria-pressed={item.id === viewport} className={item.id === viewport ? "active" : ""} onClick={() => setViewport(item.id)} key={item.id}><strong>{item.label}</strong><small>{item.detail}</small></button>)}</div>
    </section>
    <div className="ux-lab-caption" aria-live="polite"><strong>{direction.name}</strong><span>{direction.emphasis}</span><p>{direction.summary}</p></div>
    <UxShell direction={direction} viewport={viewport} />
  </div>;
}

export default UxLab;
