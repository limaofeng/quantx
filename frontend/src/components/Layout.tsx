import { Bell, Wallet } from 'lucide-react';
import { type ReactNode, useEffect, useState } from 'react';
import { useLocation } from 'wouter';

import {
  isStudioWorkspacePath,
  StudioWorkspace,
} from '@/components/studio-workspace';
import { Button } from '@/components/ui/button';
import { useCurrentAccount } from '@/features/dashboard/hooks';
import { useIsMobile } from '@/hooks/use-mobile';
import { getPageTitle } from '@/router';
import { STORAGE_KEYS } from '@/shared/constants/app';
import { cn } from '@/utils/cn';

import MobileNav from './MobileNav';
import Sidebar from './Sidebar';

interface LayoutProps {
  children: ReactNode;
}

function getInitialSidebarCollapsed() {
  if (typeof window === 'undefined') {
    return false;
  }

  try {
    return (
      window.localStorage.getItem(STORAGE_KEYS.SIDEBAR_COLLAPSED) === 'true'
    );
  } catch {
    return false;
  }
}

export default function Layout({ children }: LayoutProps) {
  const [location, setLocation] = useLocation();
  const currentTitle = getPageTitle(location);
  const isMobile = useIsMobile();
  const isStudioRoute = isStudioWorkspacePath(location);
  const shouldUseStudioWorkspace = isStudioRoute && !isMobile;
  const [isSidebarCollapsed, setIsSidebarCollapsed] = useState(
    getInitialSidebarCollapsed
  );

  const { data: accountData } = useCurrentAccount();

  useEffect(() => {
    try {
      window.localStorage.setItem(
        STORAGE_KEYS.SIDEBAR_COLLAPSED,
        String(isSidebarCollapsed)
      );
    } catch {
      // localStorage may be unavailable in hardened browser contexts.
    }
  }, [isSidebarCollapsed]);

  return (
    <div className="flex h-screen bg-slate-50 dark:bg-[#020617] text-slate-900 dark:text-slate-100 overflow-hidden font-sans">
      {/* Desktop Sidebar */}
      {!isStudioRoute && (
        <Sidebar
          collapsed={isSidebarCollapsed}
          onToggleCollapsed={() =>
            setIsSidebarCollapsed(collapsed => !collapsed)
          }
        />
      )}

      {/* Main Content */}
      <main
        className={cn(
          'flex-1 flex flex-col min-w-0 overflow-hidden relative',
          isStudioRoute && 'bg-[var(--studio-bg)]'
        )}
      >
        {/* Header */}
        {!isStudioRoute && (
          <header className="h-[var(--header-height)] glass-effect border-b border-slate-200 dark:border-white/5 px-4 md:px-6 flex items-center justify-between sticky top-0 z-10 shrink-0">
            <div className="flex items-center gap-3">
              <div className="lg:hidden w-8 h-8 rounded-lg bg-blue-600 flex items-center justify-center mr-1">
                <Wallet className="text-white h-4 w-4" />
              </div>
              <div>
                <h2
                  id="page-title"
                  className="text-lg font-semibold text-slate-900 dark:text-white"
                  data-testid="page-title"
                >
                  {currentTitle}
                </h2>
                <div className="flex items-center gap-1.5 text-[11px] font-medium text-slate-400 mt-0.5">
                  <span className="inline-block w-1.5 h-1.5 rounded-full bg-emerald-500" />
                  系统运行正常
                </div>
              </div>
            </div>

            <div className="flex items-center gap-2">
              {/* Asset Insight Card */}
              <button
                type="button"
                onClick={() => setLocation('/account')}
                className="hidden sm:flex items-center gap-3 px-4 py-2 rounded-lg bg-slate-50 dark:bg-white/[0.03] border border-slate-200 dark:border-white/5 shadow-sm group hover:border-blue-500/30 transition-all duration-300"
                aria-label="打开账户中心"
              >
                <div className="p-1.5 rounded-md bg-blue-600/10 text-blue-600 dark:text-blue-400 group-hover:scale-105 transition-transform">
                  <Wallet size={17} strokeWidth={2.5} />
                </div>
                <div className="text-right">
                  <p className="text-[11px] font-medium text-slate-400 leading-none mb-1">
                    总资产
                  </p>
                  <p
                    className="text-sm font-semibold text-slate-900 dark:text-white font-mono leading-none"
                    data-testid="total-assets"
                  >
                    {typeof accountData?.currentAccount?.totalAsset === 'number'
                      ? `¥${accountData.currentAccount.totalAsset.toLocaleString()}`
                      : '--'}
                  </p>
                </div>
              </button>

              <div className="h-6 w-px bg-slate-200 dark:bg-white/10 hidden sm:block mx-1" />

              <div className="flex items-center gap-1.5">
                <Button
                  variant="ghost"
                  size="icon"
                  className="h-9 w-9 text-slate-500 hover:text-slate-900 dark:text-slate-400 dark:hover:text-slate-100 hover:bg-slate-100 dark:hover:bg-white/5 rounded-lg transition-all duration-300 relative"
                  data-testid="notifications-button"
                >
                  <Bell className="h-[18px] w-[18px]" />
                  <span className="absolute top-2.5 right-2.5 w-1.5 h-1.5 bg-rose-500 rounded-full border border-white dark:border-slate-900" />
                </Button>
              </div>
            </div>
          </header>
        )}

        {/* Page Content */}
        <div
          className={cn(
            'flex-1 overflow-y-auto custom-scrollbar p-2 md:p-6',
            isStudioRoute && 'overflow-hidden p-0 md:p-0'
          )}
        >
          <div
            className={cn(
              'max-w-[1600px] mx-auto animate-fade-in',
              isStudioRoute && 'h-full max-w-none'
            )}
          >
            {shouldUseStudioWorkspace ? (
              <StudioWorkspace>{children}</StudioWorkspace>
            ) : (
              children
            )}
          </div>
        </div>
      </main>

      {/* Mobile Bottom Navigation */}
      {!isStudioRoute && <MobileNav />}
    </div>
  );
}
