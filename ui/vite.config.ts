import { fileURLToPath } from 'node:url'
import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'
import { viteSingleFile } from 'vite-plugin-singlefile'

// One self-contained HTML file: the macOS host loads it with
// loadHTMLString, so there are no file:// module or font fetches to fail.
export default defineConfig({
  base: './',
  plugins: [react(), tailwindcss(), viteSingleFile()],
  build: {
    outDir: fileURLToPath(new URL('../src/mcp_vision/buddy/web', import.meta.url)),
    emptyOutDir: true,
    assetsInlineLimit: 100_000_000,
    cssCodeSplit: false,
    target: 'safari16',
    reportCompressedSize: false,
  },
})
