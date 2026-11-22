import { describe, it, expect, vi, beforeEach } from 'vitest';
import { ExecutionApiClient, getStoredApiKey, setStoredApiKey } from '../src/services/api';
import { ExecutionRequest, MilestoneEvent } from '../src/types/execution';

describe('ExecutionApiClient SSE Streaming & Dynamic Runtimes', () => {
  let client: ExecutionApiClient;

  beforeEach(() => {
    client = new ExecutionApiClient();
    setStoredApiKey('');
    vi.restoreAllMocks();
  });

  it('attaches Authorization and X-API-Key headers when API key is configured', async () => {
    setStoredApiKey('token_xyz_12345');
    expect(getStoredApiKey()).toBe('token_xyz_12345');

    let capturedHeaders: Record<string, string> = {};
    const mockFetch = vi.fn().mockImplementation(async (_url: string, init?: RequestInit) => {
      capturedHeaders = (init?.headers || {}) as Record<string, string>;
      return {
        ok: true,
        json: async () => ({ status: 'SERVING', runner_version: '2.0', active_workers: 1 }),
      };
    });
    vi.stubGlobal('fetch', mockFetch);

    await client.checkHealth();
    expect(capturedHeaders['Authorization']).toBe('Bearer token_xyz_12345');
    expect(capturedHeaders['X-API-Key']).toBe('token_xyz_12345');

    // Clear key
    setStoredApiKey('');
    expect(getStoredApiKey()).toBe('');
    await client.checkHealth();
    expect(capturedHeaders['Authorization']).toBeUndefined();
    expect(capturedHeaders['X-API-Key']).toBeUndefined();
  });

  it('streams milestones in real time from SSE endpoint and parses final result', async () => {
    const sseChunks = [
      'data: {"type": "milestone", "milestone": "COMPILING", "timestampMs": 100, "details": "Invoking gcc 14"}\n\n',
      'data: {"type": "milestone", "milestone": "RUNNING", "timestampMs": 200, "details": "Executing solution binary"}\n\n',
      'data: {"type": "result", "result": {"requestId": "req-stream-1", "languageId": "c", "finalStatus": "COMPLETED", "exitCode": 0, "signal": 0, "wallMs": 45, "cpuMs": 30, "peakMemoryKb": 1024, "stdout": "Stream OK\\n", "stderr": "", "status": "COMPLETED", "milestones": []}}\n\n',
    ];

    const encoder = new TextEncoder();
    let chunkIndex = 0;

    const mockReadableStream = new ReadableStream({
      pull(controller) {
        if (chunkIndex < sseChunks.length) {
          controller.enqueue(encoder.encode(sseChunks[chunkIndex]));
          chunkIndex++;
        } else {
          controller.close();
        }
      },
    });

    const mockFetch = vi.fn().mockResolvedValue({
      ok: true,
      body: mockReadableStream,
    });
    vi.stubGlobal('fetch', mockFetch);

    const receivedMilestones: MilestoneEvent[] = [];
    const request: ExecutionRequest = {
      requestId: 'req-stream-1',
      languageId: 'c',
      sourceCode: '#include <stdio.h>\nint main() { printf("Stream OK\\n"); return 0; }',
    };

    const result = await client.executeStream(request, (ev) => {
      receivedMilestones.push(ev);
    });

    // Verify milestones received in order
    expect(receivedMilestones.length).toBeGreaterThanOrEqual(3);
    expect(receivedMilestones[0].milestone).toBe('ACCEPTED');
    expect(receivedMilestones[1].milestone).toBe('COMPILING');
    expect(receivedMilestones[1].details).toBe('Invoking gcc 14');
    expect(receivedMilestones[2].milestone).toBe('RUNNING');

    // Verify final result
    expect(result.finalStatus).toBe('COMPLETED');
    expect(result.stdout).toBe('Stream OK\n');
    expect(result.wallMs).toBe(45);
  });

  it('populates runtime status truthfully from worker inventory and keeps browser runtimes client-side', async () => {
    const mockRuntimesResponse = {
      runtimes: [
        { id: 'python3', is_installed: true, installed_version: 'Python 3.12.1' },
        { id: 'rust', is_installed: false, installed_version: 'Not installed on host' },
        { id: 'react', is_installed: false, installed_version: 'Browser-only runtime: client-side execution' },
        { id: 'vanilla_js', is_installed: false, installed_version: 'Browser-only runtime: client-side execution' },
      ],
    };

    const mockFetch = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => mockRuntimesResponse,
    });
    vi.stubGlobal('fetch', mockFetch);

    const runtimes = await client.getSupportedRuntimes();

    const py = runtimes.find((r) => r.id === 'python3');
    expect(py?.status).toBe('supported');
    expect(py?.installedVersion).toBe('Python 3.12.1');

    const rust = runtimes.find((r) => r.id === 'rust');
    expect(rust?.status).toBe('uninstalled');

    // Browser-only runtimes must remain supported in the client studio
    const react = runtimes.find((r) => r.id === 'react');
    expect(react?.status).toBe('supported');

    const vjs = runtimes.find((r) => r.id === 'vanilla_js');
    expect(vjs?.status).toBe('supported');
  });

  it('falls back gracefully to honest offline result when endpoint is unreachable', async () => {
    const mockFetch = vi.fn().mockRejectedValue(new Error('Network error'));
    vi.stubGlobal('fetch', mockFetch);

    const request: ExecutionRequest = {
      requestId: 'req-offline',
      languageId: 'python3',
      sourceCode: 'print(1)',
    };

    const result = await client.executeStream(request);
    expect(result.finalStatus).toBe('SANDBOX_ERROR');
    expect(result.stderr).toContain('Backend daemon connection failed');
  });
});
