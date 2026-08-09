/* eslint-disable react-refresh/only-export-components */
import { createContext, useContext, useEffect, type ReactNode } from 'react';

type Theme = 'dark';

interface ThemeContextType {
  theme: Theme;
}

const ThemeContext = createContext<ThemeContextType | undefined>(undefined);
const DARK_THEME: Theme = 'dark';

interface ThemeProviderProps {
  children: ReactNode;
}

export function ThemeProvider({ children }: ThemeProviderProps) {
  useEffect(() => {
    // 固定深色主题，并覆盖旧的浅色偏好。
    const root = document.documentElement;
    root.classList.add('dark');

    try {
      localStorage.setItem('theme', DARK_THEME);
    } catch {
      // localStorage may be unavailable in hardened browser contexts.
    }
  }, []);

  return (
    <ThemeContext.Provider value={{ theme: DARK_THEME }}>
      {children}
    </ThemeContext.Provider>
  );
}

export function useTheme() {
  const context = useContext(ThemeContext);
  if (context === undefined) {
    throw new Error('useTheme must be used within a ThemeProvider');
  }
  return context;
}
