import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useMutation, useQuery, useSubscription } from 'urql';

import { useCurrentAccount } from '@/features/dashboard/hooks';
import { useFragment as readFragment } from '@/generated/gql/fragment-masking';
import {
  AiAssistantApprovalDecision,
  AiAssistantCapabilitiesDocument,
  AiAssistantEventsDocument,
  AiAssistantEventType,
  AiAssistantMessageFieldsFragmentDoc,
  AiAssistantMessagesDocument,
  AiAssistantRunFieldsFragmentDoc,
  AiAssistantRunStatus,
  AiAssistantThreadFieldsFragmentDoc,
  AiAssistantThreadsDocument,
  CancelAiAssistantRunDocument,
  CreateAiAssistantThreadDocument,
  DeleteAiAssistantThreadDocument,
  ResolveAiAssistantApprovalDocument,
  RetryAiAssistantRunDocument,
  SendAiAssistantMessageDocument,
  UpdateAiAssistantThreadDocument,
  type AiAssistantRunFieldsFragment,
} from '@/generated/gql/graphql';
import { createClientId } from '@/utils/clientId';

export interface PendingAssistantApproval {
  runId: string;
  toolCallId: string;
  toolName: string;
  summary: string | null;
}

export function useAiAssistant(currentPath: string) {
  const { data: currentAccountData } = useCurrentAccount();
  const currentAccountId = currentAccountData?.currentAccount?.id || null;
  const [selectedThreadId, setSelectedThreadId] = useState<string | null>(null);
  const [streamingText, setStreamingText] = useState('');
  const [activeRun, setActiveRun] =
    useState<AiAssistantRunFieldsFragment | null>(null);
  const [pendingApprovals, setPendingApprovals] = useState<
    PendingAssistantApproval[]
  >([]);
  const lastEventSequence = useRef(0);

  const [capabilitiesResult] = useQuery({
    query: AiAssistantCapabilitiesDocument,
    requestPolicy: 'cache-and-network',
  });
  const [threadsResult, refreshThreads] = useQuery({
    query: AiAssistantThreadsDocument,
    variables: { first: 30, after: null },
    requestPolicy: 'cache-and-network',
  });
  const threads = useMemo(
    () => [
      ...readFragment(
        AiAssistantThreadFieldsFragmentDoc,
        threadsResult.data?.aiAssistantThreads.edges.map(edge => edge.node) ||
          []
      ),
    ],
    [threadsResult.data]
  );

  useEffect(() => {
    if (!selectedThreadId && threads[0]?.id) {
      setSelectedThreadId(threads[0].id);
    }
  }, [selectedThreadId, threads]);

  useEffect(() => {
    lastEventSequence.current = 0;
    setStreamingText('');
    setActiveRun(null);
    setPendingApprovals([]);
  }, [selectedThreadId]);

  const [messagesResult, refreshMessages] = useQuery({
    query: AiAssistantMessagesDocument,
    variables: {
      threadId: selectedThreadId || '',
      afterSequence: 0,
      limit: 200,
    },
    pause: !selectedThreadId,
    requestPolicy: 'cache-and-network',
  });

  const [, createThreadMutation] = useMutation(CreateAiAssistantThreadDocument);
  const [, sendMessageMutation] = useMutation(SendAiAssistantMessageDocument);
  const [, cancelRunMutation] = useMutation(CancelAiAssistantRunDocument);
  const [, resolveApprovalMutation] = useMutation(
    ResolveAiAssistantApprovalDocument
  );
  const [, retryRunMutation] = useMutation(RetryAiAssistantRunDocument);
  const [, updateThreadMutation] = useMutation(UpdateAiAssistantThreadDocument);
  const [, deleteThreadMutation] = useMutation(DeleteAiAssistantThreadDocument);

  const [eventsResult] = useSubscription(
    {
      query: AiAssistantEventsDocument,
      variables: {
        threadId: selectedThreadId || '',
        afterSequence: 0,
      },
      pause: !selectedThreadId,
    },
    (_previous, data) => data
  );

  useEffect(() => {
    const event = eventsResult.data?.aiAssistantEvents;
    if (
      !event ||
      event.threadId !== selectedThreadId ||
      event.sequence <= lastEventSequence.current
    )
      return;
    lastEventSequence.current = event.sequence;
    const eventType = event.eventType;
    const eventRun = readFragment(AiAssistantRunFieldsFragmentDoc, event.run);
    if (eventType === AiAssistantEventType.MessageDelta && event.text) {
      setStreamingText(current => current + event.text);
    }
    if (eventRun) setActiveRun(eventRun);
    if (eventType === AiAssistantEventType.MessageCompleted) {
      setStreamingText('');
      void refreshMessages({ requestPolicy: 'network-only' });
      void refreshThreads({ requestPolicy: 'network-only' });
    }
    if (
      eventType === AiAssistantEventType.ApprovalRequired &&
      event.toolCallId &&
      event.toolName
    ) {
      const toolCallId = event.toolCallId;
      const toolName = event.toolName;
      setPendingApprovals(current =>
        current.some(item => item.toolCallId === toolCallId)
          ? current
          : [
              ...current,
              {
                runId: event.runId,
                toolCallId,
                toolName,
                summary: event.toolSummary || null,
              },
            ]
      );
    }
    if (
      eventType === AiAssistantEventType.ToolCallCompleted &&
      event.toolCallId
    ) {
      setPendingApprovals(current =>
        current.filter(item => item.toolCallId !== event.toolCallId)
      );
    }
    if (
      eventType === AiAssistantEventType.RunFailed ||
      eventRun?.status === AiAssistantRunStatus.Cancelled ||
      eventRun?.status === AiAssistantRunStatus.Completed
    ) {
      setPendingApprovals([]);
    }
  }, [eventsResult.data, refreshMessages, refreshThreads, selectedThreadId]);

  const createThread = useCallback(async () => {
    const result = await createThreadMutation({
      input: { agentId: 'research_assistant', title: null, accountId: null },
    });
    if (result.error) throw result.error;
    const thread = readFragment(
      AiAssistantThreadFieldsFragmentDoc,
      result.data?.createAiAssistantThread
    );
    if (!thread) throw new Error('创建对话失败');
    setSelectedThreadId(thread.id);
    void refreshThreads({ requestPolicy: 'network-only' });
    return thread.id;
  }, [createThreadMutation, refreshThreads]);

  const sendMessage = useCallback(
    async (text: string, attachAccount: boolean) => {
      const threadId = selectedThreadId || (await createThread());
      setStreamingText('');
      const result = await sendMessageMutation({
        input: {
          threadId,
          text,
          clientMessageId: createClientId('ai-message'),
          routeContext: { path: currentPath, objectType: 'CURRENT_ROUTE' },
          contextRefs:
            attachAccount && currentAccountId
              ? [
                  {
                    kind: 'PORTFOLIO_ACCOUNT',
                    objectId: currentAccountId,
                    label: '当前资金账户',
                  },
                ]
              : [],
        },
      });
      if (result.error) throw result.error;
      const run = readFragment(
        AiAssistantRunFieldsFragmentDoc,
        result.data?.sendAiAssistantMessage
      );
      if (run) {
        setActiveRun(run);
      }
      void refreshMessages({ requestPolicy: 'network-only' });
    },
    [
      createThread,
      currentPath,
      refreshMessages,
      selectedThreadId,
      sendMessageMutation,
      currentAccountId,
    ]
  );

  const cancelRun = useCallback(async () => {
    if (!activeRun) return;
    const result = await cancelRunMutation({ runId: activeRun.id });
    if (result.error) throw result.error;
    const run = readFragment(
      AiAssistantRunFieldsFragmentDoc,
      result.data?.cancelAiAssistantRun
    );
    if (run) {
      setActiveRun(run);
    }
  }, [activeRun, cancelRunMutation]);

  const resolveApproval = useCallback(
    async (approval: PendingAssistantApproval, approved: boolean) => {
      const result = await resolveApprovalMutation({
        input: {
          runId: approval.runId,
          toolCallId: approval.toolCallId,
          decision: approved
            ? AiAssistantApprovalDecision.Approve
            : AiAssistantApprovalDecision.Reject,
        },
      });
      if (result.error) throw result.error;
      setPendingApprovals(current =>
        current.filter(item => item.toolCallId !== approval.toolCallId)
      );
      const run = readFragment(
        AiAssistantRunFieldsFragmentDoc,
        result.data?.resolveAiAssistantApproval
      );
      if (run) {
        setActiveRun(run);
      }
    },
    [resolveApprovalMutation]
  );

  const retryRun = useCallback(async () => {
    if (!activeRun) return;
    const result = await retryRunMutation({ runId: activeRun.id });
    if (result.error) throw result.error;
    const run = readFragment(
      AiAssistantRunFieldsFragmentDoc,
      result.data?.retryAiAssistantRun
    );
    if (run) {
      setActiveRun(run);
      setStreamingText('');
    }
  }, [activeRun, retryRunMutation]);

  const setExternalSearch = useCallback(
    async (enabled: boolean) => {
      if (!selectedThreadId) return;
      const result = await updateThreadMutation({
        input: {
          threadId: selectedThreadId,
          externalSearchEnabled: enabled,
          title: null,
        },
      });
      if (result.error) throw result.error;
      void refreshThreads({ requestPolicy: 'network-only' });
    },
    [refreshThreads, selectedThreadId, updateThreadMutation]
  );

  const deleteThread = useCallback(async () => {
    if (!selectedThreadId) return;
    const result = await deleteThreadMutation({ threadId: selectedThreadId });
    if (result.error) throw result.error;
    setSelectedThreadId(null);
    void refreshThreads({ requestPolicy: 'network-only' });
  }, [deleteThreadMutation, refreshThreads, selectedThreadId]);

  const selectedThread =
    threads.find(thread => thread.id === selectedThreadId) || null;
  const messages = [
    ...readFragment(
      AiAssistantMessageFieldsFragmentDoc,
      messagesResult.data?.aiAssistantMessages.items || []
    ),
  ];

  return {
    activeRun,
    cancelRun,
    capabilities: capabilitiesResult.data?.aiAssistantCapabilities || null,
    createThread,
    currentAccountId,
    deleteThread,
    error:
      capabilitiesResult.error || threadsResult.error || messagesResult.error,
    fetching:
      capabilitiesResult.fetching ||
      threadsResult.fetching ||
      messagesResult.fetching,
    messages,
    pendingApprovals,
    resolveApproval,
    retryRun,
    selectedThread,
    selectedThreadId,
    sendMessage,
    setExternalSearch,
    setSelectedThreadId,
    streamingText,
    threads,
  };
}
