export type RuntimeFamily =
  | 'NATIVE'
  | 'JVM'
  | 'INTERPRETED'
  | 'DOTNET'
  | 'DATABASE'
  | 'FRAMEWORK';

export type RuntimeSupportStatus = 'supported' | 'in_development' | 'uninstalled';

export interface LanguageDefinition {
  id: string;
  displayName: string;
  monacoLanguage: string;
  family: RuntimeFamily;
  officialVersion: string;
  installedVersion: string;
  status: RuntimeSupportStatus;
  statusDetails: string;
  defaultFilename: string;
}
