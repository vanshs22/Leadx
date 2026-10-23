import fs from 'node:fs';
import path from 'node:path';
import process from 'node:process';
import { createRequire } from 'node:module';
import { execFileSync } from 'node:child_process';

const root = process.cwd();
const front = path.join(root, 'lead-crm-portal');

function loadTypeScript() {
  const candidates = [];
  // Prefer the frontend's declared devDependency when node_modules is present.
  try {
    const req = createRequire(path.join(front, 'package.json'));
    candidates.push(req.resolve('typescript'));
  } catch {}
  // Fall back to the active npm global root. This works across Node 20/22/24
  // and avoids hard-coding a stale /opt/nvm/<version> path.
  try {
    const globalRoot = execFileSync('npm', ['root', '-g'], { encoding: 'utf8' }).trim();
    if (globalRoot) candidates.push(path.join(globalRoot, 'typescript', 'lib', 'typescript.js'));
  } catch {}
  // Last-resort resolution for common managed Node images.
  if (process.env.NODE_PATH) {
    for (const dir of process.env.NODE_PATH.split(path.delimiter).filter(Boolean)) {
      candidates.push(path.join(dir, 'typescript', 'lib', 'typescript.js'));
    }
  }
  for (const candidate of candidates) {
    try {
      return createRequire(import.meta.url)(candidate);
    } catch {}
  }
  throw new Error(
    'TypeScript compiler is unavailable. Run `cd lead-crm-portal && npm ci` or install TypeScript in the active Node environment.'
  );
}

const ts = loadTypeScript();
const files = [];
function walk(dir) {
  for (const ent of fs.readdirSync(dir, { withFileTypes: true })) {
    if (['node_modules', '.next'].includes(ent.name)) continue;
    const p = path.join(dir, ent.name);
    if (ent.isDirectory()) walk(p);
    else if (/\.(ts|tsx)$/.test(ent.name) && !ent.name.endsWith('.d.ts')) files.push(p);
  }
}
walk(front);

const errors = [];
for (const file of files) {
  const source = fs.readFileSync(file, 'utf8');
  const out = ts.transpileModule(source, {
    fileName: file,
    reportDiagnostics: true,
    compilerOptions: {
      target: ts.ScriptTarget.ES2017,
      module: ts.ModuleKind.ESNext,
      jsx: ts.JsxEmit.Preserve,
      isolatedModules: true,
      moduleResolution: ts.ModuleResolutionKind.Bundler,
    },
  });
  for (const d of out.diagnostics || []) {
    if (d.category === ts.DiagnosticCategory.Error) {
      errors.push(`${path.relative(front, file)}: ${ts.flattenDiagnosticMessageText(d.messageText, ' ')}`);
    }
  }
}

console.log(`frontend_files=${files.length}`);
if (errors.length) {
  console.error(`syntax_errors=${errors.length}`);
  for (const e of errors.slice(0, 50)) console.error(e);
  process.exit(1);
}
console.log('frontend_syntax=PASS');
