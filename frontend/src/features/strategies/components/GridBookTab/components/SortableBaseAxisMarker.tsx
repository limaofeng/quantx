import { useSortable } from '@dnd-kit/sortable';
import { CSS } from '@dnd-kit/utilities';
import type { ReactNode } from 'react';

import { BASE_MARKER_ID } from '../constants';

export function SortableBaseAxisMarker({ children }: { children: ReactNode }) {
  const { setNodeRef, transform, transition, isOver } = useSortable({
    id: BASE_MARKER_ID,
    // Base price is a valid boundary drop target, but cannot be dragged itself.
    disabled: {
      draggable: true,
      // In dnd-kit this means "do not disable droppable".
      droppable: false,
    },
  });

  return (
    <div
      ref={setNodeRef}
      style={{
        transform: CSS.Transform.toString(transform),
        transition,
      }}
      className={`cursor-default select-none transition-colors ${
        isOver ? 'rounded-xl bg-blue-500/[0.05]' : ''
      }`}
    >
      {children}
    </div>
  );
}
