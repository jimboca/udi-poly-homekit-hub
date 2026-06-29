"""Local dev edition override via dev_edition.txt (local dev install only)."""

import json
import logging
import os

_PLUGIN_DIR = os.path.dirname(os.path.abspath(__file__))
_DEV_EDITION_FILE = os.path.join(_PLUGIN_DIR, 'dev_edition.txt')
_ALLOWED_EDITIONS = {'Standard', 'Professional', 'Production', 'Test'}
_EDITION_RANK = {'Standard': 0, 'Production': 0, 'Professional': 1, 'Test': 99}


def licensed_edition(poly):
    return poly.pg3init.get('edition', 'Standard')


def edition_at_least(edition: str, minimum: str) -> bool:
    """Two-tier ordering: Standard < Professional (Production maps to Standard)."""
    cur = _normalize_edition(str(edition or 'Standard'))
    need = _normalize_edition(str(minimum or 'Standard'))
    return _EDITION_RANK.get(cur, 0) >= _EDITION_RANK.get(need, 0)


_STORE_URL_SUFFIXES = ('.zip', '.tgz', '.tar.gz')


def _server_json_dev_mode():
    try:
        with open(os.path.join(os.getcwd(), 'server.json'), encoding='utf-8') as f:
            data = json.load(f)
        return bool(data.get('devMode'))
    except (OSError, ValueError, TypeError):
        return False


def is_dev_mode(poly):
    config = poly.getConfig() if hasattr(poly, 'getConfig') else None
    if isinstance(config, dict) and config.get('devMode'):
        return True
    serverdata = getattr(poly, 'serverdata', None) or {}
    if serverdata.get('devMode'):
        return True
    return _server_json_dev_mode()


def _poly_config(poly):
    config = poly.getConfig() if hasattr(poly, 'getConfig') else None
    return config if isinstance(config, dict) else {}


def _install_url(poly):
    return str(_poly_config(poly).get('url', '')).strip()


def _nodeserver_home(poly):
    home = str(_poly_config(poly).get('home', '')).strip()
    if home:
        return home
    return os.getcwd()


def is_local_install(poly):
    """True for local dev nodeservers (devMode + symlink/git install, not store zip)."""
    if not is_dev_mode(poly):
        return False

    url = _install_url(poly).lower()
    if url.endswith(_STORE_URL_SUFFIXES):
        return False

    if _install_url(poly).startswith('lnk:'):
        return True

    for path in (_nodeserver_home(poly), os.getcwd()):
        if path and os.path.islink(path):
            return True

    src_url = _install_url(poly)
    if src_url and ('github.com' in src_url or src_url.startswith('/')):
        return True

    return True


def dev_edition_override_active(poly, effective_edition):
    return is_local_install(poly) and effective_edition != licensed_edition(poly)


def _normalize_edition(value):
    edition = value.strip()
    if edition.lower() == 'production':
        return 'Standard'
    return edition


def _read_dev_edition_file():
    try:
        with open(_DEV_EDITION_FILE, encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith('#'):
                    continue
                return _normalize_edition(line)
    except FileNotFoundError:
        return None
    except OSError:
        return None
    return None


def resolve_edition(poly, logger: logging.Logger):
    edition = licensed_edition(poly)
    if not is_local_install(poly):
        override = _read_dev_edition_file()
        if override is not None and override != edition:
            logger.warning(
                'Ignoring dev_edition.txt on non-local install (licensed %s, file %s)',
                edition,
                override,
            )
        return edition

    override = _read_dev_edition_file()
    if override is None:
        return edition

    if override not in _ALLOWED_EDITIONS:
        logger.warning('Ignoring invalid dev edition in dev_edition.txt: %r', override)
        return edition

    if override != edition:
        logger.warning(
            'Dev edition override: %s -> %s (dev_edition.txt, local install)',
            edition,
            override,
        )
    return override
