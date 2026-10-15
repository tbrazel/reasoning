import { useEffect, useRef } from "react";
import type { RefObject } from "react";

const FOCUSABLE = [
  "button:not([disabled])",
  "[href]",
  "input:not([disabled])",
  "select:not([disabled])",
  "textarea:not([disabled])",
  "[tabindex]:not([tabindex='-1'])",
].join(",");

const modalStack: RefObject<HTMLDivElement>[] = [];

/** Keep keyboard focus inside a modal, close it with Escape, then return
 * focus to the control that opened it. */
export function useModalDialog(onClose: () => void): RefObject<HTMLDivElement> {
  const ref = useRef<HTMLDivElement>(null);
  const onCloseRef = useRef(onClose);
  useEffect(() => {
    onCloseRef.current = onClose;
  }, [onClose]);

  useEffect(() => {
    const previous = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    modalStack.push(ref);
    const frame = window.requestAnimationFrame(() => {
      const target = ref.current?.querySelector<HTMLElement>("[autofocus], input, button, select, textarea, [href]");
      target?.focus();
    });
    const keydown = (event: KeyboardEvent) => {
      if (modalStack.at(-1) !== ref) return;
      if (event.key === "Escape") {
        event.preventDefault();
        onCloseRef.current();
        return;
      }
      if (event.key !== "Tab" || !ref.current) return;
      const values = [...ref.current.querySelectorAll<HTMLElement>(FOCUSABLE)]
        .filter((element) => element.offsetParent !== null);
      if (!values.length) return;
      const first = values[0];
      const last = values[values.length - 1];
      if (!ref.current.contains(document.activeElement)) {
        event.preventDefault();
        (event.shiftKey ? last : first).focus();
      } else if (event.shiftKey && document.activeElement === first) {
        event.preventDefault(); last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault(); first.focus();
      }
    };
    document.addEventListener("keydown", keydown);
    return () => {
      window.cancelAnimationFrame(frame);
      document.removeEventListener("keydown", keydown);
      const index = modalStack.lastIndexOf(ref);
      if (index !== -1) modalStack.splice(index, 1);
      if (previous?.isConnected) previous.focus();
    };
  }, []);
  return ref;
}
