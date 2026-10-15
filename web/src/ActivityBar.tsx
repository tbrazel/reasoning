type ActivityIconName = "folder" | "search" | "ledger" | "collaboration" | "library" | "prices" | "settings";

function ActivityIcon({ name }: { name: ActivityIconName }) {
  return (
    <svg
      aria-hidden="true"
      data-icon={name}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.75"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      {name === "folder" && (
        <>
          <path d="M3 7.5V6a2 2 0 0 1 2-2h4l2 2h6a2 2 0 0 1 2 2v1.5" />
          <path d="M3.5 10.5h17l-2.1 7A2 2 0 0 1 16.5 19h-11a2 2 0 0 1-2-2Z" />
        </>
      )}
      {name === "search" && (
        <>
          <circle cx="10.5" cy="10.5" r="5.5" />
          <path d="m15 15 4.5 4.5" />
        </>
      )}
      {name === "ledger" && (
        <>
          <rect x="5" y="3.5" width="14" height="17" rx="2" />
          <path d="M8.5 8h7M8.5 12h7M8.5 16h4" />
        </>
      )}
      {name === "library" && (
        <>
          <path d="M4.5 4.5h5v15h-5zM9.5 4.5h5v15h-5z" />
          <path d="m15.5 5.5 3.2-.8 2.8 13.8-3.2.8z" />
        </>
      )}

      {name === "collaboration" && (
        <>
          <circle cx="8" cy="8" r="3" />
          <circle cx="17" cy="9" r="2.5" />
          <path d="M3.5 19c.5-4 2.2-6 4.8-6s4.4 2 4.8 6M13 14.5c1-.9 2.1-1.3 3.4-1.3 2.2 0 3.6 1.8 4.1 4.8" />
        </>
      )}
      {name === "prices" && (
        <>
          <circle cx="12" cy="12" r="8.5" />
          <path d="M15 8.5h-4.2a2.1 2.1 0 0 0 0 4.2h2.4a2.1 2.1 0 0 1 0 4.2H9M12 6.5v2M12 17v1.5" />
        </>
      )}
      {name === "settings" && (
        <>
          <path d="M4 6h7M15 6h5M4 12h3M11 12h9M4 18h10M18 18h2" />
          <circle cx="13" cy="6" r="2" />
          <circle cx="9" cy="12" r="2" />
          <circle cx="16" cy="18" r="2" />
        </>
      )}
    </svg>
  );
}

export function ActivityBar({
  explorerOpen,
  ledgerOpen,
  bibliographyOpen,
  pricingOpen,
  collaborationOpen,
  hasProject,
  onToggleExplorer,
  onOpenSearch,
  onToggleLedger,
  onOpenBibliography,
  onOpenPricing,
  onOpenCollaboration,
  onOpenSettings,
}: {
  explorerOpen: boolean;
  ledgerOpen: boolean;
  bibliographyOpen: boolean;
  pricingOpen: boolean;
  collaborationOpen: boolean;
  hasProject: boolean;
  onToggleExplorer: () => void;
  onOpenSearch: () => void;
  onToggleLedger: () => void;
  onOpenBibliography: () => void;
  onOpenPricing: () => void;
  onOpenCollaboration: () => void;
  onOpenSettings: () => void;
}) {
  return (
    <nav className="activity-bar" aria-label="Workbench">
      <button className={explorerOpen ? "active" : ""} aria-label="Toggle Explorer panel" title="Toggle Explorer panel" aria-pressed={explorerOpen} onClick={onToggleExplorer}>
        <ActivityIcon name="folder" />
      </button>
      <button aria-label="Quick open" title="Quick open" onClick={onOpenSearch}>
        <ActivityIcon name="search" />
      </button>
      <button className={ledgerOpen ? "active" : ""} aria-label="Toggle Ledger panel" title="Toggle Ledger panel" aria-pressed={ledgerOpen} disabled={!hasProject} onClick={onToggleLedger}>
        <ActivityIcon name="ledger" />
      </button>

      <button className={collaborationOpen ? "active" : ""} aria-label="Cloud workspaces" title="Cloud workspaces" aria-pressed={collaborationOpen} onClick={onOpenCollaboration}>
        <ActivityIcon name="collaboration" />
      </button>
      <button className={bibliographyOpen ? "active" : ""} aria-label="Library" title="Library" aria-pressed={bibliographyOpen} onClick={onOpenBibliography}>
        <ActivityIcon name="library" />
      </button>
      <button className={pricingOpen ? "active" : ""} aria-label="Money" title="Money" aria-pressed={pricingOpen} onClick={onOpenPricing}>
        <ActivityIcon name="prices" />
      </button>
      <span className="activity-spacer" />
      <button aria-label="Settings" title="Settings" onClick={onOpenSettings}>
        <ActivityIcon name="settings" />
      </button>
    </nav>
  );
}
