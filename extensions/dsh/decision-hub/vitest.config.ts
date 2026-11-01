import { defineConfig } from 'vitest/config'
import { mergeConfig } from 'vite'
import { resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import hostConfig from './vite.host.config.js'

const sourceRoot = process.env.DSH_UPSTREAM_SOURCE_ROOT
  ?? fileURLToPath(new URL('../../../.cache/dsh-upstream/source', import.meta.url))

export default mergeConfig({ resolve: hostConfig.resolve }, defineConfig({
  resolve: {
    alias: [{ find: /^@deepseek-ai\/dsh-llm$/, replacement: resolve(sourceRoot, 'packages/llm/llm/lib/index.js') }],
  },
  test: {
    environment: 'node',
    include: ['tests/*.spec.ts'],
    restoreMocks: true,
  },
}))
