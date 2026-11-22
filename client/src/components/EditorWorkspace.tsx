import React from 'react';
import Editor, { OnMount } from '@monaco-editor/react';
import { LanguageDefinition } from '../types/language';

interface EditorWorkspaceProps {
  language: LanguageDefinition;
  code: string;
  onChange: (value: string) => void;
  onRun: () => void;
}

export const EditorWorkspace: React.FC<EditorWorkspaceProps> = ({
  language,
  code,
  onChange,
  onRun,
}) => {
  const handleEditorDidMount: OnMount = (editor, monaco) => {
    // Add Ctrl+Enter or Cmd+Enter keybinding to execute
    editor.addCommand(monaco.KeyMod.CtrlCmd | monaco.KeyCode.Enter, () => {
      onRun();
    });
  };

  return (
    <div className="editor-workspace">
      <div className="editor-tab-bar">
        <div className="file-tab active">
          <span className="file-name">{language.defaultFilename}</span>
          <span className="syntax-badge">{language.monacoLanguage}</span>
        </div>
        <div className="editor-shortcuts-hint">
          <kbd>Ctrl</kbd> + <kbd>Enter</kbd> to execute
        </div>
      </div>

      <div className="monaco-wrapper">
        <Editor
          height="100%"
          language={language.monacoLanguage}
          value={code}
          theme="vs-dark"
          onChange={(val) => onChange(val || '')}
          onMount={handleEditorDidMount}
          options={{
            fontSize: 13,
            lineHeight: 20,
            fontFamily: '"Fira Code", "JetBrains Mono", Consolas, "Courier New", monospace',
            fontLigatures: true,
            minimap: { enabled: false },
            scrollBeyondLastLine: false,
            automaticLayout: true,
            tabSize: 4,
            insertSpaces: true,
            renderWhitespace: 'selection',
            bracketPairColorization: { enabled: true },
          }}
        />
      </div>
    </div>
  );
};
