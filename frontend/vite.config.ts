import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

export default defineConfig({
  plugins: [react()],
  // The browser only talks to the Vite server; /api is forwarded to FastAPI, so no CORS setup is needed.
  server: { proxy: { '/api': 'http://localhost:8000' } },
})
