import { lazy } from "react";
import { createPortal } from "react-dom";
import { ModelPriceInfo } from "./api";
import { AsyncSurface } from "./AsyncSurface";
import { useModalDialog } from "./useModalDialog";

const PriceBrowser = lazy(() =>
  import("./PriceBrowser").then((module) => ({ default: module.PriceBrowser })),
);

export function PriceModelPickerDialog({
  targetLabel,
  rankingContext,
  onClose,
  onSelect,
}: {
  targetLabel: string;
  rankingContext?: { projectId: string; workflowId: string };
  onClose: () => void;
  onSelect: (modelId: string) => void;
}) {
  const dialogRef = useModalDialog(onClose);
  const select = (model: ModelPriceInfo) => {
    onSelect(model.model_id);
    onClose();
  };

  return createPortal(
    <div className="name-overlay price-picker-overlay" onClick={onClose}>
      <div
        ref={dialogRef}
        className="price-picker-dialog"
        role="dialog"
        aria-modal="true"
        aria-label={`Choose a priced model for ${targetLabel}`}
        onClick={(event) => event.stopPropagation()}
      >
        <button
          className="price-picker-close"
          type="button"
          aria-label="Close model price picker"
          title="Close"
          onClick={onClose}
        >
          ×
        </button>
        <AsyncSurface label="model prices">
          <PriceBrowser selection={{ targetLabel, onSelect: select, rankingContext }} />
        </AsyncSurface>
      </div>
    </div>,
    document.body,
  );
}
