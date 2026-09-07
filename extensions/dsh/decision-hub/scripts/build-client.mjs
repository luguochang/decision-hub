import { mkdir, readFile, writeFile } from 'node:fs/promises'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..')
const source = await readFile(resolve(root, 'src/client/index.js'), 'utf8')
const id = '@decision-hub/dsh-plugin'
const output = [
  'window.__ModuleLoader__.load({',
  `  id: ${JSON.stringify(id)},`,
  '  factory: (require) => {',
  '    var module = { exports: {} };',
  '    var exports = module.exports;',
  source.split('\n').map(line => line.length === 0 ? '' : `    ${line}`).join('\n'),
  '    return module.exports;',
  '  },',
  '});',
  '',
].join('\n')
await mkdir(resolve(root, 'lib'), { recursive: true })
await writeFile(resolve(root, 'lib/client.js'), output, 'utf8')
