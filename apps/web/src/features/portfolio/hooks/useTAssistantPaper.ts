import { useState } from 'react';
import { useQuery } from 'urql';

import type {
  Portfolio_TAssistantPaperExecutionsQuery,
  Portfolio_TAssistantPaperFactsQuery,
} from '@/generated/paper/graphql';

import {
  TAssistantPaperExecutionsQuery,
  TAssistantPaperFactsQuery,
} from './tAssistantPaperQueries';

export type PaperSection =
  'opportunities' | 'allocations' | 'reasons' | 'orders' | 'exitPlans';
export type PaperExecution =
  Portfolio_TAssistantPaperExecutionsQuery['tAssistantPaperExecutions']['nodes'][number];
export type PaperFacts = Portfolio_TAssistantPaperFactsQuery;

const initial = (accountId: string) => ({
  accountId,
  selectedId: '',
  factScope: '',
  section: 'opportunities' as PaperSection,
  executions: [null] as (string | null)[],
  facts: [null] as (string | null)[],
});

export function useTAssistantPaper(accountId: string) {
  const [state, setState] = useState(() => initial(accountId));
  const current = state.accountId === accountId ? state : initial(accountId);
  if (state.accountId !== accountId) setState(current);
  const [list, refreshList] = useQuery({
    query: TAssistantPaperExecutionsQuery,
    variables: { accountId, first: 20, after: current.executions.at(-1) },
    pause: !accountId,
    requestPolicy: 'cache-and-network',
  });
  const listCurrent =
    list.operation?.variables.accountId === accountId &&
    list.operation.variables.after === current.executions.at(-1);
  const executionPage = listCurrent
    ? list.data?.tAssistantPaperExecutions
    : undefined;
  const executionId =
    current.selectedId || executionPage?.nodes[0]?.executionId || '';
  const factHistory =
    current.factScope === executionId ? current.facts : [null];
  if (current.factScope !== executionId) {
    setState({ ...current, factScope: executionId, facts: [null] });
  }
  const [detail, refreshDetail] = useQuery({
    query: TAssistantPaperFactsQuery,
    variables: {
      accountId,
      executionId,
      first: 20,
      after: factHistory.at(-1),
      opportunities: current.section === 'opportunities',
      allocations: current.section === 'allocations',
      reasons: current.section === 'reasons',
      orders: current.section === 'orders',
      exitPlans: current.section === 'exitPlans',
    },
    pause: !accountId || !executionId,
    requestPolicy: 'cache-and-network',
  });
  const detailCurrent =
    detail.operation?.variables.accountId === accountId &&
    detail.operation.variables.executionId === executionId &&
    detail.operation.variables.after === factHistory.at(-1) &&
    detail.operation.variables[current.section] === true;
  const facts = detailCurrent ? detail.data : undefined;
  const execution = facts?.tAssistantPaperExecution;
  const scopeError = executionPage?.nodes.some(
    item => item.environment !== 'PAPER'
  )
    ? '执行列表范围不匹配，请刷新重试。'
    : execution &&
        (execution.executionId !== executionId ||
          execution.environment !== 'PAPER')
      ? '执行范围不匹配，请刷新后重试。'
      : null;
  const page =
    facts?.[
      (
        {
          opportunities: 'tAssistantPaperOpportunities',
          allocations: 'tAssistantPaperAllocations',
          reasons: 'tAssistantPaperReasons',
          orders: 'tAssistantPaperOrders',
          exitPlans: 'tAssistantPaperExitPlans',
        } as const
      )[current.section]
    ];
  return {
    accountId,
    executionId,
    section: current.section,
    executions: scopeError ? [] : (executionPage?.nodes ?? []),
    execution: scopeError ? null : execution,
    facts: scopeError ? undefined : facts,
    listLoading: list.fetching,
    loading: detail.fetching,
    error:
      scopeError ||
      (listCurrent ? list.error?.message : null) ||
      (detailCurrent ? detail.error?.message : null) ||
      null,
    executionPage: current.executions.length,
    factPage: factHistory.length,
    hasNextExecutionPage: Boolean(executionPage?.pageInfo.hasNextPage),
    hasNextFactPage: Boolean(page?.pageInfo.hasNextPage),
    selectExecution: (selectedId: string) =>
      setState({
        ...current,
        selectedId,
        factScope: selectedId,
        facts: [null],
      }),
    selectSection: (section: PaperSection) =>
      setState({ ...current, section, facts: [null] }),
    previousExecutions: () => {
      if (current.executions.length > 1)
        setState({
          ...current,
          selectedId: '',
          executions: current.executions.slice(0, -1),
          facts: [null],
        });
    },
    nextExecutions: () => {
      if (
        executionPage?.pageInfo.hasNextPage &&
        executionPage.pageInfo.endCursor
      )
        setState({
          ...current,
          selectedId: '',
          executions: [...current.executions, executionPage.pageInfo.endCursor],
          facts: [null],
        });
    },
    previousFacts: () => {
      if (factHistory.length > 1)
        setState({ ...current, facts: factHistory.slice(0, -1) });
    },
    nextFacts: () => {
      if (page?.pageInfo.hasNextPage && page.pageInfo.endCursor)
        setState({
          ...current,
          facts: [...factHistory, page.pageInfo.endCursor],
        });
    },
    refresh: () => {
      refreshList({ requestPolicy: 'network-only' });
      refreshDetail({ requestPolicy: 'network-only' });
    },
  };
}
