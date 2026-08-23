/* eslint-disable */
import { TypedDocumentNode as DocumentNode } from '@graphql-typed-document-node/core';
export type Maybe<T> = T | null;
export type InputMaybe<T> = T | null | undefined;
export type Exact<T extends { [key: string]: unknown }> = { [K in keyof T]: T[K] };
export type MakeOptional<T, K extends keyof T> = Omit<T, K> & { [SubKey in K]?: Maybe<T[SubKey]> };
export type MakeMaybe<T, K extends keyof T> = Omit<T, K> & { [SubKey in K]: Maybe<T[SubKey]> };
export type MakeEmpty<T extends { [key: string]: unknown }, K extends keyof T> = { [_ in K]?: never };
export type Incremental<T> = T | { [P in keyof T]?: P extends ' $fragmentName' | '__typename' ? T[P] : never };
/** All built-in and custom scalars, mapped to their actual values */
export type Scalars = {
  ID: { input: string; output: string; }
  String: { input: string; output: string; }
  Boolean: { input: boolean; output: boolean; }
  Int: { input: number; output: number; }
  Float: { input: number; output: number; }
  /** Date (isoformat) */
  Date: { input: string; output: string; }
  /** Date with time (isoformat) */
  DateTime: { input: string; output: string; }
  /** The `JSON` scalar type represents JSON values as specified by [ECMA-404](https://ecma-international.org/wp-content/uploads/ECMA-404_2nd_edition_december_2017.pdf). */
  JSON: { input: any; output: any; }
};

/** 添加自选股输入 */
export type AddWatchlistItemInput = {
  /** 资金账号 */
  accountId?: InputMaybe<Scalars['String']['input']>;
  /** 排序 */
  displayOrder?: InputMaybe<Scalars['Int']['input']>;
  /** 分组 */
  groupName?: InputMaybe<Scalars['String']['input']>;
  /** 证券名称 */
  instrumentName?: InputMaybe<Scalars['String']['input']>;
  /** 备注 */
  note?: InputMaybe<Scalars['String']['input']>;
  /** 证券代码 */
  stockCode: Scalars['String']['input'];
};

export enum AiAssistantApprovalDecision {
  Approve = 'APPROVE',
  Reject = 'REJECT'
}

export type AiAssistantContextRefInput = {
  kind: Scalars['String']['input'];
  label?: InputMaybe<Scalars['String']['input']>;
  objectId: Scalars['String']['input'];
};

export enum AiAssistantEventType {
  ApprovalRequired = 'APPROVAL_REQUIRED',
  MessageCompleted = 'MESSAGE_COMPLETED',
  MessageDelta = 'MESSAGE_DELTA',
  RunFailed = 'RUN_FAILED',
  RunStatusChanged = 'RUN_STATUS_CHANGED',
  ToolCallCompleted = 'TOOL_CALL_COMPLETED',
  ToolCallStarted = 'TOOL_CALL_STARTED',
  UsageRecorded = 'USAGE_RECORDED'
}

export type AiAssistantRouteContextInput = {
  objectId?: InputMaybe<Scalars['String']['input']>;
  objectType?: InputMaybe<Scalars['String']['input']>;
  path: Scalars['String']['input'];
};

export enum AiAssistantRunStatus {
  Cancelled = 'CANCELLED',
  Completed = 'COMPLETED',
  Failed = 'FAILED',
  Queued = 'QUEUED',
  Running = 'RUNNING',
  WaitingApproval = 'WAITING_APPROVAL'
}

export enum AiRuntimeApplyState {
  Applied = 'APPLIED',
  Offline = 'OFFLINE',
  Pending = 'PENDING'
}

export enum AiRuntimeSettingsSource {
  DatabaseOverride = 'DATABASE_OVERRIDE',
  Environment = 'ENVIRONMENT'
}

export enum AiRuntimeStatus {
  Disabled = 'DISABLED',
  Offline = 'OFFLINE',
  Ready = 'READY',
  Unavailable = 'UNAVAILABLE',
  Unconfigured = 'UNCONFIGURED'
}

/** 撤单输入参数 */
export type CancelOrderInput = {
  /** 资金账号 */
  accountId?: InputMaybe<Scalars['String']['input']>;
  /** 调用方生成的撤单幂等键 */
  idempotencyKey?: InputMaybe<Scalars['String']['input']>;
  /** 订单ID */
  orderId: Scalars['Int']['input'];
};

/** 条件清仓单输入 */
export type ConditionalLiquidationOrderInput = {
  /** 资金账号 */
  accountId?: InputMaybe<Scalars['String']['input']>;
  /** 明确授权自动卖出 */
  autoExitAuthorized?: Scalars['Boolean']['input'];
  /** 动态止盈参数；为空使用平衡型默认值 */
  dynamicPolicy?: InputMaybe<Scalars['JSON']['input']>;
  /** 是否启用 */
  enabled?: Scalars['Boolean']['input'];
  /** paper 或 live */
  executionMode?: Scalars['String']['input'];
  /** 条件清仓单ID */
  id?: InputMaybe<Scalars['String']['input']>;
  /** 证券名称 */
  instrumentName?: InputMaybe<Scalars['String']['input']>;
  /** 备注 */
  remark?: InputMaybe<Scalars['String']['input']>;
  /** 卖出数量模式 */
  sellMode?: Scalars['String']['input'];
  /** 卖出可卖数量比例 */
  sellRatioPct?: InputMaybe<Scalars['Float']['input']>;
  /** 固定卖出股数 */
  sellVolume?: InputMaybe<Scalars['Int']['input']>;
  /** 证券代码 */
  stockCode: Scalars['String']['input'];
  /** IMMEDIATE 或 ADAPTIVE_VOLUME_PRICE_TRAILING */
  strategy?: Scalars['String']['input'];
  /** 目标触发价 */
  targetPrice?: InputMaybe<Scalars['Float']['input']>;
  /** 目标收益率百分比 */
  targetProfitPct?: InputMaybe<Scalars['Float']['input']>;
};

export type CreateAiAssistantThreadInput = {
  accountId?: InputMaybe<Scalars['String']['input']>;
  agentId?: Scalars['String']['input'];
  title?: InputMaybe<Scalars['String']['input']>;
};

/** 创建建仓/加仓托管计划 */
export type CreateEntryPlanInput = {
  bucket: Scalars['String']['input'];
  completionPolicy: EntryPlanCompletionInput;
  executionPolicy: EntryPlanExecutionInput;
  exitProtection?: InputMaybe<EntryExitProtectionInput>;
  idempotencyKey?: Scalars['String']['input'];
  instrumentCode: Scalars['String']['input'];
  note?: Scalars['String']['input'];
  pacingPolicy: EntryPlanPacingInput;
  startImmediately?: Scalars['Boolean']['input'];
  targetPolicy: EntryPlanTargetInput;
  triggerRules: Array<EntryPlanRuleInput>;
};

/** 创建人工计划 */
export type CreateManualExitPlanInput = {
  accountId?: InputMaybe<Scalars['String']['input']>;
  autoExitAuthorized?: Scalars['Boolean']['input'];
  bucket?: Scalars['String']['input'];
  costBasis: ExitPlanCostBasisInput;
  enabled?: Scalars['Boolean']['input'];
  executionMode?: Scalars['String']['input'];
  /** 调用方生成的创建请求幂等键 */
  idempotencyKey: Scalars['String']['input'];
  instrumentCode: Scalars['String']['input'];
  protectedVolume: Scalars['Int']['input'];
  remark?: InputMaybe<Scalars['String']['input']>;
  rules: Scalars['JSON']['input'];
};

/** 复权类型 */
export enum DividendType {
  Back = 'BACK',
  BackRatio = 'BACK_RATIO',
  Front = 'FRONT',
  FrontRatio = 'FRONT_RATIO',
  None = 'NONE'
}

/** 真实买入成交后创建的卖出保护模板 */
export type EntryExitProtectionInput = {
  enabled?: Scalars['Boolean']['input'];
  grossTakeProfitPct?: InputMaybe<Scalars['Float']['input']>;
  maxHoldingDays?: InputMaybe<Scalars['Int']['input']>;
  stopPrice?: InputMaybe<Scalars['Float']['input']>;
  trailingArmProfitPct?: InputMaybe<Scalars['Float']['input']>;
  trailingDrawdownPct?: InputMaybe<Scalars['Float']['input']>;
};

/** 确认设备绑定的自动建仓授权挑战 */
export type EntryPlanAuthorizationConfirmationInput = {
  challengeId: Scalars['ID']['input'];
  configVersion: Scalars['Int']['input'];
  confirmationToken: Scalars['String']['input'];
  planId: Scalars['ID']['input'];
};

/** 自动建仓授权预览 */
export type EntryPlanAuthorizationPreviewInput = {
  configVersion: Scalars['Int']['input'];
  idempotencyKey: Scalars['String']['input'];
  planId: Scalars['ID']['input'];
};

/** 计划完成条件 */
export type EntryPlanCompletionInput = {
  cancelUnsubmittedOnExpiry?: Scalars['Boolean']['input'];
  expireAtMs?: InputMaybe<Scalars['Int']['input']>;
  maxBuyPrice?: Scalars['Float']['input'];
  stopWhenBudgetExhausted?: Scalars['Boolean']['input'];
  stopWhenTargetReached?: Scalars['Boolean']['input'];
};

/** 执行环境、确认方式和保护限价参数 */
export type EntryPlanExecutionInput = {
  approvalTtlMs?: Scalars['Int']['input'];
  authorizationMode?: Scalars['String']['input'];
  environment?: Scalars['String']['input'];
  maxPriceDeviationBps?: Scalars['Float']['input'];
  maxSlippageBps?: Scalars['Float']['input'];
  priceReference?: Scalars['String']['input'];
};

/** 分批节奏与计划级容量 */
export type EntryPlanPacingInput = {
  cashBufferPct?: Scalars['Float']['input'];
  cooldownAfterRejectSeconds?: Scalars['Int']['input'];
  maxDailyFilledAmountCny?: Scalars['Float']['input'];
  maxOrdersPerDay?: Scalars['Int']['input'];
  maxSingleIntentAmountCny?: Scalars['Float']['input'];
  minIntervalSeconds?: Scalars['Int']['input'];
  trancheCount?: Scalars['Int']['input'];
  trendAdjustmentEnabled?: Scalars['Boolean']['input'];
};

/** 买入触发规则；字段按 ruleType 受 capabilities 约束 */
export type EntryPlanRuleInput = {
  enabled?: Scalars['Boolean']['input'];
  fastEmaPeriod?: InputMaybe<Scalars['Int']['input']>;
  ladderLevels: Array<EntryPriceLadderLevelInput>;
  manualTriggerSequence?: InputMaybe<Scalars['Int']['input']>;
  maxPullbackPct?: InputMaybe<Scalars['Float']['input']>;
  minPullbackPct?: InputMaybe<Scalars['Float']['input']>;
  once?: Scalars['Boolean']['input'];
  presetId?: InputMaybe<Scalars['String']['input']>;
  priority?: Scalars['Int']['input'];
  reboundConfirmationPct?: InputMaybe<Scalars['Float']['input']>;
  ruleId: Scalars['String']['input'];
  ruleType: Scalars['String']['input'];
  slowEmaPeriod?: InputMaybe<Scalars['Int']['input']>;
};

/** 建仓/加仓目标与绝对风险上限 */
export type EntryPlanTargetInput = {
  additionalVolume?: InputMaybe<Scalars['Int']['input']>;
  incrementalAmountCny?: InputMaybe<Scalars['Float']['input']>;
  maxPositionPct?: Scalars['Float']['input'];
  maxTotalAmountCny?: Scalars['Float']['input'];
  mode: Scalars['String']['input'];
  targetPositionPct?: InputMaybe<Scalars['Float']['input']>;
};

/** 价格阶梯中的一个一次性档位 */
export type EntryPriceLadderLevelInput = {
  levelId: Scalars['String']['input'];
  priority?: Scalars['Int']['input'];
  trancheAmountCny?: InputMaybe<Scalars['Float']['input']>;
  trancheVolume?: InputMaybe<Scalars['Int']['input']>;
  triggerPrice: Scalars['Float']['input'];
};

/** 确认既有 LIVE 退出计划的精确自动实盘授权 */
export type ExitPlanAuthorizationConfirmationInput = {
  /** 预览时的主账户 */
  accountId: Scalars['String']['input'];
  /** 预览返回的确认挑战 ID */
  challengeId: Scalars['String']['input'];
  /** 预览返回的一次性确认凭据 */
  confirmationToken: Scalars['String']['input'];
  /** 预览时的配置版本 */
  expectedConfigVersion: Scalars['Int']['input'];
  /** 预览时的业务幂等键 */
  idempotencyKey: Scalars['String']['input'];
  /** 预览时的退出计划 ID */
  planId: Scalars['String']['input'];
};

/** 预览既有 LIVE 退出计划的精确自动实盘授权 */
export type ExitPlanAuthorizationPreviewInput = {
  /** 当前会话的唯一授权账户 */
  accountId: Scalars['String']['input'];
  /** 预期配置版本 */
  expectedConfigVersion: Scalars['Int']['input'];
  /** 调用方生成的业务幂等键 */
  idempotencyKey: Scalars['String']['input'];
  /** 既有退出计划 ID */
  planId: Scalars['String']['input'];
};

/** 人工计划不可变成本依据 */
export type ExitPlanCostBasisInput = {
  /** BROKER_BUY_ORDERS 或 MANUAL_UNIT_COST */
  mode: Scalars['String']['input'];
  /** 成交委托模式下必填的买入委托编号 */
  orderIds?: InputMaybe<Array<Scalars['String']['input']>>;
  /** 手工模式下每股全成本，已包含买入费用 */
  unitCostCny?: InputMaybe<Scalars['Float']['input']>;
};

/** 财务同步健康状态 */
export enum FinancialSyncHealthStatus {
  Failed = 'FAILED',
  NeverRun = 'NEVER_RUN',
  PartialFailure = 'PARTIAL_FAILURE',
  Running = 'RUNNING',
  Stale = 'STALE',
  Success = 'SUCCESS'
}

/** 首板候选账户偏好；偏好不能绕过硬否决 */
export type FirstBoardCandidatePreferenceInput = {
  accountId: Scalars['String']['input'];
  idempotencyKey?: Scalars['String']['input'];
  instrumentCode: Scalars['String']['input'];
  preference: Scalars['String']['input'];
};

/** 节假日输入 */
export type HolidayInput = {
  date: Scalars['Date']['input'];
  description?: InputMaybe<Scalars['String']['input']>;
};

export type InstrumentOrder = {
  /** 排序方向 */
  direction?: OrderDirection;
  /** 排序字段 */
  field: InstrumentOrderField;
};

export enum InstrumentOrderField {
  Code = 'CODE',
  DelistDate = 'DELIST_DATE',
  InstrumentType = 'INSTRUMENT_TYPE',
  IsTrading = 'IS_TRADING',
  ListDate = 'LIST_DATE',
  Market = 'MARKET',
  Name = 'NAME',
  PreClose = 'PRE_CLOSE'
}

/**
 * 金融产品类型
 *
 * - INDEX: 指数 - 股票市场的一个重要组成部分
 * - STOCK: 股票 - 代表公司所有权的金融工具
 * - FUND: 基金 - 由多个投资者共同投资的集合投资工具
 * - ETF: 交易型开放式指数基金
 * - TRR: 国债逆回购 - 一种短期融资工具
 */
export enum InstrumentType {
  Etf = 'ETF',
  Fund = 'FUND',
  Index = 'INDEX',
  Stock = 'STOCK',
  Trr = 'TRR'
}

export type InstrumentWhereInput = {
  /** 是否可交易 */
  isTrading?: InputMaybe<Scalars['Boolean']['input']>;
  /** 按市场过滤 (e.g., 'SH', 'SZ') */
  market?: InputMaybe<Scalars['String']['input']>;
  /** 按名称模糊搜索 */
  name_contains?: InputMaybe<Scalars['String']['input']>;
  /** 按板块名称过滤 */
  sector?: InputMaybe<Scalars['String']['input']>;
  /** 按股票代码模糊搜索 */
  stockCode_contains?: InputMaybe<Scalars['String']['input']>;
  /** 按工具类型过滤 (e.g., 'STOCK', 'FUND') */
  type?: InputMaybe<InstrumentType>;
  /** 按多个工具类型过滤 */
  type_in?: InputMaybe<Array<InstrumentType>>;
};

/** 盘中全市场量能筛选输入 */
export type IntradayVolumeScreenInput = {
  /** 排除行业 */
  excludeIndustries?: InputMaybe<Array<Scalars['String']['input']>>;
  /** 是否排除 ST/*ST 风险警示股票 */
  excludeSt?: Scalars['Boolean']['input'];
  /** 包含行业 */
  includeIndustries?: InputMaybe<Array<Scalars['String']['input']>>;
  /** 每页数量，最大200 */
  limit?: Scalars['Int']['input'];
  /** 最小盘中成交额进度倍数 */
  minAmountPaceRatio?: InputMaybe<Scalars['Float']['input']>;
  /** 最小五档盘口量失衡 */
  minDepthImbalance5?: InputMaybe<Scalars['Float']['input']>;
  /** 最小盘中换手率 % */
  minIntradayTurnoverRate?: InputMaybe<Scalars['Float']['input']>;
  /** 最小近5分钟放量倍数 */
  minLast5mVolumeRatio?: InputMaybe<Scalars['Float']['input']>;
  /** 最小盘中量能进度倍数 */
  minVolumePaceRatio?: InputMaybe<Scalars['Float']['input']>;
  /** 偏移量 */
  offset?: Scalars['Int']['input'];
  /** 超过该秒数未更新标记为 stale */
  staleAfterSeconds?: Scalars['Int']['input'];
  /** 标的范围：默认股票，可切换 ETF 或股票+ETF */
  universe?: StockScreenUniverse;
};

/** K线分页参数 */
export type KLinePageInput = {
  /** 游标时间(不包含) */
  cursor?: InputMaybe<Scalars['DateTime']['input']>;
  /** 分页方向 */
  direction?: PageDirection;
  /** 复权类型 */
  dividendType?: DividendType;
  /** 每页数量 */
  limit?: Scalars['Int']['input'];
  /** 返回排序方向，可选 asc 或 desc */
  order?: Scalars['String']['input'];
  /** K线周期 */
  period?: KLinePeriod;
  /** 股票代码 */
  stockCode: Scalars['String']['input'];
};

/** K线周期 */
export enum KLinePeriod {
  Day_1 = 'DAY_1',
  HalfYear_1 = 'HALF_YEAR_1',
  Hour_1 = 'HOUR_1',
  Min_1 = 'MIN_1',
  Min_5 = 'MIN_5',
  Min_15 = 'MIN_15',
  Min_30 = 'MIN_30',
  Min_60 = 'MIN_60',
  Month_1 = 'MONTH_1',
  Quarter_1 = 'QUARTER_1',
  Week_1 = 'WEEK_1',
  Year_1 = 'YEAR_1'
}

/** 账户级打板助手设置 */
export type LimitUpBoardAssistantSettingsInput = {
  accountId: Scalars['String']['input'];
  approvalTtlMs?: Scalars['Int']['input'];
  autoExitAcknowledged?: Scalars['Boolean']['input'];
  enabled?: Scalars['Boolean']['input'];
  entryDistanceTicks?: Scalars['Int']['input'];
  entryEndTime?: Scalars['String']['input'];
  entryOrderTtlMs?: Scalars['Int']['input'];
  entryStartTime?: Scalars['String']['input'];
  executionQuoteMaxAgeSeconds?: Scalars['Float']['input'];
  exitLimitBreakTicks?: Scalars['Int']['input'];
  exitMaxSlippageBps?: Scalars['Float']['input'];
  exitMinSealSeconds?: Scalars['Float']['input'];
  exitTrailingArmProfitPct?: Scalars['Float']['input'];
  exitTrailingDrawdownPct?: Scalars['Float']['input'];
  exitTrailingPercent?: Scalars['Float']['input'];
  maxDailyExposurePct?: Scalars['Float']['input'];
  maxEntryAttemptsPerDay?: Scalars['Int']['input'];
  maxHoldingExitTime?: Scalars['String']['input'];
  maxHoldingTradingDays?: Scalars['Int']['input'];
  maxOpenPositions?: Scalars['Int']['input'];
  maxPriceDeviationBps?: Scalars['Float']['input'];
  maxRankedCandidates?: Scalars['Int']['input'];
  maxSinglePositionPct?: Scalars['Float']['input'];
  mode?: Scalars['String']['input'];
  plannedTailLossPct?: Scalars['Float']['input'];
  promotionModelMode?: Scalars['String']['input'];
};

/** 打板候选布防操作 */
export type LimitUpBoardCandidateActionInput = {
  accountId: Scalars['String']['input'];
  idempotencyKey?: Scalars['String']['input'];
  instrumentCode: Scalars['String']['input'];
};

/** 打板助手历史回放成交情景档案 */
export enum LimitUpBoardReplayScenarioProfile {
  StandardV1 = 'STANDARD_V1'
}

/** 启动账户级打板助手历史回放 */
export type LimitUpBoardReplayStartInput = {
  accountId: Scalars['String']['input'];
  endTime: Scalars['DateTime']['input'];
  idempotencyKey: Scalars['String']['input'];
  initialCash?: InputMaybe<Scalars['Float']['input']>;
  initialTotalAsset?: InputMaybe<Scalars['Float']['input']>;
  scenarioProfile?: LimitUpBoardReplayScenarioProfile;
  startTime: Scalars['DateTime']['input'];
};

/** 打板助手历史回放更新类型 */
export enum LimitUpBoardReplayUpdateKind {
  Created = 'CREATED',
  Progress = 'PROGRESS',
  ResultReady = 'RESULT_READY',
  StatusChanged = 'STATUS_CHANGED'
}

/** 沪深全市场打板雷达查询输入 */
export type LimitUpRadarInput = {
  /** 包含行业 */
  includeIndustries?: InputMaybe<Array<Scalars['String']['input']>>;
  /** 每页数量，最大200 */
  limit?: Scalars['Int']['input'];
  /** 最低雷达评分 */
  minScore?: InputMaybe<Scalars['Float']['input']>;
  /** 偏移量 */
  offset?: Scalars['Int']['input'];
  /** 代码或名称搜索 */
  search?: InputMaybe<Scalars['String']['input']>;
  /** 排序方向 */
  sortDirection?: StockScreenSortDirection;
  /** 排序字段 */
  sortField?: LimitUpRadarSortField;
  /** 候选阶段过滤 */
  stages?: InputMaybe<Array<LimitUpRadarStage>>;
};

/** 打板雷达排序字段 */
export enum LimitUpRadarSortField {
  Amount = 'AMOUNT',
  Cvar95 = 'CVAR95',
  DistanceToLimit = 'DISTANCE_TO_LIMIT',
  ExpectedNetReturn = 'EXPECTED_NET_RETURN',
  PromotionScore = 'PROMOTION_SCORE',
  Score = 'SCORE',
  UpdatedAt = 'UPDATED_AT'
}

/** 打板雷达候选阶段 */
export enum LimitUpRadarStage {
  Broken = 'BROKEN',
  Momentum = 'MOMENTUM',
  NearLimit = 'NEAR_LIMIT',
  Resealed = 'RESEALED',
  Sealed = 'SEALED',
  Surging = 'SURGING',
  Touching = 'TOUCHING'
}

/** 一键清仓输入 */
export type LiquidateAllPositionsInput = {
  /** 资金账号 */
  accountId?: InputMaybe<Scalars['String']['input']>;
  /** 风险确认 */
  confirm: Scalars['Boolean']['input'];
  /** 最大重试次数 */
  maxRetry?: Scalars['Int']['input'];
};

/** 个股清仓输入 */
export type LiquidatePositionInput = {
  /** 资金账号 */
  accountId?: InputMaybe<Scalars['String']['input']>;
  /** 风险确认 */
  confirm: Scalars['Boolean']['input'];
  /** 最大重试次数 */
  maxRetry?: Scalars['Int']['input'];
  /** 股票代码 */
  stockCode: Scalars['String']['input'];
};

/** 批量或一键清仓 */
export type LiquidatePositionsInput = {
  accountId?: InputMaybe<Scalars['String']['input']>;
  autoExitAuthorized?: Scalars['Boolean']['input'];
  completionStrategy: Scalars['String']['input'];
  confirm: Scalars['Boolean']['input'];
  conflictStrategy: Scalars['String']['input'];
  executionMode?: Scalars['String']['input'];
  instrumentCodes?: InputMaybe<Array<Scalars['String']['input']>>;
  scope?: Scalars['String']['input'];
};

/** 清仓完成策略 */
export enum LiquidationCompletionStrategy {
  AvailableNow = 'AVAILABLE_NOW',
  UntilSnapshotCleared = 'UNTIL_SNAPSHOT_CLEARED'
}

/** 移动端组级清仓确认输入 */
export type LiquidationConfirmationInput = {
  /** 预览返回的确认挑战 ID */
  challengeId: Scalars['String']['input'];
  /** 预览返回的一次性确认凭据 */
  confirmationToken: Scalars['String']['input'];
};

/** 清仓计划冲突策略 */
export enum LiquidationConflictStrategy {
  ReplaceCancellable = 'REPLACE_CANCELLABLE',
  UnallocatedOnly = 'UNALLOCATED_ONLY'
}

/** 清仓执行模式；默认 PAPER，LIVE 需要额外实盘门禁 */
export enum LiquidationExecutionMode {
  Live = 'LIVE',
  Paper = 'PAPER'
}

/** 移动端组级清仓预览输入 */
export type LiquidationPreviewInput = {
  /** 必填资金账号 */
  accountId: Scalars['String']['input'];
  /** 处理当前可卖量或持续处理预览持仓快照 */
  completionStrategy: LiquidationCompletionStrategy;
  /** 只使用未分配数量或替换可取消计划 */
  conflictStrategy: LiquidationConflictStrategy;
  /** 默认 PAPER；LIVE 需要实盘门禁和最新完整对账 */
  executionMode?: LiquidationExecutionMode;
  /** 调用方生成的业务幂等键 */
  idempotencyKey: Scalars['String']['input'];
  /** SINGLE/SELECTED 必填；ALL 必须为空 */
  instrumentCodes?: InputMaybe<Array<Scalars['String']['input']>>;
  /** 单只、选中或全部 */
  scope: LiquidationScope;
};

/** 移动端清仓范围 */
export enum LiquidationScope {
  All = 'ALL',
  Selected = 'SELECTED',
  Single = 'SINGLE'
}

/** 日志级别 */
export enum LogLevel {
  Debug = 'DEBUG',
  Error = 'ERROR',
  Info = 'INFO',
  Success = 'SUCCESS',
  Warning = 'WARNING'
}

/** 移动端手动委托确认输入 */
export type ManualOrderConfirmationInput = {
  /** 预览返回的确认挑战 ID */
  challengeId: Scalars['String']['input'];
  /** 预览返回的一次性确认凭据 */
  confirmationToken: Scalars['String']['input'];
};

/** 移动端手动委托执行模式 */
export enum ManualOrderExecutionMode {
  Live = 'LIVE',
  Paper = 'PAPER'
}

/** 移动端手动委托预览输入 */
export type ManualOrderPreviewInput = {
  /** 必填资金账号 */
  accountId: Scalars['String']['input'];
  /** 默认 PAPER；LIVE 额外要求实盘灰度、对账和唯一 Agent 就绪 */
  executionMode?: ManualOrderExecutionMode;
  /** 调用方生成的业务幂等键 */
  idempotencyKey: Scalars['String']['input'];
  /** 带市场后缀的证券代码 */
  instrumentCode: Scalars['String']['input'];
  /** LIMIT 必填；BEST 必须为空 */
  limitPrice?: InputMaybe<Scalars['Float']['input']>;
  /** LIMIT 或 BEST */
  priceType: ManualOrderPriceType;
  /** BUY 或 SELL */
  side: ManualOrderSide;
  /** 请求委托数量 */
  volume: Scalars['Int']['input'];
};

/** 移动端手动委托报价类型 */
export enum ManualOrderPriceType {
  Best = 'BEST',
  Limit = 'LIMIT'
}

/** 移动端手动委托方向 */
export enum ManualOrderSide {
  Buy = 'BUY',
  Sell = 'SELL'
}

/** 解锁后允许导航的非敏感路由类型 */
export enum NotificationRouteType {
  QuantWorkspace = 'QUANT_WORKSPACE',
  SystemStatus = 'SYSTEM_STATUS',
  TodayAction = 'TODAY_ACTION',
  TradingOrders = 'TRADING_ORDERS',
  TradingSafety = 'TRADING_SAFETY'
}

export enum OrderDirection {
  Asc = 'ASC',
  Desc = 'DESC'
}

/** 订单输入参数 */
export type OrderInput = {
  /** 资金账号 */
  accountId?: InputMaybe<Scalars['String']['input']>;
  /** 调用方生成的业务幂等键 */
  idempotencyKey?: InputMaybe<Scalars['String']['input']>;
  /** 订单备注 */
  orderRemark?: InputMaybe<Scalars['String']['input']>;
  /** 委托价格 */
  price: Scalars['Float']['input'];
  /** 报价类型: LIMIT/MARKET/BEST */
  priceType: Scalars['String']['input'];
  /** 股票代码 */
  stockCode: Scalars['String']['input'];
  /** 策略名称 */
  strategyName?: InputMaybe<Scalars['String']['input']>;
  /** 委托类型: BUY/SELL */
  type: Scalars['String']['input'];
  /** 委托数量 */
  volume: Scalars['Int']['input'];
};

export enum OrderPriceType {
  Any = 'ANY',
  Best = 'BEST',
  EnhancedLimit = 'ENHANCED_LIMIT',
  Limit = 'LIMIT',
  PropAllotment = 'PROP_ALLOTMENT',
  PropBuyback = 'PROP_BUYBACK',
  PropCancelPlacing = 'PROP_CANCEL_PLACING',
  PropCollateralTransfer = 'PROP_COLLATERAL_TRANSFER',
  PropCrossMarket = 'PROP_CROSS_MARKET',
  PropDebtConversion = 'PROP_DEBT_CONVERSION',
  PropDecide = 'PROP_DECIDE',
  PropDirectSecuRepay = 'PROP_DIRECT_SECU_REPAY',
  PropDividend = 'PROP_DIVIDEND',
  PropDjzy = 'PROP_DJZY',
  PropEquity = 'PROP_EQUITY',
  PropEtf = 'PROP_ETF',
  PropExercis = 'PROP_EXERCIS',
  PropFullRealCancel = 'PROP_FULL_REAL_CANCEL',
  PropFundChaihe = 'PROP_FUND_CHAIHE',
  PropFundDevidend = 'PROP_FUND_DEVIDEND',
  PropFundEntrust = 'PROP_FUND_ENTRUST',
  PropIncreaseShare = 'PROP_INCREASE_SHARE',
  PropInstbusiRestcancel = 'PROP_INSTBUSI_RESTCANCEL',
  PropJdjy = 'PROP_JDJY',
  PropL5FirstCancel = 'PROP_L5_FIRST_CANCEL',
  PropL5FirstLimitpx = 'PROP_L5_FIRST_LIMITPX',
  PropMimePriceFirst = 'PROP_MIME_PRICE_FIRST',
  PropNeeqLimit = 'PROP_NEEQ_LIMIT',
  PropNeeqMatchConfirm = 'PROP_NEEQ_MATCH_CONFIRM',
  PropNeeqMutualMatchConfirm = 'PROP_NEEQ_MUTUAL_MATCH_CONFIRM',
  PropNeeqPricing = 'PROP_NEEQ_PRICING',
  PropPeerPriceFirst = 'PROP_PEER_PRICE_FIRST',
  PropPlacing = 'PROP_PLACING',
  PropRefer = 'PROP_REFER',
  PropSellback = 'PROP_SELLBACK',
  PropShenzhenPlacing = 'PROP_SHENZHEN_PLACING',
  PropSubscribe = 'PROP_SUBSCRIBE',
  PropVote = 'PROP_VOTE',
  PropWdjy = 'PROP_WDJY',
  PropWdzy = 'PROP_WDZY',
  PropYsyyjc = 'PROP_YSYYJC',
  PropYysgys = 'PROP_YYSGYS',
  RetailLimit = 'RETAIL_LIMIT'
}

export enum OrderStatus {
  Canceled = 'CANCELED',
  Junk = 'JUNK',
  PartsuccCancel = 'PARTSUCC_CANCEL',
  PartCancel = 'PART_CANCEL',
  PartSucc = 'PART_SUCC',
  Reported = 'REPORTED',
  ReportedCancel = 'REPORTED_CANCEL',
  Succeeded = 'SUCCEEDED',
  Unknown = 'UNKNOWN',
  Unreported = 'UNREPORTED',
  WaitReporting = 'WAIT_REPORTING'
}

/**
 * 委托类型
 *
 * - BUY: 买入 - 买入股票的委托类型
 * - SELL: 卖出 - 卖出股票的委托类型
 */
export enum OrderType {
  Buy = 'BUY',
  Sell = 'SELL'
}

/** 时间序列分页方向 */
export enum PageDirection {
  Next = 'NEXT',
  Prev = 'PREV'
}

/** 可配置的普通通知类别 */
export enum PushCategory {
  ActionRequired = 'ACTION_REQUIRED',
  AutomationError = 'AUTOMATION_ERROR',
  ConnectionData = 'CONNECTION_DATA',
  OrderUpdate = 'ORDER_UPDATE',
  RiskSafety = 'RISK_SAFETY'
}

/** 单个普通通知类别偏好 */
export type PushCategoryPreferenceInput = {
  category: PushCategory;
  enabled: Scalars['Boolean']['input'];
};

/** APNs 服务环境 */
export enum PushEnvironment {
  Production = 'PRODUCTION',
  Sandbox = 'SANDBOX'
}

/** 资金赎回输入 */
export type RedeemPositionInput = {
  /** 赎回金额，为空则赎回全部 */
  amount?: InputMaybe<Scalars['Float']['input']>;
  /** 股票代码 */
  stockCode: Scalars['String']['input'];
};

/** 注册或轮换当前安装的 APNs Token */
export type RegisterPushDeviceInput = {
  appBundleId: Scalars['String']['input'];
  appVersion: Scalars['String']['input'];
  deviceInstallId: Scalars['String']['input'];
  deviceToken: Scalars['String']['input'];
  environment: PushEnvironment;
};

/** 自选股排序输入 */
export type ReorderWatchlistInput = {
  /** 资金账号 */
  accountId?: InputMaybe<Scalars['String']['input']>;
  /** 排序后的证券代码列表 */
  symbols: Array<Scalars['String']['input']>;
};

export type ResolveAiAssistantApprovalInput = {
  decision: AiAssistantApprovalDecision;
  runId: Scalars['ID']['input'];
  toolCallId: Scalars['ID']['input'];
};

/**
 * 策略风险等级
 *
 * - LOW: 低风险 - 保守型策略,适合风险厌恶型投资者
 * - MEDIUM: 中风险 - 平衡型策略,风险和收益相对均衡
 * - HIGH: 高风险 - 激进型策略,追求高收益但承担较高风险
 * - VERY_HIGH: 极高风险 - 高杠杆或高频策略,需要专业投资者
 */
export enum RiskLevel {
  High = 'HIGH',
  Low = 'LOW',
  Medium = 'MEDIUM',
  VeryHigh = 'VERY_HIGH'
}

/** ROE（TTM）独立质量状态 */
export enum RoeQualityStatus {
  Invalid = 'INVALID',
  Stale = 'STALE',
  Suspicious = 'SUSPICIOUS',
  Unverified = 'UNVERIFIED',
  Valid = 'VALID'
}

export type SendAiAssistantMessageInput = {
  clientMessageId: Scalars['String']['input'];
  contextRefs?: Array<AiAssistantContextRefInput>;
  routeContext?: InputMaybe<AiAssistantRouteContextInput>;
  text: Scalars['String']['input'];
  threadId: Scalars['ID']['input'];
};

/** 选股字段条件 */
export type StockFieldConditionInput = {
  /** 快照字段名 */
  field: Scalars['String']['input'];
  /** 操作符: gte/lte/gt/lt/eq/between */
  operator?: Scalars['String']['input'];
  /** 比较值 */
  value: Scalars['Float']['input'];
  /** 区间结束值 */
  valueTo?: InputMaybe<Scalars['Float']['input']>;
};

/** 条件选股输入 */
export type StockScreenInput = {
  /** 排除行业 */
  excludeIndustries?: InputMaybe<Array<Scalars['String']['input']>>;
  /** 是否排除 ST/*ST 风险警示股票 */
  excludeSt?: Scalars['Boolean']['input'];
  /** 基础字段条件 */
  fieldConditions?: InputMaybe<Array<StockFieldConditionInput>>;
  /** 包含行业 */
  includeIndustries?: InputMaybe<Array<Scalars['String']['input']>>;
  /** 每页数量，最大200 */
  limit?: Scalars['Int']['input'];
  /** 最小归母净利润单季同比增速 */
  minNetProfitGrowth?: InputMaybe<Scalars['Float']['input']>;
  /** 最小 ROE（TTM），仅使用截至快照已披露且验证通过的数据 */
  minRoe?: InputMaybe<Scalars['Float']['input']>;
  /** 最小营收单季同比增速 */
  minYoyGrowth?: InputMaybe<Scalars['Float']['input']>;
  /** 偏移量 */
  offset?: Scalars['Int']['input'];
  /** 是否要求当日信号完成 */
  requireFresh?: Scalars['Boolean']['input'];
  /** 评分规则 */
  scoreRules?: InputMaybe<Array<StockSignalWeightInput>>;
  /** 信号条件 */
  signalConditions?: InputMaybe<Array<StockSignalConditionInput>>;
  /** 排序配置 */
  sort?: InputMaybe<StockScreenSortInput>;
  /** 标的范围：默认股票，可切换 ETF 或股票+ETF */
  universe?: StockScreenUniverse;
};

/** 条件选股排序方向 */
export enum StockScreenSortDirection {
  Asc = 'ASC',
  Desc = 'DESC'
}

/** 条件选股排序字段 */
export enum StockScreenSortField {
  AmountPercentile_60 = 'AMOUNT_PERCENTILE_60',
  AmountRatio_20 = 'AMOUNT_RATIO_20',
  ChangePct = 'CHANGE_PCT',
  Code = 'CODE',
  CurrentPrice = 'CURRENT_PRICE',
  DaysSincePeak = 'DAYS_SINCE_PEAK',
  KdjJ = 'KDJ_J',
  Name = 'NAME',
  NetProfitGrowth = 'NET_PROFIT_GROWTH',
  PriceDropPct = 'PRICE_DROP_PCT',
  Roe = 'ROE',
  Rsi12 = 'RSI12',
  SignalCount = 'SIGNAL_COUNT',
  TurnoverRate = 'TURNOVER_RATE',
  VolumePercentile_60 = 'VOLUME_PERCENTILE_60',
  VolumeRatio = 'VOLUME_RATIO',
  VolumeRatio_5 = 'VOLUME_RATIO_5',
  YoyGrowth = 'YOY_GROWTH'
}

/** 条件选股排序输入 */
export type StockScreenSortInput = {
  /** 排序方向 */
  direction?: StockScreenSortDirection;
  /** 排序字段 */
  field: StockScreenSortField;
};

/** 条件选股标的范围 */
export enum StockScreenUniverse {
  Etf = 'ETF',
  Stock = 'STOCK',
  StockAndEtf = 'STOCK_AND_ETF'
}

/** 选股信号条件 */
export type StockSignalConditionInput = {
  /** 是否必须命中 */
  required?: Scalars['Boolean']['input'];
  /** 信号码 */
  signalCode: Scalars['String']['input'];
};

/** 选股评分规则 */
export type StockSignalWeightInput = {
  /** 信号码 */
  signalCode: Scalars['String']['input'];
  /** 命中权重 */
  weight?: Scalars['Float']['input'];
};

/**
 * 策略分类
 *
 * - TREND_FOLLOWING: 趋势跟随 - 跟随市场趋势方向进行交易
 * - MEAN_REVERSION: 均值回归 - 基于价格向均值回归的特性进行交易
 * - ARBITRAGE: 套利策略 - 利用价格差异进行无风险套利
 * - MARKET_MAKING: 做市策略 - 提供流动性并赚取买卖价差
 * - OTHER: 其他策略 - 不属于上述分类的策略
 */
export enum StrategyCategory {
  Arbitrage = 'ARBITRAGE',
  MarketMaking = 'MARKET_MAKING',
  MeanReversion = 'MEAN_REVERSION',
  Other = 'OTHER',
  TrendFollowing = 'TREND_FOLLOWING'
}

/** 需要设备逐次确认的实盘策略控制动作 */
export enum StrategyControlAction {
  CloneToLive = 'CLONE_TO_LIVE',
  ResumeLive = 'RESUME_LIVE',
  StartLive = 'START_LIVE'
}

/** 实盘策略控制确认输入 */
export type StrategyControlConfirmationInput = {
  challengeId: Scalars['String']['input'];
  confirmationToken: Scalars['String']['input'];
};

/** 实盘策略控制预览输入 */
export type StrategyControlPreviewInput = {
  /** 当前设备会话绑定的资金账号 */
  accountId: Scalars['String']['input'];
  action: StrategyControlAction;
  /** 必须等于当前移动参数 configVersion */
  expectedConfigVersion: Scalars['String']['input'];
  /** 调用方生成、当前动作内唯一的幂等键 */
  idempotencyKey: Scalars['String']['input'];
  /** 目标或来源策略实例 ID */
  instanceId: Scalars['String']['input'];
};

/** Pullback Grid 网格簿档位更新 */
export type StrategyGridBookLevelInput = {
  enabled?: Scalars['Boolean']['input'];
  expectedProfit?: InputMaybe<Scalars['Float']['input']>;
  gridId?: InputMaybe<Scalars['String']['input']>;
  levelIndex: Scalars['Int']['input'];
  pctFromBase?: InputMaybe<Scalars['Float']['input']>;
  plannedShares: Scalars['Int']['input'];
  price: Scalars['Float']['input'];
  side: Scalars['String']['input'];
};

/** Pullback Grid 网格簿更新输入 */
export type StrategyGridBookUpdateInput = {
  basePrice?: InputMaybe<Scalars['Float']['input']>;
  levels: Array<StrategyGridBookLevelInput>;
};

/** 创建策略实例输入 */
export type StrategyInstanceCreateInput = {
  displayName?: InputMaybe<Scalars['String']['input']>;
  endTime?: InputMaybe<Scalars['DateTime']['input']>;
  instrumentCode: Scalars['String']['input'];
  mode?: StrategyRunMode;
  parameters?: InputMaybe<Scalars['JSON']['input']>;
  startTime?: InputMaybe<Scalars['DateTime']['input']>;
  strategyKey: Scalars['String']['input'];
};

/** 更新策略实例参数输入 */
export type StrategyInstanceParameterUpdateInput = {
  applyImmediately?: Scalars['Boolean']['input'];
  /** 原生移动端必填；必须等于当前 configVersion */
  expectedVersion?: InputMaybe<Scalars['String']['input']>;
  parameters: Scalars['JSON']['input'];
};

/**
 * 策略标的范围
 *
 * - SINGLE: 单标的 - 仅支持单只股票/标的
 * - MULTI: 多标的 - 支持多只股票/标的
 */
export enum StrategyInstrumentScope {
  Multi = 'MULTI',
  Single = 'SINGLE'
}

/**
 * 策略标的池来源
 *
 * - STATIC: 创建运行实例时固定指定标的
 * - ACCOUNT_HOLDINGS: 由账户持仓快照动态维护标的池
 * - RADAR_CANDIDATES: 由 Engine 打板雷达协调候选标的池
 */
export enum StrategyInstrumentUniverseMode {
  AccountHoldings = 'ACCOUNT_HOLDINGS',
  RadarCandidates = 'RADAR_CANDIDATES',
  Static = 'STATIC'
}

/** 策略运行实例输入参数 */
export type StrategyRunInput = {
  /** 结束时间（回测模式必需） */
  endTime?: InputMaybe<Scalars['DateTime']['input']>;
  /** 交易标的列表 */
  instruments: Array<Scalars['String']['input']>;
  /** 运行模式 */
  mode: StrategyRunMode;
  /** 运行实例名称（可选） */
  name?: InputMaybe<Scalars['String']['input']>;
  /** 策略参数 */
  parameters: Scalars['JSON']['input'];
  /** 开始时间（回测模式必需） */
  startTime?: InputMaybe<Scalars['DateTime']['input']>;
  /** 策略模板ID */
  strategyId: Scalars['Int']['input'];
};

/**
 * 策略运行模式
 *
 * - BACKTEST: 回测模式 - 使用历史数据进行策略回测
 * - PAPER: 模拟盘 - 使用实时数据进行虚拟交易（Paper Trading）
 * - LIVE: 实盘模式 - 真实交易环境（Live Trading）
 */
export enum StrategyRunMode {
  Backtest = 'BACKTEST',
  Live = 'LIVE',
  Paper = 'PAPER'
}

/**
 * 策略运行状态
 *
 * - PENDING: 待启动 - 策略实例已创建但尚未开始运行
 * - RUNNING: 运行中 - 策略正在执行
 * - PAUSED: 暂停 - 策略运行已暂停
 * - STOPPED: 已停止 - 策略运行已手动停止
 * - COMPLETED: 已完成 - 策略运行正常结束（如回测完成）
 * - ERROR: 错误 - 策略运行出错
 */
export enum StrategyRunStatus {
  Completed = 'COMPLETED',
  Error = 'ERROR',
  Paused = 'PAUSED',
  Pending = 'PENDING',
  Running = 'RUNNING',
  Stopped = 'STOPPED'
}

/** 策略运行实例更新输入参数 */
export type StrategyRunUpdateInput = {
  /** 策略参数 */
  parameters?: InputMaybe<Scalars['JSON']['input']>;
};

/**
 * 策略模板状态
 *
 * - ACTIVE: 激活 - 策略可用，可创建运行实例
 * - UPGRADING: 待升级 - 策略代码已更新，待确认升级
 * - DEPRECATED: 已弃用 - 策略已删除，不可创建新实例
 */
export enum StrategyStatus {
  Active = 'ACTIVE',
  Deprecated = 'DEPRECATED',
  Upgrading = 'UPGRADING'
}

/** 确认 V3 做 T 候选时客户端观察到的 CAS 身份 */
export type TTradeCandidateApprovalExpectationInput = {
  candidateFingerprint: Scalars['String']['input'];
  candidateId: Scalars['ID']['input'];
  candidateStateVersion: Scalars['Int']['input'];
  configVersion: Scalars['Int']['input'];
  policyVersion: Scalars['String']['input'];
  signalVersion: Scalars['Int']['input'];
};

/** 做 T 候选生命周期 */
export enum TTradeCandidateStatus {
  AwaitingApproval = 'AWAITING_APPROVAL',
  Latched = 'LATCHED',
  None = 'NONE',
  Rearming = 'REARMING',
  Suppressed = 'SUPPRESSED'
}

/** 做 T 候选全链路完整性 */
export enum TTradeCandidateTraceIntegrityStatus {
  Broken = 'BROKEN',
  Complete = 'COMPLETE',
  InProgress = 'IN_PROGRESS'
}

/** 做 T V3 客户端低基数遥测事件 */
export enum TTradeClientTelemetryEvent {
  RefreshFailure = 'REFRESH_FAILURE',
  RefreshSuccess = 'REFRESH_SUCCESS',
  SubscriptionReconnected = 'SUBSCRIPTION_RECONNECTED'
}

/** 上报做 T V3 客户端低基数遥测 */
export type TTradeClientTelemetryInput = {
  accountId: Scalars['String']['input'];
  event: TTradeClientTelemetryEvent;
  platform: TTradeClientTelemetryPlatform;
  surface: TTradeClientTelemetrySurface;
};

/** 做 T V3 客户端遥测平台 */
export enum TTradeClientTelemetryPlatform {
  Ios = 'IOS',
  Web = 'WEB'
}

/** 做 T V3 客户端遥测固定界面 */
export enum TTradeClientTelemetrySurface {
  TTradeSignalV3 = 'T_TRADE_SIGNAL_V3'
}

/** 原生端两阶段做 T 安全控制动作 */
export enum TTradeControlAction {
  ActivateCanary = 'ACTIVATE_CANARY',
  ActivateLive = 'ACTIVATE_LIVE',
  BeginControlledWindow = 'BEGIN_CONTROLLED_WINDOW',
  KillSwitch = 'KILL_SWITCH'
}

/** 消费原生端做 T 安全控制确认凭据 */
export type TTradeControlConfirmationInput = {
  challengeId: Scalars['ID']['input'];
  confirmationToken: Scalars['String']['input'];
};

/** 生成原生端做 T 安全控制确认预览 */
export type TTradeControlPreviewInput = {
  accountId: Scalars['String']['input'];
  action: TTradeControlAction;
  idempotencyKey: Scalars['String']['input'];
  policyVersion: Scalars['Int']['input'];
  reason?: Scalars['String']['input'];
  snapshotId?: Scalars['String']['input'];
  targetStage?: InputMaybe<TTradeRolloutTarget>;
};

/** 做 T 列表压缩展示的主导阶段 */
export enum TTradeDominantPhase {
  MomentumAccelerating = 'MOMENTUM_ACCELERATING',
  MomentumBaselining = 'MOMENTUM_BASELINING',
  MomentumBuilding = 'MOMENTUM_BUILDING',
  MomentumCandidateLatched = 'MOMENTUM_CANDIDATE_LATCHED',
  MomentumObserving = 'MOMENTUM_OBSERVING',
  MomentumOverextended = 'MOMENTUM_OVEREXTENDED',
  MomentumSuppressed = 'MOMENTUM_SUPPRESSED',
  None = 'NONE',
  PullbackCandidateLatched = 'PULLBACK_CANDIDATE_LATCHED',
  PullbackForming = 'PULLBACK_FORMING',
  PullbackLowStabilizing = 'PULLBACK_LOW_STABILIZING',
  PullbackObserving = 'PULLBACK_OBSERVING',
  PullbackReboundConfirming = 'PULLBACK_REBOUND_CONFIRMING',
  PullbackSuppressed = 'PULLBACK_SUPPRESSED'
}

/** 导入外部已成交的做 T 买入批次 */
export type TTradeExternalEntryInput = {
  accountId: Scalars['String']['input'];
  orderId: Scalars['String']['input'];
  runId: Scalars['String']['input'];
};

/** 保存全局持仓做 T 监控设置 */
export type TTradeGlobalSettingsInput = {
  accountId: Scalars['String']['input'];
  autoExitAcknowledged?: Scalars['Boolean']['input'];
  baseFloorPct?: Scalars['Float']['input'];
  cooldownSeconds?: Scalars['Int']['input'];
  enabled?: Scalars['Boolean']['input'];
  expectedConfigVersion: Scalars['Int']['input'];
  hardStopEnabled?: Scalars['Boolean']['input'];
  hardStopPct?: Scalars['Float']['input'];
  highProfitArmPct?: Scalars['Float']['input'];
  highProfitLockEnabled?: Scalars['Boolean']['input'];
  highProfitMaxDrawdownPct?: Scalars['Float']['input'];
  ignoredStockCodes: Array<Scalars['String']['input']>;
  initialGapPct?: Scalars['Float']['input'];
  limitUpTouchExitEnabled?: Scalars['Boolean']['input'];
  limitUpTouchToleranceTicks?: Scalars['Int']['input'];
  maxConcurrentBatches?: Scalars['Int']['input'];
  maxGapPct?: Scalars['Float']['input'];
  maxHoldingTradingDays?: Scalars['Int']['input'];
  maxPriceDeviationPct?: Scalars['Float']['input'];
  maxTotalTExposurePct?: Scalars['Float']['input'];
  maxTradeAmount?: Scalars['Float']['input'];
  mode?: Scalars['String']['input'];
  rapidReversalConfirmTicks?: Scalars['Int']['input'];
  rapidReversalDrawdownPct?: Scalars['Float']['input'];
  rapidReversalEnabled?: Scalars['Boolean']['input'];
  rapidReversalWindowSeconds?: Scalars['Int']['input'];
  signalPolicy: TTradeSignalPolicyInput;
  targetProfitPct?: Scalars['Float']['input'];
  targetTradeAmount?: Scalars['Float']['input'];
  timeExitMode?: TTradeTimeExitMode;
  timeExitTime?: Scalars['String']['input'];
  trailingGapSlope?: Scalars['Float']['input'];
};

/** 做 T 动量加速分支阶段 */
export enum TTradeMomentumPhase {
  Accelerating = 'ACCELERATING',
  Baselining = 'BASELINING',
  CandidateLatched = 'CANDIDATE_LATCHED',
  MomentumBuilding = 'MOMENTUM_BUILDING',
  Observing = 'OBSERVING',
  Overextended = 'OVEREXTENDED',
  Suppressed = 'SUPPRESSED'
}

/** 做 T 回撤反弹分支阶段 */
export enum TTradePullbackPhase {
  CandidateLatched = 'CANDIDATE_LATCHED',
  LowStabilizing = 'LOW_STABILIZING',
  Observing = 'OBSERVING',
  PullbackForming = 'PULLBACK_FORMING',
  ReboundConfirming = 'REBOUND_CONFIRMING',
  Suppressed = 'SUPPRESSED'
}

/** 做 T 历史回放的手工初始持仓 */
export type TTradeReplayPositionInput = {
  availableVolume: Scalars['Int']['input'];
  avgPrice?: Scalars['Float']['input'];
  instrumentName?: Scalars['String']['input'];
  lastPrice?: Scalars['Float']['input'];
  marketValue?: Scalars['Float']['input'];
  stockCode: Scalars['String']['input'];
  volume: Scalars['Int']['input'];
};

/** 启动做 T 历史回放 */
export type TTradeReplayStartInput = {
  accountId: Scalars['String']['input'];
  baseFloorPct?: Scalars['Float']['input'];
  commissionRate?: Scalars['Float']['input'];
  cooldownSeconds?: Scalars['Int']['input'];
  endTime: Scalars['DateTime']['input'];
  hardStopEnabled?: Scalars['Boolean']['input'];
  hardStopPct?: Scalars['Float']['input'];
  highProfitArmPct?: Scalars['Float']['input'];
  highProfitLockEnabled?: Scalars['Boolean']['input'];
  highProfitMaxDrawdownPct?: Scalars['Float']['input'];
  idempotencyKey: Scalars['String']['input'];
  initialCash?: InputMaybe<Scalars['Float']['input']>;
  initialGapPct?: Scalars['Float']['input'];
  initialPortfolioAsOf?: InputMaybe<Scalars['DateTime']['input']>;
  initialPositions: Array<TTradeReplayPositionInput>;
  initialTotalAsset?: InputMaybe<Scalars['Float']['input']>;
  limitUpTouchExitEnabled?: Scalars['Boolean']['input'];
  limitUpTouchToleranceTicks?: Scalars['Int']['input'];
  maxConcurrentBatches?: Scalars['Int']['input'];
  maxGapPct?: Scalars['Float']['input'];
  maxHoldingTradingDays?: Scalars['Int']['input'];
  maxPriceDeviationPct?: Scalars['Float']['input'];
  maxTotalTExposurePct?: Scalars['Float']['input'];
  maxTradeAmount?: Scalars['Float']['input'];
  minimumCommission?: Scalars['Float']['input'];
  rapidReversalConfirmTicks?: Scalars['Int']['input'];
  rapidReversalDrawdownPct?: Scalars['Float']['input'];
  rapidReversalEnabled?: Scalars['Boolean']['input'];
  rapidReversalWindowSeconds?: Scalars['Int']['input'];
  signalPolicy: TTradeSignalPolicyInput;
  slippageRate?: Scalars['Float']['input'];
  stampTaxRate?: Scalars['Float']['input'];
  startTime: Scalars['DateTime']['input'];
  targetProfitPct?: Scalars['Float']['input'];
  targetTradeAmount?: Scalars['Float']['input'];
  timeExitMode?: TTradeTimeExitMode;
  timeExitTime?: Scalars['String']['input'];
  trailingGapSlope?: Scalars['Float']['input'];
  transferFeeRate?: Scalars['Float']['input'];
};

/** 做 T 历史回放更新类型 */
export enum TTradeReplayUpdateKind {
  Created = 'CREATED',
  Progress = 'PROGRESS',
  ResultReady = 'RESULT_READY',
  StatusChanged = 'STATUS_CHANGED'
}

/** 账户自动交易目标灰度阶段 */
export enum TTradeRolloutTarget {
  Canary = 'CANARY',
  Live = 'LIVE'
}

/** 做 T 机会引擎数据健康 */
export enum TTradeSignalDataHealth {
  ContinuityLost = 'CONTINUITY_LOST',
  Degraded = 'DEGRADED',
  Insufficient = 'INSUFFICIENT',
  Ready = 'READY',
  Stale = 'STALE',
  Warming = 'WARMING'
}

/** 做 T 信号评估持久化种类 */
export enum TTradeSignalEvaluationKind {
  CoalescedDiagnostic = 'COALESCED_DIAGNOSTIC',
  Material = 'MATERIAL'
}

/** 做 T 信号路径 */
export enum TTradeSignalPath {
  MomentumAcceleration = 'MOMENTUM_ACCELERATION',
  PullbackRebound = 'PULLBACK_REBOUND'
}

/** V3 做 T 机会引擎规则参数 */
export type TTradeSignalPolicyInput = {
  allowedSessionCodes: Array<Scalars['String']['input']>;
  candidateConfirmSeconds: Scalars['Int']['input'];
  candidateConfirmTicks: Scalars['Int']['input'];
  candidateScore: Scalars['Float']['input'];
  candidateTtlSeconds: Scalars['Int']['input'];
  closeProtectionSeconds: Scalars['Int']['input'];
  continuousAmEndTime: Scalars['String']['input'];
  continuousAmStartTime: Scalars['String']['input'];
  continuousPmEndTime: Scalars['String']['input'];
  continuousPmStartTime: Scalars['String']['input'];
  maxQuoteAgeMs: Scalars['Int']['input'];
  maxSamples: Scalars['Int']['input'];
  momentumBaselineCoverageRatio: Scalars['Float']['input'];
  momentumBaselineSeconds: Scalars['Int']['input'];
  momentumBookImbalanceScoreMaxRatio: Scalars['Float']['input'];
  momentumBookImbalanceScoreMinRatio: Scalars['Float']['input'];
  momentumBookImbalanceWeight: Scalars['Float']['input'];
  momentumDataQualityPenaltyPoints: Scalars['Float']['input'];
  momentumEnabled: Scalars['Boolean']['input'];
  momentumFormationThresholdMultiplier: Scalars['Float']['input'];
  momentumHighToleranceTicks: Scalars['Int']['input'];
  momentumLiquidityFullScoreSpreadTicks: Scalars['Float']['input'];
  momentumLiquidityWeight: Scalars['Float']['input'];
  momentumLiquidityZeroScoreSpreadTicks: Scalars['Float']['input'];
  momentumMaxSpreadPct: Scalars['Float']['input'];
  momentumMaxSpreadTicks: Scalars['Int']['input'];
  momentumMaxVwapPremiumPct: Scalars['Float']['input'];
  momentumMinAmountVelocityRatio: Scalars['Float']['input'];
  momentumMinCoverageSeconds: Scalars['Int']['input'];
  momentumMinMoveSeconds: Scalars['Int']['input'];
  momentumMinRisePct: Scalars['Float']['input'];
  momentumMinSamples: Scalars['Int']['input'];
  momentumMinVwapPremiumPct: Scalars['Float']['input'];
  momentumOverextensionPenaltyFullPremiumPct: Scalars['Float']['input'];
  momentumOverextensionPenaltyPoints: Scalars['Float']['input'];
  momentumOverextensionPenaltyStartPremiumPct: Scalars['Float']['input'];
  momentumPersistenceScoreMaxRatio: Scalars['Float']['input'];
  momentumPersistenceScoreMinRatio: Scalars['Float']['input'];
  momentumPersistenceWeight: Scalars['Float']['input'];
  momentumRequiredFields: Array<Scalars['String']['input']>;
  momentumRiseScoreMinPct: Scalars['Float']['input'];
  momentumRiseScoreTargetMultiplier: Scalars['Float']['input'];
  momentumRiseWeight: Scalars['Float']['input'];
  momentumSlopeScoreMinPctPerSecond: Scalars['Float']['input'];
  momentumSlopeScoreTargetMultiplier: Scalars['Float']['input'];
  momentumSlopeWeight: Scalars['Float']['input'];
  momentumTurnoverScoreMinRatio: Scalars['Float']['input'];
  momentumTurnoverScoreTargetMultiplier: Scalars['Float']['input'];
  momentumTurnoverWeight: Scalars['Float']['input'];
  momentumVwapWeight: Scalars['Float']['input'];
  momentumVwapZeroScoreMaxPremiumPct: Scalars['Float']['input'];
  momentumVwapZeroScoreMinPremiumPct: Scalars['Float']['input'];
  momentumWindowSeconds: Scalars['Int']['input'];
  previewScore: Scalars['Float']['input'];
  profileMomentumRiseMaxMultiplier: Scalars['Float']['input'];
  profileMomentumRiseMinMultiplier: Scalars['Float']['input'];
  profileMomentumVelocityMaxRatio: Scalars['Float']['input'];
  profileMomentumVelocityMinRatio: Scalars['Float']['input'];
  profilePullbackThresholdMaxMultiplier: Scalars['Float']['input'];
  profilePullbackThresholdMinMultiplier: Scalars['Float']['input'];
  pullbackChasePenaltyFullPremiumPct: Scalars['Float']['input'];
  pullbackChasePenaltyPoints: Scalars['Float']['input'];
  pullbackChasePenaltyStartPremiumPct: Scalars['Float']['input'];
  pullbackDataQualityPenaltyPoints: Scalars['Float']['input'];
  pullbackDepthScoreMinPct: Scalars['Float']['input'];
  pullbackDepthScoreTargetMultiplier: Scalars['Float']['input'];
  pullbackDepthWeight: Scalars['Float']['input'];
  pullbackFormationThresholdMultiplier: Scalars['Float']['input'];
  pullbackLiquidityFullScoreSpreadTicks: Scalars['Float']['input'];
  pullbackLiquidityWeight: Scalars['Float']['input'];
  pullbackLiquidityZeroScoreSpreadTicks: Scalars['Float']['input'];
  pullbackLookbackSeconds: Scalars['Int']['input'];
  pullbackMaxSpreadTicks: Scalars['Int']['input'];
  pullbackMinCoverageSeconds: Scalars['Int']['input'];
  pullbackMinSamples: Scalars['Int']['input'];
  pullbackReboundScoreMaxPct: Scalars['Float']['input'];
  pullbackReboundScoreMinPct: Scalars['Float']['input'];
  pullbackReboundThresholdPct: Scalars['Float']['input'];
  pullbackReboundWeight: Scalars['Float']['input'];
  pullbackRequiredFields: Array<Scalars['String']['input']>;
  pullbackStabilizationScoreMaxSeconds: Scalars['Float']['input'];
  pullbackStabilizationScoreMinSeconds: Scalars['Float']['input'];
  pullbackStabilizationSeconds: Scalars['Int']['input'];
  pullbackStabilizationWeight: Scalars['Float']['input'];
  pullbackThresholdPct: Scalars['Float']['input'];
  pullbackTurnSlopeScoreMaxPctPerSecond: Scalars['Float']['input'];
  pullbackTurnSlopeScoreMinPctPerSecond: Scalars['Float']['input'];
  pullbackTurnSlopeWeight: Scalars['Float']['input'];
  pullbackVolumeBaselineWindowSeconds: Scalars['Int']['input'];
  pullbackVolumeScoreMaxRatio: Scalars['Float']['input'];
  pullbackVolumeScoreMinRatio: Scalars['Float']['input'];
  pullbackVolumeShortWindowSeconds: Scalars['Int']['input'];
  pullbackVolumeWeight: Scalars['Float']['input'];
  pullbackVwapFullScoreMaxPremiumPct: Scalars['Float']['input'];
  pullbackVwapWeight: Scalars['Float']['input'];
  pullbackVwapZeroScorePremiumPct: Scalars['Float']['input'];
  rearmScore: Scalars['Float']['input'];
  rearmSeconds: Scalars['Int']['input'];
  revalidateScore: Scalars['Float']['input'];
  sparseDegradedGapSeconds: Scalars['Int']['input'];
};

/** 纯校验做 T 信号规则，不写入运行配置 */
export type TTradeSignalPolicyPreviewInput = {
  accountId: Scalars['String']['input'];
  expectedConfigVersion: Scalars['Int']['input'];
  signalPolicy: TTradeSignalPolicyInput;
};

/** T 批次时间退出模式 */
export enum TTradeTimeExitMode {
  EndOfDay = 'END_OF_DAY',
  MaxHoldingDays = 'MAX_HOLDING_DAYS',
  Unlimited = 'UNLIMITED'
}

/** 交易事件类型 (个人量化软件专用) */
export enum TradingEventType {
  OrderCancelled = 'ORDER_CANCELLED',
  OrderCreated = 'ORDER_CREATED',
  OrderFilled = 'ORDER_FILLED',
  OrderRejected = 'ORDER_REJECTED'
}

/** 注销当前安装的 APNs Token */
export type UnregisterPushDeviceInput = {
  appBundleId: Scalars['String']['input'];
  deviceInstallId: Scalars['String']['input'];
  environment: PushEnvironment;
};

export type UpdateAiAssistantThreadInput = {
  externalSearchEnabled?: InputMaybe<Scalars['Boolean']['input']>;
  threadId: Scalars['ID']['input'];
  title?: InputMaybe<Scalars['String']['input']>;
};

export type UpdateAiRuntimeSettingsInput = {
  enabled: Scalars['Boolean']['input'];
  expectedVersion: Scalars['Int']['input'];
  maxConcurrentRuns: Scalars['Int']['input'];
  maxToolCalls: Scalars['Int']['input'];
  maxTurns: Scalars['Int']['input'];
  model: Scalars['String']['input'];
  runTimeoutSeconds: Scalars['Int']['input'];
};

/** 更新不存在待收敛订单的建仓/加仓计划 */
export type UpdateEntryPlanInput = {
  completionPolicy: EntryPlanCompletionInput;
  configVersion: Scalars['Int']['input'];
  executionPolicy: EntryPlanExecutionInput;
  exitProtection?: InputMaybe<EntryExitProtectionInput>;
  idempotencyKey?: Scalars['String']['input'];
  note?: Scalars['String']['input'];
  pacingPolicy: EntryPlanPacingInput;
  planId: Scalars['ID']['input'];
  targetPolicy: EntryPlanTargetInput;
  triggerRules: Array<EntryPlanRuleInput>;
};

/** 更新人工计划 */
export type UpdateManualExitPlanInput = {
  accountId?: InputMaybe<Scalars['String']['input']>;
  autoExitAuthorized?: InputMaybe<Scalars['Boolean']['input']>;
  configVersion: Scalars['Int']['input'];
  executionMode?: InputMaybe<Scalars['String']['input']>;
  planId: Scalars['String']['input'];
  protectedVolume?: InputMaybe<Scalars['Int']['input']>;
  remark?: InputMaybe<Scalars['String']['input']>;
  rules: Scalars['JSON']['input'];
};

/** 更新当前安装的普通通知类别偏好 */
export type UpdatePushPreferencesInput = {
  appBundleId: Scalars['String']['input'];
  deviceInstallId: Scalars['String']['input'];
  environment: PushEnvironment;
  preferences: Array<PushCategoryPreferenceInput>;
};

export type Market_IndicesDirectoryQueryVariables = Exact<{
  first: Scalars['Int']['input'];
  after?: InputMaybe<Scalars['String']['input']>;
  where?: InputMaybe<InstrumentWhereInput>;
  orderBy?: InputMaybe<InstrumentOrder>;
}>;


export type Market_IndicesDirectoryQuery = { __typename?: 'Query', instrumentsConnection: { __typename?: 'InstrumentConnection', totalCount: number, edges: Array<{ __typename?: 'InstrumentEdge', cursor: string, node: { __typename?: 'Instrument', id: string, instrumentId: string, name?: string | null, market?: string | null, type?: InstrumentType | null } }>, pageInfo: { __typename?: 'PageInfo', hasNextPage: boolean, endCursor?: string | null } } };

export type Dashboard_MarketIndexSnapshotsQueryVariables = Exact<{ [key: string]: never; }>;


export type Dashboard_MarketIndexSnapshotsQuery = { __typename?: 'Query', shanghaiTick: Array<{ __typename?: 'TickData', stockCode: string, time: string, lastPrice: number, open: number, high: number, low: number, preClose: number, volume: number }>, shenzhenTick: Array<{ __typename?: 'TickData', stockCode: string, time: string, lastPrice: number, open: number, high: number, low: number, preClose: number, volume: number }>, chinextTick: Array<{ __typename?: 'TickData', stockCode: string, time: string, lastPrice: number, open: number, high: number, low: number, preClose: number, volume: number }>, kechuangCompositeTick: Array<{ __typename?: 'TickData', stockCode: string, time: string, lastPrice: number, open: number, high: number, low: number, preClose: number, volume: number }>, kechuang50Tick: Array<{ __typename?: 'TickData', stockCode: string, time: string, lastPrice: number, open: number, high: number, low: number, preClose: number, volume: number }>, csiA500Tick: Array<{ __typename?: 'TickData', stockCode: string, time: string, lastPrice: number, open: number, high: number, low: number, preClose: number, volume: number }>, csi300Tick: Array<{ __typename?: 'TickData', stockCode: string, time: string, lastPrice: number, open: number, high: number, low: number, preClose: number, volume: number }>, csi1000Tick: Array<{ __typename?: 'TickData', stockCode: string, time: string, lastPrice: number, open: number, high: number, low: number, preClose: number, volume: number }>, shanghai50Tick: Array<{ __typename?: 'TickData', stockCode: string, time: string, lastPrice: number, open: number, high: number, low: number, preClose: number, volume: number }>, shenzhen100Tick: Array<{ __typename?: 'TickData', stockCode: string, time: string, lastPrice: number, open: number, high: number, low: number, preClose: number, volume: number }>, csi500Tick: Array<{ __typename?: 'TickData', stockCode: string, time: string, lastPrice: number, open: number, high: number, low: number, preClose: number, volume: number }>, chinext50Tick: Array<{ __typename?: 'TickData', stockCode: string, time: string, lastPrice: number, open: number, high: number, low: number, preClose: number, volume: number }>, kechuang100Tick: Array<{ __typename?: 'TickData', stockCode: string, time: string, lastPrice: number, open: number, high: number, low: number, preClose: number, volume: number }>, shanghaiIndex: Array<{ __typename?: 'KLineData', stockCode: string, time: string, open: number, high: number, low: number, close: number, preClose: number, volume: number }>, shenzhenComponent: Array<{ __typename?: 'KLineData', stockCode: string, time: string, open: number, high: number, low: number, close: number, preClose: number, volume: number }>, chinextIndex: Array<{ __typename?: 'KLineData', stockCode: string, time: string, open: number, high: number, low: number, close: number, preClose: number, volume: number }>, kechuangComposite: Array<{ __typename?: 'KLineData', stockCode: string, time: string, open: number, high: number, low: number, close: number, preClose: number, volume: number }>, kechuang50: Array<{ __typename?: 'KLineData', stockCode: string, time: string, open: number, high: number, low: number, close: number, preClose: number, volume: number }>, csiA500: Array<{ __typename?: 'KLineData', stockCode: string, time: string, open: number, high: number, low: number, close: number, preClose: number, volume: number }>, csi300: Array<{ __typename?: 'KLineData', stockCode: string, time: string, open: number, high: number, low: number, close: number, preClose: number, volume: number }>, csi1000: Array<{ __typename?: 'KLineData', stockCode: string, time: string, open: number, high: number, low: number, close: number, preClose: number, volume: number }>, shanghai50: Array<{ __typename?: 'KLineData', stockCode: string, time: string, open: number, high: number, low: number, close: number, preClose: number, volume: number }>, shenzhen100: Array<{ __typename?: 'KLineData', stockCode: string, time: string, open: number, high: number, low: number, close: number, preClose: number, volume: number }>, csi500: Array<{ __typename?: 'KLineData', stockCode: string, time: string, open: number, high: number, low: number, close: number, preClose: number, volume: number }>, chinext50: Array<{ __typename?: 'KLineData', stockCode: string, time: string, open: number, high: number, low: number, close: number, preClose: number, volume: number }>, kechuang100: Array<{ __typename?: 'KLineData', stockCode: string, time: string, open: number, high: number, low: number, close: number, preClose: number, volume: number }> };


export const Market_IndicesDirectoryDocument = {"kind":"Document","definitions":[{"kind":"OperationDefinition","operation":"query","name":{"kind":"Name","value":"Market_IndicesDirectory"},"variableDefinitions":[{"kind":"VariableDefinition","variable":{"kind":"Variable","name":{"kind":"Name","value":"first"}},"type":{"kind":"NonNullType","type":{"kind":"NamedType","name":{"kind":"Name","value":"Int"}}}},{"kind":"VariableDefinition","variable":{"kind":"Variable","name":{"kind":"Name","value":"after"}},"type":{"kind":"NamedType","name":{"kind":"Name","value":"String"}}},{"kind":"VariableDefinition","variable":{"kind":"Variable","name":{"kind":"Name","value":"where"}},"type":{"kind":"NamedType","name":{"kind":"Name","value":"InstrumentWhereInput"}}},{"kind":"VariableDefinition","variable":{"kind":"Variable","name":{"kind":"Name","value":"orderBy"}},"type":{"kind":"NamedType","name":{"kind":"Name","value":"InstrumentOrder"}}}],"selectionSet":{"kind":"SelectionSet","selections":[{"kind":"Field","name":{"kind":"Name","value":"instrumentsConnection"},"arguments":[{"kind":"Argument","name":{"kind":"Name","value":"first"},"value":{"kind":"Variable","name":{"kind":"Name","value":"first"}}},{"kind":"Argument","name":{"kind":"Name","value":"after"},"value":{"kind":"Variable","name":{"kind":"Name","value":"after"}}},{"kind":"Argument","name":{"kind":"Name","value":"where"},"value":{"kind":"Variable","name":{"kind":"Name","value":"where"}}},{"kind":"Argument","name":{"kind":"Name","value":"orderBy"},"value":{"kind":"Variable","name":{"kind":"Name","value":"orderBy"}}}],"selectionSet":{"kind":"SelectionSet","selections":[{"kind":"Field","name":{"kind":"Name","value":"totalCount"}},{"kind":"Field","name":{"kind":"Name","value":"edges"},"selectionSet":{"kind":"SelectionSet","selections":[{"kind":"Field","name":{"kind":"Name","value":"cursor"}},{"kind":"Field","name":{"kind":"Name","value":"node"},"selectionSet":{"kind":"SelectionSet","selections":[{"kind":"Field","name":{"kind":"Name","value":"id"}},{"kind":"Field","name":{"kind":"Name","value":"instrumentId"}},{"kind":"Field","name":{"kind":"Name","value":"name"}},{"kind":"Field","name":{"kind":"Name","value":"market"}},{"kind":"Field","name":{"kind":"Name","value":"type"}}]}}]}},{"kind":"Field","name":{"kind":"Name","value":"pageInfo"},"selectionSet":{"kind":"SelectionSet","selections":[{"kind":"Field","name":{"kind":"Name","value":"hasNextPage"}},{"kind":"Field","name":{"kind":"Name","value":"endCursor"}}]}}]}}]}}]} as unknown as DocumentNode<Market_IndicesDirectoryQuery, Market_IndicesDirectoryQueryVariables>;
export const Dashboard_MarketIndexSnapshotsDocument = {"kind":"Document","definitions":[{"kind":"OperationDefinition","operation":"query","name":{"kind":"Name","value":"Dashboard_MarketIndexSnapshots"},"selectionSet":{"kind":"SelectionSet","selections":[{"kind":"Field","alias":{"kind":"Name","value":"shanghaiTick"},"name":{"kind":"Name","value":"ticks"},"arguments":[{"kind":"Argument","name":{"kind":"Name","value":"stockCode"},"value":{"kind":"StringValue","value":"000001.SH","block":false}},{"kind":"Argument","name":{"kind":"Name","value":"limit"},"value":{"kind":"IntValue","value":"1"}},{"kind":"Argument","name":{"kind":"Name","value":"order"},"value":{"kind":"StringValue","value":"desc","block":false}}],"selectionSet":{"kind":"SelectionSet","selections":[{"kind":"Field","name":{"kind":"Name","value":"stockCode"}},{"kind":"Field","name":{"kind":"Name","value":"time"}},{"kind":"Field","name":{"kind":"Name","value":"lastPrice"}},{"kind":"Field","name":{"kind":"Name","value":"open"}},{"kind":"Field","name":{"kind":"Name","value":"high"}},{"kind":"Field","name":{"kind":"Name","value":"low"}},{"kind":"Field","name":{"kind":"Name","value":"preClose"}},{"kind":"Field","name":{"kind":"Name","value":"volume"}}]}},{"kind":"Field","alias":{"kind":"Name","value":"shenzhenTick"},"name":{"kind":"Name","value":"ticks"},"arguments":[{"kind":"Argument","name":{"kind":"Name","value":"stockCode"},"value":{"kind":"StringValue","value":"399001.SZ","block":false}},{"kind":"Argument","name":{"kind":"Name","value":"limit"},"value":{"kind":"IntValue","value":"1"}},{"kind":"Argument","name":{"kind":"Name","value":"order"},"value":{"kind":"StringValue","value":"desc","block":false}}],"selectionSet":{"kind":"SelectionSet","selections":[{"kind":"Field","name":{"kind":"Name","value":"stockCode"}},{"kind":"Field","name":{"kind":"Name","value":"time"}},{"kind":"Field","name":{"kind":"Name","value":"lastPrice"}},{"kind":"Field","name":{"kind":"Name","value":"open"}},{"kind":"Field","name":{"kind":"Name","value":"high"}},{"kind":"Field","name":{"kind":"Name","value":"low"}},{"kind":"Field","name":{"kind":"Name","value":"preClose"}},{"kind":"Field","name":{"kind":"Name","value":"volume"}}]}},{"kind":"Field","alias":{"kind":"Name","value":"chinextTick"},"name":{"kind":"Name","value":"ticks"},"arguments":[{"kind":"Argument","name":{"kind":"Name","value":"stockCode"},"value":{"kind":"StringValue","value":"399006.SZ","block":false}},{"kind":"Argument","name":{"kind":"Name","value":"limit"},"value":{"kind":"IntValue","value":"1"}},{"kind":"Argument","name":{"kind":"Name","value":"order"},"value":{"kind":"StringValue","value":"desc","block":false}}],"selectionSet":{"kind":"SelectionSet","selections":[{"kind":"Field","name":{"kind":"Name","value":"stockCode"}},{"kind":"Field","name":{"kind":"Name","value":"time"}},{"kind":"Field","name":{"kind":"Name","value":"lastPrice"}},{"kind":"Field","name":{"kind":"Name","value":"open"}},{"kind":"Field","name":{"kind":"Name","value":"high"}},{"kind":"Field","name":{"kind":"Name","value":"low"}},{"kind":"Field","name":{"kind":"Name","value":"preClose"}},{"kind":"Field","name":{"kind":"Name","value":"volume"}}]}},{"kind":"Field","alias":{"kind":"Name","value":"kechuangCompositeTick"},"name":{"kind":"Name","value":"ticks"},"arguments":[{"kind":"Argument","name":{"kind":"Name","value":"stockCode"},"value":{"kind":"StringValue","value":"000680.SH","block":false}},{"kind":"Argument","name":{"kind":"Name","value":"limit"},"value":{"kind":"IntValue","value":"1"}},{"kind":"Argument","name":{"kind":"Name","value":"order"},"value":{"kind":"StringValue","value":"desc","block":false}}],"selectionSet":{"kind":"SelectionSet","selections":[{"kind":"Field","name":{"kind":"Name","value":"stockCode"}},{"kind":"Field","name":{"kind":"Name","value":"time"}},{"kind":"Field","name":{"kind":"Name","value":"lastPrice"}},{"kind":"Field","name":{"kind":"Name","value":"open"}},{"kind":"Field","name":{"kind":"Name","value":"high"}},{"kind":"Field","name":{"kind":"Name","value":"low"}},{"kind":"Field","name":{"kind":"Name","value":"preClose"}},{"kind":"Field","name":{"kind":"Name","value":"volume"}}]}},{"kind":"Field","alias":{"kind":"Name","value":"kechuang50Tick"},"name":{"kind":"Name","value":"ticks"},"arguments":[{"kind":"Argument","name":{"kind":"Name","value":"stockCode"},"value":{"kind":"StringValue","value":"000688.SH","block":false}},{"kind":"Argument","name":{"kind":"Name","value":"limit"},"value":{"kind":"IntValue","value":"1"}},{"kind":"Argument","name":{"kind":"Name","value":"order"},"value":{"kind":"StringValue","value":"desc","block":false}}],"selectionSet":{"kind":"SelectionSet","selections":[{"kind":"Field","name":{"kind":"Name","value":"stockCode"}},{"kind":"Field","name":{"kind":"Name","value":"time"}},{"kind":"Field","name":{"kind":"Name","value":"lastPrice"}},{"kind":"Field","name":{"kind":"Name","value":"open"}},{"kind":"Field","name":{"kind":"Name","value":"high"}},{"kind":"Field","name":{"kind":"Name","value":"low"}},{"kind":"Field","name":{"kind":"Name","value":"preClose"}},{"kind":"Field","name":{"kind":"Name","value":"volume"}}]}},{"kind":"Field","alias":{"kind":"Name","value":"csiA500Tick"},"name":{"kind":"Name","value":"ticks"},"arguments":[{"kind":"Argument","name":{"kind":"Name","value":"stockCode"},"value":{"kind":"StringValue","value":"000510.SH","block":false}},{"kind":"Argument","name":{"kind":"Name","value":"limit"},"value":{"kind":"IntValue","value":"1"}},{"kind":"Argument","name":{"kind":"Name","value":"order"},"value":{"kind":"StringValue","value":"desc","block":false}}],"selectionSet":{"kind":"SelectionSet","selections":[{"kind":"Field","name":{"kind":"Name","value":"stockCode"}},{"kind":"Field","name":{"kind":"Name","value":"time"}},{"kind":"Field","name":{"kind":"Name","value":"lastPrice"}},{"kind":"Field","name":{"kind":"Name","value":"open"}},{"kind":"Field","name":{"kind":"Name","value":"high"}},{"kind":"Field","name":{"kind":"Name","value":"low"}},{"kind":"Field","name":{"kind":"Name","value":"preClose"}},{"kind":"Field","name":{"kind":"Name","value":"volume"}}]}},{"kind":"Field","alias":{"kind":"Name","value":"csi300Tick"},"name":{"kind":"Name","value":"ticks"},"arguments":[{"kind":"Argument","name":{"kind":"Name","value":"stockCode"},"value":{"kind":"StringValue","value":"000300.SH","block":false}},{"kind":"Argument","name":{"kind":"Name","value":"limit"},"value":{"kind":"IntValue","value":"1"}},{"kind":"Argument","name":{"kind":"Name","value":"order"},"value":{"kind":"StringValue","value":"desc","block":false}}],"selectionSet":{"kind":"SelectionSet","selections":[{"kind":"Field","name":{"kind":"Name","value":"stockCode"}},{"kind":"Field","name":{"kind":"Name","value":"time"}},{"kind":"Field","name":{"kind":"Name","value":"lastPrice"}},{"kind":"Field","name":{"kind":"Name","value":"open"}},{"kind":"Field","name":{"kind":"Name","value":"high"}},{"kind":"Field","name":{"kind":"Name","value":"low"}},{"kind":"Field","name":{"kind":"Name","value":"preClose"}},{"kind":"Field","name":{"kind":"Name","value":"volume"}}]}},{"kind":"Field","alias":{"kind":"Name","value":"csi1000Tick"},"name":{"kind":"Name","value":"ticks"},"arguments":[{"kind":"Argument","name":{"kind":"Name","value":"stockCode"},"value":{"kind":"StringValue","value":"000852.SH","block":false}},{"kind":"Argument","name":{"kind":"Name","value":"limit"},"value":{"kind":"IntValue","value":"1"}},{"kind":"Argument","name":{"kind":"Name","value":"order"},"value":{"kind":"StringValue","value":"desc","block":false}}],"selectionSet":{"kind":"SelectionSet","selections":[{"kind":"Field","name":{"kind":"Name","value":"stockCode"}},{"kind":"Field","name":{"kind":"Name","value":"time"}},{"kind":"Field","name":{"kind":"Name","value":"lastPrice"}},{"kind":"Field","name":{"kind":"Name","value":"open"}},{"kind":"Field","name":{"kind":"Name","value":"high"}},{"kind":"Field","name":{"kind":"Name","value":"low"}},{"kind":"Field","name":{"kind":"Name","value":"preClose"}},{"kind":"Field","name":{"kind":"Name","value":"volume"}}]}},{"kind":"Field","alias":{"kind":"Name","value":"shanghai50Tick"},"name":{"kind":"Name","value":"ticks"},"arguments":[{"kind":"Argument","name":{"kind":"Name","value":"stockCode"},"value":{"kind":"StringValue","value":"000016.SH","block":false}},{"kind":"Argument","name":{"kind":"Name","value":"limit"},"value":{"kind":"IntValue","value":"1"}},{"kind":"Argument","name":{"kind":"Name","value":"order"},"value":{"kind":"StringValue","value":"desc","block":false}}],"selectionSet":{"kind":"SelectionSet","selections":[{"kind":"Field","name":{"kind":"Name","value":"stockCode"}},{"kind":"Field","name":{"kind":"Name","value":"time"}},{"kind":"Field","name":{"kind":"Name","value":"lastPrice"}},{"kind":"Field","name":{"kind":"Name","value":"open"}},{"kind":"Field","name":{"kind":"Name","value":"high"}},{"kind":"Field","name":{"kind":"Name","value":"low"}},{"kind":"Field","name":{"kind":"Name","value":"preClose"}},{"kind":"Field","name":{"kind":"Name","value":"volume"}}]}},{"kind":"Field","alias":{"kind":"Name","value":"shenzhen100Tick"},"name":{"kind":"Name","value":"ticks"},"arguments":[{"kind":"Argument","name":{"kind":"Name","value":"stockCode"},"value":{"kind":"StringValue","value":"399330.SZ","block":false}},{"kind":"Argument","name":{"kind":"Name","value":"limit"},"value":{"kind":"IntValue","value":"1"}},{"kind":"Argument","name":{"kind":"Name","value":"order"},"value":{"kind":"StringValue","value":"desc","block":false}}],"selectionSet":{"kind":"SelectionSet","selections":[{"kind":"Field","name":{"kind":"Name","value":"stockCode"}},{"kind":"Field","name":{"kind":"Name","value":"time"}},{"kind":"Field","name":{"kind":"Name","value":"lastPrice"}},{"kind":"Field","name":{"kind":"Name","value":"open"}},{"kind":"Field","name":{"kind":"Name","value":"high"}},{"kind":"Field","name":{"kind":"Name","value":"low"}},{"kind":"Field","name":{"kind":"Name","value":"preClose"}},{"kind":"Field","name":{"kind":"Name","value":"volume"}}]}},{"kind":"Field","alias":{"kind":"Name","value":"csi500Tick"},"name":{"kind":"Name","value":"ticks"},"arguments":[{"kind":"Argument","name":{"kind":"Name","value":"stockCode"},"value":{"kind":"StringValue","value":"000905.SH","block":false}},{"kind":"Argument","name":{"kind":"Name","value":"limit"},"value":{"kind":"IntValue","value":"1"}},{"kind":"Argument","name":{"kind":"Name","value":"order"},"value":{"kind":"StringValue","value":"desc","block":false}}],"selectionSet":{"kind":"SelectionSet","selections":[{"kind":"Field","name":{"kind":"Name","value":"stockCode"}},{"kind":"Field","name":{"kind":"Name","value":"time"}},{"kind":"Field","name":{"kind":"Name","value":"lastPrice"}},{"kind":"Field","name":{"kind":"Name","value":"open"}},{"kind":"Field","name":{"kind":"Name","value":"high"}},{"kind":"Field","name":{"kind":"Name","value":"low"}},{"kind":"Field","name":{"kind":"Name","value":"preClose"}},{"kind":"Field","name":{"kind":"Name","value":"volume"}}]}},{"kind":"Field","alias":{"kind":"Name","value":"chinext50Tick"},"name":{"kind":"Name","value":"ticks"},"arguments":[{"kind":"Argument","name":{"kind":"Name","value":"stockCode"},"value":{"kind":"StringValue","value":"399673.SZ","block":false}},{"kind":"Argument","name":{"kind":"Name","value":"limit"},"value":{"kind":"IntValue","value":"1"}},{"kind":"Argument","name":{"kind":"Name","value":"order"},"value":{"kind":"StringValue","value":"desc","block":false}}],"selectionSet":{"kind":"SelectionSet","selections":[{"kind":"Field","name":{"kind":"Name","value":"stockCode"}},{"kind":"Field","name":{"kind":"Name","value":"time"}},{"kind":"Field","name":{"kind":"Name","value":"lastPrice"}},{"kind":"Field","name":{"kind":"Name","value":"open"}},{"kind":"Field","name":{"kind":"Name","value":"high"}},{"kind":"Field","name":{"kind":"Name","value":"low"}},{"kind":"Field","name":{"kind":"Name","value":"preClose"}},{"kind":"Field","name":{"kind":"Name","value":"volume"}}]}},{"kind":"Field","alias":{"kind":"Name","value":"kechuang100Tick"},"name":{"kind":"Name","value":"ticks"},"arguments":[{"kind":"Argument","name":{"kind":"Name","value":"stockCode"},"value":{"kind":"StringValue","value":"000698.SH","block":false}},{"kind":"Argument","name":{"kind":"Name","value":"limit"},"value":{"kind":"IntValue","value":"1"}},{"kind":"Argument","name":{"kind":"Name","value":"order"},"value":{"kind":"StringValue","value":"desc","block":false}}],"selectionSet":{"kind":"SelectionSet","selections":[{"kind":"Field","name":{"kind":"Name","value":"stockCode"}},{"kind":"Field","name":{"kind":"Name","value":"time"}},{"kind":"Field","name":{"kind":"Name","value":"lastPrice"}},{"kind":"Field","name":{"kind":"Name","value":"open"}},{"kind":"Field","name":{"kind":"Name","value":"high"}},{"kind":"Field","name":{"kind":"Name","value":"low"}},{"kind":"Field","name":{"kind":"Name","value":"preClose"}},{"kind":"Field","name":{"kind":"Name","value":"volume"}}]}},{"kind":"Field","alias":{"kind":"Name","value":"shanghaiIndex"},"name":{"kind":"Name","value":"klines"},"arguments":[{"kind":"Argument","name":{"kind":"Name","value":"stockCode"},"value":{"kind":"StringValue","value":"000001.SH","block":false}},{"kind":"Argument","name":{"kind":"Name","value":"period"},"value":{"kind":"EnumValue","value":"DAY_1"}},{"kind":"Argument","name":{"kind":"Name","value":"limit"},"value":{"kind":"IntValue","value":"1"}},{"kind":"Argument","name":{"kind":"Name","value":"order"},"value":{"kind":"StringValue","value":"desc","block":false}}],"selectionSet":{"kind":"SelectionSet","selections":[{"kind":"Field","name":{"kind":"Name","value":"stockCode"}},{"kind":"Field","name":{"kind":"Name","value":"time"}},{"kind":"Field","name":{"kind":"Name","value":"open"}},{"kind":"Field","name":{"kind":"Name","value":"high"}},{"kind":"Field","name":{"kind":"Name","value":"low"}},{"kind":"Field","name":{"kind":"Name","value":"close"}},{"kind":"Field","name":{"kind":"Name","value":"preClose"}},{"kind":"Field","name":{"kind":"Name","value":"volume"}}]}},{"kind":"Field","alias":{"kind":"Name","value":"shenzhenComponent"},"name":{"kind":"Name","value":"klines"},"arguments":[{"kind":"Argument","name":{"kind":"Name","value":"stockCode"},"value":{"kind":"StringValue","value":"399001.SZ","block":false}},{"kind":"Argument","name":{"kind":"Name","value":"period"},"value":{"kind":"EnumValue","value":"DAY_1"}},{"kind":"Argument","name":{"kind":"Name","value":"limit"},"value":{"kind":"IntValue","value":"1"}},{"kind":"Argument","name":{"kind":"Name","value":"order"},"value":{"kind":"StringValue","value":"desc","block":false}}],"selectionSet":{"kind":"SelectionSet","selections":[{"kind":"Field","name":{"kind":"Name","value":"stockCode"}},{"kind":"Field","name":{"kind":"Name","value":"time"}},{"kind":"Field","name":{"kind":"Name","value":"open"}},{"kind":"Field","name":{"kind":"Name","value":"high"}},{"kind":"Field","name":{"kind":"Name","value":"low"}},{"kind":"Field","name":{"kind":"Name","value":"close"}},{"kind":"Field","name":{"kind":"Name","value":"preClose"}},{"kind":"Field","name":{"kind":"Name","value":"volume"}}]}},{"kind":"Field","alias":{"kind":"Name","value":"chinextIndex"},"name":{"kind":"Name","value":"klines"},"arguments":[{"kind":"Argument","name":{"kind":"Name","value":"stockCode"},"value":{"kind":"StringValue","value":"399006.SZ","block":false}},{"kind":"Argument","name":{"kind":"Name","value":"period"},"value":{"kind":"EnumValue","value":"DAY_1"}},{"kind":"Argument","name":{"kind":"Name","value":"limit"},"value":{"kind":"IntValue","value":"1"}},{"kind":"Argument","name":{"kind":"Name","value":"order"},"value":{"kind":"StringValue","value":"desc","block":false}}],"selectionSet":{"kind":"SelectionSet","selections":[{"kind":"Field","name":{"kind":"Name","value":"stockCode"}},{"kind":"Field","name":{"kind":"Name","value":"time"}},{"kind":"Field","name":{"kind":"Name","value":"open"}},{"kind":"Field","name":{"kind":"Name","value":"high"}},{"kind":"Field","name":{"kind":"Name","value":"low"}},{"kind":"Field","name":{"kind":"Name","value":"close"}},{"kind":"Field","name":{"kind":"Name","value":"preClose"}},{"kind":"Field","name":{"kind":"Name","value":"volume"}}]}},{"kind":"Field","alias":{"kind":"Name","value":"kechuangComposite"},"name":{"kind":"Name","value":"klines"},"arguments":[{"kind":"Argument","name":{"kind":"Name","value":"stockCode"},"value":{"kind":"StringValue","value":"000680.SH","block":false}},{"kind":"Argument","name":{"kind":"Name","value":"period"},"value":{"kind":"EnumValue","value":"DAY_1"}},{"kind":"Argument","name":{"kind":"Name","value":"limit"},"value":{"kind":"IntValue","value":"1"}},{"kind":"Argument","name":{"kind":"Name","value":"order"},"value":{"kind":"StringValue","value":"desc","block":false}}],"selectionSet":{"kind":"SelectionSet","selections":[{"kind":"Field","name":{"kind":"Name","value":"stockCode"}},{"kind":"Field","name":{"kind":"Name","value":"time"}},{"kind":"Field","name":{"kind":"Name","value":"open"}},{"kind":"Field","name":{"kind":"Name","value":"high"}},{"kind":"Field","name":{"kind":"Name","value":"low"}},{"kind":"Field","name":{"kind":"Name","value":"close"}},{"kind":"Field","name":{"kind":"Name","value":"preClose"}},{"kind":"Field","name":{"kind":"Name","value":"volume"}}]}},{"kind":"Field","alias":{"kind":"Name","value":"kechuang50"},"name":{"kind":"Name","value":"klines"},"arguments":[{"kind":"Argument","name":{"kind":"Name","value":"stockCode"},"value":{"kind":"StringValue","value":"000688.SH","block":false}},{"kind":"Argument","name":{"kind":"Name","value":"period"},"value":{"kind":"EnumValue","value":"DAY_1"}},{"kind":"Argument","name":{"kind":"Name","value":"limit"},"value":{"kind":"IntValue","value":"1"}},{"kind":"Argument","name":{"kind":"Name","value":"order"},"value":{"kind":"StringValue","value":"desc","block":false}}],"selectionSet":{"kind":"SelectionSet","selections":[{"kind":"Field","name":{"kind":"Name","value":"stockCode"}},{"kind":"Field","name":{"kind":"Name","value":"time"}},{"kind":"Field","name":{"kind":"Name","value":"open"}},{"kind":"Field","name":{"kind":"Name","value":"high"}},{"kind":"Field","name":{"kind":"Name","value":"low"}},{"kind":"Field","name":{"kind":"Name","value":"close"}},{"kind":"Field","name":{"kind":"Name","value":"preClose"}},{"kind":"Field","name":{"kind":"Name","value":"volume"}}]}},{"kind":"Field","alias":{"kind":"Name","value":"csiA500"},"name":{"kind":"Name","value":"klines"},"arguments":[{"kind":"Argument","name":{"kind":"Name","value":"stockCode"},"value":{"kind":"StringValue","value":"000510.SH","block":false}},{"kind":"Argument","name":{"kind":"Name","value":"period"},"value":{"kind":"EnumValue","value":"DAY_1"}},{"kind":"Argument","name":{"kind":"Name","value":"limit"},"value":{"kind":"IntValue","value":"1"}},{"kind":"Argument","name":{"kind":"Name","value":"order"},"value":{"kind":"StringValue","value":"desc","block":false}}],"selectionSet":{"kind":"SelectionSet","selections":[{"kind":"Field","name":{"kind":"Name","value":"stockCode"}},{"kind":"Field","name":{"kind":"Name","value":"time"}},{"kind":"Field","name":{"kind":"Name","value":"open"}},{"kind":"Field","name":{"kind":"Name","value":"high"}},{"kind":"Field","name":{"kind":"Name","value":"low"}},{"kind":"Field","name":{"kind":"Name","value":"close"}},{"kind":"Field","name":{"kind":"Name","value":"preClose"}},{"kind":"Field","name":{"kind":"Name","value":"volume"}}]}},{"kind":"Field","alias":{"kind":"Name","value":"csi300"},"name":{"kind":"Name","value":"klines"},"arguments":[{"kind":"Argument","name":{"kind":"Name","value":"stockCode"},"value":{"kind":"StringValue","value":"000300.SH","block":false}},{"kind":"Argument","name":{"kind":"Name","value":"period"},"value":{"kind":"EnumValue","value":"DAY_1"}},{"kind":"Argument","name":{"kind":"Name","value":"limit"},"value":{"kind":"IntValue","value":"1"}},{"kind":"Argument","name":{"kind":"Name","value":"order"},"value":{"kind":"StringValue","value":"desc","block":false}}],"selectionSet":{"kind":"SelectionSet","selections":[{"kind":"Field","name":{"kind":"Name","value":"stockCode"}},{"kind":"Field","name":{"kind":"Name","value":"time"}},{"kind":"Field","name":{"kind":"Name","value":"open"}},{"kind":"Field","name":{"kind":"Name","value":"high"}},{"kind":"Field","name":{"kind":"Name","value":"low"}},{"kind":"Field","name":{"kind":"Name","value":"close"}},{"kind":"Field","name":{"kind":"Name","value":"preClose"}},{"kind":"Field","name":{"kind":"Name","value":"volume"}}]}},{"kind":"Field","alias":{"kind":"Name","value":"csi1000"},"name":{"kind":"Name","value":"klines"},"arguments":[{"kind":"Argument","name":{"kind":"Name","value":"stockCode"},"value":{"kind":"StringValue","value":"000852.SH","block":false}},{"kind":"Argument","name":{"kind":"Name","value":"period"},"value":{"kind":"EnumValue","value":"DAY_1"}},{"kind":"Argument","name":{"kind":"Name","value":"limit"},"value":{"kind":"IntValue","value":"1"}},{"kind":"Argument","name":{"kind":"Name","value":"order"},"value":{"kind":"StringValue","value":"desc","block":false}}],"selectionSet":{"kind":"SelectionSet","selections":[{"kind":"Field","name":{"kind":"Name","value":"stockCode"}},{"kind":"Field","name":{"kind":"Name","value":"time"}},{"kind":"Field","name":{"kind":"Name","value":"open"}},{"kind":"Field","name":{"kind":"Name","value":"high"}},{"kind":"Field","name":{"kind":"Name","value":"low"}},{"kind":"Field","name":{"kind":"Name","value":"close"}},{"kind":"Field","name":{"kind":"Name","value":"preClose"}},{"kind":"Field","name":{"kind":"Name","value":"volume"}}]}},{"kind":"Field","alias":{"kind":"Name","value":"shanghai50"},"name":{"kind":"Name","value":"klines"},"arguments":[{"kind":"Argument","name":{"kind":"Name","value":"stockCode"},"value":{"kind":"StringValue","value":"000016.SH","block":false}},{"kind":"Argument","name":{"kind":"Name","value":"period"},"value":{"kind":"EnumValue","value":"DAY_1"}},{"kind":"Argument","name":{"kind":"Name","value":"limit"},"value":{"kind":"IntValue","value":"1"}},{"kind":"Argument","name":{"kind":"Name","value":"order"},"value":{"kind":"StringValue","value":"desc","block":false}}],"selectionSet":{"kind":"SelectionSet","selections":[{"kind":"Field","name":{"kind":"Name","value":"stockCode"}},{"kind":"Field","name":{"kind":"Name","value":"time"}},{"kind":"Field","name":{"kind":"Name","value":"open"}},{"kind":"Field","name":{"kind":"Name","value":"high"}},{"kind":"Field","name":{"kind":"Name","value":"low"}},{"kind":"Field","name":{"kind":"Name","value":"close"}},{"kind":"Field","name":{"kind":"Name","value":"preClose"}},{"kind":"Field","name":{"kind":"Name","value":"volume"}}]}},{"kind":"Field","alias":{"kind":"Name","value":"shenzhen100"},"name":{"kind":"Name","value":"klines"},"arguments":[{"kind":"Argument","name":{"kind":"Name","value":"stockCode"},"value":{"kind":"StringValue","value":"399330.SZ","block":false}},{"kind":"Argument","name":{"kind":"Name","value":"period"},"value":{"kind":"EnumValue","value":"DAY_1"}},{"kind":"Argument","name":{"kind":"Name","value":"limit"},"value":{"kind":"IntValue","value":"1"}},{"kind":"Argument","name":{"kind":"Name","value":"order"},"value":{"kind":"StringValue","value":"desc","block":false}}],"selectionSet":{"kind":"SelectionSet","selections":[{"kind":"Field","name":{"kind":"Name","value":"stockCode"}},{"kind":"Field","name":{"kind":"Name","value":"time"}},{"kind":"Field","name":{"kind":"Name","value":"open"}},{"kind":"Field","name":{"kind":"Name","value":"high"}},{"kind":"Field","name":{"kind":"Name","value":"low"}},{"kind":"Field","name":{"kind":"Name","value":"close"}},{"kind":"Field","name":{"kind":"Name","value":"preClose"}},{"kind":"Field","name":{"kind":"Name","value":"volume"}}]}},{"kind":"Field","alias":{"kind":"Name","value":"csi500"},"name":{"kind":"Name","value":"klines"},"arguments":[{"kind":"Argument","name":{"kind":"Name","value":"stockCode"},"value":{"kind":"StringValue","value":"000905.SH","block":false}},{"kind":"Argument","name":{"kind":"Name","value":"period"},"value":{"kind":"EnumValue","value":"DAY_1"}},{"kind":"Argument","name":{"kind":"Name","value":"limit"},"value":{"kind":"IntValue","value":"1"}},{"kind":"Argument","name":{"kind":"Name","value":"order"},"value":{"kind":"StringValue","value":"desc","block":false}}],"selectionSet":{"kind":"SelectionSet","selections":[{"kind":"Field","name":{"kind":"Name","value":"stockCode"}},{"kind":"Field","name":{"kind":"Name","value":"time"}},{"kind":"Field","name":{"kind":"Name","value":"open"}},{"kind":"Field","name":{"kind":"Name","value":"high"}},{"kind":"Field","name":{"kind":"Name","value":"low"}},{"kind":"Field","name":{"kind":"Name","value":"close"}},{"kind":"Field","name":{"kind":"Name","value":"preClose"}},{"kind":"Field","name":{"kind":"Name","value":"volume"}}]}},{"kind":"Field","alias":{"kind":"Name","value":"chinext50"},"name":{"kind":"Name","value":"klines"},"arguments":[{"kind":"Argument","name":{"kind":"Name","value":"stockCode"},"value":{"kind":"StringValue","value":"399673.SZ","block":false}},{"kind":"Argument","name":{"kind":"Name","value":"period"},"value":{"kind":"EnumValue","value":"DAY_1"}},{"kind":"Argument","name":{"kind":"Name","value":"limit"},"value":{"kind":"IntValue","value":"1"}},{"kind":"Argument","name":{"kind":"Name","value":"order"},"value":{"kind":"StringValue","value":"desc","block":false}}],"selectionSet":{"kind":"SelectionSet","selections":[{"kind":"Field","name":{"kind":"Name","value":"stockCode"}},{"kind":"Field","name":{"kind":"Name","value":"time"}},{"kind":"Field","name":{"kind":"Name","value":"open"}},{"kind":"Field","name":{"kind":"Name","value":"high"}},{"kind":"Field","name":{"kind":"Name","value":"low"}},{"kind":"Field","name":{"kind":"Name","value":"close"}},{"kind":"Field","name":{"kind":"Name","value":"preClose"}},{"kind":"Field","name":{"kind":"Name","value":"volume"}}]}},{"kind":"Field","alias":{"kind":"Name","value":"kechuang100"},"name":{"kind":"Name","value":"klines"},"arguments":[{"kind":"Argument","name":{"kind":"Name","value":"stockCode"},"value":{"kind":"StringValue","value":"000698.SH","block":false}},{"kind":"Argument","name":{"kind":"Name","value":"period"},"value":{"kind":"EnumValue","value":"DAY_1"}},{"kind":"Argument","name":{"kind":"Name","value":"limit"},"value":{"kind":"IntValue","value":"1"}},{"kind":"Argument","name":{"kind":"Name","value":"order"},"value":{"kind":"StringValue","value":"desc","block":false}}],"selectionSet":{"kind":"SelectionSet","selections":[{"kind":"Field","name":{"kind":"Name","value":"stockCode"}},{"kind":"Field","name":{"kind":"Name","value":"time"}},{"kind":"Field","name":{"kind":"Name","value":"open"}},{"kind":"Field","name":{"kind":"Name","value":"high"}},{"kind":"Field","name":{"kind":"Name","value":"low"}},{"kind":"Field","name":{"kind":"Name","value":"close"}},{"kind":"Field","name":{"kind":"Name","value":"preClose"}},{"kind":"Field","name":{"kind":"Name","value":"volume"}}]}}]}}]} as unknown as DocumentNode<Dashboard_MarketIndexSnapshotsQuery, Dashboard_MarketIndexSnapshotsQueryVariables>;