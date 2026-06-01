import {
  type StrategyRunMode,
  type StrategyRunStatus,
} from '@/generated/gql/graphql';

export type StrategyRunModeKey = 'BACKTEST' | 'PAPER' | 'LIVE';
export type StrategyRunStatusKey =
  | 'PENDING'
  | 'RUNNING'
  | 'PAUSED'
  | 'STOPPED'
  | 'COMPLETED'
  | 'ERROR';

export type StrategyRunActionId =
  | 'view_detail'
  | 'edit_parameters'
  | 'delete'
  | 'view_logs'
  | 'view_error'
  | 'view_performance'
  | 'start_backtest'
  | 'stop_backtest'
  | 'resume_backtest'
  | 'rerun_backtest'
  | 'clone_to_paper'
  | 'clone_to_live'
  | 'start_paper'
  | 'pause_paper'
  | 'resume_paper'
  | 'stop_paper'
  | 'clone_paper'
  | 'start_live'
  | 'pause_live'
  | 'resume_live'
  | 'stop_live';

export type StrategyRunTone =
  | 'slate'
  | 'blue'
  | 'emerald'
  | 'amber'
  | 'rose'
  | 'purple';

export interface StrategyRunAction {
  id: StrategyRunActionId;
  label: string;
  tone?: StrategyRunTone;
  dangerous?: boolean;
}

export interface StrategyRunStateView {
  mode: StrategyRunModeKey;
  status: StrategyRunStatusKey;
  modeLabel: string;
  statusLabel: string;
  color: StrategyRunTone;
  icon: 'clock' | 'activity' | 'pause' | 'stop' | 'check' | 'error';
  isActive: boolean;
  isTerminal: boolean;
  canDelete: boolean;
  listPrimaryAction: StrategyRunAction;
  detailPrimaryAction?: StrategyRunAction;
  detailSecondaryActions: StrategyRunAction[];
}

const VIEW_DETAIL_ACTION: StrategyRunAction = {
  id: 'view_detail',
  label: '查看详情',
  tone: 'blue',
};

const EDIT_ACTION: StrategyRunAction = {
  id: 'edit_parameters',
  label: '编辑参数',
  tone: 'slate',
};

const DELETE_ACTION: StrategyRunAction = {
  id: 'delete',
  label: '删除',
  tone: 'rose',
  dangerous: true,
};

const VIEW_LOGS_ACTION: StrategyRunAction = {
  id: 'view_logs',
  label: '查看日志',
  tone: 'slate',
};

const VIEW_ERROR_ACTION: StrategyRunAction = {
  id: 'view_error',
  label: '查看错误',
  tone: 'rose',
};

const VIEW_PERFORMANCE_ACTION: StrategyRunAction = {
  id: 'view_performance',
  label: '查看绩效',
  tone: 'blue',
};

const MODE_LABELS: Record<StrategyRunModeKey, string> = {
  BACKTEST: '回测',
  PAPER: '模拟',
  LIVE: '实盘',
};

const STATUS_META: Record<
  StrategyRunStatusKey,
  Pick<StrategyRunStateView, 'color' | 'icon' | 'isActive' | 'isTerminal'>
> = {
  PENDING: {
    color: 'slate',
    icon: 'clock',
    isActive: false,
    isTerminal: false,
  },
  RUNNING: {
    color: 'emerald',
    icon: 'activity',
    isActive: true,
    isTerminal: false,
  },
  PAUSED: {
    color: 'amber',
    icon: 'pause',
    isActive: true,
    isTerminal: false,
  },
  STOPPED: {
    color: 'slate',
    icon: 'stop',
    isActive: false,
    isTerminal: true,
  },
  COMPLETED: {
    color: 'blue',
    icon: 'check',
    isActive: false,
    isTerminal: true,
  },
  ERROR: {
    color: 'rose',
    icon: 'error',
    isActive: false,
    isTerminal: true,
  },
};

const MATRIX: Record<
  StrategyRunModeKey,
  Record<
    StrategyRunStatusKey,
    {
      statusLabel: string;
      primary?: StrategyRunAction;
      secondary: StrategyRunAction[];
    }
  >
> = {
  BACKTEST: {
    PENDING: {
      statusLabel: '待回测',
      primary: { id: 'start_backtest', label: '开始回测', tone: 'blue' },
      secondary: [EDIT_ACTION],
    },
    RUNNING: {
      statusLabel: '回测中',
      primary: {
        id: 'stop_backtest',
        label: '停止回测',
        tone: 'rose',
        dangerous: true,
      },
      secondary: [VIEW_LOGS_ACTION],
    },
    PAUSED: {
      statusLabel: '回测已暂停',
      primary: { id: 'resume_backtest', label: '继续回测', tone: 'blue' },
      secondary: [
        {
          id: 'stop_backtest',
          label: '停止回测',
          tone: 'rose',
          dangerous: true,
        },
        EDIT_ACTION,
      ],
    },
    STOPPED: {
      statusLabel: '回测已停止',
      primary: { id: 'rerun_backtest', label: '重新回测', tone: 'purple' },
      secondary: [EDIT_ACTION],
    },
    COMPLETED: {
      statusLabel: '回测完成',
      primary: { id: 'rerun_backtest', label: '重新回测', tone: 'purple' },
      secondary: [
        { id: 'clone_to_paper', label: '转模拟盘', tone: 'emerald' },
        {
          id: 'clone_to_live',
          label: '转实盘',
          tone: 'rose',
          dangerous: true,
        },
        VIEW_PERFORMANCE_ACTION,
      ],
    },
    ERROR: {
      statusLabel: '回测失败',
      primary: { id: 'rerun_backtest', label: '重新回测', tone: 'purple' },
      secondary: [VIEW_ERROR_ACTION, EDIT_ACTION],
    },
  },
  PAPER: {
    PENDING: {
      statusLabel: '模拟待启动',
      primary: { id: 'start_paper', label: '启动模拟', tone: 'emerald' },
      secondary: [EDIT_ACTION],
    },
    RUNNING: {
      statusLabel: '模拟运行中',
      primary: { id: 'pause_paper', label: '暂停模拟', tone: 'amber' },
      secondary: [
        {
          id: 'stop_paper',
          label: '停止模拟',
          tone: 'rose',
          dangerous: true,
        },
        VIEW_LOGS_ACTION,
      ],
    },
    PAUSED: {
      statusLabel: '模拟已暂停',
      primary: { id: 'resume_paper', label: '恢复模拟', tone: 'emerald' },
      secondary: [
        {
          id: 'stop_paper',
          label: '停止模拟',
          tone: 'rose',
          dangerous: true,
        },
        EDIT_ACTION,
      ],
    },
    STOPPED: {
      statusLabel: '模拟已停止',
      primary: { id: 'clone_paper', label: '复制为模拟', tone: 'emerald' },
      secondary: [],
    },
    COMPLETED: {
      statusLabel: '模拟已停止',
      primary: { id: 'clone_paper', label: '复制为模拟', tone: 'emerald' },
      secondary: [],
    },
    ERROR: {
      statusLabel: '模拟异常',
      primary: { id: 'clone_paper', label: '复制为模拟', tone: 'emerald' },
      secondary: [VIEW_ERROR_ACTION],
    },
  },
  LIVE: {
    PENDING: {
      statusLabel: '实盘待启动',
      primary: {
        id: 'start_live',
        label: '启动实盘',
        tone: 'rose',
        dangerous: true,
      },
      secondary: [EDIT_ACTION],
    },
    RUNNING: {
      statusLabel: '实盘运行中',
      primary: { id: 'pause_live', label: '暂停实盘', tone: 'amber' },
      secondary: [
        {
          id: 'stop_live',
          label: '停止实盘',
          tone: 'rose',
          dangerous: true,
        },
      ],
    },
    PAUSED: {
      statusLabel: '实盘已暂停',
      primary: { id: 'resume_live', label: '恢复实盘', tone: 'emerald' },
      secondary: [
        {
          id: 'stop_live',
          label: '停止实盘',
          tone: 'rose',
          dangerous: true,
        },
        EDIT_ACTION,
      ],
    },
    STOPPED: {
      statusLabel: '实盘已停止',
      secondary: [],
    },
    COMPLETED: {
      statusLabel: '实盘已停止',
      secondary: [],
    },
    ERROR: {
      statusLabel: '实盘异常',
      secondary: [VIEW_ERROR_ACTION],
    },
  },
};

export function normalizeStrategyRunMode(
  mode?: StrategyRunMode | string | null
): StrategyRunModeKey {
  const value = String(mode || '')
    .trim()
    .toUpperCase();
  if (value === 'PAPER') return 'PAPER';
  if (value === 'LIVE') return 'LIVE';
  return 'BACKTEST';
}

export function normalizeStrategyRunStatus(
  status?: StrategyRunStatus | string | null
): StrategyRunStatusKey {
  const value = String(status || '')
    .trim()
    .toUpperCase();
  if (value === 'RUNNING') return 'RUNNING';
  if (value === 'PAUSED') return 'PAUSED';
  if (value === 'STOPPED') return 'STOPPED';
  if (value === 'COMPLETED') return 'COMPLETED';
  if (value === 'ERROR' || value === 'FAILED') return 'ERROR';
  return 'PENDING';
}

export function getStrategyRunState(
  mode?: StrategyRunMode | string | null,
  status?: StrategyRunStatus | string | null
): StrategyRunStateView {
  const normalizedMode = normalizeStrategyRunMode(mode);
  const normalizedStatus = normalizeStrategyRunStatus(status);
  const state = MATRIX[normalizedMode][normalizedStatus];
  const meta = STATUS_META[normalizedStatus];
  const canDelete = meta.isTerminal;

  return {
    mode: normalizedMode,
    status: normalizedStatus,
    modeLabel: MODE_LABELS[normalizedMode],
    statusLabel: state.statusLabel,
    color: normalizedStatus === 'RUNNING' ? meta.color : meta.color,
    icon: meta.icon,
    isActive: meta.isActive,
    isTerminal: meta.isTerminal,
    canDelete,
    listPrimaryAction: VIEW_DETAIL_ACTION,
    detailPrimaryAction: state.primary,
    detailSecondaryActions: canDelete
      ? [...state.secondary, DELETE_ACTION]
      : state.secondary,
  };
}
