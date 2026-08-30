import { useEffect, useRef, useState } from 'react';

import {
  getMonitorHistory,
  getMonitorIncidents,
  getMonitorSummary,
  type MonitorHistory,
  type MonitorIncidentPage,
  type MonitorIncidentSnapshot,
  type MonitorRange,
  type MonitorSummary,
} from '@/features/system/monitor-api';

interface Resource<T> {
  key: string;
  data?: T;
  error?: boolean;
}

interface RetryableResource<T> extends Partial<Resource<T>> {
  retry: () => void;
}

export function useServiceStatusHistory(
  targetId: string,
  range: MonitorRange,
  page: number,
  pageSize: number,
  revision: number
): {
  summary: RetryableResource<MonitorSummary>;
  history: RetryableResource<MonitorHistory>;
  incidents: RetryableResource<MonitorIncidentPage>;
} {
  const [summary, setSummary] = useState<Resource<MonitorSummary>>({ key: '' });
  const [history, setHistory] = useState<Resource<MonitorHistory>>({ key: '' });
  const [incidents, setIncidents] = useState<Resource<MonitorIncidentPage>>({
    key: '',
  });
  const [retries, setRetries] = useState({
    summary: 0,
    history: 0,
    incidents: 0,
  });
  const queryKey = `${targetId}:${range}:${revision}`;
  const summaryKey = `${queryKey}:${retries.summary}`;
  const historyKey = `${queryKey}:${retries.history}`;
  const incidentKey = `${queryKey}:${page}:${pageSize}:${retries.incidents}`;
  const incidentCutoff = useRef<MonitorIncidentSnapshot>();

  useEffect(() => {
    incidentCutoff.current = undefined;
  }, [queryKey]);

  useEffect(() => {
    const controller = new AbortController();
    setSummary({ key: summaryKey });
    void getMonitorSummary('24h', controller.signal)
      .then(data => {
        if (!controller.signal.aborted) setSummary({ key: summaryKey, data });
      })
      .catch(() => {
        if (!controller.signal.aborted)
          setSummary({ key: summaryKey, error: true });
      });
    return () => controller.abort();
  }, [summaryKey]);

  useEffect(() => {
    const controller = new AbortController();
    setHistory({ key: historyKey });
    void getMonitorHistory(targetId, range, controller.signal)
      .then(data => {
        if (!controller.signal.aborted) setHistory({ key: historyKey, data });
      })
      .catch(() => {
        if (!controller.signal.aborted)
          setHistory({ key: historyKey, error: true });
      });
    return () => controller.abort();
  }, [targetId, range, historyKey]);

  useEffect(() => {
    const controller = new AbortController();
    setIncidents({ key: incidentKey });
    void getMonitorIncidents(
      range,
      targetId,
      page,
      pageSize,
      controller.signal,
      incidentCutoff.current
    )
      .then(data => {
        if (controller.signal.aborted) return;
        incidentCutoff.current = {
          asOf: data.asOf,
          maxIncidentId: data.maxIncidentId,
        };
        setIncidents({ key: incidentKey, data });
      })
      .catch(() => {
        if (!controller.signal.aborted)
          setIncidents({ key: incidentKey, error: true });
      });
    return () => controller.abort();
  }, [targetId, range, page, pageSize, incidentKey]);

  function retry(resource: keyof typeof retries) {
    setRetries(previous => ({
      ...previous,
      [resource]: previous[resource] + 1,
    }));
  }

  return {
    summary: {
      ...(summary.key === summaryKey ? summary : {}),
      retry: () => retry('summary'),
    },
    history: {
      ...(history.key === historyKey ? history : {}),
      retry: () => retry('history'),
    },
    incidents: {
      ...(incidents.key === incidentKey ? incidents : {}),
      retry: () => retry('incidents'),
    },
  };
}
