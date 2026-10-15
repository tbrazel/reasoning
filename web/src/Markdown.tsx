import {
  Component,
  lazy,
  memo,
  Suspense,
  type ErrorInfo,
  type ReactNode,
} from "react";

export { artifactNameFromPath } from "./artifactLinks";
export { openArtifact, openLedgerEntry } from "./workbenchEvents";

let richTextModule: Promise<typeof import("./MarkdownRenderer")> | null = null;

const loadRichText = () => (richTextModule ??= import("./MarkdownRenderer"));

/** Start fetching the rich-text renderer before any transcript mounts. A
 *  chat opens dozens of Markdown nodes in one commit; without the head
 *  start that whole commit suspends together and can be left showing its
 *  plain-text fallback — raw asterisks and TeX source — for good. A failed
 *  warm is not an error: the lazy component retries on its own render and
 *  falls back to plain text if that fails too. */
export function warmRichText(): void {
  loadRichText().catch(() => {});
}

const RichTextRenderer = lazy(() =>
  loadRichText().then((module) => ({
    default: module.RichTextRenderer,
  })),
);

function PlainText({ inline, text }: { inline: boolean; text: string }) {
  return inline
    ? <span className="md rich-text-fallback">{text}</span>
    : <div className="md rich-text-fallback">{text}</div>;
}

class RichTextBoundary extends Component<
  { children: ReactNode; fallback: ReactNode },
  { failed: boolean }
> {
  state = { failed: false };

  static getDerivedStateFromError() {
    return { failed: true };
  }
  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error("Rich-text rendering failed; showing plain text", error, info);
  }

  render() {
    return this.state.failed ? this.props.fallback : this.props.children;
  }
}

function DeferredRichText({ inline, text }: { inline: boolean; text: string }) {
  const fallback = <PlainText inline={inline} text={text} />;
  return (
    <RichTextBoundary fallback={fallback}>
      <Suspense fallback={fallback}>
        <RichTextRenderer inline={inline} text={text} />
      </Suspense>
    </RichTextBoundary>
  );
}

// Transcript renders are memoized: once the rich renderer has loaded,
// Markdown and KaTeX parsing should not repeat on unrelated parent updates.
export const Markdown = memo(function Markdown({ text }: { text: string }) {
  return <DeferredRichText inline={false} text={text} />;
});

/** Inline-only variant for claim statements and compact activity details. */
export const TeX = memo(function TeX({ text }: { text: string }) {
  return <DeferredRichText inline text={text} />;
});
