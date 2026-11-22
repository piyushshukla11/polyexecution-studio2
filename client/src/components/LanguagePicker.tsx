import React, { useState, useMemo } from 'react';
import { LanguageDefinition } from '../types/language';

interface LanguagePickerProps {
  languages: LanguageDefinition[];
  selectedLanguage: LanguageDefinition;
  onSelectLanguage: (lang: LanguageDefinition) => void;
}

export const LanguagePicker: React.FC<LanguagePickerProps> = ({
  languages,
  selectedLanguage,
  onSelectLanguage,
}) => {
  const [isOpen, setIsOpen] = useState(false);
  const [search, setSearch] = useState('');
  const [familyFilter, setFamilyFilter] = useState<string>('ALL');

  const families = useMemo(() => {
    const set = new Set<string>();
    languages.forEach((l) => set.add(l.family));
    return ['ALL', ...Array.from(set).sort()];
  }, [languages]);

  const filteredLanguages = useMemo(() => {
    return languages.filter((l) => {
      const matchesSearch =
        l.displayName.toLowerCase().includes(search.toLowerCase()) ||
        l.id.toLowerCase().includes(search.toLowerCase()) ||
        l.monacoLanguage.toLowerCase().includes(search.toLowerCase());
      const matchesFamily = familyFilter === 'ALL' || l.family === familyFilter;
      return matchesSearch && matchesFamily;
    });
  }, [languages, search, familyFilter]);

  const getStatusBadgeClass = (status: string) => {
    switch (status) {
      case 'supported':
        return 'badge-supported';
      case 'in_development':
        return 'badge-in-development';
      case 'uninstalled':
      default:
        return 'badge-uninstalled';
    }
  };

  const getStatusLabel = (status: string) => {
    switch (status) {
      case 'supported':
        return 'Supported';
      case 'in_development':
        return 'In Dev';
      case 'uninstalled':
      default:
        return 'Uninstalled';
    }
  };

  return (
    <div className="language-picker-container">
      <button
        type="button"
        className="language-selector-btn"
        onClick={() => setIsOpen(!isOpen)}
        aria-expanded={isOpen}
      >
        <span className="lang-label">Language:</span>
        <span className="lang-name">{selectedLanguage.displayName}</span>
        <span className={`status-badge ${getStatusBadgeClass(selectedLanguage.status)}`}>
          {getStatusLabel(selectedLanguage.status)}
        </span>
        <span className="dropdown-arrow">{isOpen ? '▲' : '▼'}</span>
      </button>

      {isOpen && (
        <div className="language-dropdown-menu">
          <div className="dropdown-header">
            <input
              type="text"
              className="lang-search-input"
              placeholder="Search 28 languages..."
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              autoFocus
            />

            <div className="family-filter-chips">
              {families.map((fam) => (
                <button
                  key={fam}
                  type="button"
                  className={`family-chip ${familyFilter === fam ? 'active' : ''}`}
                  onClick={() => setFamilyFilter(fam)}
                >
                  {fam}
                </button>
              ))}
            </div>
          </div>

          <div className="language-list">
            {filteredLanguages.map((lang) => (
              <div
                key={lang.id}
                className={`language-item ${lang.id === selectedLanguage.id ? 'selected' : ''}`}
                onClick={() => {
                  onSelectLanguage(lang);
                  setIsOpen(false);
                }}
              >
                <div className="lang-item-top">
                  <span className="lang-item-name">{lang.displayName}</span>
                  <span className={`status-badge ${getStatusBadgeClass(lang.status)}`}>
                    {getStatusLabel(lang.status)}
                  </span>
                </div>
                <div className="lang-item-details">
                  <span className="lang-item-family">{lang.family}</span>
                  <span className="lang-item-version">{lang.officialVersion}</span>
                </div>
                <div className="lang-item-status-details">
                  {lang.statusDetails}
                </div>
              </div>
            ))}
            {filteredLanguages.length === 0 && (
              <div className="no-languages-found">
                No environments matched "{search}"
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
};
