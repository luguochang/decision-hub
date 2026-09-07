import { defineConfig } from 'vite'

export default defineConfig({
  build: {
    lib: {
      entry: {
        index: 'src/index.ts',
        'research-tool': 'src/research-tool.ts',
        'synthesis-tool': 'src/synthesis-tool.ts',
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
