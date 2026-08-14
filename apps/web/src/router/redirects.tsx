import { Redirect } from 'wouter';

export function MarketWorkbenchRedirect() {
  return <Redirect replace to="/" />;
}

export function LegacyAgentSettingsRedirect() {
  return <Redirect replace to="/settings/qmt" />;
}
