import { useEffect, useMemo, useState } from 'react';
import { useQuery } from 'urql';

import { gql } from '@/generated/gql';
import {
  LimitUpRadarSortField,
  LimitUpRadarStage,
  StockScreenSortDirection,
} from '@/generated/gql/graphql';

export type RadarStage =
  | 'MOMENTUM'
  | 'SURGING'
  | 'NEAR_LIMIT'
  | 'TOUCHING'
  | 'SEALED'
  | 'BROKEN'
  | 'RESEALED';

export interface RadarScoreFactor {
  code: string;
  explanation: string;
  label: string;
  maxScore: number;
  score: number;
}

export interface RadarEvent {
  eventId: string;
  occurredAt: string;
  score: number;
  stage: RadarStage;
  stageLabel: string;
}

export interface RadarCandidate {
  amount: number;
  amountPaceRatio: number;
  ask1Price?: number | null;
  ask1Volume: number;
  bid1Price?: number | null;
  bid1Volume: number;
  blockedReasons: string[];
  breakCount: number;
  canCreateInstance: boolean;
  changePct: number;
  code: string;
  currentPrice: number;
  depthImbalance5: number;
  distanceToLimitPct: number;
  distanceToLimitTicks: number;
  events: RadarEvent[];
  existingInstanceId?: string | null;
  firstSealedAt?: string | null;
  firstTouchAt?: string | null;
  industry?: string | null;
  intradayTurnoverRatePct?: number | null;
  isStale: boolean;
  last5mVolumeRatio: number;
  lastStageAt?: string | null;
  limitUpPrice: number;
  name: string;
  oneWordLimitUp: boolean;
  priceChange5mPct: number;
  radarScore: number;
  scoreBreakdown: RadarScoreFactor[];
  scoreVersion: string;
  stage: RadarStage;
  stageLabel: string;
  updatedAt: string;
  volumePaceRatio: number;
  boardSegment: string;
  promotionObserved: boolean;
  promotionEligible: boolean;
  promotionScore: number;
  promotionModelVersion: string;
  exitPolicyVersion: string;
  promotionSnapshotVersion: string;
  normalizedLimitProgress: number;
  firstBoardCloseProbability: number;
  nextDayLimitTouchProbability: number;
  nextDayLimitSealProbability: number;
  expectedNetReturnPct: number;
  cvar95LossPct: number;
  highPositionType: string;
  candidatePreference?: string | null;
  promotionFactors: Array<{
    code: string;
    contribution: number;
    explanation: string;
    label: string;
  }>;
  researchArtifact?: {
    artifactId: string;
    status: string;
    summary: string;
    catalysts: string[];
    announcementRisks: string[];
    citations: string[];
    dataGaps: string[];
    confidenceNote: string;
    inputSnapshotVersion: string;
    generatedAt: string;
  } | null;
}

export interface RadarIndustryHeat {
  averageScore: number;
  candidateCount: number;
  industry: string;
  nearLimitCount: number;
  sealedCount: number;
}

export interface RadarSummary {
  brokenCount: number;
  candidateCount: number;
  excludedCount: number;
  nearLimitCount: number;
  scannedCount: number;
  sealedCount: number;
  staleCount: number;
  discoveredCount: number;
  eligibleCount: number;
}

const LIMIT_UP_RADAR_QUERY = gql(`
  query FirstBoardPromotionDesk($accountId: String!, $input: LimitUpRadarInput!) {
    firstBoardPromotionDesk(accountId: $accountId, input: $input) {
      total
      scoreVersion
      promotionModelVersion
      updatedAt
      isScannerRunning
      warnings
      chain {
        snapshotVersion
        maxBoardCount
        firstBoardCount
        sealedCount
        brokenCount
        breakRate
        promotionRate
      }
      summary {
        scannedCount
        candidateCount
        nearLimitCount
        sealedCount
        brokenCount
        staleCount
        excludedCount
        discoveredCount
        eligibleCount
      }
      industries {
        industry
        candidateCount
        nearLimitCount
        sealedCount
        averageScore
      }
      items {
        code
        name
        industry
        currentPrice
        changePct
        limitUpPrice
        distanceToLimitPct
        distanceToLimitTicks
        priceChange5mPct
        amount
        amountPaceRatio
        volumePaceRatio
        last5mVolumeRatio
        intradayTurnoverRatePct
        depthImbalance5
        bid1Price
        ask1Price
        bid1Volume
        ask1Volume
        stage
        stageLabel
        radarScore
        scoreVersion
        boardSegment
        promotionObserved
        promotionEligible
        promotionScore
        promotionModelVersion
        exitPolicyVersion
        promotionSnapshotVersion
        normalizedLimitProgress
        firstBoardCloseProbability
        nextDayLimitTouchProbability
        nextDayLimitSealProbability
        expectedNetReturnPct
        cvar95LossPct
        highPositionType
        candidatePreference
        promotionFactors {
          code
          label
          contribution
          explanation
        }
        researchArtifact {
          artifactId
          status
          summary
          catalysts
          announcementRisks
          citations
          dataGaps
          confidenceNote
          inputSnapshotVersion
          generatedAt
        }
        scoreBreakdown {
          code
          label
          score
          maxScore
          explanation
        }
        breakCount
        firstTouchAt
        firstSealedAt
        lastStageAt
        events {
          eventId
          stage
          stageLabel
          occurredAt
          score
        }
        oneWordLimitUp
        isStale
        blockedReasons
        canCreateInstance
        existingInstanceId
        updatedAt
      }
    }
  }
`);

const STAGE_INPUT: Record<RadarStage, LimitUpRadarStage> = {
  BROKEN: LimitUpRadarStage.Broken,
  MOMENTUM: LimitUpRadarStage.Momentum,
  NEAR_LIMIT: LimitUpRadarStage.NearLimit,
  RESEALED: LimitUpRadarStage.Resealed,
  SEALED: LimitUpRadarStage.Sealed,
  SURGING: LimitUpRadarStage.Surging,
  TOUCHING: LimitUpRadarStage.Touching,
};

const EMPTY_SUMMARY: RadarSummary = {
  brokenCount: 0,
  candidateCount: 0,
  excludedCount: 0,
  nearLimitCount: 0,
  scannedCount: 0,
  sealedCount: 0,
  staleCount: 0,
  discoveredCount: 0,
  eligibleCount: 0,
};

export function useLimitUpRadar(active: boolean, accountId?: string) {
  const [stage, setStage] = useState<RadarStage | 'ALL'>('ALL');
  const [industry, setIndustry] = useState('ALL');
  const [search, setSearch] = useState('');
  const input = useMemo(
    () => ({
      stages: stage === 'ALL' ? null : [STAGE_INPUT[stage]],
      includeIndustries: industry === 'ALL' ? null : [industry],
      minScore: null,
      search: search.trim() || null,
      sortField: LimitUpRadarSortField.Score,
      sortDirection: StockScreenSortDirection.Desc,
      limit: 200,
      offset: 0,
    }),
    [industry, search, stage]
  );
  const [result, refresh] = useQuery({
    query: LIMIT_UP_RADAR_QUERY,
    variables: { accountId: accountId || '__MARKET_ONLY__', input },
    pause: !active,
    requestPolicy: 'network-only',
  });

  useEffect(() => {
    if (!active) return;
    const intervalId = window.setInterval(() => {
      if (document.visibilityState === 'visible') {
        refresh({ requestPolicy: 'network-only' });
      }
    }, 3000);
    return () => window.clearInterval(intervalId);
  }, [active, refresh]);

  const page = result.data?.firstBoardPromotionDesk;
  return {
    candidates: (page?.items ?? []) as RadarCandidate[],
    error: result.error,
    fetching: result.fetching,
    industries: (page?.industries ?? []) as RadarIndustryHeat[],
    industry,
    isScannerRunning: Boolean(page?.isScannerRunning),
    chain: page?.chain ?? null,
    refresh,
    scoreVersion: page?.scoreVersion ?? 'limit-up-radar-v1',
    promotionModelVersion:
      page?.promotionModelVersion ?? 'first-board-promotion-v2-shadow-1',
    search,
    setIndustry,
    setSearch,
    setStage,
    stage,
    summary: (page?.summary ?? EMPTY_SUMMARY) as RadarSummary,
    total: page?.total ?? 0,
    updatedAt: page?.updatedAt ?? null,
    warnings: page?.warnings ?? [],
  };
}
