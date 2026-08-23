import { beforeEach, describe, expect, it, vi } from 'vitest';

import { TTradeTimeExitMode } from '@/generated/gql/graphql';

import {
  clearPersistedOperation,
  persistUncertainOperation,
  readUncertainOperation,
  tTradeOperationMaxIdentityLength,
  tTradeOperationQuarantineKey,
  tTradeOperationStorageKey,
} from './operationPersistence';
import { defaultSignalPolicyForm, signalPolicyInput } from './signalPolicy';

const operation = (key: string, identity = 'candidate-v1') => ({
  identity,
  idempotencyKey: key,
  uncertain: true,
});

const defaultReplayInput = {
  accountId: 'account-a',
  startTime: '2026-08-03T09:30:00',
  endTime: '2026-08-07T15:00:00',
  initialPositions: [],
  targetTradeAmount: 10000,
  maxTradeAmount: 12000,
  maxConcurrentBatches: 3,
  maxTotalTExposurePct: 0.1,
  signalPolicy: signalPolicyInput(defaultSignalPolicyForm),
  maxPriceDeviationPct: 0.3,
  targetProfitPct: 2,
  baseFloorPct: 0.5,
  initialGapPct: 1.5,
  trailingGapSlope: 0.25,
  maxGapPct: 3,
  highProfitLockEnabled: true,
  highProfitArmPct: 4,
  highProfitMaxDrawdownPct: 1.2,
  rapidReversalEnabled: true,
  rapidReversalWindowSeconds: 15,
  rapidReversalDrawdownPct: 0.8,
  rapidReversalConfirmTicks: 2,
  limitUpTouchExitEnabled: true,
  limitUpTouchToleranceTicks: 0,
  hardStopEnabled: false,
  hardStopPct: -0.8,
  timeExitMode: TTradeTimeExitMode.Unlimited,
  timeExitTime: '14:50',
  maxHoldingTradingDays: 5,
  cooldownSeconds: 300,
};

const defaultReplayIdentity = JSON.stringify(defaultReplayInput);

describe('T-trade client operation journal', () => {
  let values = new Map<string, string>();

  beforeEach(() => {
    values = new Map();
    vi.mocked(sessionStorage.getItem).mockImplementation(
      key => values.get(key) ?? null
    );
    vi.mocked(sessionStorage.setItem).mockImplementation((key, value) => {
      values.set(key, value);
    });
    vi.mocked(sessionStorage.removeItem).mockImplementation(key => {
      values.delete(key);
    });
    vi.mocked(sessionStorage.clear).mockImplementation(() => {
      values.clear();
    });
  });

  it('restores an unknown operation after a page refresh and keeps its key', () => {
    persistUncertainOperation(
      'approve:account-a:run-1:intent-1',
      operation('key-1')
    );

    // A new page has no in-memory ref, but the durable tab journal restores it.
    expect(readUncertainOperation('approve:account-a:run-1:intent-1')).toEqual(
      operation('key-1')
    );
  });

  it('persists and restores the complete default replay payload identity', () => {
    expect(defaultReplayIdentity.length).toBeGreaterThan(4096);
    expect(defaultReplayIdentity.length).toBeLessThanOrEqual(
      tTradeOperationMaxIdentityLength
    );

    const pending = operation('default-replay-key', defaultReplayIdentity);
    expect(persistUncertainOperation('replay:account-a', pending)).toBe(true);
    expect(readUncertainOperation('replay:account-a')).toEqual(pending);
  });

  it('keeps account scopes isolated and clears only a terminal scope', () => {
    persistUncertainOperation('begin-window:account-a', operation('key-a'));
    persistUncertainOperation('begin-window:account-b', operation('key-b'));

    expect(
      readUncertainOperation('begin-window:account-a')?.idempotencyKey
    ).toBe('key-a');
    expect(
      readUncertainOperation('begin-window:account-b')?.idempotencyKey
    ).toBe('key-b');

    clearPersistedOperation('begin-window:account-a');
    expect(readUncertainOperation('begin-window:account-a')).toBeNull();
    expect(
      readUncertainOperation('begin-window:account-b')?.idempotencyKey
    ).toBe('key-b');
  });

  it('bounds persisted uncertain operations', () => {
    for (let index = 0; index < 32; index += 1) {
      persistUncertainOperation(
        `reconcile:account-${index}`,
        operation(`key-${index}`)
      );
    }

    const records = JSON.parse(
      sessionStorage.getItem(tTradeOperationStorageKey) || '[]'
    ) as unknown[];
    expect(records).toHaveLength(32);
    expect(readUncertainOperation('reconcile:account-31')?.idempotencyKey).toBe(
      'key-31'
    );
    expect(readUncertainOperation('reconcile:account-0')?.idempotencyKey).toBe(
      'key-0'
    );
  });

  it('keeps a quarantine marker so malformed storage stays blocked', () => {
    sessionStorage.setItem(tTradeOperationStorageKey, '{broken');

    const firstRead = readUncertainOperation('kill-switch:account-a');
    const secondRead = readUncertainOperation('kill-switch:account-a');

    expect(firstRead?.blocked).toBe(true);
    expect(secondRead?.blocked).toBe(true);
    expect(sessionStorage.getItem(tTradeOperationQuarantineKey)).not.toBeNull();
    expect(sessionStorage.getItem(tTradeOperationStorageKey)).toBeNull();

    // Clearing one business scope must not release the global quarantine.
    expect(clearPersistedOperation('kill-switch:account-a')).toBe(false);
    expect(sessionStorage.getItem(tTradeOperationQuarantineKey)).not.toBeNull();
  });

  it('rejects invalid or cross-action scopes instead of writing them', () => {
    expect(
      persistUncertainOperation(
        'activate-live:account-a:UNKNOWN',
        operation('key')
      )
    ).toBe(false);
    expect(
      persistUncertainOperation('approve:account-a:run-only', operation('key'))
    ).toBe(false);
    expect(sessionStorage.getItem(tTradeOperationStorageKey)).toBeNull();
    expect(
      readUncertainOperation('activate-live:account-a:UNKNOWN')?.blocked
    ).toBe(true);
  });

  it('refuses a 33rd scope without evicting any existing uncertain operation', () => {
    for (let index = 0; index < 32; index += 1) {
      expect(
        persistUncertainOperation(
          `reconcile:account-${index}`,
          operation(`key-${index}`)
        )
      ).toBe(true);
    }

    expect(
      persistUncertainOperation('reconcile:account-32', operation('key-32'))
    ).toBe(false);
    for (let index = 0; index < 32; index += 1) {
      expect(
        readUncertainOperation(`reconcile:account-${index}`)?.idempotencyKey
      ).toBe(`key-${index}`);
    }
    expect(readUncertainOperation('reconcile:account-32')).toBeNull();
  });

  it('enforces the service idempotency key boundary at 128 characters', () => {
    expect(
      persistUncertainOperation(
        'reconcile:account-128',
        operation('k'.repeat(128))
      )
    ).toBe(true);
    expect(
      persistUncertainOperation(
        'reconcile:account-129',
        operation('k'.repeat(129))
      )
    ).toBe(false);
    expect(
      readUncertainOperation('reconcile:account-128')?.idempotencyKey
    ).toHaveLength(128);
    expect(readUncertainOperation('reconcile:account-129')).toBeNull();
  });

  it('enforces the identity N/N+1 boundary without replacing the valid record', () => {
    const maxIdentity = 'i'.repeat(tTradeOperationMaxIdentityLength);
    const overIdentity = `${maxIdentity}i`;

    expect(
      persistUncertainOperation(
        'reconcile:identity-max',
        operation('identity-max-key', maxIdentity)
      )
    ).toBe(true);
    expect(
      persistUncertainOperation(
        'reconcile:identity-over',
        operation('identity-over-key', overIdentity)
      )
    ).toBe(false);
    expect(
      readUncertainOperation('reconcile:identity-max')?.identity
    ).toHaveLength(tTradeOperationMaxIdentityLength);
    expect(readUncertainOperation('reconcile:identity-over')).toBeNull();
  });

  it('refuses a journal that would exceed the total storage bound without eviction', () => {
    const largeIdentity = 'j'.repeat(tTradeOperationMaxIdentityLength);
    const acceptedScopes: string[] = [];
    let rejectedScope: string | null = null;

    for (let index = 0; index < 32; index += 1) {
      const scope = `reconcile:large-account-${index}`;
      if (
        persistUncertainOperation(
          scope,
          operation(`large-key-${index}`, largeIdentity)
        )
      ) {
        acceptedScopes.push(scope);
      } else {
        rejectedScope = scope;
        break;
      }
    }

    expect(acceptedScopes.length).toBeGreaterThan(1);
    expect(rejectedScope).not.toBeNull();
    for (const scope of acceptedScopes) {
      expect(readUncertainOperation(scope)?.identity).toHaveLength(
        tTradeOperationMaxIdentityLength
      );
    }
    expect(
      rejectedScope ? readUncertainOperation(rejectedScope) : null
    ).toBeNull();
    const records = JSON.parse(
      sessionStorage.getItem(tTradeOperationStorageKey) || '[]'
    ) as unknown[];
    expect(records).toHaveLength(acceptedScopes.length);
  });
});
