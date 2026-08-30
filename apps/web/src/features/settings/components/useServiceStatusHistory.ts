import { useEffect, useRef, useState } from 'react';

import {
  getMonitorHistory,
  getMonitorIncidents,
  getMonitorSummary,
  type MonitorHistory,
  type MonitorIncidentPage,
  type MonitorRange,
  type MonitorSummary,
} from '@/features/system/monitor-api';

interface Resource<T> {
  key: string;
  data?: T;
  error?: boolean;
}

export function useServiceStatusHistory(
  targetId: string,
  range: MonitorRange,
  page: number,
  pageSize: number,
  revision: number
): {
  summary: Partial<Resource<MonitorSummary>>;
  history: Partial<Resource<MonitorHistory>>;
  incidents: Partial<Resource<MonitorIncidentPage>>;
} {
  const [summary, setSummary] = useState<Resource<MonitorSummary>>({ key: '' });
  const [history, setHistory] = useState<Resource<MonitorHistory>>({ key: '' });
  const [incidents, setIncidents] = useState<Resource<MonitorIncidentPage>>({
    key: '',
  });
  const summaryKey = String(revision);
  const historyKey = `${targetId}:${range}:${revision}`;
  const incidentKey = `${historyKey}:${page}:${pageSize}`;
  const incidentCutoff = useRef<{ key: string; asOf: string }>();

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
    const asOf =
      incidentCutoff.current?.key === historyKey
        ? incidentCutoff.current.asOf
        : undefined;
    void getMonitorIncidents(
      range,
      targetId,
      page,
      pageSize,
      controller.signal,
      asOf
    )
      .then(data => {
        if (controller.signal.aborted) return;
        incidentCutoff.current = { key: historyKey, asOf: data.asOf };
        setIncidents({ key: incidentKey, data });
      })
      .catch(() => {
        if (!controller.signal.aborted)
          setIncidents({ key: incidentKey, error: true });
      });
    return () => controller.abort();
  }, [targetId, range, page, pageSize, incidentKey, historyKey]);

  return {
    summary: summary.key === summaryKey ? summary : {},
    history: history.key === historyKey ? history : {},
    incidents: incidents.key === incidentKey ? incidents : {},
  };
}
