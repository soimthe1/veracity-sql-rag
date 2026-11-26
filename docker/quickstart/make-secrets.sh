#!/bin/sh
# Generates the quick start's secrets once; later starts keep them.
set -eu
cd /secrets
gen() { tr -dc 'A-Za-z0-9' </dev/urandom | head -c "$1"; }
if [ ! -f catalog.env ]; then
  gen 32 > db_password
  gen 32 > reader_password
  {
    echo "CATALOG_DB_PASSWORD=$(cat db_password)"
    echo "SRC_DEMO_PASSWORD=$(cat reader_password)"
    echo "CATALOG_AUTH_JWT_SECRET=$(gen 48)"
    echo "CATALOG_ANSWER_SERVICE_KEY=$(gen 40)"
  } > catalog.env
  chmod 644 db_password reader_password catalog.env
  echo "secrets generated"
else
  echo "secrets kept"
fi
