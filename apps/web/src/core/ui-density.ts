export type UiDensity = 'standard' | 'compact';

export const UI_DENSITY_STORAGE_KEY = 'quantx-ui-density';

export function readUiDensity(): UiDensity {
  try {
    return window.localStorage.getItem(UI_DENSITY_STORAGE_KEY) === 'compact'
      ? 'compact'
      : 'standard';
  } catch {
    return 'standard';
  }
}

export function applyUiDensity(density: UiDensity) {
  document.documentElement.dataset.uiDensity = density;
}

export function initializeUiDensity(): UiDensity {
  const density = readUiDensity();
  applyUiDensity(density);
  return density;
}

export function saveUiDensity(density: UiDensity) {
  applyUiDensity(density);
  try {
    window.localStorage.setItem(UI_DENSITY_STORAGE_KEY, density);
  } catch {
    // The current session still uses the selected density when storage is blocked.
  }
}
