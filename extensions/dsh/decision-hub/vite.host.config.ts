import { defineConfig } from 'vite'
import { fileURLToPath } from 'node:url'

export default defineConfig({
  resolve: { alias: { '@decision-hub/contracts-ts': fileURLToPath(new URL('../../../packages/contracts_ts/src/index.ts', import.meta.url)) } },
  build: {
    lib: {
      entry: {
        index: 'src/index.ts',
        'research-tool': 'src/research-tool.ts',
        'synthesis-tool': 'src/synthesis-tool.ts',
        'reviewer-tool': 'src/reviewer-tool.ts',
      },
      formats: ['es'],
      fileName: (_format, entryName) => `${entryName}.js`,
    },
    outDir: 'lib',
    emptyOutDir: true,
    target: 'es2022',
    sourcemap: true,
    rollupOptions: {
      external: id => id.startsWith('@deepseek-ai/') || id.startsWith('node:'),
    },
  },
})
