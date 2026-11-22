import { generateIframeSrcDoc } from '../src/services/browserSandbox';
import * as fs from 'fs';

const languageId = (process.argv[2] as 'react' | 'vanilla_js') || 'react';
let sourceCode = process.argv[3];

if (sourceCode === undefined) {
  try {
    sourceCode = fs.readFileSync(0, 'utf-8');
  } catch {
    sourceCode = '';
  }
}

const html = generateIframeSrcDoc(sourceCode, languageId);
process.stdout.write(html);
