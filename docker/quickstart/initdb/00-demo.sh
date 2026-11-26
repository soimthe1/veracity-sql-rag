#!/bin/sh
# Runs once, on an empty Postgres volume: creates the demo databases and a
# read-only login the catalog uses to read them.
set -eu
READER_PW="$(cat /secrets/reader_password)"
psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d postgres <<SQL
CREATE ROLE catalog_reader LOGIN PASSWORD '${READER_PW}';
CREATE DATABASE sales;
CREATE DATABASE hr;
CREATE DATABASE inventory;
SQL
for db in sales hr inventory; do
  psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$db" -f "/demo/$db.sql"
  psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$db" <<SQL
GRANT CONNECT ON DATABASE $db TO catalog_reader;
GRANT USAGE ON SCHEMA public TO catalog_reader;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO catalog_reader;
SQL
done
