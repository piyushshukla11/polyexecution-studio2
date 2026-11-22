import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, act } from '@testing-library/react';
import {
  transpileReactSource,
  generateIframeSrcDoc,
  STRICT_IFRAME_CSP,
} from '../src/services/browserSandbox';
import { BrowserPreview } from '../src/components/BrowserPreview';

describe('Browser Sandbox Hardening & Real JSX Execution', () => {
  it('transpiles real JSX and ES imports into CommonJS via Sucrase without external CDNs', () => {
    const rawJsx = `import React from 'react';
export default function App() {
  return <div className="card"><h1>Test Header</h1><p>Paragraph</p></div>;
}`;

    const { code, error } = transpileReactSource(rawJsx);
    expect(error).toBeNull();
    expect(code).toContain('require(\'react\')');
    expect(code).toContain("createElement('div'");
    expect(code).toContain("createElement('h1'");
    expect(code).toContain('exports.default = App');
  });

  it('captures JSX syntax errors gracefully and reports error message', () => {
    const brokenJsx = `export default function App( { return <div; }`;
    const { code, error } = transpileReactSource(brokenJsx);
    expect(code).toBe('');
    expect(error).not.toBeNull();
    expect(error).toContain('Unexpected token');
  });

  it('generates sandboxed iframe srcDoc with strict CSP and bundled React', () => {
    const srcDoc = generateIframeSrcDoc(
      'export default function App() { return <span>Hello</span>; }',
      'react'
    );

    // Strict CSP preventing network exfiltration
    expect(srcDoc).toContain(STRICT_IFRAME_CSP);
    expect(srcDoc).toContain("connect-src 'none'");
    expect(srcDoc).toContain("default-src 'none'");

    // Bundled React / ReactDOM scripts
    expect(srcDoc).toContain('window.React');
    expect(srcDoc).toContain('window.ReactDOM');

    // Local module require shim
    expect(srcDoc).toContain("function require(name)");
    expect(srcDoc).toContain("_moduleRegistry");

    // PostMessage communication harness
    expect(srcDoc).toContain('safePostMessage');
    expect(srcDoc).toContain('READY');
  });

  it('generates error document when JSX compilation fails', () => {
    const brokenJsx = `export default function App( { return <div; }`;
    const srcDoc = generateIframeSrcDoc(brokenJsx, 'react');
    expect(srcDoc).toContain('JSX Compilation Error:');
    expect(srcDoc).toContain('RUNTIME_ERROR');
  });
});

describe('BrowserPreview Component Watchdog & Message Validation', () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it('enforces parent-side execution watchdog timeout for stuck code', () => {
    const onMilestone = vi.fn();
    const onConsoleOutput = vi.fn();
    const onResult = vi.fn();

    render(
      <BrowserPreview
        sourceCode="while(true) {}"
        languageId="react"
        timeoutMs={200}
        onMilestone={onMilestone}
        onConsoleOutput={onConsoleOutput}
        onResult={onResult}
      />
    );

    // Initial ACCEPTED milestone dispatched
    expect(onMilestone).toHaveBeenCalledWith(
      expect.objectContaining({ milestone: 'ACCEPTED' })
    );

    // Advance clock past 200ms watchdog ceiling
    act(() => {
      vi.advanceTimersByTime(250);
    });

    // Verify TIMEOUT milestone dispatched by parent watchdog
    expect(onMilestone).toHaveBeenCalledWith(
      expect.objectContaining({
        milestone: 'TIMEOUT',
        details: expect.stringContaining('200ms limit and was terminated'),
      })
    );

    // Verify error reported on console
    expect(onConsoleOutput).toHaveBeenCalledWith(
      expect.stringContaining('[Watchdog Timeout]')
    );

    // Verify final result reported
    expect(onResult).toHaveBeenCalledWith(
      expect.objectContaining({
        finalStatus: 'TIMEOUT',
        status: 'TIMEOUT',
      })
    );

    // Verify UI reflects terminated state
    expect(screen.getByText('Terminated (Timeout)')).toBeInTheDocument();
  });

  it('ignores malformed postMessage events and non-allowed types', () => {
    const onConsoleOutput = vi.fn();
    const onMilestone = vi.fn();

    render(
      <BrowserPreview
        sourceCode="console.log('hi')"
        languageId="vanilla_js"
        onConsoleOutput={onConsoleOutput}
        onMilestone={onMilestone}
      />
    );

    onMilestone.mockClear();

    const iframe = screen.getByTitle('PES2 Sandbox Client Preview') as HTMLIFrameElement;

    // 1. Unknown message type
    window.dispatchEvent(
      new MessageEvent('message', {
        data: { type: 'MALICIOUS_INJECTION', payload: { foo: 'bar' } },
        source: iframe.contentWindow,
      })
    );
    expect(onConsoleOutput).not.toHaveBeenCalled();

    // 2. Non-object data
    window.dispatchEvent(
      new MessageEvent('message', {
        data: 'string-payload',
        source: iframe.contentWindow,
      })
    );
    expect(onConsoleOutput).not.toHaveBeenCalled();

    // 3. Unauthorized sender window (not from our iframe)
    window.dispatchEvent(
      new MessageEvent('message', {
        data: {
          type: 'CONSOLE',
          payload: { level: 'log', text: 'Message from untrusted window' },
        },
        source: window,
      })
    );
    expect(onConsoleOutput).not.toHaveBeenCalled();

    // 4. Allowed type CONSOLE from our iframe's contentWindow with valid text
    window.dispatchEvent(
      new MessageEvent('message', {
        data: {
          type: 'CONSOLE',
          payload: { level: 'log', text: 'Clean valid console message' },
        },
        source: iframe.contentWindow,
      })
    );
    expect(onConsoleOutput).toHaveBeenCalledWith(
      '[Console log] Clean valid console message'
    );
  });
});
