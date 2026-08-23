import { useSortable } from '@dnd-kit/sortable';
import { CSS } from '@dnd-kit/utilities';
import { GripVertical } from 'lucide-react';
import { type CSSProperties, type ReactNode } from 'react';

import { formatNumber, formatSignedPercent } from '../formatters';
import type { DisplayGridBookLevel } from '../types';

interface SortableGridLevelRowProps {
  level: DisplayGridBookLevel;
  canDragLevel: boolean;
  isSell: boolean;
  isDisabled: boolean;
  pctFromBase?: number | null;
  children: (dragHandle: ReactNode, isDragging: boolean) => ReactNode;
}

export function SortableGridLevelRow({
  level,
  canDragLevel,
  isSell,
  isDisabled,
  pctFromBase,
  children,
}: SortableGridLevelRowProps) {
  const {
    attributes,
    listeners,
    setNodeRef,
    transform,
    transition,
    isDragging,
  } = useSortable({
    id: level.gridId,
    disabled: !canDragLevel,
  });
  const style: CSSProperties = {
    transform: CSS.Transform.toString(transform),
    transition,
    position: isDragging ? 'relative' : undefined,
    zIndex: isDragging ? 30 : undefined,
  };
  const dragHandle = canDragLevel ? (
    <button
      type="button"
      aria-label="拖拽档位"
      title="拖拽档位"
      className={`flex h-7 w-6 cursor-grab touch-none items-center justify-center rounded-md transition-colors active:cursor-grabbing focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-400/60 ${
        isDisabled
          ? 'text-slate-700 hover:bg-white/[0.03] hover:text-slate-500'
          : 'text-slate-500 hover:bg-white/5 hover:text-slate-200'
      }`}
      {...attributes}
      {...(listeners || {})}
    >
      <GripVertical className="h-4 w-4" />
    </button>
  ) : (
    <div className="h-7 w-6" />
  );

  return (
    <div ref={setNodeRef} style={style} className="space-y-2">
      <div className="relative grid grid-cols-[68px_minmax(0,1fr)] items-center gap-3">
        <div className="relative z-20 flex min-h-14 items-center justify-center">
          <div
            className={`absolute left-1/2 h-px w-5 -translate-x-1/2 ${
              isDisabled
                ? 'bg-slate-600/40'
                : isSell
                  ? 'bg-market-down/55'
                  : 'bg-market-up/55'
            }`}
          />
          <div
            className={`relative min-w-[56px] rounded-lg border bg-white/95 px-2 py-1 text-center shadow-sm dark:bg-slate-950/95 ${
              isDisabled
                ? 'border-slate-600/35 text-slate-500 shadow-none dark:text-slate-500'
                : isSell
                  ? 'border-market-down/25 text-market-down'
                  : 'border-market-up/25 text-market-up'
            }`}
          >
            <div className="font-mono text-[11px] font-black leading-none">
              {formatNumber(level.price)}
            </div>
            <div className="mt-1 font-mono text-[8px] font-bold leading-none opacity-70">
              {formatSignedPercent(pctFromBase)}
            </div>
          </div>
        </div>
        <div className="min-w-0">{children(dragHandle, isDragging)}</div>
      </div>
    </div>
  );
}
