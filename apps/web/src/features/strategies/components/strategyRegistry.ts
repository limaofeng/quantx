import { DefaultConfigPanel } from './config-panels/DefaultConfigPanel';
import { LimitUpBoardConfigPanel } from './config-panels/LimitUpBoardConfigPanel';
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
    (strategyName.includes('打板') ||
      strategyName.includes('Limit Up Board') ||
      strategyName.includes('Limit-up Board'))
  ) {
    return LimitUpBoardConfigPanel;
  }
  if (
    strategyName &&
    (strategyName.includes('Pullback Grid') || strategyName.includes('网格'))
  ) {
    return PullbackGridConfigPanel;
  }
  return null;
};
