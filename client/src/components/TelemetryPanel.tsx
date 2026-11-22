import React from 'react';
import { ExecutionResult, MilestoneEvent } from '../types/execution';

interface TelemetryPanelProps {
  result: ExecutionResult | null;
  isRunning: boolean;
  milestones: MilestoneEvent[];
}

export const TelemetryPanel: React.FC<TelemetryPanelProps> = ({
  result,
  isRunning,
  milestones,
}) => {
  const formatMemory = (kb: number): string => {
    if (kb <= 0) return '0 KiB';
    if (kb >= 1024) {
      return `${(kb / 1024).toFixed(1)} MiB`;
    }
    return `${kb.toLocaleString()} KiB`;
  };

  const formatDuration = (ms: number): string => {
    if (ms <= 0) return '0 ms';
    if (ms >= 1000) {
      return `${(ms / 1000).toFixed(2)} s`;
    }
    return `${ms} ms`;
  };

  const getStatusBadge = (status: string) => {
    switch (status) {
      case 'COMPLETED':
        return <span className="telemetry-badge badge-success">COMPLETED</span>;
      case 'COMPILE_ERROR':
      case 'RUNTIME_ERROR':
        return <span className="telemetry-badge badge-danger">{status}</span>;
      case 'TIMEOUT':
        return <span className="telemetry-badge badge-warning">TIMEOUT</span>;
      case 'MEMORY_LIMIT':
        return <span className="telemetry-badge badge-warning">MEMORY LIMIT</span>;
      case 'BLOCKED_BY_POLICY':
      case 'SANDBOX_ERROR':
        return <span className="telemetry-badge badge-danger">{status}</span>;
      case 'CANCELLED':
        return <span className="telemetry-badge badge-neutral">CANCELLED</span>;
      case 'RUNNING':
        return <span className="telemetry-badge badge-info">RUNNING</span>;
      default:
        return <span className="telemetry-badge badge-neutral">{status || 'IDLE'}</span>;
    }
  };

  return (
    <div className="telemetry-panel">
      <div className="panel-title-bar">
        <h4>Kernel Telemetry</h4>
        <span className="telemetry-subtitle">cgroups v2 & Monotonic Timer</span>
      </div>

      <div className="telemetry-grid">
        <div className="telemetry-item">
          <span className="telemetry-label">Status</span>
          <div className="telemetry-value">
            {isRunning ? (
              <span className="telemetry-badge badge-info">RUNNING</span>
            ) : result ? (
              getStatusBadge(result.finalStatus || result.status)
            ) : (
              <span className="telemetry-badge badge-neutral">IDLE</span>
            )}
          </div>
        </div>

        <div className="telemetry-item">
          <span className="telemetry-label">Wall Clock</span>
          <span className="telemetry-value metric-font">
            {result ? formatDuration(result.wallMs) : '--'}
          </span>
        </div>

        <div className="telemetry-item">
          <span className="telemetry-label">CPU Time</span>
          <span className="telemetry-value metric-font">
            {result ? formatDuration(result.cpuMs) : '--'}
          </span>
        </div>

        <div className="telemetry-item">
          <span className="telemetry-label">Peak Memory (cgroup)</span>
          <span className="telemetry-value metric-font">
            {result ? formatMemory(result.peakMemoryKb) : '--'}
          </span>
        </div>

        <div className="telemetry-item">
          <span className="telemetry-label">Exit Code</span>
          <span className="telemetry-value metric-font">
            {result ? (
              result.exitCode === 0 ? (
                <span className="exit-ok">0 (Success)</span>
              ) : (
                <span className="exit-err">{result.exitCode}</span>
              )
            ) : (
              '--'
            )}
          </span>
        </div>

        <div className="telemetry-item">
          <span className="telemetry-label">Kernel Signal</span>
          <span className="telemetry-value metric-font">
            {result ? (result.signal === 0 ? 'None (0)' : `Signal ${result.signal}`) : '--'}
          </span>
        </div>
      </div>

      {milestones.length > 0 && (
        <div className="milestones-timeline">
          <span className="timeline-title">Lifecycle Milestone Emissions:</span>
          <div className="milestones-track">
            {milestones.map((m, idx) => (
              <div key={`${m.milestone}-${idx}`} className="milestone-chip">
                <span className="chip-name">{m.milestone}</span>
                {m.details && <span className="chip-details">{m.details}</span>}
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
};
