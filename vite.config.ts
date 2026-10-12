import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
export default defineConfig({ plugins: [react({exclude: [/node_modules/, /\.fpilot-cache\//]})],
  // Each checkout has its own cache, including acceptance copies sharing node_modules.
  cacheDir: '.fpilot-cache/vite',
  resolve: { dedupe: ['react', 'react-dom'] },
  optimizeDeps: { entries: ['index.html'], include: ['react', 'react-dom/client', '@rjsf/core', '@rjsf/validator-ajv8', '@xyflow/react'] },
  define: { '__FPILOT_CONTROL_URL__': JSON.stringify((globalThis as any).process?.env?.FPILOT_CONTROL_URL || '') },
  server: {
  watch: { ignored: ['**/workspace-data/**', '**/.fpilot-runtime/**', '**/.fpilot-cache/**'] },
  proxy: { '/api': `http://127.0.0.1:${Number((globalThis as any).process?.env?.FPILOT_API_PORT || 8000)}` },
} });
