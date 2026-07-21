import { GripVertical, MoreVertical, Pin, type LucideIcon } from 'lucide-react';
import {
  useMemo,
  useState,
  type CSSProperties,
  type DragEvent,
  type Key,
  type MouseEvent,
  type ReactNode,
} from 'react';

import { cn } from '@/utils/cn';

import { StudioDataGrid } from './StudioDataGrid';

export type StudioDataTableAlign = 'center' | 'left' | 'right';

export interface StudioDataTableColumn<TSortField extends string = string> {
  align?: StudioDataTableAlign;
  alwaysFrozen?: boolean;
  defaultDirection?: string;
  icon?: LucideIcon;
  id: string;
  label: ReactNode;
  locked?: boolean;
  sortField?: TSortField;
  width: number;
  widthClass?: string;
}

export interface StudioDataTableApi<
  TRow,
  TColumn extends StudioDataTableColumn,
> {
  canMoveColumn: (column: TColumn) => boolean;
  getColumnStyle: (column: TColumn, layer: 'body' | 'header') => CSSProperties;
  getFrozenClass: (column: TColumn, backgroundClass?: string) => string;
  isFrozenColumn: (column: TColumn) => boolean;
  orderedColumns: TColumn[];
  resetColumnLayout: () => void;
  rows: TRow[];
  toggleFrozenColumn: (column: TColumn) => void;
}

interface StudioDataTableCellContext<
  TRow,
  TColumn extends StudioDataTableColumn,
> {
  column: TColumn;
  row: TRow;
  rowIndex: number;
  table: StudioDataTableApi<TRow, TColumn>;
}

interface StudioDataTableHeaderContext<
  TRow,
  TColumn extends StudioDataTableColumn,
> {
  column: TColumn;
  isSorted: boolean;
  table: StudioDataTableApi<TRow, TColumn>;
}

export interface StudioDataTableProps<
  TRow,
  TColumn extends StudioDataTableColumn,
> {
  ariaLabel?: string;
  className?: string;
  columnMenuTestIdPrefix?: string;
  columns: TColumn[];
  defaultFrozenColumnIds?: readonly string[];
  emptyState?: ReactNode;
  frozenColumnIds?: readonly string[];
  getCellBackgroundClass?: (
    context: StudioDataTableCellContext<TRow, TColumn>
  ) => string | undefined;
  getCellClassName?: (
    context: StudioDataTableCellContext<TRow, TColumn>
  ) => string | undefined;
  getRowKey: (row: TRow) => Key;
  getRowTitle?: (row: TRow) => string | undefined;
  gridClassName?: string;
  headerCellClassName?: (
    context: StudioDataTableHeaderContext<TRow, TColumn>
  ) => string | undefined;
  isColumnSorted?: (column: TColumn) => boolean;
  loading?: boolean;
  loadingOverlay?: ReactNode;
  minTableWidth?: number;
  notice?: ReactNode;
  onFrozenColumnIdsChange?: (ids: string[]) => void;
  onColumnContextMenu?: (
    event: MouseEvent<HTMLTableCellElement>,
    column: TColumn,
    table: StudioDataTableApi<TRow, TColumn>
  ) => void;
  onColumnMenuOpen?: (
    event: MouseEvent<HTMLButtonElement>,
    column: TColumn,
    table: StudioDataTableApi<TRow, TColumn>
  ) => void;
  onColumnSortToggle?: (column: TColumn) => void;
  onRowClick?: (row: TRow) => void;
  onRowContextMenu?: (
    event: MouseEvent<HTMLTableRowElement>,
    row: TRow
  ) => void;
  renderCell: (context: StudioDataTableCellContext<TRow, TColumn>) => ReactNode;
  renderCellElement?: (
    context: StudioDataTableCellContext<TRow, TColumn>
  ) => ReactNode;
  renderHeaderLabel?: (
    context: StudioDataTableHeaderContext<TRow, TColumn>
  ) => ReactNode;
  renderOverlays?: (table: StudioDataTableApi<TRow, TColumn>) => ReactNode;
  renderSortIndicator?: (column: TColumn) => ReactNode;
  rowClassName?: (row: TRow, rowIndex: number) => string | undefined;
  rows: TRow[];
  sortTestIdPrefix?: string;
  tableClassName?: string;
  testId?: string;
  toolbarLeft?: ReactNode;
  toolbarRight?: ReactNode;
}

function getTextAlignClass(align?: StudioDataTableAlign) {
  if (align === 'right') return 'text-right';
  if (align === 'center') return 'text-center';
  return 'text-left';
}

export function StudioDataTable<TRow, TColumn extends StudioDataTableColumn>({
  ariaLabel,
  className,
  columnMenuTestIdPrefix = 'studio-data-table-menu',
  columns,
  defaultFrozenColumnIds = [],
  emptyState,
  frozenColumnIds: controlledFrozenColumnIds,
  getCellBackgroundClass,
  getCellClassName,
  getRowKey,
  getRowTitle,
  gridClassName,
  headerCellClassName,
  isColumnSorted,
  loading,
  loadingOverlay,
  minTableWidth = 760,
  notice,
  onFrozenColumnIdsChange,
  onColumnContextMenu,
  onColumnMenuOpen,
  onColumnSortToggle,
  onRowClick,
  onRowContextMenu,
  renderCell,
  renderCellElement,
  renderHeaderLabel,
  renderOverlays,
  renderSortIndicator,
  rowClassName,
  rows,
  sortTestIdPrefix = 'studio-data-table-sort',
  tableClassName,
  testId,
  toolbarLeft,
  toolbarRight,
}: StudioDataTableProps<TRow, TColumn>) {
  const [columnOrder, setColumnOrder] = useState(() =>
    columns.map(column => column.id)
  );
  const [uncontrolledFrozenColumnIds, setUncontrolledFrozenColumnIds] =
    useState<Set<string>>(() => new Set(defaultFrozenColumnIds));
  const [draggingColumnId, setDraggingColumnId] = useState<string | null>(null);
  const frozenColumnIds = useMemo(
    () =>
      new Set(
        controlledFrozenColumnIds ?? Array.from(uncontrolledFrozenColumnIds)
      ),
    [controlledFrozenColumnIds, uncontrolledFrozenColumnIds]
  );

  const setFrozenColumnIds = (next: Set<string>) => {
    if (controlledFrozenColumnIds) {
      onFrozenColumnIdsChange?.(Array.from(next));
      return;
    }
    setUncontrolledFrozenColumnIds(next);
  };

  const orderedColumns = useMemo(() => {
    const columnsById = new Map(columns.map(column => [column.id, column]));
    const normalizedIds = [
      ...columnOrder.filter(id => columnsById.has(id)),
      ...columns
        .map(column => column.id)
        .filter(id => !columnOrder.includes(id)),
    ];
    const normalizedColumns = normalizedIds
      .map(id => columnsById.get(id))
      .filter((column): column is TColumn => Boolean(column));
    const lockedColumns = normalizedColumns.filter(column => column.locked);
    const movableColumns = normalizedColumns.filter(column => !column.locked);
    const frozenColumns = movableColumns.filter(
      column => column.alwaysFrozen || frozenColumnIds.has(column.id)
    );
    const regularColumns = movableColumns.filter(
      column => !column.alwaysFrozen && !frozenColumnIds.has(column.id)
    );

    return [...frozenColumns, ...regularColumns, ...lockedColumns];
  }, [columnOrder, columns, frozenColumnIds]);

  const frozenColumnOffsets = useMemo(() => {
    const offsets = new Map<string, number>();
    let left = 0;

    orderedColumns.forEach(column => {
      if (!column.alwaysFrozen && !frozenColumnIds.has(column.id)) return;
      offsets.set(column.id, left);
      left += column.width;
    });

    return offsets;
  }, [frozenColumnIds, orderedColumns]);

  const lastFrozenColumnId = useMemo(() => {
    const frozenColumns = orderedColumns.filter(
      column => column.alwaysFrozen || frozenColumnIds.has(column.id)
    );
    return frozenColumns[frozenColumns.length - 1]?.id;
  }, [frozenColumnIds, orderedColumns]);

  const isFrozenColumn = (column: TColumn) =>
    column.alwaysFrozen || frozenColumnIds.has(column.id);

  const canMoveColumn = (column: TColumn) =>
    !column.locked && !column.alwaysFrozen;

  const getColumnStyle = (
    column: TColumn,
    layer: 'body' | 'header'
  ): CSSProperties => {
    const baseStyle: CSSProperties = {
      maxWidth: column.width,
      minWidth: column.width,
      width: column.width,
    };
    const left = frozenColumnOffsets.get(column.id);
    if (left === undefined) return baseStyle;

    return {
      ...baseStyle,
      left,
      position: 'sticky',
      zIndex: layer === 'header' ? 30 : 20,
    };
  };

  const getFrozenClass = (
    column: TColumn,
    backgroundClass = 'bg-[#08101d]'
  ) => {
    if (!isFrozenColumn(column)) return '';
    return cn(
      backgroundClass,
      lastFrozenColumnId === column.id &&
        'shadow-[8px_0_14px_-12px_rgba(16,185,129,0.95)]'
    );
  };

  const toggleFrozenColumn = (column: TColumn) => {
    if (column.locked || column.alwaysFrozen) return;
    const next = new Set(frozenColumnIds);
    if (next.has(column.id)) next.delete(column.id);
    else next.add(column.id);
    setFrozenColumnIds(next);
  };

  const resetColumnLayout = () => {
    setColumnOrder(columns.map(column => column.id));
    setFrozenColumnIds(new Set(defaultFrozenColumnIds));
  };

  const tableApi: StudioDataTableApi<TRow, TColumn> = {
    canMoveColumn,
    getColumnStyle,
    getFrozenClass,
    isFrozenColumn,
    orderedColumns,
    resetColumnLayout,
    rows,
    toggleFrozenColumn,
  };

  const moveColumn = (sourceId: string, targetId: string) => {
    if (sourceId === targetId) return;
    const sourceColumn = columns.find(column => column.id === sourceId);
    const targetColumn = columns.find(column => column.id === targetId);
    if (
      !sourceColumn ||
      !targetColumn ||
      !canMoveColumn(sourceColumn) ||
      !canMoveColumn(targetColumn)
    )
      return;

    setColumnOrder(current => {
      const baseOrder = [
        ...current.filter(id =>
          columns.some(column => column.id === id && !column.locked)
        ),
        ...columns
          .filter(column => !column.locked && !current.includes(column.id))
          .map(column => column.id),
      ];
      const sourceIndex = baseOrder.indexOf(sourceId);
      const targetIndex = baseOrder.indexOf(targetId);
      if (sourceIndex === -1 || targetIndex === -1) return current;

      const nextOrder = [...baseOrder];
      nextOrder.splice(sourceIndex, 1);
      nextOrder.splice(targetIndex, 0, sourceId);
      return [
        ...nextOrder,
        ...columns.filter(column => column.locked).map(column => column.id),
      ];
    });
  };

  const handleColumnDragStart = (
    event: DragEvent<HTMLTableCellElement>,
    column: TColumn
  ) => {
    if (!canMoveColumn(column)) return;
    setDraggingColumnId(column.id);
    event.dataTransfer.effectAllowed = 'move';
    event.dataTransfer.setData('text/plain', column.id);
  };

  const handleColumnDrop = (
    event: DragEvent<HTMLTableCellElement>,
    targetColumn: TColumn
  ) => {
    event.preventDefault();
    const sourceId =
      event.dataTransfer.getData('text/plain') || draggingColumnId;
    if (sourceId) moveColumn(sourceId, targetColumn.id);
    setDraggingColumnId(null);
  };

  const tableMinWidth = Math.max(
    minTableWidth,
    orderedColumns.reduce((total, column) => total + column.width, 0)
  );

  const renderHeaderCell = (column: TColumn) => {
    const isSorted = Boolean(isColumnSorted?.(column));
    const labelText =
      typeof column.label === 'string' ? column.label : '操作列';
    const headerContext = {
      column,
      isSorted,
      table: tableApi,
    };
    const ariaSort = isSorted ? 'other' : undefined;

    return (
      <th
        key={column.id}
        aria-sort={ariaSort}
        draggable={canMoveColumn(column)}
        onContextMenu={event => onColumnContextMenu?.(event, column, tableApi)}
        onDragEnd={() => setDraggingColumnId(null)}
        onDragOver={event => {
          if (canMoveColumn(column)) {
            event.preventDefault();
            event.dataTransfer.dropEffect = 'move';
          }
        }}
        onDragStart={event => handleColumnDragStart(event, column)}
        onDrop={event => handleColumnDrop(event, column)}
        scope="col"
        style={getColumnStyle(column, 'header')}
        className={cn(
          'group/header sticky top-0 z-10 border-b border-r border-white/10 bg-slate-900/95 px-3 py-2.5 text-[10px] font-bold text-slate-500 backdrop-blur last:border-r-0',
          column.widthClass,
          getTextAlignClass(column.align),
          canMoveColumn(column) && 'cursor-grab active:cursor-grabbing',
          draggingColumnId === column.id && 'opacity-60',
          isSorted && 'bg-[#0c222a] text-emerald-100',
          isFrozenColumn(column) &&
            getFrozenClass(column, isSorted ? 'bg-[#0c222a]' : 'bg-slate-900'),
          headerCellClassName?.(headerContext)
        )}
      >
        <div className="relative min-w-0 pr-7">
          <button
            type="button"
            data-testid={`${sortTestIdPrefix}-${column.id}`}
            disabled={!column.sortField || !onColumnSortToggle}
            onClick={() => onColumnSortToggle?.(column)}
            className={cn(
              'flex min-w-0 items-center gap-1.5 leading-3 outline-none transition-colors disabled:cursor-default',
              column.align === 'right' && 'ml-auto justify-end',
              column.align === 'center' && 'mx-auto justify-center',
              column.sortField &&
                onColumnSortToggle &&
                'cursor-pointer hover:text-slate-200 focus-visible:text-slate-100'
            )}
            title={
              column.sortField && onColumnSortToggle
                ? `${labelText} 排序`
                : labelText
            }
          >
            {canMoveColumn(column) && (
              <GripVertical className="h-3 w-3 shrink-0 text-slate-700 opacity-0 transition-opacity group-hover/header:opacity-100" />
            )}
            {isFrozenColumn(column) && (
              <Pin className="h-3 w-3 shrink-0 text-emerald-300" />
            )}
            <span className="min-w-0 overflow-hidden text-ellipsis whitespace-nowrap">
              {renderHeaderLabel?.(headerContext) ?? column.label}
            </span>
            {renderSortIndicator?.(column)}
          </button>
          {!column.locked && onColumnMenuOpen && (
            <button
              type="button"
              data-testid={`${columnMenuTestIdPrefix}-${column.id}`}
              onClick={event => onColumnMenuOpen(event, column, tableApi)}
              className={cn(
                'absolute right-0 top-1/2 flex h-6 w-6 -translate-y-1/2 items-center justify-center rounded-[4px] text-slate-500 opacity-0 transition-all duration-150 hover:bg-white/[0.08] hover:text-slate-100 focus:bg-white/[0.08] focus:text-slate-100 focus:opacity-100 focus:outline-none group-hover/header:opacity-100',
                isSorted && 'opacity-100'
              )}
              aria-label={`${labelText} 列菜单`}
              title={`${labelText} 列菜单`}
            >
              <MoreVertical className="h-3.5 w-3.5" />
            </button>
          )}
        </div>
      </th>
    );
  };

  return (
    <div
      className={cn(
        'flex min-h-[500px] flex-col overflow-hidden rounded-md border border-white/10 bg-[#050915] shadow-sm',
        className
      )}
    >
      {(toolbarLeft || toolbarRight) && (
        <div className="flex flex-col gap-2 border-b border-white/5 bg-slate-900/40 px-4 py-2 md:flex-row md:items-center md:justify-between">
          <div className="flex min-w-0 flex-wrap items-center gap-2">
            {toolbarLeft}
          </div>
          {toolbarRight && (
            <div className="w-full md:w-auto">{toolbarRight}</div>
          )}
        </div>
      )}

      <StudioDataGrid
        ariaLabel={ariaLabel}
        gridClassName={gridClassName}
        loadingOverlay={loadingOverlay}
        notice={notice}
        tableClassName={tableClassName}
        tableStyle={{ minWidth: tableMinWidth }}
        testId={testId}
      >
        <thead className="sticky top-0 z-10 bg-slate-900/95 backdrop-blur">
          <tr>{orderedColumns.map(renderHeaderCell)}</tr>
        </thead>
        <tbody className="font-mono">
          {!loading && rows.length === 0 && emptyState}

          {rows.map((row, rowIndex) => (
            <tr
              key={getRowKey(row)}
              role={onRowClick ? 'link' : undefined}
              tabIndex={onRowClick ? 0 : undefined}
              title={getRowTitle?.(row)}
              className={cn(
                'group transition-colors hover:bg-white/[0.02]',
                onRowClick &&
                  'cursor-pointer focus-visible:bg-white/[0.04] focus-visible:outline-none',
                rowClassName?.(row, rowIndex)
              )}
              onClick={() => onRowClick?.(row)}
              onContextMenu={event => onRowContextMenu?.(event, row)}
              onKeyDown={event => {
                if (!onRowClick) return;
                if (event.key === 'Enter' || event.key === ' ') {
                  event.preventDefault();
                  onRowClick(row);
                }
              }}
            >
              {orderedColumns.map(column => {
                const context = {
                  column,
                  row,
                  rowIndex,
                  table: tableApi,
                };
                const customCell = renderCellElement?.(context);
                if (customCell) return customCell;
                const backgroundClass = getCellBackgroundClass?.(context);

                return (
                  <td
                    key={`${String(getRowKey(row))}-${column.id}`}
                    style={getColumnStyle(column, 'body')}
                    className={cn(
                      'h-[33px] overflow-hidden whitespace-nowrap border-b border-r border-white/5 px-3 py-1.5 last:border-r-0',
                      column.widthClass,
                      getTextAlignClass(column.align),
                      backgroundClass,
                      getFrozenClass(column, backgroundClass || 'bg-[#08101d]'),
                      getCellClassName?.(context)
                    )}
                  >
                    {renderCell(context)}
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </StudioDataGrid>

      {renderOverlays?.(tableApi)}
    </div>
  );
}
