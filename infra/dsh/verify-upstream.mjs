#!/usr/bin/env node
import { readFile } from 'node:fs/promises'
import { resolve } from 'node:path'
import process from 'node:process'

const repoRoot = resolve(import.meta.dirname, '../..')
const lock = JSON.parse(await readFile(resolve(repoRoot, 'infra/dsh/upstream.lock.json'), 'utf8'))
const sourceRoot = resolve(process.argv[2] ?? resolve(repoRoot, '.cache/dsh-upstream/source'))

async function readJson(path) {
  return JSON.parse(await readFile(resolve(sourceRoot, path), 'utf8'))
}

async function readText(path) {
  return readFile(resolve(sourceRoot, path), 'utf8')
}

function requireEqual(label, actual, expected) {
  if (actual !== expected) throw new Error(`${label}: expected ${expected}, got ${actual}`)
}

function requireMatch(label, text, expression) {
  if (!expression.test(text)) throw new Error(`${label}: public seam not found (${expression})`)
}

const rootPackage = await readJson('package.json')
const webPackage = await readJson('packages/bundle/web-app/package.json')
const sessionPackage = await readJson('packages/api/session-controller/package.json')
const webServerPackage = await readJson('packages/host/webserver/package.json')
const clientModulesPackage = await readJson('packages/client/modules/package.json')

requireEqual('root version', rootPackage.version, lock.source_version)
requireEqual('root license', rootPackage.license, lock.license)
requireEqual('root package manager', rootPackage.packageManager, `pnpm@${lock.pnpm}`)
requireEqual('root Node engine', rootPackage.engines?.node, lock.node)

for (const pkg of [webPackage, sessionPackage, webServerPackage, clientModulesPackage]) {
  requireEqual(`${pkg.name} version`, pkg.version, lock.packages[pkg.name])
}

const controller = await readText('packages/api/session-controller/src/index.ts')
for (const method of ['create', 'prompt', 'cancel']) {
  requireMatch(`SessionController.${method}`, controller, new RegExp(`@Remote\\(['\"]?${method}['\"]?\\)[\\s\\S]{0,500}\\b${method}\\s*\\(`, 'u'))
}
for (const method of ['follow', 'control']) {
  requireMatch(`SessionController.${method}`, controller, new RegExp(`@Remote\\(\\{\\s*mode:\\s*['\"]stream['\"]\\s*\\}\\)[\\s\\S]{0,500}\\b${method}\\s*\\(`, 'u'))
}
requireMatch('SessionController service declaration', controller, /sessionController:\s*SessionController/u)

const webServer = await readText('packages/host/webserver/src/index.ts')
requireMatch('WebServer service declaration', webServer, /webServer:\s*WebServer/u)
requireMatch('WebServer route registration', webServer, /\bregister\s*\(route:\s*WebRoute\)/u)

const webPatch = await readText('packages/bundle/web-app/cordis.patch.yml')
requireMatch('dsh.bundle host row', webPatch, /name:\s*["']?@deepseek-ai\/dsh-web-app/u)
requireMatch('dsh.client modules row', webPatch, /name:\s*["']?@deepseek-ai\/dsh-client-modules/u)
requireMatch('Session Controller row', webPatch, /name:\s*["']?@deepseek-ai\/dsh-api-session-controller/u)

console.log(JSON.stringify({
  status: 'verified',
  source: sourceRoot,
  commit: lock.commit,
  version: lock.source_version,
  seams: ['session.create', 'session.prompt', 'session.cancel', 'session.follow', 'session.control', 'webServer.route', 'dsh.bundle', 'dsh.client'],
}, null, 2))
