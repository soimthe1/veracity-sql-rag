/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_API_BASE_URL?: string;
  readonly VITE_ANSWER_BASE_URL?: string;
  readonly VITE_DEV_PORT?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}

// Set by public/env.js (regenerated at container startup — see
// docker/docker-entrypoint-web.sh), read by src/api.ts. Optional because
// a plain `npm run dev` still works without it (falls back to
// VITE_API_BASE_URL / VITE_ANSWER_BASE_URL).
interface Window {
  __ENV__?: { API_BASE_URL?: string; ANSWER_BASE_URL?: string };
}
