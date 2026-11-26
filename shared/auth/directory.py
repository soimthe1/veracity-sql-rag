"""
Active Directory person lookup for Security -> People -> "Add person".

Binds with a read-only service account (catalog.auth.ldap.search_bind_user,
password from the env var named by search_bind_password_env_var) and runs a
bounded search. Directory search is purely a convenience for finding the
right account name: adding someone still only creates a row in THIS app's
own users table with the roles an admin picks — AD grants nothing.

When no service account is configured, search reports configured=False and
the UI falls back to typing the account name by hand.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Callable, List, Optional

from shared.auth.ad_auth import _parse_server_url, _tls_config, ad_enabled
from shared.config import LdapConfig

logger = logging.getLogger("shared.auth.directory")

_ATTRS = ["sAMAccountName", "displayName", "mail", "department", "title", "userPrincipalName"]


@dataclass
class DirectoryPerson:
    username: str
    display_name: str
    email: Optional[str] = None
    department: Optional[str] = None
    title: Optional[str] = None


def configured(cfg: LdapConfig) -> bool:
    return bool(ad_enabled(cfg) and cfg.search_bind_user and cfg.search_base
                and os.environ.get(cfg.search_bind_password_env_var))


def escape_filter_value(value: str) -> str:
    """RFC 4515 escaping so a search box can never inject filter syntax."""
    out = []
    for ch in value:
        if ch in "\\*()\0":
            out.append("\\%02x" % ord(ch))
        else:
            out.append(ch)
    return "".join(out)


def _first(entry, attr) -> Optional[str]:
    try:
        val = entry[attr].value
    except (KeyError, IndexError, AttributeError):
        return None
    if isinstance(val, list):
        val = val[0] if val else None
    return str(val) if val not in (None, "") else None


def search(cfg: LdapConfig, query: str, connection_factory: Optional[Callable] = None) -> List[DirectoryPerson]:
    q = (query or "").strip()
    if len(q) < 2:
        return []
    from ldap3 import SUBTREE, Connection, Server

    flt = cfg.directory_filter.replace("{q}", escape_filter_value(q))
    if connection_factory is not None:
        conn = connection_factory()
    else:
        host, port, use_ssl = _parse_server_url(cfg.server_url)
        server = Server(host, port=port, use_ssl=use_ssl, tls=_tls_config(cfg, use_ssl), connect_timeout=cfg.connect_timeout_s)
        bind_user = cfg.search_bind_user
        if "@" not in bind_user and "\\" not in bind_user and "=" not in bind_user:
            bind_user = cfg.bind_template.format(username=bind_user, domain=cfg.domain)
        conn = Connection(server, user=bind_user, password=os.environ.get(cfg.search_bind_password_env_var, ""),
                          receive_timeout=cfg.connect_timeout_s)
        if cfg.use_starttls and not use_ssl:
            conn.open()
            conn.start_tls()
    if not conn.bind():
        logger.warning("Directory search: service account bind failed.")
        return []
    try:
        conn.search(cfg.search_base, flt, search_scope=SUBTREE, attributes=_ATTRS, size_limit=cfg.directory_max_results)
        people = []
        for entry in conn.entries:
            username = _first(entry, "sAMAccountName") or (_first(entry, "userPrincipalName") or "").split("@")[0]
            if not username:
                continue
            people.append(DirectoryPerson(
                username=username.lower(), display_name=_first(entry, "displayName") or username,
                email=_first(entry, "mail"), department=_first(entry, "department"), title=_first(entry, "title"),
            ))
        return people[: cfg.directory_max_results]
    finally:
        try:
            conn.unbind()
        except Exception:  # noqa: BLE001
            pass
