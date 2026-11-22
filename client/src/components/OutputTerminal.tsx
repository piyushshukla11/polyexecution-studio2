import React, { useState, useMemo } from 'react';
import { ExecutionResult, TabularData, MilestoneEvent } from '../types/execution';
import { TabularPreview } from './TabularPreview';
import { BrowserPreview } from './BrowserPreview';

interface OutputTerminalProps {
  result: ExecutionResult | null;
  isRunning: boolean;
  selectedLanguageId: string;
  sourceCode: string;
  onMilestone?: (event: MilestoneEvent) => void;
  onResult?: (result: ExecutionResult) => void;
}

export const OutputTerminal: React.FC<OutputTerminalProps> = ({
  result,
  isRunning,
  selectedLanguageId,
  sourceCode,
  onMilestone,
  onResult,
}) => {
  const [activeTab, setActiveTab] = useState<'stdout' | 'stderr' | 'tabular' | 'preview'>('stdout');
  const [liveLogs, setLiveLogs] = useState<string[]>([]);

  // Check if stdout contains structured tabular data
  const parsedTabularData: TabularData | TabularData[] | null = useMemo(() => {
    if (!result?.stdout) return null;
    try {
      const trimmed = result.stdout.trim();
      if ((trimmed.startsWith('{') && trimmed.endsWith('}')) || (trimmed.startsWith('[') && trimmed.endsWith(']'))) {
        const parsed = JSON.parse(trimmed);
        if (parsed && typeof parsed === 'object') {
          if ('columns' in parsed && 'rows' in parsed) {
            return parsed as TabularData;
          }
          if (Array.isArray(parsed) && parsed.length > 0 && 'columns' in parsed[0]) {
            return parsed as TabularData[];
          }
        }
      }
    } catch {
      // Not JSON tabular data
    }
    return null;
  }, [result?.stdout]);

  const hasTabular = parsedTabularData !== null;
  const isBrowserLang = selectedLanguageId === 'react' || selectedLanguageId === 'vanilla_js';

  // Automatically switch tab on new result
  React.useEffect(() => {
    if (result) {
      if (result.stderr && !result.stdout) {
        setActiveTab('stderr');
      } else if (hasTabular) {
        setActiveTab('tabular');
      } else if (isBrowserLang) {
        setActiveTab('preview');
      } else {
        setActiveTab('stdout');
      }
    }
  }, [result, hasTabular, isBrowserLang]);

  return (
    <div className="output-terminal-container">
      <div className="terminal-tab-bar">
        <button
          type="button"
          className={`tab-btn ${activeTab === 'stdout' ? 'active' : ''}`}
          onClick={() => setActiveTab('stdout')}
        >
          Stdout
          {result?.stdout && <span className="tab-dot" />}
        </button>

        <button
          type="button"
          className={`tab-btn ${activeTab === 'stderr' ? 'active' : ''} ${result?.stderr ? 'has-error' : ''}`}
          onClick={() => setActiveTab('stderr')}
        >
          Stderr
          {result?.stderr && <span className="tab-error-badge">!</span>}
        </button>

        {hasTabular && (
          <button
            type="button"
            className={`tab-btn ${activeTab === 'tabular' ? 'active' : ''}`}
            onClick={() => setActiveTab('tabular')}
          >
            Table View
            <span className="tab-badge">JSON</span>
          </button>
        )}

        {isBrowserLang && (
          <button
            type="button"
            className={`tab-btn ${activeTab === 'preview' ? 'active' : ''}`}
            onClick={() => setActiveTab('preview')}
          >
            Live UI Preview
            <span className="tab-badge">DOM</span>
          </button>
        )}

        <div className="tab-actions">
          {result?.stdout && activeTab === 'stdout' && (
            <button
              type="button"
              className="copy-btn"
              onClick={() => navigator.clipboard?.writeText(result.stdout)}
              title="Copy output to clipboard"
            >
              Copy
            </button>
          )}
        </div>
      </div>

      <div className="terminal-content">
        {isRunning && (
          <div className="terminal-loading-overlay">
            <span className="terminal-spinner" />
            <span>Execution in progress inside kernel sandbox...</span>
          </div>
        )}

        {activeTab === 'stdout' && (
          <pre className="terminal-stream stdout-stream">
            {result?.stdout ? (
              result.stdout
            ) : (
              <span className="terminal-placeholder">
                {isRunning
                  ? 'Awaiting stdout milestone...'
                  : 'Press "Run Code" to execute submission and stream output.'}
              </span>
            )}
          </pre>
        )}

        {activeTab === 'stderr' && (
          <pre className="terminal-stream stderr-stream">
            {result?.stderr ? (
              result.stderr
            ) : (
              <span className="terminal-placeholder">No errors reported on stderr.</span>
            )}
          </pre>
        )}

        {activeTab === 'tabular' && parsedTabularData && (
          <TabularPreview data={parsedTabularData} />
        )}

        {activeTab === 'preview' && isBrowserLang && (
          <div className="browser-preview-tab-wrapper">
            <BrowserPreview
              sourceCode={sourceCode}
              languageId={selectedLanguageId as 'react' | 'vanilla_js'}
              onConsoleOutput={(log) => setLiveLogs((prev) => [...prev, log])}
              onMilestone={onMilestone}
              onResult={onResult}
            />
            {liveLogs.length > 0 && (
              <div className="live-logs-drawer">
                <div className="drawer-title">Sandbox Console Stream</div>
                <div className="drawer-logs">
                  {liveLogs.map((l, i) => (
                    <div key={i} className="live-log-line">{l}</div>
                  ))}
                </div>
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
};
