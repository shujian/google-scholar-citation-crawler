"""NO_PROXY sanitising -- **the single engine** (host: stage 1, the earliest stage
that needs it).

Consumers: enrich_info (3.a.1) / citation_analysis_info (3.a.2) /
citation_analysis_content (3.b), each through its own ``code/_shared.py``
(sys.path injection, mechanism A).  All four repositories used to carry a copy and
they had **drifted apart**:

===========================  ==============================  ======================
side                         what it does with ``[::1]``     returns
===========================  ==============================  ======================
stage 1 (crawler)            **unwraps** it to ``::1``       int (entries rewritten)
3.a.1 / 3.a.2 / 3.b          **drops** the whole entry       bool (something changed)
===========================  ==============================  ======================

Dropping a bracketed literal loses the "do not proxy localhost" intent, so neither
behaviour is objectively better, and **both sides pin theirs with tests**
(``test_proxy_config.py`` / ``test_scholar_patch.py`` assert the unwrapping and the
count; the three 3.x ``test_lib_env.py`` files assert the drop and the bool).
Moving the *policy* is therefore not allowed under "move it, do not change its
semantics" -- but the **engine** can be shared, so that is what this module is:

* the engine owns the part that is identical everywhere -- ``os.environ`` default,
  iterating the spellings, splitting/trimming entries, skipping variables with no
  ``[`` at all, joining, idempotence;
* each caller passes its own **policy**: which keys, which pattern counts as a
  bracketed entry, what to do with a hit (``unwrap`` vs drop), what to return
  (``count`` vs bool) and whether a variable that contained ``[`` is rewritten even
  when nothing matched (``always_write``, which is what stage 1 used to do).

Why the module has to exist at all: ``httpx`` 0.27.2 parses ``NO_PROXY`` /
``no_proxy`` while an ``httpx.Client`` is being constructed, turning each entry into
a URL pattern; a bracketed IPv6 literal such as ``[::1]`` becomes ``all://*[::1]``
and raises ``httpx.InvalidURL: Invalid port: ':1]'``.  ``anthropic.Anthropic(...)``
and ``scholarly``'s Navigator both build exactly such a client, so nothing starts
until the entry is dealt with.  The ambient proxy **must stay in force**
(``https_proxy`` is how these machines reach the network), so ``trust_env=False``
is not an option.

This module writes to the process environment on purpose and never reads a project
setting back out of it: it stays the allowlisted exception inside each consumer's
``code/`` (see their ``tests/test_env_free.py`` / ``test_config_and_env_free.py``).
"""

import os

#: Both spellings: the shell here exports upper- and lower-case alike, and httpx
#: honours either, so a fix applied to only one of them changes nothing.
NO_PROXY_ENV_KEYS = ("NO_PROXY", "no_proxy")


def sanitize_entries(env=None, *, keys, pattern, unwrap=False, count=False, always_write=False):
    """Rewrite ``NO_PROXY`` / ``no_proxy`` entries matching ``pattern``.

    :param env: mapping to rewrite, defaults to ``os.environ`` (dicts are accepted
        so tests stay hermetic).
    :param keys: the variable names to look at, in order.
    :param pattern: compiled regex; a matching entry is a "hit".  When ``unwrap``
        is true its first group replaces the entry.
    :param unwrap: keep ``pattern``'s first group instead of dropping the entry.
    :param count: return the number of hit entries (``False`` -> return a bool).
    :param always_write: rewrite the variable even when it contained ``[`` but
        nothing matched (this is what stage 1 used to do).
    :return: number of rewritten entries, or ``True``/``False``.
    """
    environ = os.environ if env is None else env
    hits = 0
    for name in keys:
        raw = environ.get(name)
        if not raw or "[" not in raw:
            continue
        parts = []
        matched = 0
        for token in raw.split(","):
            entry = token.strip()
            match = pattern.match(entry)
            if match:
                matched += 1
                hits += 1
                if unwrap:
                    parts.append(match.group(1))
                continue
            parts.append(entry)
        if matched or always_write:
            environ[name] = ",".join(parts)
    return hits if count else bool(hits)


__all__ = ["NO_PROXY_ENV_KEYS", "sanitize_entries"]
