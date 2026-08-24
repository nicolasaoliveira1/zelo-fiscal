import { readdirSync } from 'node:fs';
import { join, resolve } from 'node:path';
import { execFileSync } from 'node:child_process';

function listar_arquivos_js(diretorio) {
  return readdirSync(diretorio, { withFileTypes: true }).flatMap((entrada) => {
    const caminho = join(diretorio, entrada.name);
    if (entrada.isDirectory()) return listar_arquivos_js(caminho);
    return entrada.isFile() && entrada.name.endsWith('.js') ? [caminho] : [];
  });
}

const raiz = resolve('app/static/js');
const arquivos = listar_arquivos_js(raiz).sort();

for (const arquivo of arquivos) {
  execFileSync(process.execPath, ['--check', arquivo], { stdio: 'inherit' });
}
