export const lockedStatuses = new Set(['PENDING', 'PARTIAL_FILLED', 'FILLED']);

export const SELL_ZONE_ID = 'grid-zone-sell';
export const BUY_ZONE_ID = 'grid-zone-buy';
export const BASE_MARKER_ID = 'grid-base-marker';

export const LOW_SIGNAL_GRID_REASONS = new Set([
  'initialized',
  'grid_book_updated',
]);

export type GridZoneId = typeof SELL_ZONE_ID | typeof BUY_ZONE_ID;
