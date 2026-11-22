import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { LanguagePicker } from '../src/components/LanguagePicker';
import { LANGUAGES } from '../src/constants/languages';

describe('LanguagePicker Component', () => {
  it('renders selected language and truthful status badge', () => {
    const python3 = LANGUAGES.find((l) => l.id === 'python3')!;
    const onSelect = vi.fn();

    render(
      <LanguagePicker
        languages={LANGUAGES}
        selectedLanguage={python3}
        onSelectLanguage={onSelect}
      />
    );

    expect(screen.getByText('Python 3')).toBeInTheDocument();
    expect(screen.getByText('Supported')).toBeInTheDocument();
  });

  it('renders uninstalled badge truthfully for absent runtimes', () => {
    const kotlin = LANGUAGES.find((l) => l.id === 'kotlin')!;
    const onSelect = vi.fn();

    render(
      <LanguagePicker
        languages={LANGUAGES}
        selectedLanguage={kotlin}
        onSelectLanguage={onSelect}
      />
    );

    expect(screen.getByText('Kotlin')).toBeInTheDocument();
    expect(screen.getByText('Uninstalled')).toBeInTheDocument();
  });

  it('opens dropdown and allows searching across all 28 environments', () => {
    const python3 = LANGUAGES.find((l) => l.id === 'python3')!;
    const onSelect = vi.fn();

    render(
      <LanguagePicker
        languages={LANGUAGES}
        selectedLanguage={python3}
        onSelectLanguage={onSelect}
      />
    );

    const btn = screen.getByRole('button', { name: /Language:/i });
    fireEvent.click(btn);

    // Search input should appear
    const searchInput = screen.getByPlaceholderText('Search 28 languages...');
    expect(searchInput).toBeInTheDocument();

    // Filter by 'sqlite'
    fireEvent.change(searchInput, { target: { value: 'sqlite' } });
    expect(screen.getByText('SQLite')).toBeInTheDocument();
  });

  it('filters languages by execution family chips', () => {
    const python3 = LANGUAGES.find((l) => l.id === 'python3')!;
    const onSelect = vi.fn();

    render(
      <LanguagePicker
        languages={LANGUAGES}
        selectedLanguage={python3}
        onSelectLanguage={onSelect}
      />
    );

    fireEvent.click(screen.getByRole('button', { name: /Language:/i }));

    const dbChip = screen.getByRole('button', { name: 'DATABASE' });
    fireEvent.click(dbChip);

    expect(screen.getByText('SQLite')).toBeInTheDocument();
    expect(screen.getByText('PostgreSQL')).toBeInTheDocument();
  });
});
