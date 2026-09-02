import type {
  StockSelectionDatasetVersion,
  StockSelectionTrainingBackend,
  StockSelectionTrainingInput,
  StockSelectionTrainingUniverseKind,
} from '@/generated/gql/graphql';

export type UniverseDraft = {
  kind: StockSelectionTrainingUniverseKind;
  indexCode: string;
  stockCodes: string;
  benchmarkCode: string;
  minimumListingDays: number;
};

/** Small projection used by the standalone preflight summary and its tests. */
export type TrainingPreviewEvidence = {
  datasetVersion: string;
  requestedBackend: string;
  resolvedBackend: string;
  canSubmit: boolean;
  folds: readonly unknown[];
  coverage: unknown;
  leakage: unknown;
  resourceEstimate: unknown;
  shadowReasons: readonly string[];
  blockers: readonly string[];
  warnings: readonly string[];
  previewFingerprint?: string;
  coordinateHash?: string;
  specHash?: string;
};

export type PreviewState = {
  signature: string;
  preview: TrainingPreviewEvidence;
};

export type PreviewRequest = {
  input: StockSelectionTrainingInput;
  signature: string;
};

export type WizardInputDraft = {
  datasetVersion: string;
  dateStart: string;
  dateEnd: string;
  universe: UniverseDraft;
  backend: StockSelectionTrainingBackend;
  bootstrapSamples: number;
  randomSeed: number;
  workerBatchSize: number;
  note: string;
};

export type DatasetStateProps = {
  fetching: boolean;
  error?: { message: string };
  datasets: readonly StockSelectionDatasetVersion[];
  onRetry: () => void;
};
