"""
Prints the handful of values dev.ps1 needs (API host/port, the web
dev-server port, the API base URL the browser should call) as plain
KEY=VALUE lines, one per line, reading them the exact same way the
engine/api processes do — through shared.config.load_config(). This is
the single place those values live (config.yaml's catalog.api / catalog.web
sections); dev.ps1 parses this script's output instead of hardcoding or
re-parsing YAML itself, so there is never a second source of truth for
"what port does the API run on" between the app and its own dev tooling.

Not a test harness — this is a small piece of the shipped dev workflow,
invoked by scripts/dev.ps1 on every run.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from shared.config import load_config  # noqa: E402

cfg = load_config()

print(f"API_HOST={cfg.catalog.api.host}")
print(f"API_PORT={cfg.catalog.api.port}")
print(f"ANSWER_HOST={cfg.catalog.answer.host}")
print(f"ANSWER_PORT={cfg.catalog.answer.port}")
print(f"ANSWER_ENABLED={'true' if cfg.catalog.answer.enabled else 'false'}")
print(f"WEB_DEV_PORT={cfg.catalog.web.dev_port}")
print(f"WEB_API_BASE_URL={cfg.catalog.web.api_base_url}")
print(f"WEB_ANSWER_BASE_URL={cfg.catalog.web.answer_base_url}")
print(f"AUTH_ENABLED={'true' if cfg.catalog.auth.enabled else 'false'}")
print(f"CATALOG_ENABLED={'true' if cfg.catalog.enabled else 'false'}")
print(f"DISCOVERY_INTERVAL_MINUTES={cfg.catalog.discovery.interval_minutes}")
print(f"VALIDATION_INTERVAL_HOURS={cfg.catalog.validation.interval_hours}")
print(f"SOURCES_CONFIGURED={len(cfg.catalog.sources)}")
print(f"LOG_DIR={cfg.service.log_dir}")
print(f"LOG_TO_FILE={'true' if cfg.service.log_to_file else 'false'}")
