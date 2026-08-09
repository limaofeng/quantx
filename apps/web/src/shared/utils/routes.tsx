// Deprecated compatibility layer. Use "@/router" as the route source of truth.
export {
  appRoutes as routes,
  getPageTitle,
  preloadImportantRoutes,
} from '@/router';

export type { AppRouteConfig as RouteConfig } from '@/router';
