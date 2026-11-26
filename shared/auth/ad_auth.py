"""
LDAP/AD simple-bind authentication — adapted from the knowledge
platform's own AD authentication module, reused deliberately rather than
inventing a new mechanism. Behavior kept as close to the original as this
project's own config shape allows:

- Parses an ldap:// / ldaps:// server URL into host/port/use_ssl.
- Supports STARTTLS-upgrade-before-bind as a third encryption option.
- Builds a bind DN from a configurable template (default
  '{username}@{domain}').
- Returns a single boolean, NEVER a failure reason (anti-enumeration —
  a caller must not be able to tell "wrong password" from "no such
  user" from "server unreachable" from this function's return value
  alone; shared/auth/login.py's LoginFailed message is equally generic
  for the same reason).
- Rejects an empty password explicitly (many AD servers "succeed" an
  unauthenticated/anonymous bind when given an empty password, which
  would otherwise let a blank password through as a valid login).
- Logs loudly (but never the password) if binding unencrypted.
"""
from __future__ import annotations

import logging
from typing import Tuple

from shared.config import LdapConfig

logger = logging.getLogger("shared.auth.ad_auth")


def _parse_server_url(url: str) -> Tuple[str, int, bool]:
    """'ldap://host:port' or 'ldaps://host:port' -> (host, port, use_ssl).
    Defaults to 389 (ldap) / 636 (ldaps) when the port is omitted."""
    use_ssl = url.startswith("ldaps://")
    rest = url.split("://", 1)[-1]
    host, _, port_s = rest.partition(":")
    port = int(port_s) if port_s else (636 if use_ssl else 389)
    return host, port, use_ssl


def _tls_config(cfg: LdapConfig, use_ssl: bool):
    """TLS settings for ldaps:// and STARTTLS. catalog.auth.ldap.tls_verify
    (on in production) checks the AD server's certificate and host name,
    against ca_certs_file or the system's trusted CAs."""
    if not use_ssl and not cfg.use_starttls:
        return None
    import ssl

    from ldap3 import Tls

    if not cfg.tls_verify:
        logger.warning("AD server certificate is NOT verified (catalog.auth.ldap.tls_verify: false) — "
                       "turn it on outside development.")
        return Tls(validate=ssl.CERT_NONE)
    return Tls(validate=ssl.CERT_REQUIRED, ca_certs_file=cfg.ca_certs_file or None)


def ad_enabled(cfg: LdapConfig) -> bool:
    return bool(cfg.server_url and cfg.domain)


def authenticate(cfg: LdapConfig, username: str, password: str) -> bool:
    """Simple bind as DOMAIN\\username / username@domain (per
    cfg.bind_template) with the supplied password. Returns True only on
    a successful bind; every other outcome (unreachable server, bad
    credentials, malformed config, missing ldap3 dependency) returns
    False and is logged at warning level — never at the cost of raising
    into the caller, and never logging the password."""
    if not password:
        return False
    if not ad_enabled(cfg):
        logger.warning("AD authentication requested but catalog.auth.ldap.server_url/domain is not configured.")
        return False

    try:
        from ldap3 import Connection, Server
        from ldap3.core.exceptions import LDAPException
    except ImportError:
        logger.error("ldap3 is not installed — cannot authenticate against AD. `pip install ldap3`.")
        return False

    host, port, use_ssl = _parse_server_url(cfg.server_url)
    if not use_ssl and not cfg.use_starttls:
        logger.warning(
            "Binding to AD unencrypted (%s) — set catalog.auth.ldap.server_url to ldaps://, or "
            "catalog.auth.ldap.use_starttls: true, before using this in production.",
            cfg.server_url,
        )

    bind_dn = cfg.bind_template.format(username=username, domain=cfg.domain)

    try:
        server = Server(host, port=port, use_ssl=use_ssl, tls=_tls_config(cfg, use_ssl), connect_timeout=cfg.connect_timeout_s)
        conn = Connection(server, user=bind_dn, password=password, receive_timeout=cfg.connect_timeout_s)
        if cfg.use_starttls and not use_ssl:
            conn.open()
            if not conn.start_tls():
                logger.warning("STARTTLS upgrade failed before bind for %s.", host)
                return False
        if not conn.bind():
            return False
        conn.unbind()
        return True
    except LDAPException as exc:
        logger.warning("LDAP bind failed for %s: %s", host, exc)
        return False
    except Exception as exc:  # noqa: BLE001 - an AD hiccup must never crash the caller
        logger.warning("Unexpected error during AD bind for %s: %s", host, exc)
        return False
