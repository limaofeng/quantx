from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace

import pytest
from quantx_api.auth.errors import AuthError
from quantx_api.auth.principal import Principal
from quantx_api.auth.tokens import utcnow
from quantx_api.gqlapi.schemas import notification_schema
from quantx_api.gqlapi.schemas.notification_schema import (
  NotificationMutation,
  NotificationQuery,
)
from quantx_api.gqlapi.types.notification_types import (
  PushCategory,
  PushCategoryPreferenceInput,
  PushEnvironment,
  RegisterPushDeviceInput,
  UnregisterPushDeviceInput,
  UpdatePushPreferencesInput,
)
from quantx_api.notifications.service import (
  PushNotificationService,
  build_minimal_apns_payload,
)
from quantx_infrastructure.database.relational_base import Base
from quantx_infrastructure.models.auth import (
  AuthDeviceSession,
  AuthUser,
  AuthUserAccountAccess,
)
from quantx_infrastructure.models.ios_notifications import (
  IosNotificationEvent,
  IosNotificationOutbox,
  IosPushCategoryPreference,
  IosPushRegistration,
)
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

SIGNING_KEY = b"notification-test-signing-key-longer-than-thirty-two-bytes"
INSTALL_ID = "3bc6b00f-934d-4c76-a7b1-443deeb4a461"
BUNDLE_ID = "com.quantx.personal"
DEVICE_TOKEN = "a1" * 37


def _principal(
  *,
  session_id: str = "session-native-1",
  permissions: frozenset[str] = frozenset({"notification:manage"}),
  native_session: bool = True,
) -> Principal:
  return Principal(
    user_id="user-1",
    username="owner",
    display_name="Owner",
    device_session_id=session_id,
    access_token_expires_at=utcnow() + timedelta(minutes=5),
    permissions=permissions,
    authorized_account_ids=("ACCOUNT-1",),
    is_native_session=native_session,
  )


def _info(principal: Principal | None = None) -> SimpleNamespace:
  return SimpleNamespace(context={"principal": principal or _principal()})


@pytest.fixture
async def notification_database(monkeypatch):
  engine = create_async_engine("sqlite+aiosqlite:///:memory:")
  tables = [
    AuthUser.__table__,
    AuthUserAccountAccess.__table__,
    AuthDeviceSession.__table__,
    IosPushRegistration.__table__,
    IosPushCategoryPreference.__table__,
    IosNotificationEvent.__table__,
    IosNotificationOutbox.__table__,
  ]
  async with engine.begin() as connection:
    await connection.run_sync(
      lambda sync_connection: Base.metadata.create_all(
        sync_connection,
        tables=tables,
      )
    )
  sessions = async_sessionmaker(engine, expire_on_commit=False)
  async with sessions() as db:
    db.add(
      AuthUser(
        id="user-1",
        username="owner",
        display_name="Owner",
        password_hash="hash",
        is_active=True,
        permissions=["notification:manage", "mutation:write"],
      )
    )
    db.add(
      AuthUserAccountAccess(
        user_id="user-1",
        account_id="ACCOUNT-1",
        is_default=True,
      )
    )
    db.add_all(
      [
        AuthDeviceSession(
          id="session-native-1",
          user_id="user-1",
          refresh_token_hash="1" * 64,
          expires_at=utcnow() + timedelta(hours=1),
          revoked_at=None,
          last_used_at=utcnow(),
          device_name="iPhone",
          granted_permissions=["notification:manage"],
        ),
        AuthDeviceSession(
          id="session-native-2",
          user_id="user-1",
          refresh_token_hash="2" * 64,
          expires_at=utcnow() + timedelta(hours=1),
          revoked_at=None,
          last_used_at=utcnow(),
          device_name="iPad",
          granted_permissions=["notification:manage"],
        ),
        AuthDeviceSession(
          id="session-web-1",
          user_id="user-1",
          refresh_token_hash="3" * 64,
          expires_at=utcnow() + timedelta(hours=1),
          revoked_at=None,
          last_used_at=utcnow(),
          device_name="Web",
          granted_permissions=None,
        ),
      ]
    )
    await db.commit()
  monkeypatch.setattr(notification_schema, "AsyncSessionLocal", sessions)
  monkeypatch.setattr(
    notification_schema,
    "settings",
    SimpleNamespace(secret_key=SIGNING_KEY.decode("ascii"), algorithm="HS256"),
  )
  yield sessions
  await engine.dispose()


@pytest.mark.asyncio
async def test_register_rotates_encrypted_token_and_never_returns_it(
  notification_database,
):
  mutation = NotificationMutation()
  first = await mutation.register_push_device(
    _info(),
    RegisterPushDeviceInput(
      device_token=DEVICE_TOKEN,
      environment=PushEnvironment.SANDBOX,
      app_bundle_id=BUNDLE_ID,
      app_version="1.0 (1)",
      device_install_id=INSTALL_ID,
    ),
  )
  rotated_token = "b2" * 51
  second = await mutation.register_push_device(
    _info(),
    RegisterPushDeviceInput(
      device_token=rotated_token,
      environment=PushEnvironment.SANDBOX,
      app_bundle_id=BUNDLE_ID,
      app_version="1.0 (2)",
      device_install_id=INSTALL_ID,
    ),
  )

  assert first.id == second.id
  assert second.app_version == "1.0 (2)"
  assert len(second.preferences) == 5
  assert DEVICE_TOKEN not in repr(first)
  assert rotated_token not in repr(second)
  async with notification_database() as db:
    rows = (await db.execute(select(IosPushRegistration))).scalars().all()
    assert len(rows) == 1
    assert rows[0].token_ciphertext is not None
    assert DEVICE_TOKEN not in rows[0].token_ciphertext
    assert rotated_token not in rows[0].token_ciphertext
    assert (
      PushNotificationService(db, signing_key=SIGNING_KEY).decrypt_device_token(
        rows[0].token_ciphertext
      )
      == rotated_token
    )


@pytest.mark.asyncio
async def test_preferences_gate_minimal_outbox_and_route_is_session_bound(
  notification_database,
):
  mutation = NotificationMutation()
  await mutation.register_push_device(
    _info(),
    RegisterPushDeviceInput(
      device_token=DEVICE_TOKEN,
      environment=PushEnvironment.SANDBOX,
      app_bundle_id=BUNDLE_ID,
      app_version="1.0",
      device_install_id=INSTALL_ID,
    ),
  )
  await mutation.update_push_preferences(
    _info(),
    UpdatePushPreferencesInput(
      environment=PushEnvironment.SANDBOX,
      app_bundle_id=BUNDLE_ID,
      device_install_id=INSTALL_ID,
      preferences=[
        PushCategoryPreferenceInput(
          category=PushCategory.CONNECTION_DATA,
          enabled=True,
        )
      ],
    ),
  )

  async with notification_database() as db:
    queued = await PushNotificationService(
      db, signing_key=SIGNING_KEY
    ).enqueue_event(
      user_id="user-1",
      account_id="ACCOUNT-1",
      category="CONNECTION_DATA",
      route_type="system.status",
    )
    await db.commit()
  assert len(queued) == 1

  route = await NotificationQuery().notification_event_route(
    _info(),
    queued[0].event_id,
  )
  assert route is not None
  assert route.route_type.value == "system.status"
  assert route.expired is False
  assert (
    await NotificationQuery().notification_event_route(
      _info(_principal(session_id="session-native-2")),
      queued[0].event_id,
    )
    is None
  )
  async with notification_database() as db:
    event = await db.get(IosNotificationEvent, queued[0].event_id)
    event.expires_at = utcnow() - timedelta(seconds=1)
    await db.commit()
  expired_route = await NotificationQuery().notification_event_route(
    _info(), queued[0].event_id
  )
  assert expired_route is not None and expired_route.expired is True

  payload = build_minimal_apns_payload(
    event_id=queued[0].event_id,
    category="CONNECTION_DATA",
    route_type="system.status",
  )
  assert set(payload) == {"aps", "eventId", "category", "route"}
  assert set(payload["aps"]) == {"alert", "sound"}
  serialized = str(payload).lower()
  for forbidden in (
    "account-1",
    "600000",
    "price",
    "volume",
    "amount",
    "strategy",
    DEVICE_TOKEN,
    "critical",
  ):
    assert forbidden not in serialized


@pytest.mark.asyncio
async def test_default_disabled_category_does_not_create_event(
  notification_database,
):
  await NotificationMutation().register_push_device(
    _info(),
    RegisterPushDeviceInput(
      device_token=DEVICE_TOKEN,
      environment=PushEnvironment.SANDBOX,
      app_bundle_id=BUNDLE_ID,
      app_version="1.0",
      device_install_id=INSTALL_ID,
    ),
  )
  async with notification_database() as db:
    queued = await PushNotificationService(
      db, signing_key=SIGNING_KEY
    ).enqueue_event(
      user_id="user-1",
      account_id="ACCOUNT-1",
      category="CONNECTION_DATA",
      route_type="system.status",
    )
    await db.commit()
    event_count = await db.scalar(select(func.count()).select_from(IosNotificationEvent))
  assert queued == ()
  assert event_count == 0


@pytest.mark.asyncio
async def test_unregister_purges_ciphertext_and_discards_pending_delivery(
  notification_database,
):
  mutation = NotificationMutation()
  await mutation.register_push_device(
    _info(),
    RegisterPushDeviceInput(
      device_token=DEVICE_TOKEN,
      environment=PushEnvironment.PRODUCTION,
      app_bundle_id=BUNDLE_ID,
      app_version="1.0",
      device_install_id=INSTALL_ID,
    ),
  )
  async with notification_database() as db:
    queued = await PushNotificationService(
      db, signing_key=SIGNING_KEY
    ).enqueue_event(
      user_id="user-1",
      account_id="ACCOUNT-1",
      category="ACTION_REQUIRED",
      route_type="today.action",
    )
    await db.commit()
  assert len(queued) == 1

  input = UnregisterPushDeviceInput(
    environment=PushEnvironment.PRODUCTION,
    app_bundle_id=BUNDLE_ID,
    device_install_id=INSTALL_ID,
  )
  assert (await mutation.unregister_push_device(_info(), input)).success is True
  assert (await mutation.unregister_push_device(_info(), input)).success is False
  async with notification_database() as db:
    registration = (await db.execute(select(IosPushRegistration))).scalar_one()
    outbox = (await db.execute(select(IosNotificationOutbox))).scalar_one()
    assert registration.invalidated_at is not None
    assert registration.token_ciphertext is None
    assert outbox.status == "DISCARDED"
    assert outbox.last_error_code == "DEVICE_UNREGISTERED"


@pytest.mark.asyncio
async def test_sensitive_write_revalidates_revoked_session(
  notification_database,
):
  async with notification_database() as db:
    session = await db.get(AuthDeviceSession, "session-native-1")
    session.revoked_at = utcnow()
    await db.commit()

  with pytest.raises(AuthError) as caught:
    await NotificationMutation().register_push_device(
      _info(),
      RegisterPushDeviceInput(
        device_token=DEVICE_TOKEN,
        environment=PushEnvironment.SANDBOX,
        app_bundle_id=BUNDLE_ID,
        app_version="1.0",
        device_install_id=INSTALL_ID,
      ),
    )
  assert caught.value.code == "UNAUTHENTICATED"


@pytest.mark.asyncio
async def test_legacy_web_mutation_compatibility_uses_current_security_contract(
  notification_database,
):
  result = await NotificationMutation().register_push_device(
    _info(
      _principal(
        session_id="session-web-1",
        permissions=frozenset({"mutation:write"}),
        native_session=False,
      )
    ),
    RegisterPushDeviceInput(
      device_token=DEVICE_TOKEN,
      environment=PushEnvironment.SANDBOX,
      app_bundle_id=BUNDLE_ID,
      app_version="1.0",
      device_install_id=INSTALL_ID,
    ),
  )
  assert result.device_install_id == INSTALL_ID


@pytest.mark.asyncio
async def test_invalid_device_token_error_never_echoes_sensitive_input(
  notification_database,
):
  sensitive_invalid_token = "not-a-device-token-secret"
  async with notification_database() as db:
    with pytest.raises(AuthError) as caught:
      await PushNotificationService(db, signing_key=SIGNING_KEY).register(
        user_id="user-1",
        device_session_id="session-native-1",
        account_id="ACCOUNT-1",
        device_install_id=INSTALL_ID,
        app_bundle_id=BUNDLE_ID,
        app_version="1.0",
        apns_environment="SANDBOX",
        device_token=sensitive_invalid_token,
      )
  assert caught.value.code == "INVALID_PUSH_DEVICE"
  assert sensitive_invalid_token not in str(caught.value)


def test_invalid_event_id_error_never_echoes_untrusted_input():
  sensitive_invalid_token = "not-an-event-id-secret"
  with pytest.raises(AuthError) as caught:
    build_minimal_apns_payload(
      event_id=sensitive_invalid_token,
      category="ACTION_REQUIRED",
      route_type="today.action",
    )
  assert sensitive_invalid_token not in str(caught.value)
