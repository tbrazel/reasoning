import { useModalDialog } from "./useModalDialog";

export interface ConfirmationRequest {
  title: string;
  message: string;
  confirmLabel: string;
  tone?: "danger" | "confirm";
  onConfirm: () => void;
}

export function ConfirmationDialog({
  request,
  onClose,
}: {
  request: ConfirmationRequest;
  onClose: () => void;
}) {
  const dialogRef = useModalDialog(onClose);
  return (
    <div className="name-overlay confirmation-overlay" onClick={onClose}>
      <div
        ref={dialogRef}
        className="name-dialog confirm-dialog"
        role="alertdialog"
        aria-modal="true"
        aria-labelledby="confirm-dialog-title"
        aria-describedby="confirm-dialog-message"
        onClick={(event) => event.stopPropagation()}
      >
        <div className="name-dialog-title" id="confirm-dialog-title">
          {request.title}
        </div>
        <p id="confirm-dialog-message">{request.message}</p>
        <div className="name-dialog-actions">
          <button type="button" className="icon-btn" onClick={onClose}>
            cancel
          </button>
          <button
            type="button"
            className={request.tone ?? "danger"}
            onClick={() => {
              onClose();
              request.onConfirm();
            }}
          >
            {request.confirmLabel}
          </button>
        </div>
      </div>
    </div>
  );
}
