import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { TelemetryPanel } from '../src/components/TelemetryPanel';
import { ExecutionResult } from '../src/types/execution';

describe('TelemetryPanel Component', () => {
  it('renders idle state when no result is present', () => {
    render(<TelemetryPanel result={null} isRunning={false} milestones={[]} />);
    expect(screen.getByText('Kernel Telemetry')).toBeInTheDocument();
    expect(screen.getByText('IDLE')).toBeInTheDocument();
  });

  it('renders running milestone badge during execution', () => {
    render(<TelemetryPanel result={null} isRunning={true} milestones={[]} />);
    expect(screen.getByText('RUNNING')).toBeInTheDocument();
  });

  it('renders real kernel metrics and formatted memory and time', () => {
    const mockResult: ExecutionResult = {
      requestId: 'req-1',
      languageId: 'c',
      finalStatus: 'COMPLETED',
      status: 'COMPLETED',
      milestones: [
        { milestone: 'ACCEPTED', timestampMs: 1000 },
        { milestone: 'RUNNING', timestampMs: 1050 },
        { milestone: 'COMPLETED', timestampMs: 1120 },
      ],
      exitCode: 0,
      signal: 0,
      wallMs: 120,
      cpuMs: 85,
      peakMemoryKb: 131072, // 128 MiB
      stdout: 'test output',
      stderr: '',
    };

    render(
      <TelemetryPanel
        result={mockResult}
        isRunning={false}
        milestones={mockResult.milestones}
      />
    );

    expect(screen.getAllByText('COMPLETED').length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText('120 ms')).toBeInTheDocument();
    expect(screen.getByText('85 ms')).toBeInTheDocument();
    expect(screen.getByText('128.0 MiB')).toBeInTheDocument();
    expect(screen.getByText('0 (Success)')).toBeInTheDocument();
    expect(screen.getByText('None (0)')).toBeInTheDocument();

    // Verify milestone chips
    expect(screen.getByText('ACCEPTED')).toBeInTheDocument();
  });
});
