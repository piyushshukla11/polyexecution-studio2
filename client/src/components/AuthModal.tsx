import React, { useState } from 'react';
import { getStoredApiKey, setStoredApiKey } from '../services/api';

interface AuthModalProps {
  isOpen: boolean;
  onClose: () => void;
  onKeyChange?: (hasKey: boolean) => void;
}

export const AuthModal: React.FC<AuthModalProps> = ({
  isOpen,
  onClose,
  onKeyChange,
}) => {
  const [apiKeyInput, setApiKeyInput] = useState<string>(() => getStoredApiKey());
  const [savedFeedback, setSavedFeedback] = useState<string>('');

  if (!isOpen) return null;

  const handleSave = () => {
    const trimmed = apiKeyInput.trim();
    setStoredApiKey(trimmed);
    setSavedFeedback(trimmed ? 'API key saved.' : 'API key cleared.');
    if (onKeyChange) {
      onKeyChange(Boolean(trimmed));
    }
    setTimeout(() => {
      setSavedFeedback('');
      onClose();
    }, 600);
  };

  const handleClear = () => {
    setApiKeyInput('');
    setStoredApiKey('');
    setSavedFeedback('API key cleared.');
    if (onKeyChange) {
      onKeyChange(false);
    }
    setTimeout(() => {
      setSavedFeedback('');
      onClose();
    }, 600);
  };

  return (
    <div className="collab-modal-overlay" onClick={onClose}>
      <div className="collab-modal" onClick={(e) => e.stopPropagation()}>
        <div className="collab-modal-header">
          <h3>Gateway Authentication</h3>
          <button type="button" className="close-btn" onClick={onClose}>
            ×
          </button>
        </div>

        <div className="collab-modal-body">
          <div className="collab-status-section">
            <div className="status-row">
              <span className="field-name">Current Status:</span>
              <span className="field-value">
                {getStoredApiKey() ? (
                  <span className="badge-neutral" style={{ color: '#10b981' }}>API Key Configured</span>
                ) : (
                  <span className="badge-neutral" style={{ color: '#94a3b8' }}>No API Key Configured</span>
                )}
              </span>
            </div>
          </div>

          <div style={{ marginTop: '16px', marginBottom: '16px' }}>
            <label
              htmlFor="pes2-api-key-input"
              style={{ display: 'block', fontSize: '13px', color: '#cbd5e1', marginBottom: '6px' }}
            >
              Secret API Key (PES2_API_KEY):
            </label>
            <input
              id="pes2-api-key-input"
              type="password"
              className="lang-search-input"
              style={{ width: '100%', boxSizing: 'border-box' }}
              placeholder="Enter your PES2_API_KEY for gateway authentication"
              value={apiKeyInput}
              onChange={(e) => setApiKeyInput(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter') handleSave();
              }}
            />
            <p style={{ fontSize: '12px', color: '#94a3b8', marginTop: '6px', lineHeight: '1.4' }}>
              Required when the API Gateway runs with authentication enforced (e.g., remote ingress, PES2_REQUIRE_AUTH=1, or PES2_API_KEY configured). Passed as <code>Authorization: Bearer &lt;key&gt;</code> and <code>X-API-Key</code>.
            </p>
            {savedFeedback && (
              <p style={{ fontSize: '12px', color: '#38bdf8', marginTop: '4px' }}>
                {savedFeedback}
              </p>
            )}
          </div>
        </div>

        <div className="collab-modal-footer" style={{ display: 'flex', justifyContent: 'space-between' }}>
          <button
            type="button"
            className="collab-badge-button"
            style={{ padding: '6px 12px' }}
            onClick={handleClear}
          >
            Clear Key
          </button>
          <div style={{ display: 'flex', gap: '8px' }}>
            <button
              type="button"
              className="collab-badge-button"
              style={{ padding: '6px 12px' }}
              onClick={onClose}
            >
              Cancel
            </button>
            <button
              type="button"
              className="btn-primary"
              style={{ padding: '6px 16px' }}
              onClick={handleSave}
            >
              Save Key
            </button>
          </div>
        </div>
      </div>
    </div>
  );
};
