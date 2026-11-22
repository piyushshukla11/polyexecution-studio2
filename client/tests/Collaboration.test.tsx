import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { CollaborationBanner } from '../src/components/CollaborationBanner';
import { useCollaboration, MISSING_BACKEND_COLLAB_NOTICE } from '../src/hooks/useCollaboration';

describe('Collaboration Architecture & Honesty Doctrine', () => {
  it('honestly reports standalone editor mode and missing WebSocket CRDT gateway', () => {
    const onClose = vi.fn();
    const state = {
      mode: 'standalone' as const,
      roomId: 'pes2-default-room',
      activeUsers: [
        { id: '1', name: 'Local Operator', color: '#2563eb', isSelf: true },
      ],
      backendAvailable: false,
      missingCapabilityNotice: MISSING_BACKEND_COLLAB_NOTICE,
    };

    render(
      <CollaborationBanner state={state} isOpen={true} onClose={onClose} />
    );

    expect(screen.getByText('Collaborative Editing Architecture')).toBeInTheDocument();
    expect(screen.getByText('Standalone Editor Mode')).toBeInTheDocument();
    expect(screen.getByText(/Persistent WebSocket Gateway Server/i)).toBeInTheDocument();
    expect(screen.getByText(/Honesty Doctrine:/i)).toBeInTheDocument();

    const dismissBtn = screen.getByRole('button', { name: 'Dismiss' });
    fireEvent.click(dismissBtn);
    expect(onClose).toHaveBeenCalled();
  });

  it('useCollaboration hook defaults to standalone mode without fake simulated peers', () => {
    const TestComponent = () => {
      const { state } = useCollaboration();
      return (
        <div>
          <span data-testid="mode">{state.mode}</span>
          <span data-testid="user-count">{state.activeUsers.length}</span>
          <span data-testid="notice">{state.missingCapabilityNotice}</span>
        </div>
      );
    };

    render(<TestComponent />);
    expect(screen.getByTestId('mode').textContent).toBe('standalone');
    expect(screen.getByTestId('user-count').textContent).toBe('1');
    expect(screen.getByTestId('notice').textContent).toContain('WebSocket CRDT document gateway');
  });
});
