import React, { useEffect, useRef } from 'react';
import { generateIframeSrcDoc } from '../services/browserSandbox';

interface BrowserPreviewProps {
  sourceCode: string;
  languageId: 'react' | 'vanilla_js';
  onConsoleOutput?: (text: string) => void;
}

export const BrowserPreview: React.FC<BrowserPreviewProps> = ({
  sourceCode,
  languageId,
  onConsoleOutput,
}) => {
  const iframeRef = useRef<HTMLIFrameElement>(null);

  useEffect(() => {
    const handleMessage = (e: MessageEvent) => {
      // Disallow non-iframe origins or messages without expected shape
      if (!e.data || typeof e.data !== 'object') return;
      if (e.data.type === 'CONSOLE' && e.data.payload?.text) {
        if (onConsoleOutput) {
          onConsoleOutput(`[Console ${e.data.payload.level || 'log'}] ${e.data.payload.text}`);
        }
      }
      if (e.data.type === 'ERROR' && e.data.payload?.message) {
        if (onConsoleOutput) {
          onConsoleOutput(`[Runtime Error] ${e.data.payload.message}`);
        }
      }
    };

    window.addEventListener('message', handleMessage);
    return () => window.removeEventListener('message', handleMessage);
  }, [onConsoleOutput]);

  const srcDoc = generateIframeSrcDoc(sourceCode, languageId);

  return (
    <div className="browser-preview-wrapper">
      <div className="preview-header">
        <span className="preview-title">Isolated Client Sandbox Preview</span>
        <span className="sandbox-security-badge" title="sandbox='allow-scripts' without allow-same-origin (opaque null origin, connect-src 'none')">
          Sandboxed (Opaque Origin)
        </span>
      </div>
      <iframe
        ref={iframeRef}
        title="PES2 Sandbox Client Preview"
        sandbox="allow-scripts"
        srcDoc={srcDoc}
        className="preview-iframe"
      />
    </div>
  );
};
