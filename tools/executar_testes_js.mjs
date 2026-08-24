import { readdirSync } from 'node:fs';
import { join, resolve } from 'node:path';
import { spawnSync } from 'node:child_process';

const diretorio = resolve('tests-js');
const arquivos = readdirSync(diretorio)
  .filter((nome) => nome.endsWith('.test.js'))
  .map((nome) => join(diretorio, nome))
  .sort();

const resultado = spawnSync(process.execPath, ['--test', ...arquivos], { stdio: 'inherit' });
process.exit(resultado.status ?? 1);
