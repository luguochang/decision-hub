import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import { loadEnv } from 'vite'
import { fileURLToPath } from 'node:url'
import { createHash } from 'node:crypto'
import { lstat, readFile, writeFile } from 'node:fs/promises'
import { resolve } from 'node:path'
import type { Plugin } from 'vite'

function currentArtifactInventory(): Plugin {
  let outputDirectory = ''
  return {
    name: 'decision-hub-current-artifact-inventory',
    apply: 'build',
    enforce: 'post',
    configResolved(config) {
      outputDirectory = resolve(config.root, config.build.outDir)
    },
    async writeBundle(_options, bundle) {
      const files: Record<string, string> = {}
      for (const name of Object.keys(bundle).sort()) {
        if (!name || /[\\\x00-\x1f<>:"|?*]/.test(name) || name.split('/').some(part => ['', '.', '..'].includes(part))) {
          throw new Error('product_artifact_emitted_path_invalid')
        }
        files[name] = createHash('sha256').update(await readFile(resolve(outputDirectory, name))).digest('hex')
      }
      if (!files['.vite/manifest.json']) throw new Error('product_artifact_emitted_manifest_missing')
      const directory = await lstat(resolve(outputDirectory, '.vite'))
      if (!directory.isDirectory() || directory.isSymbolicLink()) throw new Error('product_artifact_inventory_linked')
      const path = resolve(outputDirectory, '.vite/product-artifacts.json')
      const prior = await lstat(path).catch(error => {
        if (error.code === 'ENOENT') return null
        throw error
      })
      if (prior && (!prior.isFile() || prior.isSymbolicLink())) throw new Error('product_artifact_inventory_linked')
      await writeFile(path, JSON.stringify({ schema_version: 'product-emitted-artifacts.v1', files }) + '\n', 'utf8')
    },
  }
}

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '')
  const apiProxyTarget = env.VITE_API_PROXY_TARGET?.trim() || 'http://127.0.0.1:8000'

  return {
    plugins: [react(), currentArtifactInventory()],
    resolve: { alias: { '@decision-hub/contracts-ts': fileURLToPath(new URL('../../packages/contracts_ts/src/index.ts', import.meta.url)) } },
    // The managed API serves this directory during local rebuilds. Keep hashed
    // assets needed by already-open tabs until a deliberate offline cleanup.
    build: { emptyOutDir: false, manifest: true },
    server: {
      port: 5173,
      proxy: { '/v1': apiProxyTarget, '/v2': apiProxyTarget, '/health': apiProxyTarget },
    },
  }
})
