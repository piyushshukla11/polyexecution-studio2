import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { OutputTerminal } from '../src/components/OutputTerminal';
import { ExecutionResult } from '../src/types/execution';

describe('OutputTerminal Component', () => {
  it('renders stdout and stderr streams accurately', () => {
    const mockResult: ExecutionResult = {
      requestId: 'req-2',
      languageId: 'python3',
      finalStatus: 'COMPLETED',
      status: 'COMPLETED',
      milestones: [],
      exitCode: 0,
      signal: 0,
      wallMs: 50,
      cpuMs: 30,
      peakMemoryKb: 20000,
      stdout: 'Hello World stdout\n',
      stderr: '',
    };

    render(
      <OutputTerminal
        result={mockResult}
        isRunning={false}
        selectedLanguageId="python3"
        sourceCode="print('Hello World stdout')"
      />
    );

    expect(screen.getByText('Hello World stdout')).toBeInTheDocument();
  });

  it('detects structured tabular JSON output and displays Table View tab', () => {
    const tabularJson = JSON.stringify({
      columns: ['id', 'name', 'salary'],
      rows: [
        [1, 'Alice', 95000],
        [2, 'Bob', 82000],
      ],
    });

    const mockResult: ExecutionResult = {
      requestId: 'req-sql-1',
      languageId: 'sqlite',
      finalStatus: 'COMPLETED',
      status: 'COMPLETED',
      milestones: [],
      exitCode: 0,
      signal: 0,
      wallMs: 40,
      cpuMs: 20,
      peakMemoryKb: 15000,
      stdout: tabularJson,
      stderr: '',
    };

    render(
      <OutputTerminal
        result={mockResult}
        isRunning={false}
        selectedLanguageId="sqlite"
        sourceCode="SELECT * FROM employees;"
      />
    );

    // Tab button for Table View should exist
    expect(screen.getByText('Table View')).toBeInTheDocument();

    // Table headers should render
    expect(screen.getByText('name')).toBeInTheDocument();
    expect(screen.getByText('salary')).toBeInTheDocument();
    expect(screen.getByText('Alice')).toBeInTheDocument();
    expect(screen.getByText('Bob')).toBeInTheDocument();
  });
});
