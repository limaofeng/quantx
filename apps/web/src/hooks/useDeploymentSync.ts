/**
 * useDeploymentSync - 统一的 Deployment 同步管理 Hook
 *
 * 封装了获取部署状态、实时订阅状态变化和触发同步的完整逻辑。
 * 用于替代各页面/组件中重复的 GraphQL 定义和同步逻辑。
 */

import { useQuery, useMutation, useSubscription } from 'urql';

import { gql } from '@/generated/gql';
import { useToast } from '@/hooks/use-toast';

// ===== GraphQL 定义 =====

const GET_DEPLOYMENT_BY_NAME = gql(`
  query GetDeploymentByName($name: String!) {
    getDeploymentByName(name: $name) {
      id
      name
      flowName
      description
      workPoolName
      isScheduleActive
      lastRunTime
      nextRunTime
      status
      activeRunId
      activeRunStatus
      isStale
      staleReason
      latestActivityTime
    }
  }
`);

const RUN_DEPLOYMENT = gql(`
  mutation RunDeployment($deploymentId: String!, $parameters: JSON) {
    runDeployment(deploymentId: $deploymentId, parameters: $parameters) {
      id
      state
    }
  }
`);

const CANCEL_FLOW_RUN = gql(`
  mutation CancelDeploymentActiveRun($runId: String!) {
    cancelFlowRun(runId: $runId) {
      success
      message
      data
    }
  }
`);

const SET_DEPLOYMENT_SCHEDULE_ACTIVE = gql(`
  mutation SetDeploymentScheduleActive($deploymentId: String!, $active: Boolean!) {
    setDeploymentScheduleActive(deploymentId: $deploymentId, active: $active) {
      id
      name
      flowName
      description
      workPoolName
      isScheduleActive
      lastRunTime
      nextRunTime
      status
      activeRunId
      activeRunStatus
      isStale
      staleReason
      latestActivityTime
    }
  }
`);

const DEPLOYMENT_STATUS_SUBSCRIPTION = gql(`
  subscription DeploymentStatus($name: String!) {
    deploymentStatus(name: $name) {
      id
      name
      flowName
      description
      workPoolName
      isScheduleActive
      lastRunTime
      nextRunTime
      status
      activeRunId
      activeRunStatus
      isStale
      staleReason
      latestActivityTime
    }
  }
`);

// ===== 类型定义 =====

export interface DeploymentStatus {
  id: string;
  name: string;
  flowName: string;
  description: string;
  workPoolName: string;
  isScheduleActive: boolean;
  lastRunTime: string | null;
  nextRunTime: string | null;
  status: string | null;
  activeRunId: string | null;
  activeRunStatus: string | null;
  isStale: boolean;
  staleReason: string | null;
  latestActivityTime: string | null;
}

export interface UseDeploymentSyncOptions {
  /** 自定义成功提示 */
  successMessage?: string;
  /** 自定义失败提示 */
  errorMessage?: string;
}

export interface UseDeploymentSyncResult {
  /** 当前部署状态（订阅优先于查询） */
  deployment: DeploymentStatus | undefined;
  /** 是否正在同步 */
  isSyncing: boolean;
  /** 触发同步 */
  triggerSync: (
    parameters?: Record<string, unknown>
  ) => Promise<string | undefined>;
  /** 停止当前活跃运行 */
  cancelActiveRun: () => Promise<boolean>;
  /** 启用/暂停自动调度 */
  setScheduleActive: (active: boolean) => Promise<boolean>;
  /** 初始加载中 */
  isLoading: boolean;
  /** 自动调度状态更新中 */
  isScheduleUpdating: boolean;
  /** 当前运行停止中 */
  isRunCancelling: boolean;
}

// ===== Hook 实现 =====

export function useDeploymentSync(
  deploymentName: string,
  options: UseDeploymentSyncOptions = {}
): UseDeploymentSyncResult {
  const { toast } = useToast();

  const {
    successMessage = '同步已启动',
    errorMessage = '无法连接到 Prefect 管理器',
  } = options;

  // 初始获取部署状态
  const [{ data: queryData, fetching: isLoading }, refreshDeployment] =
    useQuery({
      query: GET_DEPLOYMENT_BY_NAME,
      variables: { name: deploymentName },
    });

  // 实时订阅部署状态
  const [{ data: subscriptionData }] = useSubscription({
    query: DEPLOYMENT_STATUS_SUBSCRIPTION,
    variables: { name: deploymentName },
  });

  const [scheduleResult, setDeploymentScheduleActive] = useMutation(
    SET_DEPLOYMENT_SCHEDULE_ACTIVE
  );
  const [cancelResult, cancelFlowRun] = useMutation(CANCEL_FLOW_RUN);

  // 合并部署状态（订阅优先）
  const deploymentPayload =
    scheduleResult.data?.setDeploymentScheduleActive ||
    subscriptionData?.deploymentStatus ||
    queryData?.getDeploymentByName;
  const deployment: DeploymentStatus | undefined = deploymentPayload
    ? {
        id: deploymentPayload.id,
        name: deploymentPayload.name,
        flowName: deploymentPayload.flowName,
        description: deploymentPayload.description || '',
        workPoolName: deploymentPayload.workPoolName || '',
        isScheduleActive: deploymentPayload.isScheduleActive,
        lastRunTime: deploymentPayload.lastRunTime || null,
        nextRunTime: deploymentPayload.nextRunTime || null,
        status: deploymentPayload.status || null,
        activeRunId: deploymentPayload.activeRunId || null,
        activeRunStatus: deploymentPayload.activeRunStatus || null,
        isStale: deploymentPayload.isStale,
        staleReason: deploymentPayload.staleReason || null,
        latestActivityTime: deploymentPayload.latestActivityTime || null,
      }
    : undefined;

  // 同步 Mutation
  const [syncResult, runDeployment] = useMutation(RUN_DEPLOYMENT);

  // 派生同步状态：status 保留 Prefect 原始活跃状态，isStale 只是诊断标记。
  const isSyncing =
    syncResult.fetching ||
    ['Running', 'Pending', 'Cancelling', 'Late'].includes(
      deployment?.status ?? ''
    );

  // 触发同步函数
  const triggerSync = async (parameters?: Record<string, unknown>) => {
    if (!deployment?.id) {
      toast({
        title: '无法同步',
        description: `未找到 ${deploymentName} 部署`,
        variant: 'destructive',
      });
      return undefined;
    }

    try {
      // 提交同步请求
      const result = await runDeployment({
        deploymentId: deployment.id,
        parameters,
      });

      if (result.error) {
        throw result.error;
      }

      if (result.data?.runDeployment) {
        const runId = result.data.runDeployment.id;
        toast({
          title: successMessage,
          description: `运行 ID: ${runId?.substring(0, 8)}...`,
          variant: 'success',
        });
        return runId;
      }
    } catch (e) {
      toast({
        title: '提交失败',
        description: e instanceof Error ? e.message : errorMessage,
        variant: 'destructive',
      });
    }
    return undefined;
  };

  const cancelActiveRun = async () => {
    const runId = deployment?.activeRunId;
    if (!runId) {
      toast({
        title: '无法停止',
        description: `未找到 ${deploymentName} 的运行中任务`,
        variant: 'destructive',
      });
      return false;
    }

    try {
      const result = await cancelFlowRun({ runId });

      if (result.error) {
        throw result.error;
      }

      const payload = result.data?.cancelFlowRun;
      if (!payload?.success) {
        throw new Error(payload?.message || '取消请求未被接受');
      }

      toast({
        title: '已提交停止',
        description: `运行 ID: ${runId.substring(0, 8)}...`,
        variant: 'success',
      });
      refreshDeployment({ requestPolicy: 'network-only' });
      return true;
    } catch (e) {
      toast({
        title: '停止失败',
        description: e instanceof Error ? e.message : errorMessage,
        variant: 'destructive',
      });
    }

    return false;
  };

  const setScheduleActive = async (active: boolean) => {
    if (!deployment?.id) {
      toast({
        title: active ? '无法恢复调度' : '无法暂停调度',
        description: `未找到 ${deploymentName} 部署`,
        variant: 'destructive',
      });
      return false;
    }

    try {
      const result = await setDeploymentScheduleActive({
        deploymentId: deployment.id,
        active,
      });

      if (result.error) {
        throw result.error;
      }

      if (result.data?.setDeploymentScheduleActive) {
        toast({
          title: active ? '自动调度已恢复' : '自动调度已暂停',
          description: result.data.setDeploymentScheduleActive.flowName,
          variant: 'success',
        });
        return true;
      }
    } catch (e) {
      toast({
        title: active ? '恢复调度失败' : '暂停调度失败',
        description: e instanceof Error ? e.message : errorMessage,
        variant: 'destructive',
      });
    }

    return false;
  };

  return {
    deployment,
    isSyncing,
    triggerSync,
    cancelActiveRun,
    setScheduleActive,
    isLoading,
    isScheduleUpdating: scheduleResult.fetching,
    isRunCancelling: cancelResult.fetching,
  };
}
