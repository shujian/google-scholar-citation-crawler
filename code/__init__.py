"""Google Scholar citation crawler package.

Importing any submodule first normalises ``NO_PROXY`` (see
``code.lib_env.sanitize_no_proxy``).  This must happen before ``scholarly`` is
imported: scholarly builds an ``httpx.Client`` while its Navigator singleton is
created, and httpx 0.27.2 raises ``InvalidURL`` on a bracketed IPv6 literal such
as ``[::1]`` in NO_PROXY.
"""

from code.lib_env import sanitize_no_proxy as _sanitize_no_proxy

_sanitize_no_proxy()
