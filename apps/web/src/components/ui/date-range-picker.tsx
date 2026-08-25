import { format, isValid, parse } from 'date-fns';
import { zhCN } from 'date-fns/locale';
import { Calendar as CalendarIcon, X } from 'lucide-react';
import * as React from 'react';
import type { DateRange } from 'react-day-picker';

import { Calendar } from '@/components/ui/calendar';
import {
  Popover,
  PopoverContent,
  PopoverAnchor,
} from '@/components/ui/popover';
import { cn } from '@/utils/cn';

export function DateRangePicker({
  className,
  buttonClassName,
  value,
  onChange,
}: {
  className?: string;
  buttonClassName?: string;
  value?: DateRange;
  onChange?: (date: DateRange | undefined) => void;
}) {
  const [isOpen, setIsOpen] = React.useState(false);
  const [fromInput, setFromInput] = React.useState('');
  const [toInput, setToInput] = React.useState('');

  React.useEffect(() => {
    setFromInput(
      value?.from ? format(value.from, 'yyyy-MM-dd', { locale: zhCN }) : ''
    );
  }, [value?.from]);

  React.useEffect(() => {
    setToInput(
      value?.to ? format(value.to, 'yyyy-MM-dd', { locale: zhCN }) : ''
    );
  }, [value?.to]);

  const handleFromChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const val = e.target.value;
    setFromInput(val);
    const parsed = parse(val, 'yyyy-MM-dd', new Date());
    if (isValid(parsed) && val.length === 10) {
      onChange?.({ from: parsed, to: value?.to });
    } else if (val === '') {
      onChange?.({ from: undefined, to: value?.to });
    }
  };

  const handleToChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const val = e.target.value;
    setToInput(val);
    const parsed = parse(val, 'yyyy-MM-dd', new Date());
    if (isValid(parsed) && val.length === 10) {
      onChange?.({ from: value?.from, to: parsed });
    } else if (val === '') {
      onChange?.({ from: value?.from, to: undefined });
    }
  };

  return (
    <div className={cn('grid gap-2', className)}>
      <Popover open={isOpen} onOpenChange={setIsOpen}>
        <PopoverAnchor asChild>
          <div
            id="date"
            className={cn(
              'group flex flex-1 items-center justify-between rounded-md border border-input bg-transparent px-3 py-2 text-ui-body shadow-sm ring-offset-background hover:border-slate-400 focus-within:ring-1 focus-within:ring-ring disabled:cursor-not-allowed disabled:opacity-50 transition-colors duration-200 cursor-text',
              buttonClassName,
              isOpen && 'border-blue-500 ring-1 ring-blue-500' // active state
            )}
            onClick={() => setIsOpen(true)}
          >
            <div className="flex flex-1 items-center justify-center space-x-2 px-1">
              <input
                type="text"
                placeholder="开始日期"
                value={fromInput}
                onChange={handleFromChange}
                onFocus={() => setIsOpen(true)}
                className={cn(
                  'flex-1 w-full min-w-0 bg-transparent text-center outline-none mx-1 text-ui-body placeholder:text-slate-500/50',
                  value?.from ? 'text-foreground' : 'text-slate-400'
                )}
              />
              <span className="text-slate-400 text-ui-label font-light pointer-events-none">
                ~
              </span>
              <input
                type="text"
                placeholder="结束日期"
                value={toInput}
                onChange={handleToChange}
                onFocus={() => setIsOpen(true)}
                className={cn(
                  'flex-1 w-full min-w-0 bg-transparent text-center outline-none mx-1 text-ui-body placeholder:text-slate-500/50',
                  value?.to ? 'text-foreground' : 'text-slate-400'
                )}
              />
            </div>

            <div className="flex items-center ml-2 text-slate-400 transition-colors">
              {value?.from || value?.to ? (
                <div
                  role="button"
                  tabIndex={0}
                  className="hover:text-slate-300 rounded-full p-0.5 transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring cursor-pointer"
                  onClick={e => {
                    e.stopPropagation();
                    onChange?.(undefined);
                    setFromInput('');
                    setToInput('');
                    setIsOpen(false);
                  }}
                  onKeyDown={e => {
                    if (e.key === 'Enter' || e.key === ' ') {
                      e.stopPropagation();
                      e.preventDefault();
                      onChange?.(undefined);
                      setFromInput('');
                      setToInput('');
                      setIsOpen(false);
                    }
                  }}
                >
                  <X className="h-4 w-4" />
                </div>
              ) : (
                <CalendarIcon className="h-4 w-4 opacity-70 pointer-events-none" />
              )}
            </div>
          </div>
        </PopoverAnchor>
        <PopoverContent
          className="w-auto p-0"
          align="start"
          onOpenAutoFocus={e => e.preventDefault()}
        >
          <Calendar
            mode="range"
            defaultMonth={value?.from}
            selected={value}
            onSelect={onChange}
            numberOfMonths={2}
            locale={zhCN}
          />
        </PopoverContent>
      </Popover>
    </div>
  );
}
