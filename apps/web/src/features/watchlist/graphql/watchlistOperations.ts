import { gql } from '@/generated/gql';

export const WatchlistWorkspaceQuery = gql(`
  query Watchlist_Workspace($accountId: String) {
    watchlist(accountId: $accountId) {
      id
      stockCode
      instrumentName
      displayOrder
      note
      groups {
        id
        name
        displayOrder
        itemCount
      }
      groupMemberships {
        groupId
        displayOrder
      }
    }
    watchlistGroups(accountId: $accountId) {
      id
      name
      displayOrder
      itemCount
    }
  }
`);

export const SaveWatchlistItemMutation = gql(`
  mutation Watchlist_SaveItem($input: SaveWatchlistItemInput!) {
    saveWatchlistItem(input: $input) {
      success
      message
    }
  }
`);

export const RemoveWatchlistItemMutation = gql(`
  mutation Watchlist_RemoveItem($stockCode: String!, $accountId: String) {
    removeWatchlistItem(stockCode: $stockCode, accountId: $accountId) {
      success
      message
    }
  }
`);

export const CreateWatchlistGroupMutation = gql(`
  mutation Watchlist_CreateGroup($input: CreateWatchlistGroupInput!) {
    createWatchlistGroup(input: $input) {
      success
      message
    }
  }
`);

export const RenameWatchlistGroupMutation = gql(`
  mutation Watchlist_RenameGroup($input: RenameWatchlistGroupInput!) {
    renameWatchlistGroup(input: $input) {
      success
      message
    }
  }
`);

export const DeleteWatchlistGroupMutation = gql(`
  mutation Watchlist_DeleteGroup($input: DeleteWatchlistGroupInput!) {
    deleteWatchlistGroup(input: $input) {
      success
      message
    }
  }
`);

export const ReorderWatchlistItemsMutation = gql(`
  mutation Watchlist_ReorderItems($input: ReorderWatchlistItemsInput!) {
    reorderWatchlistItems(input: $input) {
      success
      message
    }
  }
`);

export const ReorderWatchlistGroupsMutation = gql(`
  mutation Watchlist_ReorderGroups($input: ReorderWatchlistGroupsInput!) {
    reorderWatchlistGroups(input: $input) {
      success
      message
    }
  }
`);

export const ReorderWatchlistGroupItemsMutation = gql(`
  mutation Watchlist_ReorderGroupItems(
    $input: ReorderWatchlistGroupItemsInput!
  ) {
    reorderWatchlistGroupItems(input: $input) {
      success
      message
    }
  }
`);
