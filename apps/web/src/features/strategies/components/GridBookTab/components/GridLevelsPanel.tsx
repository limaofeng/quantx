import {
  closestCenter,
  DndContext,
  type DragEndEvent,
  type SensorDescriptor,
  type SensorOptions,
} from '@dnd-kit/core';
import {
  SortableContext,
  verticalListSortingStrategy,
} from '@dnd-kit/sortable';
import { Plus, Save } from 'lucide-react';

import { Button } from '@/components/ui/button';
import { Card } from '@/components/ui/card';

import { BUY_ZONE_ID, SELL_ZONE_ID } from '../constants';
import type { DisplayGridBookLevel, GridBook, UpdateGridLevel } from '../types';

import { BaseAxisMarker } from './BaseAxisMarker';
import { GridLevelCard } from './GridLevelCard';
import { SortableBaseAxisMarker } from './SortableBaseAxisMarker';
import { SortableGridZone } from './SortableGridZone';

interface GridLevelsPanelProps {
  book?: GridBook;
  editable: boolean;
  dirty: boolean;
  sellLevels: DisplayGridBookLevel[];
  buyLevels: DisplayGridBookLevel[];
  displayLevels: DisplayGridBookLevel[];
  sortableLevelIds: string[];
  sensors: SensorDescriptor<SensorOptions>[];
  onAddLevel: () => void;
  onSave: () => void | Promise<void>;
  onDragEnd: (event: DragEndEvent) => void;
  updateLevel: UpdateGridLevel;
  deleteLevel: (gridId: string) => void;
}

export function GridLevelsPanel({
  book,
  editable,
  dirty,
  sellLevels,
  buyLevels,
  displayLevels,
  sortableLevelIds,
  sensors,
  onAddLevel,
  onSave,
  onDragEnd,
  updateLevel,
  deleteLevel,
}: GridLevelsPanelProps) {
  const renderLevelRow = (level: DisplayGridBookLevel) => (
    <GridLevelCard
      key={level.gridId}
      level={level}
      book={book}
      editable={editable}
      updateLevel={updateLevel}
      deleteLevel={deleteLevel}
    />
  );

  return (
    <Card className="overflow-hidden rounded-[2rem] border border-slate-200 bg-white shadow-xl dark:border-white/10 dark:bg-slate-900/60">
      <div className="flex items-center justify-between border-b border-slate-100 px-6 py-5 dark:border-white/5">
        <div>
          <div className="text-[10px] font-black uppercase tracking-[0.24em] text-slate-700 dark:text-slate-200">
            网格档位
          </div>
          <p className="mt-1 text-[10px] font-medium text-slate-500">
            参数版本 {book?.parameterVersion || '--'} · 网格簿 v
            {book?.version || 1} · 卖出区 / 买入区可拖拽排序
          </p>
        </div>
        {editable && (
          <div className="flex gap-2">
            <Button
              variant="outline"
              className="rounded-xl text-[10px] font-black uppercase tracking-widest"
              onClick={onAddLevel}
            >
              <Plus className="mr-2 h-4 w-4" />
              新增档位
            </Button>
            <Button
              className="rounded-xl bg-blue-600 text-[10px] font-black uppercase tracking-widest text-white hover:bg-blue-500"
              disabled={!dirty}
              onClick={() => void onSave()}
            >
              <Save className="mr-2 h-4 w-4" />
              保存网格簿
            </Button>
          </div>
        )}
      </div>

      <div className="relative px-5 py-6 sm:px-6">
        <div className="pointer-events-none absolute bottom-6 left-[54px] top-6 w-px -translate-x-1/2 bg-gradient-to-b from-market-down/35 via-primary/55 to-market-up/35 sm:left-[58px]" />

        {displayLevels.length === 0 ? (
          <div className="px-6 py-12 text-center text-xs font-bold text-slate-400">
            暂无网格档位。
          </div>
        ) : (
          <DndContext
            sensors={sensors}
            collisionDetection={closestCenter}
            onDragEnd={onDragEnd}
          >
            <SortableContext
              items={sortableLevelIds}
              strategy={verticalListSortingStrategy}
            >
              <SortableGridZone
                id={SELL_ZONE_ID}
                droppable={sellLevels.length === 0}
              >
                {sellLevels.map(renderLevelRow)}
              </SortableGridZone>
              <SortableBaseAxisMarker>
                <BaseAxisMarker basePrice={book?.basePrice} />
              </SortableBaseAxisMarker>
              <SortableGridZone
                id={BUY_ZONE_ID}
                droppable={buyLevels.length === 0}
              >
                {buyLevels.map(renderLevelRow)}
              </SortableGridZone>
            </SortableContext>
          </DndContext>
        )}
      </div>
    </Card>
  );
}
