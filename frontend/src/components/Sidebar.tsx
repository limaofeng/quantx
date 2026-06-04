import {
  ChevronRight,
  PanelLeftClose,
  PanelLeftOpen,
  TrendingUp,
  User,
} from 'lucide-react';
import { Link, useLocation } from 'wouter';

import { Button } from '@/components/ui/button';
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from '@/components/ui/tooltip';
import {
  getDesktopNavigation,
  isNavigationItemActive,
  preloadRoute,
} from '@/router';
import { cn } from '@/utils/cn';

const navigation = getDesktopNavigation();

interface SidebarProps {
  collapsed: boolean;
  onToggleCollapsed: () => void;
}

export default function Sidebar({
  collapsed,
  onToggleCollapsed,
}: SidebarProps) {
  const [location] = useLocation();
  const collapseLabel = collapsed ? '展开侧边栏' : '收起侧边栏';

  return (
    <aside
      className={cn(
        'relative z-20 hidden bg-white transition-[width] duration-300 ease-in-out lg:flex lg:flex-shrink-0 dark:bg-[#0b1120] border-r border-slate-200 dark:border-white/5',
        collapsed ? 'lg:w-[72px]' : 'lg:w-[var(--sidebar-width)]'
      )}
      data-sidebar-collapsed={collapsed}
    >
      <div className="flex flex-col w-full h-full">
        {/* Logo and Title */}
        <div
          className={cn(
            'relative flex items-center h-[var(--header-height)] border-b border-slate-200 dark:border-white/5 flex-shrink-0',
            collapsed ? 'justify-center px-3' : 'px-5'
          )}
        >
          <div
            className={cn(
              'flex items-center min-w-0',
              collapsed ? 'justify-center' : 'gap-2.5'
            )}
          >
            <div className="w-9 h-9 rounded-lg bg-gradient-to-br from-blue-600 to-indigo-700 flex items-center justify-center shadow-md shadow-blue-600/15 group hover:rotate-3 transition-transform">
              <TrendingUp className="text-white h-[18px] w-[18px]" />
            </div>
            <h1
              className={cn(
                'text-base font-semibold tracking-tight text-slate-900 dark:text-white whitespace-nowrap',
                collapsed && 'sr-only'
              )}
            >
              Quant<span className="text-blue-600 not-italic">X</span>
            </h1>
          </div>

          <Tooltip>
            <TooltipTrigger asChild>
              <Button
                type="button"
                variant="ghost"
                size="icon"
                aria-label={collapseLabel}
                aria-pressed={collapsed}
                onClick={onToggleCollapsed}
                className="absolute -right-3 top-1/2 z-30 h-7 w-7 -translate-y-1/2 rounded-lg border border-slate-200 bg-white text-slate-500 shadow-sm shadow-slate-900/5 hover:bg-slate-50 hover:text-blue-600 focus-visible:ring-blue-500 dark:border-white/10 dark:bg-[#111827] dark:text-slate-400 dark:shadow-black/30 dark:hover:bg-white/10 dark:hover:text-blue-300"
                data-testid="sidebar-collapse-button"
              >
                {collapsed ? (
                  <PanelLeftOpen className="h-4 w-4" />
                ) : (
                  <PanelLeftClose className="h-4 w-4" />
                )}
              </Button>
            </TooltipTrigger>
            <TooltipContent side="right">{collapseLabel}</TooltipContent>
          </Tooltip>
        </div>

        {/* Navigation Menu */}
        <nav
          className={cn(
            'flex-1 py-5 space-y-5 overflow-y-auto custom-scrollbar',
            collapsed ? 'px-2' : 'px-3'
          )}
        >
          {navigation.map(group => (
            <div key={group.title}>
              <div className="px-3 mb-1.5">
                <p
                  className={cn(
                    'text-[11px] font-medium text-slate-400',
                    collapsed && 'sr-only'
                  )}
                >
                  {group.title}
                </p>
              </div>
              <div className="space-y-1">
                {group.items.map(item => {
                  const isActive = isNavigationItemActive(item.href, location);
                  const navItem = (
                    <div
                      onFocus={() => void preloadRoute(item.href)}
                      onMouseEnter={() => void preloadRoute(item.href)}
                      className={cn(
                        'group flex items-center text-sm font-medium rounded-lg transition-all duration-300 relative cursor-pointer overflow-hidden',
                        collapsed
                          ? 'h-10 justify-center px-0'
                          : 'justify-between px-3.5 py-2.5',
                        isActive
                          ? 'bg-blue-600 text-white shadow-md shadow-blue-600/15'
                          : 'text-slate-500 dark:text-slate-400 hover:text-slate-900 dark:hover:text-slate-100 hover:bg-slate-50 dark:hover:bg-white/5'
                      )}
                      data-testid={`nav-link-${item.href}`}
                    >
                      <div
                        className={cn(
                          'flex items-center relative z-10 min-w-0',
                          collapsed && 'justify-center'
                        )}
                      >
                        <item.icon
                          className={cn(
                            'h-4 w-4 shrink-0 transition-transform duration-300',
                            collapsed ? 'mr-0' : 'mr-2.5',
                            isActive
                              ? 'scale-105'
                              : 'group-hover:scale-105 opacity-70 group-hover:opacity-100'
                          )}
                        />
                        <span className={collapsed ? 'sr-only' : undefined}>
                          {item.label}
                        </span>
                      </div>
                      {!collapsed && isActive && (
                        <ChevronRight className="relative z-10 h-3.5 w-3.5 opacity-50" />
                      )}
                      {isActive && (
                        <div className="absolute inset-0 bg-gradient-to-r from-blue-600 via-blue-500 to-blue-600 opacity-90" />
                      )}
                    </div>
                  );

                  return (
                    <Link key={item.label} href={item.href}>
                      {collapsed ? (
                        <Tooltip>
                          <TooltipTrigger asChild>{navItem}</TooltipTrigger>
                          <TooltipContent side="right">
                            {item.label}
                          </TooltipContent>
                        </Tooltip>
                      ) : (
                        navItem
                      )}
                    </Link>
                  );
                })}
              </div>
            </div>
          ))}
        </nav>

        {/* User Profile */}
        <div
          className={cn(
            'border-t border-slate-200 dark:border-white/5',
            collapsed ? 'p-2' : 'p-3'
          )}
        >
          <div
            className={cn(
              'group flex items-center p-2.5 rounded-lg bg-slate-50/50 dark:bg-white/[0.02] border border-transparent hover:border-slate-200 dark:hover:border-white/10 transition-all duration-300 cursor-pointer',
              collapsed && 'justify-center'
            )}
          >
            <div className="relative">
              <div className="w-9 h-9 rounded-lg bg-gradient-to-br from-blue-600 to-indigo-700 flex items-center justify-center shadow-md shadow-blue-600/15 group-hover:rotate-3 transition-transform">
                <User className="text-white h-[18px] w-[18px]" />
              </div>
              <div className="absolute -bottom-0.5 -right-0.5 w-2.5 h-2.5 rounded-full bg-emerald-500 border-2 border-white dark:border-[#0b1120]" />
            </div>
            <div
              className={cn('ml-2.5 flex-1 min-w-0', collapsed && 'sr-only')}
            >
              <p
                className="text-sm font-medium text-slate-900 dark:text-white truncate"
                data-testid="username"
              >
                交易员001
              </p>
              <p
                className="text-[11px] text-emerald-500 font-medium opacity-80"
                data-testid="user-status"
              >
                实盘账户
              </p>
            </div>
          </div>
        </div>
      </div>
    </aside>
  );
}
