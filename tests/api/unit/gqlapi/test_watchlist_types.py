from types import SimpleNamespace

from quantx_api.gqlapi.types.watchlist_types import WatchlistItem


def test_watchlist_item_exposes_group_membership_order():
  model = SimpleNamespace(
    id="item-1",
    account_id="acct-a",
    stock_code="600000.SH",
    instrument_name="浦发银行",
    display_order=1,
    note=None,
    created_at=None,
    updated_at=None,
    group_memberships=[
      SimpleNamespace(group_id="group-b", display_order=2, group=None),
      SimpleNamespace(group_id="group-a", display_order=1, group=None),
    ],
  )

  item = WatchlistItem.from_model(model)

  assert [str(membership.group_id) for membership in item.group_memberships] == [
    "group-a",
    "group-b",
  ]
  assert [membership.display_order for membership in item.group_memberships] == [1, 2]
