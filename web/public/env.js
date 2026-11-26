// Runtime configuration for the built/containerized app. In dev this file
// is served as-is (API_BASE_URL left empty, so api.ts falls back to the
// VITE_API_BASE_URL build-time value from vite.config.ts/.env). In the
// Docker image, docker/docker-entrypoint-web.sh regenerates this exact
// file from the container's API_BASE_URL env var on every start — so the
// same built image can point at a different API URL per environment
// without a rebuild, matching this project's config/env-over-rebuild
// philosophy.
window.__ENV__ = {
  API_BASE_URL: "",
  ANSWER_BASE_URL: "",
};
