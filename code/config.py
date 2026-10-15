"""Centralised configuration for the Google Scholar citation crawler.

All tunable parameters — rate-limiting, thresholds, paths — live here so future
maintainers have a single place to discover them.

Constants that previously lived in ``code.common`` are imported here and
re-exported from ``common`` for backward compatibility.  New code should import
directly from ``code.config``.

Runtime configuration (as opposed to the constants below) is resolved with the
priority **command-line argument > JSON config file > code default**:

* ``crawler_config.json`` in the project root (``DEFAULT_CONFIG_PATH``) holds the
  machine-local, gitignored knobs; ``--config PATH`` points elsewhere.
* The project never requires environment variables.  The only environment
  consult is the ambient ``https_proxy`` / ``http_proxy`` / ``no_proxy`` pair
  used as the **last-resort** default for the proxy, because that is the
  universal HTTP convention that ``httpx`` / ``requests`` already honour; the
  read lives in ``code.lib_env.system_proxy_env`` and is documented there.
"""

import json
import os
from dataclasses import dataclass

# ============================================================
# Directory paths
# ============================================================

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUTPUT_DIR = os.path.join(ROOT_DIR, "output")
LOGS_DIR = os.path.join(OUTPUT_DIR, "logs")

# ============================================================
# Runtime config file
# ============================================================

#: File name looked up in the project root when ``--config`` is not given.
CONFIG_FILENAME = "crawler_config.json"
DEFAULT_CONFIG_PATH = os.path.join(ROOT_DIR, CONFIG_FILENAME)

# ============================================================
# Rate-limiting & anti-ban
# ============================================================

# Randomised delay between successive page requests (seconds).
DELAY_MIN = 45
DELAY_MAX = 90

# Pre-request delay for the **detail-style paths**, as a fraction of the normal
# delay: the publication detail page (`view_op=view_citation`), a citing entry's
# Cite dialog (`output=cite`) and its signed BibTeX download (`scholar.bib` /
# `output=citation`).  Those are one-shot, non-paginated pages, so 45-90 s each is
# mostly dead time; 1/10 puts them at ~4.5-9 s per request.
#
# This is the *single* definition of that pacing: `code/scholarly_session.py`
# applies it inside the one place that sleeps, and no other module may define a
# delay of its own (guarded by tests/test_scholar_patch.py).  `--accelerate`
# still stacks on top (the two factors multiply); citation-list pagination keeps
# the full delay and its mandatory 3-6 min breaks.
#
# Honest trade-off: Scholar rate-limits these paths by volume, so a shorter delay
# reaches the block sooner in wall-clock terms (the 2026-10-14 venue pass was
# blocked after ~75-88 pages, see issues.md).  The cache + resume path is what
# makes that acceptable: stop on the first blocked page, come back later, and the
# total wall-clock spent waiting is lower.
DETAIL_PAGE_DELAY_FACTOR = 0.1

# Soft session refresh every N pages to avoid session-based blocking.
SESSION_REFRESH_MIN = 10
SESSION_REFRESH_MAX = 20

# Mandatory long break after every N pages to let Scholar's rate-limit
# window reset.
MANDATORY_BREAK_EVERY_MIN = 8
MANDATORY_BREAK_EVERY_MAX = 12
MANDATORY_BREAK_MIN = 180   # 3 minutes
MANDATORY_BREAK_MAX = 360   # 6 minutes

# ============================================================
# Fetch thresholds
# ============================================================

# Papers with >= this many total citations switch to year-by-year fetch
# mode (better resume granularity).
YEAR_BASED_THRESHOLD = 50

# Google Scholar returns 10 results per page.
SCHOLAR_PAGE_SIZE = 10

# Maximum retries when Scholar blocks a citation fetch.
MAX_RETRIES = 3

# A paper whose fetch keeps producing the same outcome (same seen/target, e.g.
# Scholar lists fewer rows than its own "Cited by" count) stops being retried
# once this many consecutive identical outcomes have been recorded.  The stall
# clears itself as soon as the outcome changes — a larger Scholar count changes
# the signature — so a stalled paper is picked up again when Scholar's numbers
# move.
STALL_AFTER_ATTEMPTS = 2

# ============================================================
# API / network
# ============================================================

SCHOLAR_BASE_URL = "https://scholar.google.com"


def papers_root(output_dir, author_id):
    """论文数据树（`papers/`）在本仓库里的**物理位置**。

    2026-10-15 起，整条流水线共用的论文数据树放在阶段 1 的 output 下：
    ``<output_dir>/author_<ID>_papers/``。阶段 2 / 3.a.1 / 3.a.2 / 3.b / 阶段 4 **都不直接
    使用这个路径**，而是经各自的 ``papers`` 符号链接访问（链见根 AGENTS.md「仓库地图」）——
    这里只负责**创建**它（谁拥有物理位置谁负责建），以及在文档/日志里报出真实路径。
    """
    return os.path.join(output_dir, f"author_{author_id}_papers")


def ensure_papers_root(output_dir, author_id):
    """确保论文数据树的物理位置存在；**只建不删**，已存在时原样返回（幂等）。"""
    path = papers_root(output_dir, author_id)
    os.makedirs(path, exist_ok=True)
    return path

#: Resolve every citing entry's Cite dialog (five formats + BibTeX) by default
#: whenever citations are crawled: that dialog is the only place the crawler can
#: read a citing paper's *full* author list (Chicago / BibTeX) and its complete
#: venue (Vancouver), which the citation-list page truncates.  Knobs, in the
#: usual priority order: ``--cite-formats`` / ``--no-cite-formats`` > the
#: ``cite_formats`` key in ``crawler_config.json`` > this default.
CITE_FORMATS = True

# ============================================================
# Output
# ============================================================

OUTPUT_ENCODING = "utf-8"


# ============================================================
# Runtime config file + proxy resolution
# ============================================================

class ConfigError(ValueError):
    """Raised when an explicitly requested config file is unusable."""


def load_config_file(path=None):
    """Read a JSON config file and return it as a dict.

    ``path=None`` means "the project default": a missing
    ``crawler_config.json`` is not an error (the caller falls back to the code
    defaults), and returns ``{}``.  An explicitly passed ``--config`` path that
    is missing or malformed raises ``ConfigError`` instead of being silently
    ignored — a typo must not silently change how a crawl runs.
    """
    target = path or DEFAULT_CONFIG_PATH
    if not os.path.exists(target):
        if path:
            raise ConfigError(f"config file not found: {target}")
        return {}
    try:
        with open(target, "r", encoding=OUTPUT_ENCODING) as handle:
            data = json.load(handle)
    except json.JSONDecodeError as exc:
        raise ConfigError(f"config file is not valid JSON: {target}: {exc}") from exc
    if not isinstance(data, dict):
        raise ConfigError(f"config file must contain a JSON object: {target}")
    return data


@dataclass(frozen=True)
class ProxyConfig:
    """Resolved proxy settings plus where each value came from."""

    proxy_url: object = None
    no_proxy: object = None
    proxy_source: str = "none"        # cli | config | env | none
    no_proxy_source: str = "none"

    def describe(self):
        return (f"proxy={self.proxy_url or 'direct'} ({self.proxy_source}), "
                f"no_proxy={self.no_proxy or '-'} ({self.no_proxy_source})")


def resolve_proxy_config(cli_proxy_url=None, cli_no_proxy=None, config_path=None):
    """Resolve the effective proxy settings.

    Priority per key: command-line argument > config file > ambient environment
    (the last one only as the conventional HTTP default, see
    ``code.lib_env.system_proxy_env``).  ``None`` means "not specified"; an
    empty string is a deliberate value (no proxy / no bypass list).
    """
    from code.lib_env import system_proxy_env

    file_cfg = load_config_file(config_path)
    env_proxy, env_no_proxy = system_proxy_env()

    def _pick(cli_value, file_key, env_value):
        if cli_value is not None:
            return cli_value, "cli"
        if file_cfg.get(file_key) is not None:
            return file_cfg[file_key], "config"
        if env_value:
            return env_value, "env"
        return None, "none"

    proxy_url, proxy_source = _pick(cli_proxy_url, "proxy_url", env_proxy)
    no_proxy, no_proxy_source = _pick(cli_no_proxy, "no_proxy", env_no_proxy)
    return ProxyConfig(proxy_url, no_proxy, proxy_source, no_proxy_source)


def resolve_cite_formats(cli_value=None, config_path=None):
    """Resolve whether the Cite-dialog step runs: CLI > config file > default.

    Returns ``(enabled, source)`` where *source* is ``"cli"`` / ``"config"`` /
    ``"default"``.  ``cli_value=None`` means "not specified on the command line"
    (``--cite-formats`` and ``--no-cite-formats`` both default to None), so the
    config file — and otherwise :data:`CITE_FORMATS` — decides.
    """
    file_cfg = load_config_file(config_path)
    if cli_value is not None:
        return bool(cli_value), "cli"
    if file_cfg.get("cite_formats") is not None:
        return bool(file_cfg["cite_formats"]), "config"
    return CITE_FORMATS, "default"


#: 论文目录维护（阶段 1 负责"创建 / 编号 / 改名 / 归档"）的默认模式，见 code/papers_maintenance.py
RENUMBER_PAPERS = "dry-run"


def resolve_renumber_papers(cli_value=None, config_path=None):
    """Resolve the papers-maintenance mode: CLI > config file > default.

    Returns ``(mode, source)`` with *mode* in ``off`` / ``dry-run`` / ``apply`` and
    *source* in ``cli`` / ``config`` / ``default``.
    """
    mode = cli_value
    source = "cli"
    if mode is None:
        mode = load_config_file(config_path).get("renumber_papers")
        source = "config"
    if mode is None:
        mode, source = RENUMBER_PAPERS, "default"
    mode = str(mode).strip().lower()
    if mode not in ("off", "dry-run", "apply"):
        raise ValueError("renumber_papers 只能是 off / dry-run / apply，收到 %r" % (mode,))
    return mode, source
