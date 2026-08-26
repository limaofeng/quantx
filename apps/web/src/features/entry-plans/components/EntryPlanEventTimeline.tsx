import {
  CheckCircle2,
  CircleDot,
  Clock3,
  Pause,
  ShieldCheck,
  XCircle,
} from 'lucide-react';
import type { ElementType } from 'react';

import { formatEntryCurrency, formatEntryDateTime } from '../model/draft';
import type { EntryPlanEventKind, EntryPlanEventView } from '../model/types';

const eventIcons: Record<EntryPlanEventKind, ElementType> = {
  APPROVAL_REQUIRED: Clock3,
  APPROVED: ShieldCheck,
  AUTHORIZATION_CHANGED: ShieldCheck,
  EVALUATED: CircleDot,
  ORDER_SUBMITTED: Clock3,
  PAUSED: Pause,
  REJECTED: XCircle,
  RESUMED: CircleDot,
  TRADE_FILLED: CheckCircle2,
  TRIGGERED: CircleDot,
};

export function EntryPlanEventTimeline({
  events,
}: {
  events: EntryPlanEventView[];
}) {
  if (events.length === 0) {
    return (
      <div className="rounded-lg border border-dashed border-white/10 px-ui-section py-ui-empty text-center text-ui-label text-slate-500">
        暂无买入计划事件。评估不会被伪装成成交，真实买入只显示券商成交回报。
      </div>
    );
  }

  return (
    <ol aria-label="买入计划事件时间线" className="space-y-0">
      {events.map((event, index) => {
        const Icon = eventIcons[event.kind];
        return (
          <li
            className="relative grid grid-cols-[28px_minmax(0,1fr)] gap-3"
            key={event.id}
          >
            {index < events.length - 1 ? (
              <span className="absolute bottom-0 left-[13px] top-7 w-px bg-white/10" />
            ) : null}
            <span className="relative z-10 mt-1 flex h-7 w-7 items-center justify-center rounded-full border border-cyan-400/20 bg-[#0b1120] text-cyan-200">
              <Icon aria-hidden="true" className="h-3.5 w-3.5" />
            </span>
            <article className="mb-4 rounded-lg border border-white/10 bg-white/[0.025] p-3">
              <div className="flex flex-wrap items-start justify-between gap-2">
                <div>
                  <h3 className="text-ui-label font-black text-slate-100">
                    {event.title}
                  </h3>
                  <p className="mt-1 text-ui-caption text-slate-500">
                    {event.instrumentName} · {event.instrumentCode}
                  </p>
                </div>
                <time className="font-mono text-ui-caption text-slate-500">
                  {formatEntryDateTime(event.occurredAt)}
                </time>
              </div>
              <p className="mt-2 text-ui-label leading-5 text-slate-300">
                {event.description}
              </p>
              {event.amountCny || event.volume ? (
                <p className="mt-2 font-mono text-ui-caption text-market-up">
                  {event.amountCny
                    ? formatEntryCurrency(event.amountCny)
                    : null}
                  {event.amountCny && event.volume ? ' · ' : null}
                  {event.volume ? `${event.volume} 股` : null}
                </p>
              ) : null}
              {event.traceId ? (
                <p className="mt-2 font-mono text-ui-caption text-slate-600">
                  决策追踪 {event.traceId}
                </p>
              ) : null}
            </article>
          </li>
        );
      })}
    </ol>
  );
}
