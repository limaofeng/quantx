import type { StudioThemeName } from './types';

export interface StudioThemeStyles {
  activeButton: string;
  activeIndicator: string;
  activeTab: string;
  focusRing: string;
  iconBox: string;
  resizeHandle: string;
  resizeLine: string;
  resizeLineHover: string;
  resizeOverlay: string;
  tabIcon: string;
}

const STUDIO_THEME_STYLES: Record<StudioThemeName, StudioThemeStyles> = {
  cyan: {
    activeButton: 'bg-cyan-400 text-slate-950 shadow-lg shadow-cyan-400/20',
    activeIndicator: 'bg-cyan-300',
    activeTab:
      'border-x-white/10 border-t-cyan-400/70 bg-slate-900 text-slate-100',
    focusRing: 'focus-visible:ring-cyan-300/70',
    iconBox: 'border-cyan-400/25 bg-cyan-400/10 text-cyan-300',
    resizeHandle: 'bg-cyan-200',
    resizeLine: 'bg-cyan-300',
    resizeLineHover: 'group-hover:bg-cyan-300/80',
    resizeOverlay: 'bg-cyan-400/5',
    tabIcon: 'text-cyan-300',
  },
  blue: {
    activeButton: 'bg-blue-600 text-white shadow-lg shadow-blue-500/20',
    activeIndicator: 'bg-blue-600',
    activeTab:
      'border-x-white/10 border-t-blue-400/70 bg-slate-900 text-slate-100',
    focusRing: 'focus-visible:ring-blue-400/70',
    iconBox: 'border-blue-500/25 bg-blue-500/10 text-blue-300',
    resizeHandle: 'bg-blue-300',
    resizeLine: 'bg-blue-400',
    resizeLineHover: 'group-hover:bg-blue-400/80',
    resizeOverlay: 'bg-blue-500/5',
    tabIcon: 'text-blue-400',
  },
  red: {
    activeButton: 'bg-red-600 text-white shadow-lg shadow-red-500/20',
    activeIndicator: 'bg-red-600',
    activeTab:
      'border-x-white/10 border-t-red-400/70 bg-slate-900 text-slate-100',
    focusRing: 'focus-visible:ring-red-400/70',
    iconBox: 'border-red-500/25 bg-red-500/10 text-red-300',
    resizeHandle: 'bg-red-300',
    resizeLine: 'bg-red-400',
    resizeLineHover: 'group-hover:bg-red-400/80',
    resizeOverlay: 'bg-red-500/5',
    tabIcon: 'text-red-400',
  },
  amber: {
    activeButton: 'bg-amber-500 text-slate-950 shadow-lg shadow-amber-500/20',
    activeIndicator: 'bg-amber-400',
    activeTab:
      'border-x-white/10 border-t-amber-400/70 bg-slate-900 text-slate-100',
    focusRing: 'focus-visible:ring-amber-300/70',
    iconBox: 'border-amber-400/25 bg-amber-500/10 text-amber-300',
    resizeHandle: 'bg-amber-200',
    resizeLine: 'bg-amber-300',
    resizeLineHover: 'group-hover:bg-amber-300/80',
    resizeOverlay: 'bg-amber-500/5',
    tabIcon: 'text-amber-300',
  },
  emerald: {
    activeButton:
      'bg-emerald-500 text-slate-950 shadow-lg shadow-emerald-500/20',
    activeIndicator: 'bg-emerald-400',
    activeTab:
      'border-x-white/10 border-t-emerald-400/70 bg-slate-900 text-slate-100',
    focusRing: 'focus-visible:ring-emerald-300/70',
    iconBox: 'border-emerald-400/25 bg-emerald-500/10 text-emerald-300',
    resizeHandle: 'bg-emerald-200',
    resizeLine: 'bg-emerald-300',
    resizeLineHover: 'group-hover:bg-emerald-300/80',
    resizeOverlay: 'bg-emerald-500/5',
    tabIcon: 'text-emerald-300',
  },
  rose: {
    activeButton: 'bg-rose-500 text-white shadow-lg shadow-rose-500/20',
    activeIndicator: 'bg-rose-400',
    activeTab:
      'border-x-white/10 border-t-rose-400/70 bg-slate-900 text-slate-100',
    focusRing: 'focus-visible:ring-rose-300/70',
    iconBox: 'border-rose-400/25 bg-rose-500/10 text-rose-300',
    resizeHandle: 'bg-rose-200',
    resizeLine: 'bg-rose-300',
    resizeLineHover: 'group-hover:bg-rose-300/80',
    resizeOverlay: 'bg-rose-500/5',
    tabIcon: 'text-rose-300',
  },
};

export function getStudioThemeStyles(themeColor: StudioThemeName) {
  return STUDIO_THEME_STYLES[themeColor];
}
