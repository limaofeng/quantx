export const SIGNAL_DIAGNOSTIC_WINDOW_MS = 20 * 24 * 60 * 60 * 1_000;

export type RollingDiagnosticRange = {
  startTime: string;
  endTime: string;
};

export function createRollingDiagnosticRange(
  now = new Date()
): RollingDiagnosticRange {
  const endMilliseconds = now.getTime();
  if (!Number.isFinite(endMilliseconds)) {
    throw new Error('diagnostic range requires a valid time');
  }
  return {
    startTime: new Date(
      endMilliseconds - SIGNAL_DIAGNOSTIC_WINDOW_MS
    ).toISOString(),
    endTime: new Date(endMilliseconds).toISOString(),
  };
}

export type CandidateTraceIdentity = {
  accountId: string;
  strategyRunId: string;
  candidateId: string;
};

export function hasCandidateTraceIdentity<T extends CandidateTraceIdentity>(
  value: T | null | undefined,
  expected: CandidateTraceIdentity
): value is T {
  return Boolean(
    value &&
      value.accountId === expected.accountId &&
      value.strategyRunId === expected.strategyRunId &&
      value.candidateId === expected.candidateId
  );
}

export type DiagnosticVersionCoordinate = {
  policyVersion: string;
  featureSchemaVersion: string;
  profileVersion?: string | null;
};

export type VersionedSignalEvaluation = {
  policyVersion: string;
  signalSnapshot?: {
    featureSchemaVersion?: string | null;
    profileVersion?: string | null;
  } | null;
};

export function matchesDiagnosticVersion(
  evaluation: VersionedSignalEvaluation,
  partition: DiagnosticVersionCoordinate
) {
  const snapshot = evaluation.signalSnapshot;
  return Boolean(
    snapshot &&
      evaluation.policyVersion === partition.policyVersion &&
      snapshot.featureSchemaVersion === partition.featureSchemaVersion &&
      (snapshot.profileVersion || null) === (partition.profileVersion || null)
  );
}

export type TraceRelatedIdGroup = {
  key: string;
  ids: readonly string[];
};

const MAX_RELATED_ID_GROUPS = 32;
const MAX_RELATED_IDS_PER_GROUP = 100;
const MAX_RELATED_ID_LENGTH = 160;

function isPlainObject(value: unknown): value is Record<string, unknown> {
  if (value === null || typeof value !== 'object' || Array.isArray(value)) {
    return false;
  }
  try {
    const prototype = Object.getPrototypeOf(value);
    return prototype === Object.prototype || prototype === null;
  } catch {
    return false;
  }
}

export function traceRelatedIdGroups(
  value: unknown
): readonly TraceRelatedIdGroup[] {
  if (!isPlainObject(value)) return [];

  return Object.entries(value)
    .slice(0, MAX_RELATED_ID_GROUPS)
    .map(([key, raw]) => {
      const candidates =
        typeof raw === 'string'
          ? [raw]
          : Array.isArray(raw)
            ? raw.filter((item): item is string => typeof item === 'string')
            : [];
      const ids = candidates
        .map(item => item.trim().slice(0, MAX_RELATED_ID_LENGTH))
        .filter(Boolean)
        .slice(0, MAX_RELATED_IDS_PER_GROUP);
      return { key, ids };
    })
    .filter(group => group.ids.length > 0);
}
