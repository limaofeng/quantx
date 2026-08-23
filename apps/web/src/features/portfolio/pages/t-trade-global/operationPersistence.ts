export type ClientOperationRef = {
  identity: string;
  idempotencyKey: string;
  uncertain: boolean;
  /**
   * Set only when the browser-side journal could not be trusted.  Callers
   * must fail closed instead of creating a second mutation in that case.
   */
  blocked?: boolean;
};

const STORAGE_KEY = 'quantx:t-trade:operations:v1';
const QUARANTINE_KEY = 'quantx:t-trade:operations:v1:quarantine';
const MAX_RECORDS = 32;
const MAX_ACCOUNT_LENGTH = 96;
const MAX_SUBJECT_LENGTH = 256;
// Replay identity is the canonical JSON payload sent by the page.  The
// current GraphQL input has a finite schema (including the fixed signal-policy
// field/enum sets), and the complete default payload is already >4 KiB.  Keep
// a generous, explicit per-operation bound while retaining the smaller total
// journal bound below.
export const tTradeOperationMaxIdentityLength = 16 * 1024;
const MAX_IDENTITY_LENGTH = tTradeOperationMaxIdentityLength;
const MAX_IDEMPOTENCY_KEY_LENGTH = 128;
const MAX_STORAGE_LENGTH = 256 * 1024;

const OPERATION_ACTIONS = new Set([
  'replay',
  'reconcile',
  'approve',
  'begin-window',
  'activate-live',
  'kill-switch',
]);

function hasControlCharacters(value: string) {
  return Array.from(value).some(character => {
    const code = character.charCodeAt(0);
    return code <= 31 || code === 127;
  });
}

type PersistedRecord = {
  action: string;
  accountId: string;
  subject?: string;
  operation: ClientOperationRef;
  updatedAt: number;
};

type OperationScope = {
  action: string;
  accountId: string;
  subject?: string;
};

function isSafeToken(value: string, maxLength: number) {
  return (
    value.length > 0 &&
    value.length <= maxLength &&
    !hasControlCharacters(value) &&
    !/\s/.test(value) &&
    !/[<>"'`\\]/.test(value)
  );
}

function isValidScope(scope: OperationScope): boolean {
  if (
    !OPERATION_ACTIONS.has(scope.action) ||
    !isSafeToken(scope.accountId, MAX_ACCOUNT_LENGTH)
  ) {
    return false;
  }
  if (scope.subject !== undefined) {
    if (!isSafeToken(scope.subject, MAX_SUBJECT_LENGTH)) return false;
    if (scope.action === 'approve') {
      const parts = scope.subject.split(':');
      if (
        parts.length !== 2 ||
        !parts.every(part => isSafeToken(part, MAX_SUBJECT_LENGTH))
      ) {
        return false;
      }
    }
    if (
      scope.action === 'activate-live' &&
      !['CANARY', 'LIVE'].includes(scope.subject)
    ) {
      return false;
    }
  } else if (scope.action === 'approve') {
    return false;
  }
  return true;
}

function parseScope(scope: string): OperationScope | null {
  if (typeof scope !== 'string' || scope.length > 512) return null;
  const separator = scope.indexOf(':');
  if (separator <= 0) return null;
  const action = scope.slice(0, separator);
  const remainder = scope.slice(separator + 1);
  if (!OPERATION_ACTIONS.has(action) || !remainder) return null;

  const accountSeparator = remainder.indexOf(':');
  const accountId =
    accountSeparator === -1 ? remainder : remainder.slice(0, accountSeparator);
  const subject =
    accountSeparator === -1 ? undefined : remainder.slice(accountSeparator + 1);
  const parsed =
    subject === '' ? { action, accountId } : { action, accountId, subject };
  return isValidScope(parsed) ? parsed : null;
}

function sameScope(left: PersistedRecord, right: OperationScope) {
  return (
    left.action === right.action &&
    left.accountId === right.accountId &&
    left.subject === right.subject
  );
}

function isValidOperation(operation: ClientOperationRef): boolean {
  return (
    typeof operation.identity === 'string' &&
    operation.identity.length > 0 &&
    operation.identity.length <= MAX_IDENTITY_LENGTH &&
    !hasControlCharacters(operation.identity) &&
    typeof operation.idempotencyKey === 'string' &&
    isSafeToken(operation.idempotencyKey, MAX_IDEMPOTENCY_KEY_LENGTH) &&
    typeof operation.uncertain === 'boolean' &&
    (operation.blocked === undefined || operation.blocked === true)
  );
}

function storage(): Storage | null {
  if (typeof window === 'undefined') return null;
  try {
    return window.sessionStorage;
  } catch {
    return null;
  }
}

function removeStorage(store: Storage) {
  try {
    store.removeItem(STORAGE_KEY);
  } catch {
    // The caller still keeps the operation in memory for this page.
  }
}

function isQuarantined(store: Storage) {
  try {
    // The marker is deliberately opaque: even a malformed marker means the
    // journal is not trusted until an explicit operator clears it.
    return store.getItem(QUARANTINE_KEY) !== null;
  } catch {
    return true;
  }
}

function quarantineStorage(store: Storage, reason: string) {
  try {
    store.setItem(
      QUARANTINE_KEY,
      JSON.stringify({
        version: 1,
        reason: reason.slice(0, 96),
        quarantinedAt: Date.now(),
      })
    );
    // Once the marker is durable, the untrusted payload is no longer needed.
    // If removal fails, the marker still keeps every future access blocked.
    removeStorage(store);
  } catch {
    // Keep the damaged raw value when the marker cannot be written.  The next
    // read will retry quarantine and will not treat it as an empty journal.
  }
}

function readRecords(store: Storage): PersistedRecord[] | null {
  if (isQuarantined(store)) return null;
  let raw: string | null;
  try {
    raw = store.getItem(STORAGE_KEY);
  } catch {
    return null;
  }
  if (raw === null) return [];
  if (raw.length > MAX_STORAGE_LENGTH) {
    quarantineStorage(store, 'storage-too-large');
    return null;
  }
  try {
    const parsed: unknown = JSON.parse(raw);
    if (!Array.isArray(parsed) || parsed.length > MAX_RECORDS) {
      quarantineStorage(store, 'invalid-record-list');
      return null;
    }
    const records = parsed as PersistedRecord[];
    if (
      records.some(
        record =>
          !record ||
          typeof record !== 'object' ||
          !isValidScope(record) ||
          !isValidOperation(record.operation) ||
          !Number.isFinite(record.updatedAt)
      )
    ) {
      quarantineStorage(store, 'invalid-record');
      return null;
    }
    return records;
  } catch {
    quarantineStorage(store, 'invalid-json');
    return null;
  }
}

/**
 * Reads an uncertain operation by its strict account/action scope.  Invalid
 * or tampered storage is deleted and returns a blocked sentinel so callers
 * cannot silently issue a second high-risk mutation.
 */
export function readUncertainOperation(
  scope: string
): ClientOperationRef | null {
  const parsedScope = parseScope(scope);
  if (!parsedScope) {
    return {
      identity: '',
      idempotencyKey: '',
      uncertain: true,
      blocked: true,
    };
  }
  const store = storage();
  if (!store) return null;
  const records = readRecords(store);
  if (records === null) {
    return {
      identity: '',
      idempotencyKey: '',
      uncertain: true,
      blocked: true,
    };
  }
  const record = records.find(item => sameScope(item, parsedScope));
  if (!record || !record.operation.uncertain) return null;
  return { ...record.operation };
}

/**
 * Stores only an uncertain operation.  The journal is intentionally bounded;
 * newest records win and account/action scopes are exact, preventing a
 * previous account from supplying the current account's retry key.
 */
export function persistUncertainOperation(
  scope: string,
  operation: ClientOperationRef
) {
  const parsedScope = parseScope(scope);
  const store = storage();
  if (
    !parsedScope ||
    !store ||
    !operation.uncertain ||
    !isValidOperation(operation)
  ) {
    return false;
  }
  const records = readRecords(store);
  if (records === null) return false;
  const existingIndex = records.findIndex(item => sameScope(item, parsedScope));
  if (existingIndex === -1 && records.length >= MAX_RECORDS) return false;
  const next =
    existingIndex === -1
      ? [...records]
      : records.filter((_, index) => index !== existingIndex);
  const latestUpdatedAt = records.reduce(
    (latest, item) => Math.max(latest, item.updatedAt),
    0
  );
  next.push({
    ...parsedScope,
    operation: {
      identity: operation.identity,
      idempotencyKey: operation.idempotencyKey,
      uncertain: true,
    },
    updatedAt: Math.max(Date.now(), latestUpdatedAt + 1),
  });
  next.sort((left, right) => right.updatedAt - left.updatedAt);
  try {
    const serialized = JSON.stringify(next);
    if (serialized.length > MAX_STORAGE_LENGTH) {
      return false;
    }
    store.setItem(STORAGE_KEY, serialized);
    if (store.getItem(STORAGE_KEY) !== serialized) {
      quarantineStorage(store, 'write-verification-failed');
      return false;
    }
    return true;
  } catch {
    quarantineStorage(store, 'write-failed');
    return false;
  }
}

export function clearPersistedOperation(scope: string) {
  const parsedScope = parseScope(scope);
  const store = storage();
  if (!parsedScope || !store) return false;
  const records = readRecords(store);
  if (records === null) return false;
  const next = records.filter(item => !sameScope(item, parsedScope));
  try {
    if (next.length === 0) removeStorage(store);
    else store.setItem(STORAGE_KEY, JSON.stringify(next));
    return true;
  } catch {
    quarantineStorage(store, 'clear-failed');
    return false;
  }
}

export const tTradeOperationStorageKey = STORAGE_KEY;
export const tTradeOperationQuarantineKey = QUARANTINE_KEY;
