import { MoreHorizontal, Pencil, Trash2 } from 'lucide-react';
import { useState } from 'react';

import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from '@/components/ui/popover';

import type { WatchlistGroupSummary } from '../types';

interface WatchlistGroupPickerProps {
  group: WatchlistGroupSummary;
  onDelete: (group: WatchlistGroupSummary) => void;
  onRename: (group: WatchlistGroupSummary, name: string) => void;
}

export function WatchlistGroupPicker({
  group,
  onDelete,
  onRename,
}: WatchlistGroupPickerProps) {
  const [open, setOpen] = useState(false);
  const [editing, setEditing] = useState(false);
  const [name, setName] = useState(group.name);

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <button
          type="button"
          aria-label={`管理分组 ${group.name}`}
          className="inline-flex h-7 w-7 items-center justify-center rounded text-slate-600 hover:bg-white/5 hover:text-slate-300 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-400/70"
        >
          <MoreHorizontal className="h-4 w-4" />
        </button>
      </PopoverTrigger>
      <PopoverContent
        align="end"
        className="w-56 border-[#263b53] bg-[#0b1627] p-2 text-slate-200"
      >
        {editing ? (
          <form
            className="space-y-2"
            onSubmit={event => {
              event.preventDefault();
              const trimmed = name.trim();
              if (!trimmed) return;
              onRename(group, trimmed);
              setEditing(false);
              setOpen(false);
            }}
          >
            <label className="block text-[10px] font-bold text-slate-500">
              分组名称
              <input
                autoFocus
                value={name}
                maxLength={80}
                onChange={event => setName(event.target.value)}
                className="mt-1 h-8 w-full rounded border border-white/10 bg-white/[0.03] px-2 text-xs text-slate-200 outline-none focus:border-blue-400/50"
              />
            </label>
            <div className="flex justify-end gap-2">
              <button
                type="button"
                onClick={() => setEditing(false)}
                className="h-7 rounded border border-white/10 px-2 text-[10px] text-slate-400"
              >
                取消
              </button>
              <button
                type="submit"
                className="h-7 rounded bg-blue-600 px-2 text-[10px] font-bold text-white"
              >
                保存
              </button>
            </div>
          </form>
        ) : (
          <div className="space-y-1">
            <button
              type="button"
              onClick={() => setEditing(true)}
              className="flex h-8 w-full items-center gap-2 rounded px-2 text-left text-[11px] text-slate-300 hover:bg-white/5"
            >
              <Pencil className="h-3.5 w-3.5 text-slate-500" />
              重命名
            </button>
            <button
              type="button"
              onClick={() => {
                onDelete(group);
                setOpen(false);
              }}
              className="flex h-8 w-full items-center gap-2 rounded px-2 text-left text-[11px] text-rose-300 hover:bg-rose-400/10"
            >
              <Trash2 className="h-3.5 w-3.5" />
              删除分组
            </button>
          </div>
        )}
      </PopoverContent>
    </Popover>
  );
}
