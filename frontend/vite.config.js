import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

const apiTarget = process.env.ATLAS_API_TARGET || 'http://127.0.0.1:8000';

// Proxy /api to the FastAPI backend so the browser talks same-origin
// (no CORS, and image tiles aren't canvas-tainted).
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/api': { target: apiTarget, changeOrigin: true },
    },
  },
});
