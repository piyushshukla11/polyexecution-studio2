import { ExecutionRequest, ExecutionResult, MilestoneEvent } from '../types/execution';
import { LanguageDefinition } from '../types/language';
import { LANGUAGES } from '../constants/languages';

const API_BASE = '/api';

export interface HealthCheckResult {
  isServing: boolean;
  runnerVersion: string;
  activeWorkers: number;
}

interface BackendRuntimeItem {
  id: string;
  installed_version?: string;
  is_installed?: boolean;
}

export class ExecutionApiClient {
  async checkHealth(): Promise<HealthCheckResult> {
    try {
      const resp = await fetch(`${API_BASE}/health`, { method: 'GET' });
      if (resp.ok) {
        const data = await resp.json();
        return {
          isServing: data.status === 'SERVING',
          runnerVersion: data.runner_version || 'pes2-runner-2.0',
          activeWorkers: data.active_workers || 0,
        };
      }
    } catch {
      // Backend not running or unreachable
    }
    return {
      isServing: false,
      runnerVersion: 'unavailable',
      activeWorkers: 0,
    };
  }

  async getSupportedRuntimes(): Promise<LanguageDefinition[]> {
    try {
      const resp = await fetch(`${API_BASE}/runtimes`, { method: 'GET' });
      if (resp.ok) {
        const data = await resp.json();
        if (Array.isArray(data.runtimes) && data.runtimes.length > 0) {
          const backendMap = new Map<string, BackendRuntimeItem>(
            data.runtimes.map((r: BackendRuntimeItem) => [r.id, r])
          );
          return LANGUAGES.map((lang) => {
            // Browser-only runtimes run client-side in the browser sandbox
            if (lang.id === 'react' || lang.id === 'vanilla_js') {
              return {
                ...lang,
                status: 'supported',
              };
            }
            const b = backendMap.get(lang.id);
            if (b) {
              return {
                ...lang,
                installedVersion: b.installed_version || lang.installedVersion,
                status: b.is_installed ? 'supported' : 'uninstalled',
              };
            }
            return lang;
          });
        }
      }
    } catch {
      // Fallback to truthful compiled inventory
    }
    return LANGUAGES;
  }

  async executeStream(
    request: ExecutionRequest,
    onMilestone?: (event: MilestoneEvent) => void
  ): Promise<ExecutionResult> {
    const startTime = Date.now();

    if (onMilestone) {
      onMilestone({
        milestone: 'ACCEPTED',
        timestampMs: startTime,
        details: `Request ${request.requestId} queued for ${request.languageId}`,
      });
    }

    try {
      const resp = await fetch(`${API_BASE}/execute/stream`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(request),
      });

      if (resp.ok && resp.body) {
        const reader = resp.body.getReader();
        const decoder = new TextDecoder('utf-8');
        let buffer = '';
        let finalResult: ExecutionResult | null = null;

        while (true) {
          const { done, value } = await reader.read();
          if (done) break;
          buffer += decoder.decode(value, { stream: true });

          const blocks = buffer.split('\n\n');
          buffer = blocks.pop() || '';

          for (const block of blocks) {
            const trimmed = block.trim();
            if (!trimmed.startsWith('data:')) continue;
            const jsonStr = trimmed.slice(5).trim();
            try {
              const event = JSON.parse(jsonStr);
              if (event.type === 'milestone') {
                if (onMilestone) {
                  onMilestone({
                    milestone: event.milestone,
                    timestampMs: event.timestampMs || Date.now(),
                    details: event.details,
                  });
                }
              } else if (event.type === 'result') {
                finalResult = event.result as ExecutionResult;
              } else if (event.type === 'error') {
                throw new Error(event.error);
              }
            } catch {
              // Ignore partial chunk parse failures
            }
          }
        }

        if (finalResult) {
          return finalResult;
        }
      }
    } catch {
      // Fallback to executeSync or honest offline result
    }

    return this.executeSync(request, onMilestone);
  }

  async executeSync(
    request: ExecutionRequest,
    onMilestone?: (event: MilestoneEvent) => void
  ): Promise<ExecutionResult> {
    const startTime = Date.now();

    try {
      const resp = await fetch(`${API_BASE}/execute`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(request),
      });

      if (resp.ok) {
        const result: ExecutionResult = await resp.json();
        if (onMilestone && Array.isArray(result.milestones)) {
          for (const m of result.milestones) {
            onMilestone(m);
          }
        }
        return result;
      }
    } catch {
      // Backend HTTP API unavailable
    }

    const wallMs = Date.now() - startTime;
    return {
      requestId: request.requestId,
      languageId: request.languageId,
      finalStatus: 'SANDBOX_ERROR',
      milestones: [
        { milestone: 'ACCEPTED', timestampMs: startTime },
        { milestone: 'SANDBOX_ERROR', timestampMs: Date.now(), details: 'Backend gRPC worker daemon offline on loopback port 50051' },
      ],
      exitCode: 1,
      signal: 0,
      wallMs,
      cpuMs: 0,
      peakMemoryKb: 0,
      stdout: '',
      stderr: 'Backend daemon connection failed: Ensure pes2-daemon is running (python -m engine.daemon.server --port 50051).',
      status: 'SANDBOX_ERROR',
    };
  }

  async execute(
    request: ExecutionRequest,
    onMilestone?: (event: MilestoneEvent) => void
  ): Promise<ExecutionResult> {
    return this.executeStream(request, onMilestone);
  }
}

export const apiClient = new ExecutionApiClient();
