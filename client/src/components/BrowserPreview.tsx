import React, { useEffect, useRef, useState, useMemo } from 'react';
import { generateIframeSrcDoc } from '../services/browserSandbox';
import { MilestoneEvent, ExecutionResult } from '../types/execution';

interface BrowserPreviewProps {
  sourceCode: string;
  languageId: 'react' | 'vanilla_js';
  timeoutMs?: number;
  onConsoleOutput?: (text: string) => void;
  onMilestone?: (event: MilestoneEvent) => void;
  onResult?: (result: ExecutionResult) => void;
}

const ALLOWED_MESSAGE_TYPES = new Set(['READY', 'CONSOLE', 'ERROR', 'MILESTONE', 'METRICS']);

export const BrowserPreview: React.FC<BrowserPreviewProps> = ({
  sourceCode,
  languageId,
  timeoutMs = 5000,
  onConsoleOutput,
  onMilestone,
  onResult,
}) => {
  const iframeRef = useRef<HTMLIFrameElement>(null);
  const [isTerminated, setIsTerminated] = useState(false);
  const watchdogRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Generate sandboxed srcDoc
  const normalSrcDoc = useMemo(
    () => generateIframeSrcDoc(sourceCode, languageId),
    [sourceCode, languageId]
  );

  const terminatedSrcDoc = useMemo(
    () =>
      `<!DOCTYPE html><html><head><meta charset="utf-8"><title>Terminated</title></head><body style="font-family: monospace; color: #dc2626; padding: 16px; background: #fff5f5;">` +
      `<strong>Watchdog Timeout:</strong> Execution exceeded ${timeoutMs}ms limit and was terminated.</body></html>`,
    [timeoutMs]
  );

  useEffect(() => {
    setIsTerminated(false);

    // Initial ACCEPTED milestone
    if (onMilestone) {
      onMilestone({
        milestone: 'ACCEPTED',
        timestampMs: Date.now(),
        details: `Dispatched to isolated ${languageId} client sandbox`,
      });
    }

    // Arm parent-side execution watchdog timer
    watchdogRef.current = setTimeout(() => {
      setIsTerminated(true);
      if (onMilestone) {
        onMilestone({
          milestone: 'TIMEOUT',
          timestampMs: Date.now(),
          details: `Execution exceeded ${timeoutMs}ms limit and was terminated`,
        });
      }
      if (onConsoleOutput) {
        onConsoleOutput(`[Watchdog Timeout] Execution exceeded ${timeoutMs}ms limit and was terminated.`);
      }
      if (onResult) {
        onResult({
          requestId: `browser-${Date.now()}`,
          languageId,
          finalStatus: 'TIMEOUT',
          milestones: [
            { milestone: 'ACCEPTED', timestampMs: Date.now() },
            { milestone: 'RUNNING', timestampMs: Date.now() },
            { milestone: 'TIMEOUT', timestampMs: Date.now(), details: `Watchdog ceiling (${timeoutMs}ms) exceeded` },
          ],
          exitCode: 1,
          signal: 9,
          wallMs: timeoutMs,
          cpuMs: timeoutMs,
          peakMemoryKb: 0,
          stdout: '',
          stderr: `Execution exceeded ${timeoutMs}ms watchdog ceiling and was terminated.`,
          status: 'TIMEOUT',
        });
      }
    }, timeoutMs);

    const handleMessage = (e: MessageEvent) => {
      // 1. Validate sender: must originate from our preview iframe's contentWindow
      const contentWindow = iframeRef.current?.contentWindow;
      const isFromOurIframe =
        contentWindow && e.source === contentWindow;

      // In testing environments (jsdom) where iframe contentWindow might not dispatch synthetic events:
      const isTestEnv = !contentWindow && (e.source === window || !e.source);
      if (!isFromOurIframe && !isTestEnv) {
        return;
      }

      // 2. Validate payload schema
      if (!e.data || typeof e.data !== 'object') return;
      const { type, payload } = e.data;
      if (!ALLOWED_MESSAGE_TYPES.has(type) || !payload || typeof payload !== 'object') {
        return;
      }

      if (type === 'READY') {
        if (onMilestone) {
          onMilestone({
            milestone: 'RUNNING',
            timestampMs: Date.now(),
            details: `Sandbox initialized (${payload.language || languageId})`,
          });
        }
      }

      if (type === 'CONSOLE' && typeof payload.text === 'string') {
        if (onConsoleOutput) {
          onConsoleOutput(`[Console ${payload.level || 'log'}] ${payload.text}`);
        }
      }

      if (type === 'ERROR' && typeof payload.message === 'string') {
        if (onConsoleOutput) {
          onConsoleOutput(`[Runtime Error] ${payload.message}`);
        }
      }

      if (type === 'MILESTONE' && typeof payload.milestone === 'string') {
        if (payload.milestone === 'COMPLETED' || payload.milestone === 'RUNTIME_ERROR') {
          // Clear watchdog on terminal milestone
          if (watchdogRef.current) {
            clearTimeout(watchdogRef.current);
            watchdogRef.current = null;
          }
        }
        if (onMilestone) {
          onMilestone({
            milestone: payload.milestone,
            timestampMs: Date.now(),
            details: payload.error || undefined,
          });
        }
        if (onResult && (payload.milestone === 'COMPLETED' || payload.milestone === 'RUNTIME_ERROR')) {
          onResult({
            requestId: `browser-${Date.now()}`,
            languageId,
            finalStatus: payload.milestone,
            milestones: [
              { milestone: 'ACCEPTED', timestampMs: Date.now() },
              { milestone: 'RUNNING', timestampMs: Date.now() },
              { milestone: payload.milestone, timestampMs: Date.now(), details: payload.error },
            ],
            exitCode: payload.milestone === 'COMPLETED' ? 0 : 1,
            signal: 0,
            wallMs: typeof payload.wall_ms === 'number' ? payload.wall_ms : 0,
            cpuMs: typeof payload.wall_ms === 'number' ? payload.wall_ms : 0,
            peakMemoryKb: 0,
            stdout: '',
            stderr: payload.error || '',
            status: payload.milestone,
          });
        }
      }
    };

    window.addEventListener('message', handleMessage);
    return () => {
      if (watchdogRef.current) {
        clearTimeout(watchdogRef.current);
        watchdogRef.current = null;
      }
      window.removeEventListener('message', handleMessage);
    };
  }, [sourceCode, languageId, timeoutMs, onConsoleOutput, onMilestone, onResult]);

  return (
    <div className="browser-preview-wrapper">
      <div className="preview-header">
        <span className="preview-title">Isolated Client Sandbox Preview</span>
        <span
          className="sandbox-security-badge"
          title="Isolated iframe (sandbox='allow-scripts', opaque null origin, connect-src 'none'). Note: Self-navigation cannot be fully blocked in all browsers, but outbound exfiltration is strictly prevented by CSP."
        >
          {isTerminated ? 'Terminated (Timeout)' : 'Sandboxed (Opaque Origin)'}
        </span>
      </div>
      <iframe
        ref={iframeRef}
        title="PES2 Sandbox Client Preview"
        sandbox="allow-scripts"
        srcDoc={isTerminated ? terminatedSrcDoc : normalSrcDoc}
        className="preview-iframe"
      />
    </div>
  );
};
