import type {
  CreateWatchlistGroupInput,
  DeleteWatchlistGroupInput,
  RenameWatchlistGroupInput,
  ReorderWatchlistGroupItemsInput,
  ReorderWatchlistGroupsInput,
  ReorderWatchlistItemsInput,
  SaveWatchlistItemInput,
} from '@/generated/gql/graphql';

export interface WatchlistGroupSummary {
  id: string;
  name: string;
  displayOrder: number;
  itemCount: number;
}

export interface WatchlistGroupMembershipRecord {
  groupId: string;
  displayOrder: number;
}

export interface WatchlistItemRecord {
  id: string;
  stockCode: string;
  instrumentName?: string | null;
  displayOrder: number;
  note?: string | null;
  groups: WatchlistGroupSummary[];
  groupMemberships: WatchlistGroupMembershipRecord[];
}

export interface WatchlistWorkspaceData {
  items: WatchlistItemRecord[];
  groups: WatchlistGroupSummary[];
}

export type WatchlistCollection =
  | { kind: 'all'; id: 'all'; label: string }
  | { kind: 'holdings'; id: 'holdings'; label: string }
  | { kind: 'group'; id: string; label: string };

export type WatchlistSaveInput = SaveWatchlistItemInput;
export type WatchlistGroupCreateInput = CreateWatchlistGroupInput;
export type WatchlistGroupRenameInput = RenameWatchlistGroupInput;
export type WatchlistGroupDeleteInput = DeleteWatchlistGroupInput;
export type WatchlistOrderInput = ReorderWatchlistItemsInput;
export type WatchlistGroupOrderInput = ReorderWatchlistGroupsInput;
export type WatchlistGroupItemOrderInput = ReorderWatchlistGroupItemsInput;

export interface WatchlistMutationResponse {
  success: boolean;
  message: string;
}
