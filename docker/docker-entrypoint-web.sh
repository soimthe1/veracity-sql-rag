#!/bin/sh
# Writes env.js before nginx starts, so the SAME built image can point at a
# different catalog API per environment without a rebuild. The two URLs are
# catalog.web.api_base_url / answer_base_url from the mounted config folder:
# config.prod.yaml first when APP_ENV=prod, then config.yaml. Empty means
# same-origin, which works when a reverse proxy in front of the web
# container also routes /v1 and /health to the api container.
set -eu

CONFIG_DIR=/app/config
# First match wins, so list the strongest overlay first (same order as shared/config.py):
# local/config.prod.yaml > local/config.yaml > config.prod.yaml > config.yaml.
FILES="$CONFIG_DIR/local/config.yaml $CONFIG_DIR/config.yaml"
if [ "${APP_ENV:-dev}" = "prod" ]; then
  FILES="$CONFIG_DIR/local/config.prod.yaml $CONFIG_DIR/local/config.yaml $CONFIG_DIR/config.prod.yaml $CONFIG_DIR/config.yaml"
fi

# First `<key>: value` line in the first file that has one; quotes and
# trailing comments and Windows line endings removed. Both keys are unique
# within the config files.
cfg_value() {
  for f in $FILES; do
    [ -f "$f" ] || continue
    v=$(grep -E "^[[:space:]]*$1:" "$f" | head -n 1 | tr -d '\r' | sed -e "s/^[^:]*:[[:space:]]*//" -e "s/[[:space:]]*#.*$//" -e "s/^[\"']//" -e "s/[\"']$//")
    if [ -n "$v" ]; then echo "$v"; return; fi
  done
}

API_BASE_URL="$(cfg_value api_base_url)"
ANSWER_BASE_URL="$(cfg_value answer_base_url)"

cat > /usr/share/nginx/html/env.js <<EOF
window.__ENV__ = {
  API_BASE_URL: "${API_BASE_URL}",
  ANSWER_BASE_URL: "${ANSWER_BASE_URL}"
};
EOF

exec nginx -g "daemon off;"
