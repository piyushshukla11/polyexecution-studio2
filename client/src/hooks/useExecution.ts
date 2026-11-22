import { useState, useCallback } from 'react';
import { ExecutionRequest, ExecutionResult, MilestoneEvent } from '../types/execution';
import { apiClient } from '../services/api';

export function useExecution() {
  const [isRunning, setIsRunning] = useState<boolean>(false);
  const [currentMilestone, setCurrentMilestone] = useState<MilestoneEvent | null>(null);
  const [milestones, setMilestones] = useState<MilestoneEvent[]>([]);
  const [result, setResult] = useState<ExecutionResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  const execute = useCallback(async (request: ExecutionRequest) => {
    setIsRunning(true);
    setError(null);
    setMilestones([]);
    setCurrentMilestone(null);
    setResult(null);

    const onMilestone = (ev: MilestoneEvent) => {
      setCurrentMilestone(ev);
      setMilestones((prev) => [...prev, ev]);
    };

    try {
      const res = await apiClient.execute(request, onMilestone);
      setResult(res);
      if (res.finalStatus === 'SANDBOX_ERROR' || res.finalStatus === 'RUNTIME_ERROR') {
        setError(res.stderr || `Execution finished with status ${res.finalStatus}`);
      }
      return res;
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : String(err);
      setError(msg);
      return null;
    } finally {
      setIsRunning(false);
    }
  }, []);

  const reset = useCallback(() => {
    setIsRunning(false);
    setCurrentMilestone(null);
    setMilestones([]);
    setResult(null);
    setError(null);
  }, []);

  return {
    isRunning,
    currentMilestone,
    milestones,
    result,
    error,
    execute,
    reset,
  };
}
