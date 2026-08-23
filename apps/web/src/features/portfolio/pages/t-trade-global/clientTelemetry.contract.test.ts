import { readFileSync } from 'node:fs';

import { buildSchema, parse, validate } from 'graphql';
import { describe, expect, it } from 'vitest';

function source(relativePath: string) {
  return readFileSync(new URL(relativePath, import.meta.url), 'utf8');
}

describe('T trade V3 client telemetry contract', () => {
  it('hard-codes the bounded Web labels in the GraphQL operation', () => {
    const operations = source('../../hooks/useTTradeGlobal.ts');
    const telemetryBlock = operations
      .split('export const RecordTTradeClientTelemetryMutation', 2)[1]
      .split('export const ApproveTTradeEntryV3Mutation', 2)[0];
    const telemetryDocument = telemetryBlock
      .split('gql(`', 2)[1]
      .split('`);', 2)[0];

    expect(telemetryDocument).toContain('platform: WEB');
    expect(telemetryDocument).toContain('surface: T_TRADE_SIGNAL_V3');
    for (const event of [
      'REFRESH_SUCCESS',
      'REFRESH_FAILURE',
      'SUBSCRIPTION_RECONNECTED',
    ]) {
      expect(telemetryDocument).toContain(`event: ${event}`);
    }
    for (const forbidden of ['errorText', 'stockCode', 'userLabel']) {
      expect(telemetryDocument).not.toContain(forbidden);
    }
    const schema = buildSchema(
      source('../../../../../../docs/public/contracts/graphql-schema.graphql')
    );
    expect(validate(schema, parse(telemetryDocument))).toEqual([]);
  });

  it('reports from refresh and recovery paths without recursive refetch', () => {
    const page = source('../TTradeGlobalPage.tsx');
    const reporter = page
      .split('const reportClientTelemetry = React.useMemo', 2)[1]
      .split('const [saveResult', 2)[0];

    expect(page).toContain("reportClientTelemetry('SUBSCRIPTION_RECONNECTED')");
    expect(page).toContain(
      "window.addEventListener('online', refreshIfVisible)"
    );
    expect(page).toContain('signalRefreshTelemetryRef.current');
    expect(reporter).toContain('recordClientTelemetry');
    expect(reporter).not.toContain('refreshVisibleData');
    expect(reporter).not.toContain('refreshSignalEvaluations');
  });
  it('binds reconcile retries to a client operation until terminal outcome', () => {
    const operations = source('../../hooks/useTTradeGlobal.ts');
    expect(operations).toContain(
      'mutation Portfolio_ReconcileTTradeGlobalMonitor('
    );
    expect(operations).toContain('$idempotencyKey: String!');
    expect(operations).toContain('idempotencyKey: $idempotencyKey');

    const page = source('../TTradeGlobalPage.tsx');
    expect(page).toContain('const reconcileOperationRef = React.useRef');
    expect(page).toContain('reconcileOperationRef.current = null');

    const reconcile = page.slice(
      page.indexOf('const handleReconcile'),
      page.indexOf('const handleSignal')
    );
    expect(reconcile).toContain(
      "String(payload.code || '').endsWith('_COMMAND_PENDING')"
    );
    expect(reconcile).toContain(
      "String(payload.code || '').endsWith('_OUTCOME_UNKNOWN')"
    );
    expect(
      reconcile.indexOf('persistUncertainOperation(`reconcile:${accountId}`')
    ).toBeLessThan(reconcile.indexOf('await reconcileMonitor'));
  });

  it('reuses replay and approval operation keys until terminal outcomes', () => {
    const page = source('../TTradeGlobalPage.tsx');
    expect(page).toContain('const replayOperationRef = React.useRef');
    const replay = page.slice(
      page.indexOf('const handleStart'),
      page.indexOf('const handleCancel')
    );
    expect(replay).toContain(
      "String(payload.code || '').endsWith('_COMMAND_PENDING')"
    );
    expect(replay).toContain(
      "String(payload.code || '').endsWith('_OUTCOME_UNKNOWN')"
    );
    expect(page).toContain(
      'input: { ...input, idempotencyKey: operation.idempotencyKey }'
    );
    expect(page).toContain('persistUncertainOperation');
    expect(page).toContain('readUncertainOperation');
    expect(page).toContain('previousOperation?.uncertain');
    expect(page).toContain('previousOperation.identity !== identity');
    expect(page).toContain('const approveOperationRef = React.useRef');
    expect(page).toContain(
      'approveOperationRef.current.set(approvalKey, operation)'
    );
    const approval = page.slice(
      page.indexOf('const handleSignal'),
      page.indexOf('const handleImportExternalEntry')
    );
    expect(approval).toContain(
      "String(payload.code || '').endsWith('_COMMAND_PENDING')"
    );
    expect(approval).toContain(
      "String(payload.code || '').endsWith('_OUTCOME_UNKNOWN')"
    );
    expect(page).toContain('existingOperation?.uncertain');
    expect(page).toContain('controlledWindowOperationRef.current');
    expect(page).toContain('idempotencyKey: operation.idempotencyKey');
    expect(page).toContain('activateLiveOperationRef.current');
    expect(page).toContain('killSwitchOperationRef.current');
  });

  it('keeps unknown approval command-pending codes retryable', () => {
    const page = source('../TTradeGlobalPage.tsx');
    const approval = page.slice(
      page.indexOf('const handleSignal'),
      page.indexOf('const handleImportExternalEntry')
    );

    expect(approval).toContain(
      "String(payload.code || '').endsWith('_COMMAND_PENDING')"
    );
    expect(approval).toContain(
      "String(payload.code || '').endsWith('_OUTCOME_UNKNOWN')"
    );
  });

  it('does not let an unknown high-risk operation change identity', () => {
    const page = source('../TTradeGlobalPage.tsx');
    expect(page).toContain('上一笔审批结果未知');
    expect(page).toContain('上一笔账户窗口结果未知');
    expect(page).toContain('上一笔实盘提升结果未知');
    expect(page).toContain('上一笔紧急停止结果未知');
    expect(page).toContain('上一笔回放结果未知');
    expect(page).toContain(
      'const operationScope = `activate-live:${accountId}`'
    );
    expect(page).not.toContain('targetStage,\n      confirmation,');
  });

  it('binds controlled-window and LIVE operations to the confirming snapshot and policy', () => {
    const operations = source('../../hooks/useTTradeGlobal.ts');
    const begin = operations.slice(
      operations.indexOf('export const BeginTTradeControlledWindowMutation'),
      operations.indexOf('export const ActivateTTradeLiveMutation')
    );
    expect(begin).toContain('$policyVersion: Int!');
    expect(begin).toContain('policyVersion: $policyVersion');
    expect(begin).toContain('$snapshotId: String!');
    expect(begin).toContain('snapshotId: $snapshotId');

    const activate = operations.slice(
      operations.indexOf('export const ActivateTTradeLiveMutation')
    );
    expect(activate).toContain('$policyVersion: Int!');
    expect(activate).toContain('policyVersion: $policyVersion');
    expect(activate).toContain('$snapshotId: String!');
    expect(activate).toContain('snapshotId: $snapshotId');

    const page = source('../TTradeGlobalPage.tsx');
    const beginHandler = page.slice(
      page.indexOf('const handleBeginAccountExecutionWindow'),
      page.indexOf('const handleActivateLive')
    );
    expect(
      beginHandler.match(/snapshotId: readiness\.snapshotId/g)?.length
    ).toBeGreaterThanOrEqual(2);
    expect(beginHandler).toContain('policyVersion: readiness.policyVersion');

    const activation = page.slice(
      page.indexOf('const handleActivateLive'),
      page.indexOf('const handlePauseEntries')
    );
    expect(
      activation.match(/snapshotId: readiness\.snapshotId/g)?.length
    ).toBeGreaterThanOrEqual(1);
    expect(activation).toContain('policyVersion: readiness.policyVersion');
    expect(activation).toContain('snapshotId: readiness.snapshotId');
  });

  it('keeps the LIVE confirmation phrase out of the persisted identity', () => {
    const page = source('../TTradeGlobalPage.tsx');
    const activation = page.slice(
      page.indexOf('const handleActivateLive'),
      page.indexOf('const handlePauseEntries')
    );
    const identityStart = activation.indexOf('const identity = JSON.stringify');
    const identityEnd = activation.indexOf('});', identityStart);
    const identityBlock = activation.slice(identityStart, identityEnd);

    expect(identityBlock).toContain('targetStage');
    expect(identityBlock).not.toContain('confirmation');
    expect(activation).toContain('confirmation,');
  });
});
