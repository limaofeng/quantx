export {
  appRoutes,
  findRoute,
  getDesktopNavigation,
  getMobileNavigation,
  getPageTitle,
  isNavigationItemActive,
  matchAppRoute,
  normalizePath,
  preloadImportantRoutes,
  preloadRoute,
} from './routes';

export type {
  AppRouteConfig,
  NavigationGroup,
  NavigationItem,
  RouteMobileNavConfig,
  RouteNavConfig,
} from './routes';

export { RouteSkeleton, type RouteSkeletonVariant } from './skeletons';
