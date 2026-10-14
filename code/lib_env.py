"""Environment staging for third-party libraries — the ONLY module in ``code/``
that touches ``os.environ``.

This project takes **no configuration from environment variables**: every knob
comes from a command-line argument, from ``crawler_config.json`` (see
``code.config``), or from a code default.  Two chores, however, genuinely live at
the process-environment level and cannot be expressed as project configuration:

1. ``configure_third_party_env()`` — selenium / webdriver_manager telemetry
   switches (``SE_AVOID_STATS``, ``WDM_LOG_LEVEL``).  These are *written for the
   libraries*, never read back as our settings.
2. ``sanitize_no_proxy()`` — httpx 0.27.2 cannot build its client while a
   bracketed IPv6 literal (``[::1]``) sits in ``NO_PROXY``, and scholarly builds
   such a client during ``import scholarly``.  The ambient ``NO_PROXY`` must
   therefore be rewritten before that import.

``resolve_proxy_config()`` in ``code.config`` additionally consults the ambient
``https_proxy`` / ``http_proxy`` / ``no_proxy`` values as its **last-resort
default**: that is the universal HTTP ecosystem convention, and ``httpx`` /
``requests`` read those variables on their own anyway.  The read itself is
confined to this module so the rest of ``code/`` stays free of ``os.environ``.

``export_proxy_env()`` is the transport bridge in the other direction: when the
effective proxy comes from ``--proxy-url`` / ``--no-proxy`` or from
``crawler_config.json``, the value is pushed into the process environment so the
``httpx`` clients that scholarly and the crawler build actually honour it —
httpx has no other channel for proxy settings.
"""

import os
import re
from code.shared.lib_env import sanitize_entries

# ---- selenium / webdriver_manager telemetry -------------------------------

_LIBRARY_ENV_DEFAULTS = {
    'SE_AVOID_STATS': 'true',
    'WDM_LOG_LEVEL': '0',
}


def configure_third_party_env():
    """Silence selenium/webdriver_manager telemetry for the libraries' sake.

    This is library plumbing, not project configuration: the values are never
    read back by this project.  ``setdefault`` keeps an operator's explicit
    shell setting intact.
    """
    for key, value in _LIBRARY_ENV_DEFAULTS.items():
        os.environ.setdefault(key, value)


# ---- NO_PROXY normalisation ----------------------------------------------

_NO_PROXY_BRACKET_RE = re.compile(r'^\[([0-9A-Fa-f:.]+)\](?::\d+)?$')

PROXY_ENV_KEYS = ('https_proxy', 'http_proxy')
NO_PROXY_ENV_KEYS = ('no_proxy', 'NO_PROXY')


def sanitize_no_proxy(env=None):
    """Rewrite bracketed IPv6 literals in ``NO_PROXY`` / ``no_proxy``.

    httpx 0.27.2 turns every NO_PROXY entry into a URLPattern; an entry such as
    ``[::1]`` becomes ``all://*[::1]`` and raises
    ``httpx.InvalidURL: Invalid port: ':1]'`` while the client is constructed.
    scholarly builds exactly such a client when its Navigator singleton is
    created, i.e. during ``import scholarly`` — so a bracketed IPv6 entry in the
    shell environment makes the crawler impossible to start at all.

    ``[::1]`` -> ``::1``.  A port suffix is dropped: httpx cannot express
    IPv6+port in NO_PROXY, and bypassing the proxy for every port of that host
    is the harmless direction.

    Call this BEFORE importing scholarly (importing ``code`` does it
    automatically via ``code/__init__.py``).  Returns the number of rewritten
    entries; the operation is idempotent.
    
    迭代 / 拆分 / 幂等 / 回写 那一半逻辑现在只有一份：阶段 1 的
    `code/shared/lib_env.py::sanitize_entries()`（本侧只传自己的「策略」：哪些变量、哪个正则算命中、
    命中后怎么办、返回什么）。
    """
    return sanitize_entries(
        env,
        keys=('NO_PROXY', 'no_proxy'),
        pattern=_NO_PROXY_BRACKET_RE,
        unwrap=True,        # 本侧语义：解括号 `[::1]` -> `::1`
        count=True,         # 返回被改写的条目数
        always_write=True,  # 含 `[` 就回写（历史行为）
    )


# ---- ambient proxy fallback (last resort) ---------------------------------


def system_proxy_env():
    """Return the ambient ``(proxy_url, no_proxy)`` pair, or ``(None, None)``.

    Used as the last-resort default behind ``--proxy-url`` / ``--no-proxy`` and
    ``crawler_config.json``.  This mirrors the HTTP ecosystem convention that
    ``httpx`` and ``requests`` already implement; the project itself never
    requires these variables to be set.
    """
    proxy_url = None
    for key in PROXY_ENV_KEYS:
        value = os.environ.get(key)
        if value:
            proxy_url = value
            break
    no_proxy = None
    for key in NO_PROXY_ENV_KEYS:
        value = os.environ.get(key)
        if value:
            no_proxy = value
            break
    return proxy_url, no_proxy


def export_proxy_env(proxy_url, no_proxy):
    """Push the resolved proxy settings into the process environment.

    ``httpx`` (and therefore scholarly) only understands proxies through the
    environment, so a value that came from ``--proxy-url`` / ``--no-proxy`` or
    from ``crawler_config.json`` has to be staged here to take effect.  Passing
    ``None`` leaves the corresponding variable untouched; an empty string
    clears it (explicit "no proxy").
    """
    if proxy_url is not None:
        for key in PROXY_ENV_KEYS:
            if proxy_url:
                os.environ[key] = proxy_url
            else:
                os.environ.pop(key, None)
    if no_proxy is not None:
        for key in NO_PROXY_ENV_KEYS:
            os.environ[key] = no_proxy
    sanitize_no_proxy()
