import type { GridBook } from '../types';

import { InventoryLotsPanel } from './InventoryLotsPanel';
import { ReleaseEventsPanel } from './ReleaseEventsPanel';

export function SidePanels({ book }: { book?: GridBook }) {
  return (
    <div className="grid gap-5 xl:grid-rows-2">
      <InventoryLotsPanel lots={book?.inventoryLots} />
      <ReleaseEventsPanel events={book?.releaseEvents} />
    </div>
  );
}
