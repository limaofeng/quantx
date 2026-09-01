import { useEffect, useRef } from 'react';
import type { Dispatch, KeyboardEvent, RefObject, SetStateAction } from 'react';

export function useRevealReplayEvidenceTarget(
  eventKey: string | null | undefined,
  targetId: string | null | undefined,
  targetRef: RefObject<HTMLButtonElement>,
  setExpanded: Dispatch<SetStateAction<string | null>>
) {
  const revealedEventKeyRef = useRef<string | null>(null);

  useEffect(() => {
    if (!eventKey) {
      revealedEventKeyRef.current = null;
      return;
    }
    if (!targetId || revealedEventKeyRef.current === eventKey) return;

    revealedEventKeyRef.current = eventKey;
    setExpanded(targetId);

    if (typeof document === 'undefined') return;
    const activeElement = document.activeElement;
    const focusIsUnclaimed =
      !activeElement ||
      !activeElement.isConnected ||
      activeElement === document.body ||
      activeElement === document.documentElement;
    if (focusIsUnclaimed) targetRef.current?.focus();
  }, [eventKey, setExpanded, targetId, targetRef]);
}

export function collapseEvidenceOnEscape(
  event: KeyboardEvent<HTMLDivElement>,
  collapse: () => void
) {
  if (event.key !== 'Escape' || event.defaultPrevented) return;
  const row = event.currentTarget.querySelector<HTMLButtonElement>(
    'button[aria-expanded="true"]'
  );
  if (!row) return;
  event.preventDefault();
  event.stopPropagation();
  // Move focus before removing the detail that may currently own it.
  row.focus();
  collapse();
}
