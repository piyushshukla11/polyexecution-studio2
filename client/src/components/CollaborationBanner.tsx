import React from 'react';
import { CollaborationState } from '../types/collaboration';

interface CollaborationBannerProps {
  state: CollaborationState;
  isOpen: boolean;
  onClose: () => void;
}

export const CollaborationBanner: React.FC<CollaborationBannerProps> = ({
  state,
  isOpen,
  onClose,
}) => {
  if (!isOpen) return null;

  return (
    <div className="collab-modal-overlay" onClick={onClose}>
      <div className="collab-modal" onClick={(e) => e.stopPropagation()}>
        <div className="collab-modal-header">
          <h3>Collaborative Editing Architecture</h3>
          <button type="button" className="close-btn" onClick={onClose}>
            ×
          </button>
        </div>

        <div className="collab-modal-body">
          <div className="collab-status-section">
            <div className="status-row">
              <span className="field-name">Active Mode:</span>
              <span className="field-value badge-neutral">Standalone Editor Mode</span>
            </div>
            <div className="status-row">
              <span className="field-name">Workspace Room:</span>
              <span className="field-value code-font">{state.roomId}</span>
            </div>
            <div className="status-row">
              <span className="field-name">Connected Operators:</span>
              <span className="field-value">1 (Local Operator)</span>
            </div>
          </div>

          <div className="architecture-notice">
            <h4>Backend Capability Notice</h4>
            <p>{state.missingCapabilityNotice}</p>
            <div className="spec-details">
              <strong>Required Infrastructure for Multi-User CRDT:</strong>
              <ul>
                <li>Persistent WebSocket Gateway Server (e.g. Yjs / y-websocket or Automerge daemon)</li>
                <li>Document room discovery and authorization provider</li>
                <li>Presence and awareness protocol distributing operator cursor offsets</li>
              </ul>
            </div>
            <p className="no-simulation-note">
              <strong>Honesty Doctrine:</strong> PolyExecution Studio 2 strictly avoids synthetic collaborator cursors, simulated typing bots, or fake avatar lists. When the backend CRDT gateway is added in future extensions, real multi-user synchronization will activate seamlessly.
            </p>
          </div>
        </div>

        <div className="collab-modal-footer">
          <button type="button" className="btn-primary" onClick={onClose}>
            Dismiss
          </button>
        </div>
      </div>
    </div>
  );
};
