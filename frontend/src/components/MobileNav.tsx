import { Link, useLocation } from 'wouter';

import {
  getMobileNavigation,
  isNavigationItemActive,
  preloadRoute,
} from '@/router';
import { cn } from '@/utils/cn';

const navigation = getMobileNavigation();

export default function MobileNav() {
  const [location] = useLocation();

  return (
    <nav className="lg:hidden fixed bottom-0 left-0 right-0 bg-white dark:bg-[#0b1120] border-t border-slate-200 dark:border-slate-800/50 z-50 safe-area-inset-bottom">
      <div className="grid grid-cols-5 h-16">
        {navigation.map(item => {
          const isActive = isNavigationItemActive(item.href, location);
          return (
            <Link key={item.label} href={item.href}>
              <div
                onFocus={() => void preloadRoute(item.href)}
                onMouseEnter={() => void preloadRoute(item.href)}
                onTouchStart={() => void preloadRoute(item.href)}
                className={cn(
                  'flex flex-col items-center justify-center h-full transition-all duration-200 relative',
                  isActive
                    ? 'text-blue-600 dark:text-blue-400'
                    : 'text-slate-500 dark:text-slate-400 hover:text-slate-700 dark:hover:text-slate-300'
                )}
                data-testid={`mobile-nav-${item.href}`}
              >
                {isActive && (
                  <div className="absolute top-0 left-1/2 -translate-x-1/2 w-12 h-1 bg-gradient-to-r from-blue-600 to-blue-500 rounded-b-full shadow-lg shadow-blue-600/20" />
                )}
                <div
                  className={cn(
                    'rounded-xl p-2 transition-all duration-200 mb-1',
                    isActive
                      ? 'bg-blue-50 dark:bg-blue-950/30 shadow-lg shadow-blue-600/20 scale-110'
                      : 'hover:bg-slate-100 dark:hover:bg-slate-800/50'
                  )}
                >
                  <item.icon className="h-5 w-5" />
                </div>
                <span
                  className={cn(
                    'text-[10px] font-semibold',
                    isActive ? 'text-blue-600 dark:text-blue-400' : ''
                  )}
                >
                  {item.label}
                </span>
              </div>
            </Link>
          );
        })}
      </div>
    </nav>
  );
}
