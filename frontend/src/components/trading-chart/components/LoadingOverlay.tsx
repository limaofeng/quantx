import { Loader2 } from 'lucide-react';
import React from 'react';

interface LoadingOverlayProps {
  isLoading: boolean;
}

export function LoadingOverlay({ isLoading }: LoadingOverlayProps) {
  if (!isLoading) return null;

  return (
    <div className="absolute inset-0 z-30 flex items-center justify-center bg-slate-950/20 backdrop-blur-[2px]">
      <div className="flex flex-col items-center gap-2">
        <Loader2 className="w-6 h-6 text-blue-500 animate-spin" />
        <span className="text-[10px] font-bold text-blue-500 uppercase tracking-widest">
          Loading Market Data...
        </span>
      </div>
    </div>
  );
}
