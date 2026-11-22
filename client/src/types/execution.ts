export type Milestone =
  | 'ACCEPTED'
  | 'COMPILING'
  | 'RUNNING'
  | 'COMPLETED'
  | 'COMPILE_ERROR'
  | 'RUNTIME_ERROR'
  | 'TIMEOUT'
  | 'OUTPUT_LIMIT'
  | 'MEMORY_LIMIT'
  | 'BLOCKED_BY_POLICY'
  | 'SANDBOX_ERROR'
  | 'CANCELLED';

export interface MilestoneEvent {
  milestone: Milestone;
  timestampMs: number;
  details?: string;
}

export interface ResourceLimits {
  cpuMs: number;
  wallMs: number;
  memoryKb: number;
  pids: number;
  maxOutputBytes: number;
}

export interface ExecutionRequest {
  requestId: string;
  languageId: string;
  sourceCode: string;
  stdin?: string;
  customLimits?: Partial<ResourceLimits>;
}

export interface ExecutionTelemetry {
  wallMs: number;
  cpuMs: number;
  peakMemoryKb: number;
  exitCode: number;
  signal: number;
  status: Milestone;
}

export interface TabularData {
  columns: string[];
  rows: (string | number | boolean | null)[][];
}

export interface ExecutionResult extends ExecutionTelemetry {
  requestId: string;
  languageId: string;
  finalStatus?: Milestone;
  milestones: MilestoneEvent[];
  stdout: string;
  stderr: string;
  compileOutput?: string;
  tabularResult?: TabularData | TabularData[];
}
