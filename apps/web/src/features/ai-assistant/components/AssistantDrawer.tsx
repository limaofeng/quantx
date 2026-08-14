import {
  Bot,
  Globe2,
  LoaderCircle,
  MessageSquarePlus,
  Paperclip,
  Send,
  Square,
  Trash2,
  X,
} from 'lucide-react';
import { useMemo, useState } from 'react';

import { Button } from '@/components/ui/button';
import { ScrollArea } from '@/components/ui/scroll-area';
import { Switch } from '@/components/ui/switch';
import { Textarea } from '@/components/ui/textarea';
import {
  AiAssistantRunStatus,
  type AiAssistantMessageFieldsFragment,
} from '@/generated/gql/graphql';
import { useToast } from '@/hooks/use-toast';
import { cn } from '@/utils/cn';

import { useAiAssistant } from '../hooks/useAiAssistant';

function messageText(content: AiAssistantMessageFieldsFragment['content']) {
  return content
    .filter(
      block => block.__typename === 'AiAssistantTextBlock' && 'text' in block
    )
    .map(block => ('text' in block ? String(block.text) : ''))
    .join('\n');
}

export function AssistantDrawer({
  currentPath,
  onClose,
}: {
  currentPath: string;
  onClose: () => void;
}) {
  const { toast } = useToast();
  const [draft, setDraft] = useState('');
  const [attachAccount, setAttachAccount] = useState(false);
  const assistant = useAiAssistant(currentPath);
  const isRunning =
    assistant.activeRun?.status === AiAssistantRunStatus.Queued ||
    assistant.activeRun?.status === AiAssistantRunStatus.Running ||
    assistant.activeRun?.status === AiAssistantRunStatus.WaitingApproval;
  const canSend =
    Boolean(assistant.capabilities?.enabled) &&
    Boolean(draft.trim()) &&
    !isRunning;
  const statusLabel = useMemo(() => {
    if (!assistant.capabilities?.enabled) return '未配置';
    if (assistant.activeRun?.status === AiAssistantRunStatus.WaitingApproval)
      return '等待批准';
    if (isRunning) return '分析中';
    return assistant.capabilities.runtimeStatus === 'ready' ? '就绪' : '降级';
  }, [assistant.activeRun?.status, assistant.capabilities, isRunning]);

  const runAction = async (action: () => Promise<unknown>) => {
    try {
      await action();
    } catch (error) {
      toast({
        title: 'AI Assistant 操作失败',
        description: error instanceof Error ? error.message : '请稍后重试。',
        variant: 'destructive',
      });
    }
  };

  const handleSend = async () => {
    if (!canSend) return;
    const value = draft.trim();
    setDraft('');
    await runAction(() => assistant.sendMessage(value, attachAccount));
  };

  const handleDelete = async () => {
    if (!window.confirm('永久删除当前 AI 对话及其全部消息？此操作不可恢复。')) {
      return;
    }
    await runAction(assistant.deleteThread);
  };

  return (
    <aside
      aria-label="AI 助手"
      className="flex h-full w-full min-w-0 flex-col border-l border-white/10 bg-[#080e1b]"
    >
      <header className="flex h-12 shrink-0 items-center gap-3 border-b border-white/10 px-3">
        <div className="flex h-7 w-7 items-center justify-center rounded-lg border border-cyan-400/20 bg-cyan-400/10 text-cyan-300">
          <Bot className="h-4 w-4" />
        </div>
        <div className="min-w-0 flex-1">
          <div className="truncate text-sm font-semibold text-slate-100">
            {assistant.selectedThread?.title || 'QuantX AI Assistant'}
          </div>
          <div className="text-[10px] uppercase tracking-wider text-slate-500">
            {statusLabel} · {assistant.capabilities?.model || 'model pending'}
          </div>
        </div>
        <Button
          type="button"
          variant="ghost"
          size="icon"
          className="h-8 w-8 text-slate-400"
          onClick={() => void runAction(assistant.createThread)}
          title="新建对话"
        >
          <MessageSquarePlus className="h-4 w-4" />
        </Button>
        <Button
          type="button"
          variant="ghost"
          size="icon"
          className="h-8 w-8 text-slate-400"
          onClick={onClose}
          aria-label="关闭 AI 助手"
          title="关闭"
        >
          <X className="h-4 w-4" />
        </Button>
      </header>

      {assistant.threads.length > 0 && (
        <div className="flex items-center gap-2 border-b border-white/5 px-3 py-2">
          <select
            aria-label="选择 AI 对话"
            className="min-w-0 flex-1 rounded-md border border-white/10 bg-slate-950 px-2 py-1.5 text-xs text-slate-300 outline-none focus:border-cyan-500/50"
            value={assistant.selectedThreadId || ''}
            onChange={event =>
              assistant.setSelectedThreadId(event.target.value || null)
            }
          >
            {assistant.threads.map(thread => (
              <option key={thread.id} value={thread.id}>
                {thread.title}
              </option>
            ))}
          </select>
          <Button
            type="button"
            variant="ghost"
            size="icon"
            className="h-8 w-8 text-slate-500 hover:text-rose-300"
            disabled={isRunning}
            onClick={() => void handleDelete()}
            title="永久删除当前对话"
          >
            <Trash2 className="h-3.5 w-3.5" />
          </Button>
        </div>
      )}

      <ScrollArea className="min-h-0 flex-1">
        <div className="space-y-4 p-4">
          {!assistant.capabilities?.enabled && (
            <div className="rounded-lg border border-amber-400/20 bg-amber-400/5 p-3 text-xs leading-5 text-amber-200">
              AI Runtime 尚未配置。请在服务端设置 OPENAI_API_KEY；QuantX
              其他功能不受影响。
            </div>
          )}
          {assistant.messages.length === 0 && !assistant.streamingText && (
            <div className="py-12 text-center">
              <Bot className="mx-auto h-8 w-8 text-slate-700" />
              <p className="mt-3 text-sm text-slate-400">
                可以询问行情、持仓或回测结果
              </p>
              <p className="mt-1 text-xs text-slate-600">
                当前页面路径会自动附加，账户数据需要手动授权
              </p>
            </div>
          )}
          {assistant.messages.map(message => {
            const text = messageText(message.content);
            return (
              <div
                key={message.id}
                className={cn(
                  'max-w-[92%] rounded-xl px-3 py-2.5 text-sm leading-6',
                  message.role === 'USER'
                    ? 'ml-auto bg-cyan-500/15 text-cyan-50'
                    : 'border border-white/8 bg-white/[0.035] text-slate-200'
                )}
              >
                <div className="whitespace-pre-wrap break-words">{text}</div>
                {message.content
                  .filter(
                    block => block.__typename === 'AiAssistantCitationBlock'
                  )
                  .map(block =>
                    'url' in block ? (
                      <a
                        key={block.url}
                        className="mt-2 block truncate text-xs text-cyan-400 hover:underline"
                        href={block.url}
                        target="_blank"
                        rel="noreferrer"
                      >
                        {block.title}
                      </a>
                    ) : null
                  )}
              </div>
            );
          })}
          {assistant.streamingText && (
            <div className="max-w-[92%] rounded-xl border border-cyan-400/10 bg-white/[0.035] px-3 py-2.5 text-sm leading-6 text-slate-200">
              <div className="whitespace-pre-wrap break-words">
                {assistant.streamingText}
              </div>
            </div>
          )}
          {assistant.pendingApprovals.map(approval => (
            <div
              key={approval.toolCallId}
              className="rounded-xl border border-amber-400/30 bg-amber-400/5 p-3"
            >
              <div className="text-xs font-semibold text-amber-200">
                需要批准：{approval.toolName}
              </div>
              <p className="mt-1 text-xs leading-5 text-slate-400">
                {approval.summary ||
                  '该操作会创建非实盘任务，不会发送交易委托。'}
              </p>
              <div className="mt-3 flex gap-2">
                <Button
                  type="button"
                  size="sm"
                  className="h-7 bg-amber-600 text-xs hover:bg-amber-500"
                  onClick={() =>
                    void runAction(() =>
                      assistant.resolveApproval(approval, true)
                    )
                  }
                >
                  仅批准本次
                </Button>
                <Button
                  type="button"
                  size="sm"
                  variant="outline"
                  className="h-7 border-white/10 text-xs"
                  onClick={() =>
                    void runAction(() =>
                      assistant.resolveApproval(approval, false)
                    )
                  }
                >
                  拒绝
                </Button>
              </div>
            </div>
          ))}
          {assistant.activeRun?.status === AiAssistantRunStatus.Failed && (
            <div className="rounded-xl border border-rose-400/25 bg-rose-400/5 p-3 text-xs text-rose-200">
              <p>
                {assistant.activeRun.errorMessage ||
                  'AI 运行失败，请稍后重试。'}
              </p>
              <Button
                type="button"
                size="sm"
                variant="outline"
                className="mt-2 h-7 border-rose-400/20 text-xs"
                onClick={() => void runAction(assistant.retryRun)}
              >
                重试
              </Button>
            </div>
          )}
          {assistant.fetching && assistant.messages.length === 0 && (
            <LoaderCircle className="mx-auto h-5 w-5 animate-spin text-slate-600" />
          )}
        </div>
      </ScrollArea>

      <footer className="shrink-0 border-t border-white/10 p-3">
        <div className="mb-2 flex flex-wrap items-center gap-2 text-[11px] text-slate-500">
          <button
            type="button"
            className={cn(
              'inline-flex items-center gap-1 rounded-md border px-2 py-1 transition-colors',
              attachAccount
                ? 'border-cyan-400/30 bg-cyan-400/10 text-cyan-300'
                : 'border-white/10 hover:text-slate-300'
            )}
            onClick={() => setAttachAccount(value => !value)}
            disabled={!assistant.currentAccountId}
          >
            <Paperclip className="h-3 w-3" />
            {!assistant.currentAccountId
              ? '暂无可附加账户'
              : attachAccount
                ? '已附加当前账户'
                : '附加当前账户'}
          </button>
          <label className="ml-auto inline-flex items-center gap-1.5">
            <Globe2 className="h-3 w-3" />
            外部搜索
            <Switch
              checked={Boolean(assistant.selectedThread?.externalSearchEnabled)}
              disabled={
                !assistant.selectedThread ||
                !assistant.capabilities?.externalSearchAvailable
              }
              onCheckedChange={enabled =>
                void runAction(() => assistant.setExternalSearch(enabled))
              }
              className="scale-75"
            />
          </label>
        </div>
        <div className="relative">
          <Textarea
            value={draft}
            onChange={event => setDraft(event.target.value)}
            onKeyDown={event => {
              if (event.key === 'Enter' && !event.shiftKey) {
                event.preventDefault();
                void handleSend();
              }
            }}
            disabled={!assistant.capabilities?.enabled || isRunning}
            placeholder={
              isRunning ? '当前对话正在执行…' : '询问 QuantX 数据或研究任务…'
            }
            className="min-h-20 resize-none border-white/10 bg-slate-950/80 pr-12 text-sm"
            maxLength={assistant.capabilities?.maxMessageLength || 12000}
          />
          <Button
            type="button"
            size="icon"
            className="absolute bottom-2 right-2 h-8 w-8 bg-cyan-600 hover:bg-cyan-500"
            disabled={!canSend && !isRunning}
            onClick={() =>
              void (isRunning ? runAction(assistant.cancelRun) : handleSend())
            }
            title={isRunning ? '取消运行' : '发送'}
          >
            {isRunning ? (
              <Square className="h-3.5 w-3.5" />
            ) : (
              <Send className="h-3.5 w-3.5" />
            )}
          </Button>
        </div>
        <p className="mt-2 text-center text-[10px] text-slate-600">
          研究结果仅供参考；AI 无实盘交易权限
        </p>
      </footer>
    </aside>
  );
}
