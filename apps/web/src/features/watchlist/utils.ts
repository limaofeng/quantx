import type { WatchlistItemRecord } from './types';

export function mergeWatchlistGroupIds(
  item: Pick<WatchlistItemRecord, 'groups'> | undefined,
  groupId?: string
): string[] {
  const groupIds = new Set(item?.groups.map(group => group.id) ?? []);
  if (groupId) groupIds.add(groupId);
  return Array.from(groupIds);
}

export function sortWatchlistItemsForGroup(
  items: WatchlistItemRecord[],
  groupId: string
): WatchlistItemRecord[] {
  return items
    .filter(item => item.groups.some(group => group.id === groupId))
    .slice()
    .sort((left, right) => {
      const leftOrder =
        left.groupMemberships.find(membership => membership.groupId === groupId)
          ?.displayOrder ?? Number.MAX_SAFE_INTEGER;
      const rightOrder =
        right.groupMemberships.find(
          membership => membership.groupId === groupId
        )?.displayOrder ?? Number.MAX_SAFE_INTEGER;
      return (
        leftOrder - rightOrder ||
        left.displayOrder - right.displayOrder ||
        left.stockCode.localeCompare(right.stockCode)
      );
    });
}
