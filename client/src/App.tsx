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
import { useExecution } from './hooks/useExecution';
import { useCollaboration } from './hooks/useCollaboration';
import { apiClient } from './services/api';

export const App: React.FC = () => {
  const [selectedLanguage, setSelectedLanguage] = useState<LanguageDefinition>(
    LANGUAGE_MAP.get('python3') || LANGUAGES[0]
  );
  const [code, setCode] = useState<string>(
    CODE_TEMPLATES['python3'] || '// Enter your solution here\n'
  );
  const [daemonServing, setDaemonServing] = useState<boolean>(false);
  const [collabModalOpen, setCollabModalOpen] = useState<boolean>(false);

  const { isRunning, milestones, result, execute } = useExecution();
  const { state: collabState } = useCollaboration();

  // Check daemon connection on mount
  useEffect(() => {
    let mounted = true;
    const check = async () => {
      const health = await apiClient.checkHealth();
      if (mounted) {
        setDaemonServing(health.isServing);
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
      />

      <div className="studio-main-layout">
        <div className="studio-editor-column">
          <div className="editor-controls-bar">
            <LanguagePicker
              languages={LANGUAGES}
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
              result={result}
              isRunning={isRunning}
              selectedLanguageId={selectedLanguage.id}
              sourceCode={code}
            />
          </div>
        </div>
      </div>

      <CollaborationBanner
        state={collabState}
        isOpen={collabModalOpen}
        onClose={() => setCollabModalOpen(false)}
      />
    </div>
  );
};

export default App;
