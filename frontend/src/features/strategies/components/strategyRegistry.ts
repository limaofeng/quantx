import { DefaultConfigPanel } from './config-panels/DefaultConfigPanel';
import { PullbackGridConfigPanel } from './config-panels/PullbackGridConfigPanel';

export const getStrategyConfigPanel = (strategyName: string) => {
  const CustomConfigPanel = getCustomStrategyConfigPanel(strategyName);
  if (CustomConfigPanel) {
    return CustomConfigPanel;
  }
  return DefaultConfigPanel;
};

export const getCustomStrategyConfigPanel = (strategyName: string) => {
  if (
    strategyName &&
    (strategyName.includes('Pullback Grid') || strategyName.includes('网格'))
  ) {
    return PullbackGridConfigPanel;
  }
  return null;
};
