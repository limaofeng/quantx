import type { KeyboardEvent } from 'react';

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
