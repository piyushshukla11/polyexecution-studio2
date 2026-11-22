import React from 'react';
import { LanguageDefinition } from '../types/language';
import { CollaborationState } from '../types/collaboration';

interface HeaderProps {
  selectedLanguage: LanguageDefinition;
  isRunning: boolean;
  onRun: () => void;
  collabState: CollaborationState;
  onToggleCollabModal: () => void;
  daemonServing: boolean;
}

export const Header: React.FC<HeaderProps> = ({
  selectedLanguage,
  isRunning,
  onRun,
  collabState,
  onToggleCollabModal,
  daemonServing,
}) => {
  const isRunnable = selectedLanguage.status === 'supported';

  return (
    <header className="pes2-header">
      <div className="header-left">
        <div className="brand">
          <span className="brand-logo">PES2</span>
          <span className="brand-title">PolyExecution Studio</span>
          <span className="brand-sub">Remote Code Execution & Studio</span>
        </div>
      </div>

      <div className="header-center">
        <div className="status-indicator">
          <span
            className={`status-dot ${daemonServing ? 'online' : 'offline'}`}
            title={daemonServing ? 'gRPC Daemon Connected (port 50051)' : 'gRPC Daemon Offline on 127.0.0.1:50051'}
          />
          <span className="status-text">
            {daemonServing ? 'Daemon Active' : 'Daemon Standby'}
          </span>
        </div>

        <button
          type="button"
          className="collab-badge-button"
          onClick={onToggleCollabModal}
          title={`Collaboration: ${collabState.mode} (${collabState.roomId})`}
        >
          <span className="collab-badge-label">Collab:</span>
          <span className="collab-badge-val">Standalone Mode</span>
        </button>
      </div>

      <div className="header-right">
        <button
          type="button"
          className={`run-button ${isRunning ? 'running' : ''}`}
          onClick={onRun}
          disabled={isRunning || !isRunnable}
          title={!isRunnable ? `Cannot run: ${selectedLanguage.displayName} is ${selectedLanguage.status}` : 'Execute Code (Ctrl+Enter)'}
        >
          {isRunning ? (
            <span className="btn-content">
              <span className="spinner" />
              <span>Executing...</span>
            </span>
          ) : (
            <span className="btn-content">
              <span>Run Code</span>
            </span>
          )}
        </button>
      </div>
    </header>
  );
};
