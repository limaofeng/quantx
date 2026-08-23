import {
  closestCenter,
  DndContext,
  KeyboardSensor,
  PointerSensor,
  useSensor,
  useSensors,
  type DragEndEvent,
} from '@dnd-kit/core';
import {
  arrayMove,
  SortableContext,
  sortableKeyboardCoordinates,
  useSortable,
  verticalListSortingStrategy,
} from '@dnd-kit/sortable';
import { CSS } from '@dnd-kit/utilities';
import {
  Check,
  Eye,
  EyeOff,
  GripVertical,
  Plus,
  RotateCcw,
  Search,
  Settings2,
  Trash2,
} from 'lucide-react';
import { useEffect, useMemo, useRef, useState } from 'react';
import { useQuery } from 'urql';

import { Button } from '@/components/ui/button';
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetFooter,
  SheetHeader,
  SheetTitle,
  SheetTrigger,
} from '@/components/ui/sheet';

import {
  buildMarketIndexDirectoryWhere,
  marketIndexDirectoryOrder,
  MarketIndexDirectoryQuery,
  toMarketIndexDirectoryRow,
} from '../marketIndexCatalog';
import {
  CORE_MARKET_INDICES,
  MAX_MARKET_INDEXES,
  normalizeMarketIndexPreferenceItems,
  type MarketIndexPreferenceItem,
} from '../marketWorkbench';

function MarketIndexDirectorySearch({
  draft,
  onAdd,
}: {
  draft: readonly MarketIndexPreferenceItem[];
  onAdd: (row: ReturnType<typeof toMarketIndexDirectoryRow>) => void;
}) {
  const [search, setSearch] = useState('');
  const searchWhere = useMemo(
    () => buildMarketIndexDirectoryWhere(search, 'ALL'),
    [search]
  );
  const [searchResult] = useQuery({
    query: MarketIndexDirectoryQuery,
    variables: {
      after: null,
      first: 20,
      orderBy: marketIndexDirectoryOrder,
      where: searchWhere,
    },
    pause: search.trim().length < 2,
    requestPolicy: 'cache-and-network',
  });
  const searchRows = useMemo(
    () =>
      (searchResult.data?.instrumentsConnection.edges ?? []).map(edge =>
        toMarketIndexDirectoryRow(edge.node)
      ),
    [searchResult.data]
  );
  const draftCodes = useMemo(
    () => new Set(draft.map(item => item.code)),
    [draft]
  );

  return (
    <div className="mt-6 border-t border-white/5 pt-4">
      <div className="text-[10px] font-black uppercase tracking-[0.16em] text-slate-500">
        从真实目录增补
      </div>
      <div className="relative mt-2">
        <Search className="pointer-events-none absolute left-3 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-slate-600" />
        <input
          aria-label="搜索真实指数目录"
          className="h-9 w-full rounded-md border border-white/10 bg-black/20 pl-9 pr-3 text-xs text-slate-200 outline-none placeholder:text-slate-600 focus:border-blue-400/50 focus:ring-2 focus:ring-blue-500/10"
          onChange={event => setSearch(event.target.value)}
          placeholder="搜索指数名称或代码"
          value={search}
        />
      </div>
      <div
        aria-live="polite"
        className="mt-2 min-h-5 text-[10px] text-slate-500"
      >
        {search.trim().length < 2
          ? '输入至少 2 个字符开始搜索真实目录。'
          : searchResult.error
            ? '目录查询失败，请稍后重试。'
            : searchResult.fetching && searchRows.length === 0
              ? '正在查询真实指数…'
              : searchRows.length === 0
                ? '没有匹配的真实指数。'
                : `${searchRows.length} 个匹配结果`}
      </div>
      {searchRows.length > 0 ? (
        <div className="mt-1 divide-y divide-white/5 rounded-lg border border-white/5 bg-white/5">
          {searchRows.map(row => {
            const selected = draftCodes.has(row.code);
            const selectedItem = draft.find(item => item.code === row.code);
            return (
              <button
                key={row.code}
                aria-label={`${selected ? '已在工作台' : '加入工作台'}${row.name}`}
                className="flex w-full items-center justify-between gap-3 px-3 py-2.5 text-left hover:bg-white/[0.04] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-blue-400/70"
                disabled={selected && selectedItem?.visible}
                onClick={() => onAdd(row)}
                type="button"
              >
                <span className="min-w-0">
                  <span className="block truncate text-xs font-bold text-slate-200">
                    {row.name}
                  </span>
                  <span className="mt-0.5 block font-mono text-[10px] text-slate-600">
                    {row.code} · {row.group}
                  </span>
                </span>
                <span className="shrink-0 text-[10px] font-bold text-blue-300">
                  {selected ? (
                    <Check className="h-3.5 w-3.5" />
                  ) : (
                    <Plus className="h-3.5 w-3.5" />
                  )}
                </span>
              </button>
            );
          })}
        </div>
      ) : null}
    </div>
  );
}

function SortablePreferenceRow({
  item,
  index,
  onRemove,
  onToggle,
}: {
  index: number;
  item: MarketIndexPreferenceItem;
  onRemove: () => void;
  onToggle: () => void;
}) {
  const {
    attributes,
    listeners,
    setNodeRef,
    transform,
    transition,
    isDragging,
  } = useSortable({ id: item.code });
  const style = {
    transform: CSS.Transform.toString(transform),
    transition,
  };

  return (
    <div
      ref={setNodeRef}
      style={style}
      className={`flex items-center gap-2 rounded-lg border px-2.5 py-2 ${
        isDragging
          ? 'z-10 border-blue-400/40 bg-blue-500/10 shadow-xl shadow-black/30'
          : 'border-white/5 bg-white/5'
      }`}
      data-testid={`market-index-preference-${item.code}`}
    >
      <button
        aria-label={`拖拽重排${item.name}`}
        className="flex h-7 w-7 shrink-0 cursor-grab items-center justify-center rounded-md text-slate-600 hover:bg-white/[0.06] hover:text-slate-300 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-400/70 active:cursor-grabbing"
        type="button"
        {...attributes}
        {...listeners}
      >
        <GripVertical className="h-4 w-4" />
      </button>
      <span className="w-5 shrink-0 text-center font-mono text-[10px] font-bold text-slate-600">
        {index + 1}
      </span>
      <span className="min-w-0 flex-1">
        <span className="block truncate text-xs font-bold text-slate-200">
          {item.name}
        </span>
        <span className="mt-0.5 block truncate font-mono text-[10px] text-slate-600">
          {item.code} · {item.group}
        </span>
      </span>
      <button
        aria-label={`${item.visible ? '隐藏' : '显示'}${item.name}`}
        aria-pressed={item.visible}
        className={`flex h-8 w-8 shrink-0 items-center justify-center rounded-md border focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-400/70 ${
          item.visible
            ? 'border-blue-400/20 bg-blue-400/10 text-blue-300 hover:bg-blue-500/10'
            : 'border-white/5 text-slate-600 hover:bg-white/5 hover:text-slate-300'
        }`}
        onClick={onToggle}
        type="button"
      >
        {item.visible ? (
          <Eye className="h-3.5 w-3.5" />
        ) : (
          <EyeOff className="h-3.5 w-3.5" />
        )}
      </button>
      <button
        aria-label={`移除${item.name}`}
        className="flex h-8 w-8 shrink-0 items-center justify-center rounded-md text-slate-600 hover:bg-rose-400/10 hover:text-rose-300 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-400/70"
        onClick={onRemove}
        type="button"
      >
        <Trash2 className="h-3.5 w-3.5" />
      </button>
    </div>
  );
}

export function MarketIndexCustomizer({
  items,
  onSave,
  storageStatus,
}: {
  items: readonly MarketIndexPreferenceItem[];
  onSave: (items: readonly MarketIndexPreferenceItem[]) => boolean;
  storageStatus: 'available' | 'unavailable';
}) {
  const [open, setOpen] = useState(false);
  const [draft, setDraft] = useState<MarketIndexPreferenceItem[]>([]);
  const [saved, setSaved] = useState(false);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 6 } }),
    useSensor(KeyboardSensor, { coordinateGetter: sortableKeyboardCoordinates })
  );

  useEffect(() => {
    if (!open) return;
    setDraft(normalizeMarketIndexPreferenceItems(items));
    setSaved(false);
  }, [items, open]);

  const dirty = JSON.stringify(draft) !== JSON.stringify(items);

  const handleDragEnd = ({ active, over }: DragEndEvent) => {
    if (!over || active.id === over.id) return;
    const from = draft.findIndex(item => item.code === String(active.id));
    const to = draft.findIndex(item => item.code === String(over.id));
    if (from < 0 || to < 0) return;
    setDraft(current => arrayMove(current, from, to));
  };

  const addDirectoryRow = (
    row: ReturnType<typeof toMarketIndexDirectoryRow>
  ) => {
    setDraft(current => {
      const existing = current.findIndex(item => item.code === row.code);
      if (existing >= 0) {
        return current.map((item, index) =>
          index === existing ? { ...item, visible: true } : item
        );
      }
      if (current.length >= MAX_MARKET_INDEXES) return current;
      return [...current, { ...row, visible: true }];
    });
  };

  const save = () => {
    if (!dirty) {
      setOpen(false);
      return;
    }
    const persisted = onSave(draft);
    setSaved(persisted);
    setOpen(false);
  };

  return (
    <Sheet open={open} onOpenChange={setOpen}>
      <SheetTrigger asChild>
        <Button
          ref={triggerRef}
          aria-label="定制行情指数"
          className="group h-28 w-[5.75rem] shrink-0 flex-col gap-2 rounded-lg border border-white/10 bg-slate-950/70 px-2 py-2 text-center text-slate-300 hover:border-blue-400/30 hover:bg-white/[0.06] hover:text-slate-100 focus-visible:ring-2 focus-visible:ring-blue-400/70"
          variant="ghost"
        >
          <Settings2 className="h-5 w-5 text-slate-400 transition-colors group-hover:text-blue-300" />
          <span className="text-[11px] font-black">定制</span>
        </Button>
      </SheetTrigger>
      <SheetContent
        aria-describedby="market-index-customizer-description"
        className="flex w-full flex-col border-white/10 bg-slate-950 p-0 text-slate-100 sm:max-w-lg"
        onCloseAutoFocus={event => {
          event.preventDefault();
          triggerRef.current?.focus();
        }}
        side="right"
      >
        <SheetHeader className="border-b border-white/5 px-5 py-4 pr-12 text-left">
          <SheetTitle className="text-base font-black text-slate-100">
            定制行情指数
          </SheetTitle>
          <SheetDescription
            className="text-[11px] leading-5 text-slate-500"
            id="market-index-customizer-description"
          >
            拖拽或使用键盘调整顺序；隐藏只影响工作台显示，不会删除真实指数。
          </SheetDescription>
        </SheetHeader>

        <div className="min-h-0 flex-1 overflow-y-auto px-5 py-4 custom-scrollbar">
          <div className="flex items-center justify-between gap-3">
            <div>
              <div className="text-[10px] font-black uppercase tracking-[0.16em] text-slate-500">
                工作台指数
              </div>
              <div className="mt-1 text-[11px] text-slate-600">
                {draft.filter(item => item.visible).length} 显示 ·{' '}
                {draft.length}/{MAX_MARKET_INDEXES} 配置
              </div>
            </div>
            <Button
              aria-label="恢复默认指数"
              className="h-8 rounded-md border border-white/10 bg-white/[0.03] px-2.5 text-[10px] font-bold text-slate-400 hover:bg-white/[0.06] hover:text-slate-100"
              onClick={() =>
                setDraft(
                  normalizeMarketIndexPreferenceItems(
                    CORE_MARKET_INDICES.map(item => ({
                      ...item,
                      visible: true,
                    }))
                  )
                )
              }
              type="button"
              variant="ghost"
            >
              <RotateCcw className="h-3.5 w-3.5" />
              恢复默认
            </Button>
          </div>

          <DndContext
            collisionDetection={closestCenter}
            onDragEnd={handleDragEnd}
            sensors={sensors}
          >
            <SortableContext
              items={draft.map(item => item.code)}
              strategy={verticalListSortingStrategy}
            >
              <div className="mt-3 space-y-1.5">
                {draft.length === 0 ? (
                  <div className="rounded-lg border border-dashed border-white/10 px-4 py-8 text-center text-xs text-slate-600">
                    当前没有已配置指数，请从真实目录增补。
                  </div>
                ) : (
                  draft.map((item, index) => (
                    <SortablePreferenceRow
                      key={item.code}
                      index={index}
                      item={item}
                      onRemove={() =>
                        setDraft(current =>
                          current.filter(
                            candidate => candidate.code !== item.code
                          )
                        )
                      }
                      onToggle={() =>
                        setDraft(current =>
                          current.map(candidate =>
                            candidate.code === item.code
                              ? { ...candidate, visible: !candidate.visible }
                              : candidate
                          )
                        )
                      }
                    />
                  ))
                )}
              </div>
            </SortableContext>
          </DndContext>

          <MarketIndexDirectorySearch draft={draft} onAdd={addDirectoryRow} />
        </div>

        <SheetFooter className="border-t border-white/5 px-5 py-3 sm:flex-row sm:justify-between">
          <div
            aria-live="polite"
            className="flex items-center gap-2 text-[10px] text-slate-600"
          >
            {storageStatus === 'unavailable' ? (
              <>
                <span className="h-1.5 w-1.5 rounded-full bg-amber-400" />
                本机存储不可用，本次会话仍可使用
              </>
            ) : saved ? (
              <>
                <Check className="h-3.5 w-3.5 text-emerald-300" />
                已保存
              </>
            ) : dirty ? (
              '有未保存的调整'
            ) : (
              '配置已同步'
            )}
          </div>
          <div className="flex gap-2">
            <Button
              className="h-8 rounded-md border border-white/10 bg-transparent px-3 text-[10px] font-bold text-slate-400 hover:bg-white/[0.05] hover:text-slate-100"
              onClick={() => setOpen(false)}
              type="button"
              variant="ghost"
            >
              取消
            </Button>
            <Button
              className="h-8 rounded-md bg-blue-600 px-3 text-[10px] font-bold text-white hover:bg-blue-500 disabled:cursor-not-allowed disabled:opacity-40"
              disabled={!dirty}
              onClick={save}
              type="button"
            >
              保存配置
            </Button>
          </div>
        </SheetFooter>
      </SheetContent>
    </Sheet>
  );
}
