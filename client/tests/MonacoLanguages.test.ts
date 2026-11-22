import { describe, it, expect } from 'vitest';
import { LANGUAGES, LANGUAGE_MAP } from '../src/constants/languages';
import { CODE_TEMPLATES } from '../src/constants/templates';

describe('Monaco Languages & Starter Code Templates Inventory', () => {
  it('contains exactly 28 environments (27 LeetCode environments + Bash)', () => {
    expect(LANGUAGES.length).toBe(28);
  });

  it('every environment has non-empty displayName, family, and valid Monaco mode', () => {
    const validMonacoLanguages = new Set([
      'cpp',
      'c',
      'java',
      'python',
      'csharp',
      'javascript',
      'typescript',
      'ruby',
      'shell',
      'php',
      'sql',
      'dart',
      'scheme',
      'erlang',
      'elixir',
      'swift',
      'go',
      'rust',
      'kotlin',
      'scala',
    ]);

    for (const lang of LANGUAGES) {
      expect(lang.id).toBeTruthy();
      expect(lang.displayName).toBeTruthy();
      expect(lang.officialVersion).toBeTruthy();
      expect(lang.installedVersion).toBeTruthy();
      expect(validMonacoLanguages.has(lang.monacoLanguage)).toBe(true);
      expect(['supported', 'in_development', 'uninstalled'].includes(lang.status)).toBe(true);
    }
  });

  it('every environment has an idiomatic code template in CODE_TEMPLATES', () => {
    for (const lang of LANGUAGES) {
      const template = CODE_TEMPLATES[lang.id];
      expect(template, `Language ${lang.id} must have a starter code template`).toBeDefined();
      expect(template.length).toBeGreaterThan(10);
    }
  });

  it('verifies truthful statuses for supported vs uninstalled runtimes', () => {
    // Verified supported runtimes
    expect(LANGUAGE_MAP.get('python3')?.status).toBe('supported');
    expect(LANGUAGE_MAP.get('cpp')?.status).toBe('supported');
    expect(LANGUAGE_MAP.get('java')?.status).toBe('supported');
    expect(LANGUAGE_MAP.get('c')?.status).toBe('supported');
    expect(LANGUAGE_MAP.get('csharp')?.status).toBe('supported');
    expect(LANGUAGE_MAP.get('javascript')?.status).toBe('supported');
    expect(LANGUAGE_MAP.get('typescript')?.status).toBe('supported');
    expect(LANGUAGE_MAP.get('ruby')?.status).toBe('supported');
    expect(LANGUAGE_MAP.get('bash')?.status).toBe('supported');
    expect(LANGUAGE_MAP.get('php')?.status).toBe('supported');
    expect(LANGUAGE_MAP.get('sqlite')?.status).toBe('supported');
    expect(LANGUAGE_MAP.get('pandas')?.status).toBe('supported');
    expect(LANGUAGE_MAP.get('react')?.status).toBe('supported');
    expect(LANGUAGE_MAP.get('vanilla_js')?.status).toBe('supported');

    // Verified uninstalled runtimes
    expect(LANGUAGE_MAP.get('python2')?.status).toBe('uninstalled');
    expect(LANGUAGE_MAP.get('go')?.status).toBe('uninstalled');
    expect(LANGUAGE_MAP.get('rust')?.status).toBe('uninstalled');
    expect(LANGUAGE_MAP.get('swift')?.status).toBe('uninstalled');
    expect(LANGUAGE_MAP.get('kotlin')?.status).toBe('uninstalled');
    expect(LANGUAGE_MAP.get('scala')?.status).toBe('uninstalled');
    expect(LANGUAGE_MAP.get('postgresql')?.status).toBe('uninstalled');
    expect(LANGUAGE_MAP.get('mysql')?.status).toBe('uninstalled');
    expect(LANGUAGE_MAP.get('mssql')?.status).toBe('uninstalled');
    expect(LANGUAGE_MAP.get('oracle')?.status).toBe('uninstalled');
  });
});
