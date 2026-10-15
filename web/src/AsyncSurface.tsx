import {
  Component,
  type ErrorInfo,
  type ReactNode,
  Suspense,
} from "react";

interface SurfaceBoundaryProps {
  children: ReactNode;
  label: string;
}

interface SurfaceBoundaryState {
  failed: boolean;
}

class SurfaceBoundary extends Component<
  SurfaceBoundaryProps,
  SurfaceBoundaryState
> {
  state: SurfaceBoundaryState = { failed: false };

  static getDerivedStateFromError(): SurfaceBoundaryState {
    return { failed: true };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error(`Failed to open ${this.props.label}`, error, info);
  }

  render() {
    if (this.state.failed) {
      return (
        <div className="surface-fallback surface-fallback-error" role="alert">
          <span className="surface-fallback-mark" aria-hidden="true">!</span>
          <strong>{this.props.label} could not be opened</strong>
          <p>The interface module did not load. Your project data is unchanged.</p>
          <button type="button" onClick={() => window.location.reload()}>
            reload workbench
          </button>
        </div>
      );
    }
    return this.props.children;
  }
}

function SurfaceLoading({ label }: { label: string }) {
  return (
    <div className="surface-fallback surface-fallback-loading" role="status">
      <span className="surface-fallback-mark" aria-hidden="true" />
      <strong>Opening {label}</strong>
      <p>Preparing the research surface…</p>
    </div>
  );
}

export function AsyncSurface({
  children,
  label,
}: {
  children: ReactNode;
  label: string;
}) {
  return (
    <SurfaceBoundary label={label}>
      <Suspense fallback={<SurfaceLoading label={label} />}>
        {children}
      </Suspense>
    </SurfaceBoundary>
  );
}
