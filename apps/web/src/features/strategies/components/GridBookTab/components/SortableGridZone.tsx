import { useDroppable } from '@dnd-kit/core';
import type { ReactNode } from 'react';

import type { GridZoneId } from '../constants';

interface SortableGridZoneProps {
  id: GridZoneId;
  droppable: boolean;
  children: ReactNode;
}

export function SortableGridZone({
  id,
  droppable,
  children,
}: SortableGridZoneProps) {
  const { setNodeRef, isOver } = useDroppable({
    id,
    disabled: !droppable,
  });
  return (
    <div
      ref={setNodeRef}
      className={`min-h-4 space-y-2 rounded-panel transition-colors ${
        isOver ? 'bg-blue-500/[0.04]' : ''
      }`}
    >
      {children}
    </div>
  );
}
