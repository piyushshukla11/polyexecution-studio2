import React, { useState, useEffect } from 'react';
import { LANGUAGES, LANGUAGE_MAP } from './constants/languages';
import { CODE_TEMPLATES } from './constants/templates';
import { LanguageDefinition } from './types/language';
import { Header } from './components/Header';
import { LanguagePicker } from './components/LanguagePicker';
import { EditorWorkspace } from './components/EditorWorkspace';
import { TelemetryPanel } from './components/TelemetryPanel';
import { OutputTerminal } from './components/OutputTerminal';
import { CollaborationBanner } from './components/CollaborationBanner';
import { AuthModal } from './components/AuthModal';
import { useExecution } from './hooks/useExecution';
import { useCollaboration } from './hooks/useCollaboration';
import { apiClient, getStoredApiKey } from './services/api';

export const App: React.FC = () => {
  const [languages, setLanguages] = useState<LanguageDefinition[]>(LANGUAGES);
  const [selectedLanguage, setSelectedLanguage] = useState<LanguageDefinition>(
    LANGUAGE_MAP.get('python3') || LANGUAGES[0]
  );
  const [code, setCode] = useState<string>(
    CODE_TEMPLATES['python3'] || '// Enter your solution here\n'
  );
  const [daemonServing, setDaemonServing] = useState<boolean>(false);
  const [collabModalOpen, setCollabModalOpen] = useState<boolean>(false);
  const [authModalOpen, setAuthModalOpen] = useState<boolean>(false);
  const [hasApiKey, setHasApiKey] = useState<boolean>(() => Boolean(getStoredApiKey()));
  const [browserRunNonce, setBrowserRunNonce] = useState<number>(0);

  const {
    isRunning,
    milestones,
    result,
    execute,
    startBrowserExecution,
    handleBrowserMilestone,
    handleBrowserResult,
  } = useExecution();
  const { state: collabState } = useCollaboration();

  // Populate dynamic runtime inventory from worker daemon and monitor health
  useEffect(() => {
    let mounted = true;
    const check = async () => {
      const [health, inventory] = await Promise.all([
        apiClient.checkHealth(),
        apiClient.getSupportedRuntimes(),
      ]);
      if (mounted) {
        setDaemonServing(health.isServing);
        if (inventory && inventory.length > 0) {
          setLanguages(inventory);
          setSelectedLanguage((prev) => {
            const match = inventory.find((l) => l.id === prev.id);
            return match || prev;
          });
        }
      }
    };
    check();
    const interval = setInterval(check, 10000);
    return () => {
      mounted = false;
      clearInterval(interval);
    };
  }, []);

  const handleSelectLanguage = (lang: LanguageDefinition) => {
    setSelectedLanguage(lang);
    if (CODE_TEMPLATES[lang.id]) {
      setCode(CODE_TEMPLATES[lang.id]);
    }
  };

  const handleRun = async () => {
    if (isRunning) return;
    const isBrowser = selectedLanguage.id === 'react' || selectedLanguage.id === 'vanilla_js';
    if (isBrowser) {
      startBrowserExecution();
      setBrowserRunNonce((n) => n + 1);
      return;
    }

    const requestId = `req-${Date.now()}`;
    await execute({
      requestId,
      languageId: selectedLanguage.id,
      sourceCode: code,
    });
  };

  return (
    <div className="pes2-app">
      <Header
        selectedLanguage={selectedLanguage}
        isRunning={isRunning}
        onRun={handleRun}
        collabState={collabState}
        onToggleCollabModal={() => setCollabModalOpen(true)}
        daemonServing={daemonServing}
        hasApiKey={hasApiKey}
        onToggleAuthModal={() => setAuthModalOpen(true)}
      />

      <div className="studio-main-layout">
        <div className="studio-editor-column">
          <div className="editor-controls-bar">
            <LanguagePicker
              languages={languages}
              selectedLanguage={selectedLanguage}
              onSelectLanguage={handleSelectLanguage}
            />
          </div>

          <div className="editor-container">
            <EditorWorkspace
              language={selectedLanguage}
              code={code}
              onChange={setCode}
              onRun={handleRun}
            />
          </div>
        </div>

        <div className="studio-telemetry-column">
          <div className="telemetry-section">
            <TelemetryPanel
              result={result}
              isRunning={isRunning}
              milestones={milestones}
            />
          </div>

          <div className="terminal-section">
            <OutputTerminal
              key={browserRunNonce}
              result={result}
              isRunning={isRunning}
              selectedLanguageId={selectedLanguage.id}
              sourceCode={code}
              onMilestone={handleBrowserMilestone}
              onResult={handleBrowserResult}
            />
          </div>
        </div>
      </div>

      <CollaborationBanner
        state={collabState}
        isOpen={collabModalOpen}
        onClose={() => setCollabModalOpen(false)}
      />

      <AuthModal
        isOpen={authModalOpen}
        onClose={() => setAuthModalOpen(false)}
        onKeyChange={(has) => setHasApiKey(has)}
      />
    </div>
  );
};

export default App;
