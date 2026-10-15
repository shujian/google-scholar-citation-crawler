"""
code/venue_quality.py — Is a venue complete, and is it just a preprint's?

Two of the crawler's paths spend one request per item to read a *better* venue:
the publication detail page (``code/venue_detail.py``) and a citing entry's Cite
dialog (``code/cite_format.py``).  Both are pointless when the string we already
have is already the real, complete venue — and the second of them is pointless
when the author list is already spelled out in full, because those two things are
all it returns.

This module is the **single definition** of both questions:

* :func:`is_complete_venue` — the venue is present, not truncated by Scholar
  (``…`` / ``...``) and not merely a *temporary source* ("arXiv preprint
  arXiv:2401.00001", "bioRxiv", "SSRN", …).  A missing venue is **not** complete:
  "no value" is not the same as "good value".
* :func:`has_full_author_names` — at least one author carries a spelled-out given
  name.  Scholar's list pages write ``<initials> <surname>`` ("H Wang, F Zhang"),
  so the given-name part of an author is every token but the last one.

Both are pure and additionally exposed as the two skip predicates the callers use
(:func:`venue_needs_detail`, :func:`citation_needs_cite_dialog`), so the rule
lives in exactly one place per question.

Honest trade-off (recorded in ``issues.md``): the venue we derive from a Cite
dialog is **not** a superset of the list-page venue — skipping "already complete"
entries gives up the volume/issue/pages that the dialog could add.  That is the
accepted cost of not paying two requests per citing entry for rows that already
have what we came for.
"""

#: Venue strings that name a *temporary* source (a preprint server) rather than
#: the venue the work eventually appeared in.  Case-insensitive substring match on
#: the normalised string, so "arXiv preprint arXiv:2401.00001", "arXiv:2401.00001"
#: and "arxiv.org/abs/2401.00001" are all covered by ``arxiv``.
PREPRINT_VENUE_MARKERS = (
    'arxiv',
    'preprint',
    'biorxiv',
    'medrxiv',
    'ssrn',
    'techrxiv',
    'research square',
    'researchsquare',
    'preprints.org',
)

#: Placeholder venue values that mean "no venue at all".  Missing is **not**
#: complete: those rows are exactly the ones worth one more request.
MISSING_VENUE_VALUES = frozenset([
    '', 'na', 'n/a', 'none', 'null', 'nil', '-', '--', '?', 'unknown', 'n.a.',
])

#: Scholar marks a truncated string with one of these.
TRUNCATION_MARKERS = ('…', '...')

#: A given name of at least this many letters is spelled out; shorter means an
#: initial ("H Wang", "AH Kargaran").
FULL_GIVEN_NAME_MIN_LENGTH = 3

#: Name particles that carry no given name ("J van der Berg").  Without this,
#: ``van`` (3 letters) would be mistaken for a spelled-out given name and the row
#: would be skipped although its author list is still abbreviated.
_NAME_PARTICLES = frozenset([
    'van', 'von', 'der', 'den', 'de', 'del', 'della', 'di', 'da', 'dos', 'das',
    'la', 'le', 'les', 'ter', 'ten', 'bin', 'ibn', 'al', 'el', 'du', 'st',
])


def normalize_venue_text(value):
    """Lower-case and collapse whitespace; '' for None."""
    if value is None:
        return ''
    return ' '.join(str(value).split()).lower()


def is_missing_venue(venue):
    """True when *venue* carries no venue at all (empty or a placeholder)."""
    return normalize_venue_text(venue) in MISSING_VENUE_VALUES


def is_truncated_venue(venue):
    """True when Scholar cut the venue off with ``…`` or ``...``."""
    text = '' if venue is None else str(venue)
    return any(marker in text for marker in TRUNCATION_MARKERS)


def is_preprint_venue(venue):
    """True when *venue* names a preprint/temporary source (or nothing at all)."""
    text = normalize_venue_text(venue)
    if text in MISSING_VENUE_VALUES:
        return True
    return any(marker in text for marker in PREPRINT_VENUE_MARKERS)


def is_complete_venue(venue):
    """True when *venue* is a real, untruncated, non-preprint venue string."""
    if is_missing_venue(venue):
        return False
    if is_truncated_venue(venue):
        return False
    return not is_preprint_venue(venue)


def has_full_author_names(authors):
    """True when at least one author carries a spelled-out given name.

    The list-page shape is ``<initials> <surname>`` per comma-separated author
    ("H Wang, F Zhang"), so everything before the last token of an author is its
    given-name part; a token of ``FULL_GIVEN_NAME_MIN_LENGTH`` letters or more
    (ignoring particles) means it is spelled out.  An author given as a single
    token ("Agarwal") is *no* evidence of a full name — the caller keeps fetching,
    which is the conservative direction.
    """
    for author in str(authors or '').replace(';', ',').split(','):
        tokens = author.split()
        if len(tokens) < 2:
            continue
        for token in tokens[:-1]:
            cleaned = token.strip('.').lower()
            if cleaned in _NAME_PARTICLES:
                continue
            if len(cleaned) >= FULL_GIVEN_NAME_MIN_LENGTH:
                return True
    return False


def venue_needs_detail(venue):
    """True when a publication's *venue* is worth a detail-page request."""
    return not is_complete_venue(venue)


#: 比对"线上 venue"与"已保存 bib 的 venue"时，短的一侧至少要这么长才认作"被截断的前缀"。
#: 太短（`IEEE`、`Proc` 之类）会跟任何东西都前缀相同 → 宁可多抓一次，也不误跳过。
SAVED_BIB_VENUE_MIN_PREFIX = 12


def _fold_venue_for_compare(text):
    """比对用折叠：去掉截断省略号、压空白、casefold。"""
    value = str(text or '')
    for marker in ('…', '...'):
        value = value.split(marker)[0]
    return ' '.join(value.split()).casefold().strip(' .,:;-')


def venue_consistent_with_saved_bib(citation):
    """True = 该条**已经保存过 bib**、且保存的 venue 与**本次线上看到的 venue**一致。

    用户口径（2026-10-15）：crawler 下载 cite / bib 的访问量很大，所以**只要已经下载过 bib，
    就先拿线上 venue 与已保存 bib 里的 venue 比一比** —— 一致就不必再访问 cite 与 bib；
    **不一致才更新 bib**（例如 Scholar 改了会议名/年份、或这篇的发表地变了）。

    比对规则（刻意保守，宁可多抓一次也不误跳过）：
    * 两侧都折叠（去 `…`/`...` 截断符、压空白、casefold、去首尾标点）后相等 → 一致；
    * 一侧是另一侧的前缀（线上串常被 Scholar 截到 ~25 字符，会是完整串的前缀）→ 一致，
      **但短的一侧至少要有 `SAVED_BIB_VENUE_MIN_PREFIX` 个字符**；
    * 线上 venue 缺失/占位、或没有已保存的 bib → **不一致**（按原逻辑继续判定）。
    """
    if not isinstance(citation, dict):
        return False
    if is_missing_venue(citation.get('venue')):
        return False
    try:
        from code.venue_key import cite_dialog_venue
        saved, _field = cite_dialog_venue(citation)
    except Exception:
        saved = None
    if not saved or is_missing_venue(saved):
        return False
    online = _fold_venue_for_compare(citation.get('venue'))
    stored = _fold_venue_for_compare(saved)
    if not online or not stored:
        return False
    if online == stored:
        return True
    shorter, longer = (online, stored) if len(online) <= len(stored) else (stored, online)
    if len(shorter) < SAVED_BIB_VENUE_MIN_PREFIX:
        return False
    return longer.startswith(shorter)


def citation_needs_cite_dialog(citation):
    """True when a citing entry is worth a Cite-dialog request.

    三种"不用抓"的情形，按顺序判：

    1. **已保存的 bib 与本次线上 venue 一致** → 两个请求都省（用户 2026-10-15 口径，
       见 `venue_consistent_with_saved_bib()`：全名与完整 venue 早就在 bib 里了）；
    2. venue 已完整（非空、未截断、不是预印本源）**且** authors 已是全名形态 → 对话框没有
       新东西可给；
    3. 其余才抓（本次新数据、或线上 venue 与保存的 bib 不一致需要更新）。

    判据是**纯函数**（不看缓存、不发请求），所以抓取侧与"这一步还有没有待办"共用同一份判断。
    """
    if not isinstance(citation, dict):
        return False
    if venue_consistent_with_saved_bib(citation):
        return False
    if not is_complete_venue(citation.get('venue')):
        return True
    return not has_full_author_names(citation.get('authors'))
