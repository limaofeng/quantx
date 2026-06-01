import { TrendingUp, User, ChevronRight } from 'lucide-react';
import { Link, useLocation } from 'wouter';

import {
  getDesktopNavigation,
  isNavigationItemActive,
  preloadRoute,
} from '@/router';
import { cn } from '@/utils/cn';

const navigation = getDesktopNavigation();

export default function Sidebar() {
  const [location] = useLocation();

  return (
    <aside className="hidden lg:flex lg:flex-shrink-0 lg:w-[var(--sidebar-width)] bg-white dark:bg-[#0b1120] border-r border-slate-200 dark:border-white/5 relative z-20">
      <div className="flex flex-col w-full h-full">
        {/* Logo and Title */}
        <div className="flex items-center h-[var(--header-height)] px-5 border-b border-slate-200 dark:border-white/5 flex-shrink-0">
          <div className="flex items-center gap-2.5">
            <div className="w-9 h-9 rounded-lg bg-gradient-to-br from-blue-600 to-indigo-700 flex items-center justify-center shadow-md shadow-blue-600/15 group hover:rotate-3 transition-transform">
              <TrendingUp className="text-white h-[18px] w-[18px]" />
            </div>
            <h1 className="text-base font-semibold tracking-tight text-slate-900 dark:text-white">
              Quant<span className="text-blue-600 not-italic">X</span>
            </h1>
          </div>
        </div>

        {/* Navigation Menu */}
        <nav className="flex-1 px-3 py-5 space-y-5 overflow-y-auto custom-scrollbar">
          {navigation.map(group => (
            <div key={group.title}>
              <div className="px-3 mb-1.5">
                <p className="text-[11px] font-medium text-slate-400">
                  {group.title}
                </p>
              </div>
              <div className="space-y-1">
                {group.items.map(item => {
                  const isActive = isNavigationItemActive(item.href, location);
                  return (
                    <Link key={item.label} href={item.href}>
                      <div
                        onFocus={() => void preloadRoute(item.href)}
                        onMouseEnter={() => void preloadRoute(item.href)}
                        className={cn(
                          'group flex items-center justify-between px-3.5 py-2.5 text-sm font-medium rounded-lg transition-all duration-300 relative cursor-pointer overflow-hidden',
                          isActive
                            ? 'bg-blue-600 text-white shadow-md shadow-blue-600/15'
                            : 'text-slate-500 dark:text-slate-400 hover:text-slate-900 dark:hover:text-slate-100 hover:bg-slate-50 dark:hover:bg-white/5'
                        )}
                        data-testid={`nav-link-${item.href}`}
                      >
                        <div className="flex items-center relative z-10">
                          <item.icon
                            className={cn(
                              'mr-2.5 h-4 w-4 transition-transform duration-300',
                              isActive
                                ? 'scale-105'
                                : 'group-hover:scale-105 opacity-70 group-hover:opacity-100'
                            )}
                          />
                          <span>{item.label}</span>
                        </div>
                        {isActive && (
                          <ChevronRight className="relative z-10 h-3.5 w-3.5 opacity-50" />
                        )}
                        {isActive && (
                          <div className="absolute inset-0 bg-gradient-to-r from-blue-600 via-blue-500 to-blue-600 opacity-90" />
                        )}
                      </div>
                    </Link>
                  );
                })}
              </div>
            </div>
          ))}
        </nav>

        {/* User Profile */}
        <div className="p-3 border-t border-slate-200 dark:border-white/5">
          <div className="group flex items-center p-2.5 rounded-lg bg-slate-50/50 dark:bg-white/[0.02] border border-transparent hover:border-slate-200 dark:hover:border-white/10 transition-all duration-300 cursor-pointer">
            <div className="relative">
              <div className="w-9 h-9 rounded-lg bg-gradient-to-br from-blue-600 to-indigo-700 flex items-center justify-center shadow-md shadow-blue-600/15 group-hover:rotate-3 transition-transform">
                <User className="text-white h-[18px] w-[18px]" />
              </div>
              <div className="absolute -bottom-0.5 -right-0.5 w-2.5 h-2.5 rounded-full bg-emerald-500 border-2 border-white dark:border-[#0b1120]" />
            </div>
            <div className="ml-2.5 flex-1 min-w-0">
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
