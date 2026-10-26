import { defineConfig } from 'tsup'

export default defineConfig({
  entry: ['src/index.ts'],
  format: ['esm'],
  target: 'node22',
  platform: 'node',
  outDir: 'lib',
  clean: true,
  dts: false,
  sourcemap: false,
  external: [/^@deepseek-ai\//, /^ws$/],
})
