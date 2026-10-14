"""
code/venue_key.py — Canonical venue + year, e.g. ``ACL2026``.

Why this module exists
----------------------
Downstream stages judge whether two records are *the same work* by title / year /
authors / URL.  The venue is the remaining discriminator ("same title, different
venue") and it is written in a dozen shapes by Scholar: the acronym (``ACL 2024``),
the spelled-out proceedings title (``Proceedings of the 62nd Annual Meeting of the
Association for Computational Linguistics``), the journal form, and truncations
(``Proceedings of the 2024 Conference of the North American Chapter of the …``).
``venue_key`` reduces all of that to **canonical name + year** so it can take part
in identity/dedup checks.

Rules (as decided 2026-10-15):

* a *known* **conference** becomes ``<canonical name><year>`` — ``ACL2026``,
  ``EMNLP2025``, ``NeurIPS2024``, ``ICASSP2025``.
* a *known* **journal** is a continuous venue, not a yearly edition, so its key is
  the short name **without** a year — ``TACL``, ``TASLP``,
  ``Nature Machine Intelligence`` (decided 2026-10-15).
* the table started out as the vocabulary stage 3.a.2 already uses
  (``citation_analysis_info/code/config.py``: ``VENUE_PRESTIGE`` +
  ``VENUE_ALIASES``) and was extended on 2026-10-15 with the venues that the real
  data left unrecognised (speech / MT / NLP / ML conferences and journals).  See
  "Relationship to 3.a.2" below.
* **Findings is its own venue**: ``Findings of the Association for Computational
  Linguistics: ACL 2026`` → ``Findings of ACL2026``, which is deliberately
  different from ``ACL2026``.
* a venue we cannot identify keeps its own text: ``venue_key`` = the venue string
  (``venue_key_source`` = ``'raw'``).  A journal without a widely used
  abbreviation keeps its own short name (``Neurocomputing``, ``IEEE Access``).  That is a temporary, reviewable state — the
  data-upgrade tool lists every distinct raw value with counts so the table can be
  extended deliberately instead of guessed at.
* a *missing* venue (empty / ``NA`` / ``N/A`` / ``…``) gets **no** key: "no venue"
  is not a venue, and a key of ``NA`` would merge unrelated records.

Two spelling variants are merged into one key, because a dedup key must not split
the same venue: ``NIPS`` → ``NeurIPS`` and ``ACMMM`` → ``ACM MM`` (stage 3.a.2
still carries both spellings as separate prestige keys).

Year: the first 4-digit year inside the venue string (1990–2035) wins — it is the
conference edition — and the record's own year is the fallback.  A key without a
year (canonical, no year anywhere) is still emitted; identity checks treat it as
weaker evidence.

Relationship to 3.a.2 (recorded as a cross-repo todo in ``issues.md``): the table
below is a deliberate copy of the canonical vocabulary that stage 3.a.2 already
owns.  The workspace convention says shared code has a single entity living in the
earliest stage that needs it, which is this one — so the follow-up is to move the
table here as ``code/shared/venue_key.py`` and have 3.a.2 consume it through its
``code/_shared.py``.  Until that happens, the two tables must be kept in step by
hand; this module's tests pin the canonical names so a drift is visible.
"""

import re

#: canonical name -> (full name, type, extra alias texts)
#:
#: Mirrors ``citation_analysis_info/code/config.py: VENUE_PRESTIGE`` +
#: ``VENUE_ALIASES`` (2026-10-15), with the two synonym merges described above
#: applied: ``NIPS`` folded into ``NeurIPS`` and ``ACMMM`` into ``ACM MM``.
VENUE_CANONICAL = {
    'NeurIPS': ('Neural Information Processing Systems', 'conference',
                ['NIPS', 'Neural Information Processing Systems',
                 'Advances in Neural Information Processing Systems']),
    'ICML': ('International Conference on Machine Learning', 'conference', []),
    'ICLR': ('International Conference on Learning Representations', 'conference', []),
    'ACL': ('Annual Meeting of the Association for Computational Linguistics',
            'conference',
            ['Association for Computational Linguistics',
             'Meeting of the Association for Computational Linguistics',
             'Proceedings of the Association for Computational Linguistics']),
    'EMNLP': ('Conference on Empirical Methods in Natural Language Processing',
              'conference', []),
    'NAACL': ('North American Chapter of the Association for Computational Linguistics',
              'conference',
              ['Conference of the North American Chapter of the Association for '
               'Computational Linguistics']),
    'AAAI': ('AAAI Conference on Artificial Intelligence', 'conference', []),
    'IJCAI': ('International Joint Conference on Artificial Intelligence', 'conference', []),
    'ACM MM': ('ACM International Conference on Multimedia', 'conference',
               ['ACM Multimedia', 'ACMMM',
                'Proceedings of the ACM International Conference on Multimedia']),
    'SIGIR': ('ACM SIGIR Conference on Research and Development in Information Retrieval',
              'conference', []),
    'KDD': ('ACM SIGKDD Conference on Knowledge Discovery and Data Mining',
            'conference',
            ['ACM SIGKDD international conference on knowledge discovery']),
    'WWW': ('ACM Web Conference', 'conference',
            ['The Web Conference', 'International World Wide Web Conference',
             'Proceedings of the ACM Web Conference']),
    'CVPR': ('IEEE/CVF Conference on Computer Vision and Pattern Recognition',
             'conference', ['Conference on Computer Vision and Pattern Recognition']),
    'ICCV': ('IEEE/CVF International Conference on Computer Vision', 'conference',
             ['International Conference on Computer Vision']),
    'ECCV': ('European Conference on Computer Vision', 'conference', []),
    'SIGGRAPH': ('ACM SIGGRAPH', 'conference', []),
    'OSDI': ('USENIX Symposium on Operating Systems Design and Implementation',
             'conference', []),
    'SOSP': ('ACM Symposium on Operating Systems Principles', 'conference', []),
    'STOC': ('ACM Symposium on Theory of Computing', 'conference', []),
    'FOCS': ('IEEE Symposium on Foundations of Computer Science', 'conference', []),
    'ISCA': ('International Symposium on Computer Architecture', 'conference', []),
    'MICRO': ('IEEE/ACM International Symposium on Microarchitecture', 'conference', []),
    'SIGCOMM': ('ACM SIGCOMM', 'conference', []),
    'COLING': ('International Conference on Computational Linguistics', 'conference', []),
    'EACL': ('European Chapter of the Association for Computational Linguistics',
             'conference',
             ['Conference of the European Chapter of the Association for '
              'Computational Linguistics']),
    'AACL': ('Asia-Pacific Chapter of the Association for Computational Linguistics',
             'conference',
             ['Conference of the Asia-Pacific Chapter of the Association for '
              'Computational Linguistics']),
    'CoNLL': ('Conference on Computational Natural Language Learning', 'conference', []),
    'ICDM': ('IEEE International Conference on Data Mining', 'conference', []),
    'CIKM': ('ACM Conference on Information and Knowledge Management', 'conference',
             ['ACM international conference on information & knowledge management']),
    'WSDM': ('ACM Conference on Web Search and Data Mining', 'conference', []),
    'ECIR': ('European Conference on Information Retrieval', 'conference', []),
    'RecSys': ('ACM Conference on Recommender Systems', 'conference', []),
    'UIST': ('ACM Symposium on User Interface Software and Technology', 'conference', []),
    'CHI': ('ACM Conference on Human Factors in Computing Systems', 'conference', []),
    'CSCW': ('ACM Conference on Computer-Supported Cooperative Work', 'conference', []),
    'ASE': ('IEEE/ACM International Conference on Automated Software Engineering',
            'conference', []),
    'ICSE': ('IEEE/ACM International Conference on Software Engineering', 'conference', []),
    'FSE': ('ACM SIGSOFT Symposium on the Foundations of Software Engineering',
            'conference', []),
    'VLDB': ('International Conference on Very Large Data Bases', 'conference', []),
    'PODS': ('ACM Symposium on Principles of Database Systems', 'conference', []),
    'AISTATS': ('International Conference on Artificial Intelligence and Statistics',
                'conference', []),
    'UAI': ('Conference on Uncertainty in Artificial Intelligence', 'conference', []),
    'COLT': ('Conference on Learning Theory', 'conference', []),
    'Findings of ACL': ('Findings of the Association for Computational Linguistics: ACL',
                        'conference', []),
    'Findings of EMNLP': ('Findings of the Association for Computational Linguistics: EMNLP',
                          'conference',
                          ['Findings of the Conference on Empirical Methods in '
                           'Natural Language Processing']),
    'Findings of NAACL': ('Findings of the Association for Computational Linguistics: NAACL',
                          'conference',
                          ['Findings of the North American Chapter of the Association '
                           'for Computational Linguistics']),
    'Findings of EACL': ('Findings of the Association for Computational Linguistics: EACL',
                         'conference',
                         ['Findings of the European Chapter of the Association for '
                          'Computational Linguistics']),
    'Findings of AACL': ('Findings of the Association for Computational Linguistics: AACL',
                         'conference', []),
    'Findings': ('Findings of the Association for Computational Linguistics',
                 'conference', []),
    'TACL': ('Transactions of the Association for Computational Linguistics',
             'journal', []),
    'TPAMI': ('IEEE Transactions on Pattern Analysis and Machine Intelligence',
              'journal', []),
    'TKDE': ('IEEE Transactions on Knowledge and Data Engineering', 'journal', []),
    'TOIS': ('ACM Transactions on Information Systems', 'journal', []),
    'JMLR': ('Journal of Machine Learning Research', 'journal', []),
    'AIJ': ('Artificial Intelligence Journal', 'journal', []),
    'CL': ('Computational Linguistics', 'journal', []),
    'PNAS': ('Proceedings of the National Academy of Sciences', 'journal', []),
    'TIP': ('IEEE Transactions on Image Processing', 'journal', []),
    'TIFS': ('IEEE Transactions on Information Forensics and Security', 'journal', []),
    'Nature': ('Nature', 'journal', []),
    'Science': ('Science', 'journal', []),
    'Cell': ('Cell', 'journal', []),
    'Nature Machine Intelligence': ('Nature Machine Intelligence', 'journal', []),
    'Nature Communications': ('Nature Communications', 'journal', []),
    'Nature Methods': ('Nature Methods', 'journal', []),
    'Nature Medicine': ('Nature Medicine', 'journal', []),
    'Nature Neuroscience': ('Nature Neuroscience', 'journal', []),
    'Nature Genetics': ('Nature Genetics', 'journal', []),
    'Nature Biotechnology': ('Nature Biotechnology', 'journal', []),
    'Nature Immunology': ('Nature Immunology', 'journal', []),
    'Nature Materials': ('Nature Materials', 'journal', []),
    'Nature Nanotechnology': ('Nature Nanotechnology', 'journal', []),
    'Nature Photonics': ('Nature Photonics', 'journal', []),
    'Nature Physics': ('Nature Physics', 'journal', []),
    'Nature Chemistry': ('Nature Chemistry', 'journal', []),
    'Nature Climate Change': ('Nature Climate Change', 'journal', []),
    'Nature Energy': ('Nature Energy', 'journal', []),
    'Nature Human Behaviour': ('Nature Human Behaviour', 'journal', []),
    'Nature Reviews Psychology': ('Nature Reviews Psychology', 'journal', []),

    # -- speech / machine translation / NLP / ML venues (2026-10-15) ---------
    # The user asked to extend this beyond the first seven names ("EACL、ICASSP 等，
    # 同理类推"): conferences keep "acronym + year", journals use their short name.
    'ICASSP': ('IEEE International Conference on Acoustics, Speech and Signal Processing',
               'conference',
               ['International Conference on Acoustics, Speech and Signal Processing']),
    'INTERSPEECH': ('Conference of the International Speech Communication Association',
                    'conference',
                    ['Interspeech', 'International Speech Communication Association']),
    'SLT': ('IEEE Spoken Language Technology Workshop', 'conference', []),
    'ASRU': ('IEEE Automatic Speech Recognition and Understanding Workshop', 'conference', []),
    'Odyssey': ('The Speaker and Language Recognition Workshop', 'conference', []),
    'IJCNN': ('International Joint Conference on Neural Networks', 'conference', []),
    'ICANN': ('International Conference on Artificial Neural Networks', 'conference', []),
    'PAKDD': ('Pacific-Asia Conference on Knowledge Discovery and Data Mining', 'conference', []),
    'ACML': ('Asian Conference on Machine Learning', 'conference', []),
    'LREC': ('International Conference on Language Resources and Evaluation', 'conference',
             ['Language Resources and Evaluation Conference']),
    'CCMT': ('China Conference on Machine Translation', 'conference',
             ['China Conference, CCMT', 'CCMT']),
    'CWMT': ('China Workshop on Machine Translation', 'conference',
             ['China Workshop, CWMT', 'CWMT']),
    'WMT': ('Conference on Machine Translation', 'conference',
            ['Workshop on Statistical Machine Translation']),
    'AMTA': ('Conference of the Association for Machine Translation in the Americas',
             'conference', ['Association for Machine Translation in the Americas']),
    'EAMT': ('Conference of the European Association for Machine Translation', 'conference',
             ['European Association for Machine Translation']),
    'IWSLT': ('International Conference on Spoken Language Translation', 'conference', []),
    'NLPCC': ('CCF International Conference on Natural Language Processing and Chinese Computing',
              'conference', ['Natural Language Processing and Chinese Computing']),
    'CCL': ('Chinese National Conference on Computational Linguistics', 'conference',
            ['China National Conference on Computational Linguistics']),
    'IJCNLP': ('International Joint Conference on Natural Language Processing', 'conference', []),
    'PACLIC': ('Pacific Asia Conference on Language, Information and Computation', 'conference', []),
    'IALP': ('International Conference on Asian Language Processing', 'conference', []),
    'CIPS-SIGHAN': ('CIPS-SIGHAN Joint Conference on Chinese Language Processing', 'conference', []),
    'NLP-NABD': ('International Symposium on Natural Language Processing Based on Naturally '
                 'Annotated Big Data', 'conference', []),
    'LT4HALA': ('Workshop on Language Technologies for Historical and Ancient Languages',
                'conference', []),
    'ALP': ('Workshop on Ancient Language Processing', 'conference', []),
    'ALT': ('Ancient Language Translation Workshop', 'conference', []),
    'PMLR': ('Proceedings of Machine Learning Research', 'conference', []),
    'RANLP': ('Recent Advances in Natural Language Processing', 'conference', []),
    'SIGDIAL': ('Annual Meeting of the Special Interest Group on Discourse and Dialogue',
                'conference', []),
    'INLG': ('International Natural Language Generation Conference', 'conference', []),
    'ICMI': ('ACM International Conference on Multimodal Interaction', 'conference', []),
    'ICME': ('IEEE International Conference on Multimedia and Expo', 'conference', []),
    'ICPR': ('International Conference on Pattern Recognition', 'conference', []),
    'BMVC': ('British Machine Vision Conference', 'conference', []),
    'WACV': ('IEEE/CVF Winter Conference on Applications of Computer Vision', 'conference', []),

    # -- journals: the key is the journal's short name, with no year ----------
    'TASLP': ('IEEE/ACM Transactions on Audio, Speech, and Language Processing', 'journal',
              ['Transactions on Audio, Speech, and Language Processing']),
    'TALLIP': ('ACM Transactions on Asian and Low-Resource Language Information Processing',
               'journal',
               ['Transactions on Asian and Low-Resource Language Information Processing']),
    'TKDD': ('ACM Transactions on Knowledge Discovery from Data', 'journal', []),
    'TNNLS': ('IEEE Transactions on Neural Networks and Learning Systems', 'journal', []),
    'TCYB': ('IEEE Transactions on Cybernetics', 'journal', []),
    'TIST': ('ACM Transactions on Intelligent Systems and Technology', 'journal', []),
    'TMM': ('IEEE Transactions on Multimedia', 'journal', []),
    'CSUR': ('ACM Computing Surveys', 'journal', []),
    'JAIR': ('Journal of Artificial Intelligence Research', 'journal', []),
    'JCST': ('Journal of Computer Science and Technology', 'journal', []),
    'FCS': ('Frontiers of Computer Science', 'journal', []),
    'SCIS': ('SCIENCE CHINA Information Sciences', 'journal',
             ['SCIENTIA SINICA Informationis']),
    'LRE': ('Language Resources and Evaluation', 'journal', []),
    'Neurocomputing': ('Neurocomputing', 'journal', []),
    'Neural Networks': ('Neural Networks', 'journal', []),
    'Neural Computing and Applications': ('Neural Computing and Applications', 'journal', []),
    'Machine Learning': ('Machine Learning', 'journal', []),
    'Pattern Recognition': ('Pattern Recognition', 'journal', []),
    'Patterns': ('Patterns', 'journal', []),
    'Knowledge-Based Systems': ('Knowledge-Based Systems', 'journal', []),
    'Knowledge and Information Systems': ('Knowledge and Information Systems', 'journal', []),
    'Expert Systems with Applications': ('Expert Systems with Applications', 'journal', []),
    'Applied Intelligence': ('Applied Intelligence', 'journal', []),
    'Applied Soft Computing': ('Applied Soft Computing', 'journal', []),
    'Computer Speech & Language': ('Computer Speech & Language', 'journal',
                                   ['Computer Speech and Language']),
    'Information Sciences': ('Information Sciences', 'journal', []),
    'Information Fusion': ('Information Fusion', 'journal', []),
    'Information Processing & Management': ('Information Processing & Management', 'journal', []),
    'IEEE Access': ('IEEE Access', 'journal', []),
    'Applied Sciences': ('Applied Sciences', 'journal', []),
    'Electronics': ('Electronics', 'journal', []),
    'Mathematics': ('Mathematics', 'journal', []),
    'Symmetry': ('Symmetry', 'journal', []),
    'Sensors': ('Sensors', 'journal', []),
    'Information': ('Information', 'journal', []),
    'Scientific Reports': ('Scientific Reports', 'journal', []),
    'PeerJ Computer Science': ('PeerJ Computer Science', 'journal', []),
    'Procedia Computer Science': ('Procedia Computer Science', 'journal', []),
    'The Journal of Supercomputing': ('The Journal of Supercomputing', 'journal',
                                      ['Journal of Supercomputing']),
    'Cognitive Computation': ('Cognitive Computation', 'journal', []),
    'Complex & Intelligent Systems': ('Complex & Intelligent Systems', 'journal', []),
    'Bioinformatics': ('Bioinformatics', 'journal', []),
    'AI Open': ('AI Open', 'journal', []),
    'Nature Computational Science': ('Nature Computational Science', 'journal', []),
    'eLife': ('eLife', 'journal', []),
    'Chinese Journal of Electronics': ('Chinese Journal of Electronics', 'journal', []),
    '中文信息学报': ('中文信息学报', 'journal',
                    ['Journal of Chinese Information Processing']),
    '小型微型计算机系统': ('小型微型计算机系统', 'journal',
                          ['Journal of Chinese Computer Systems']),
    '软件学报': ('软件学报', 'journal', ['Journal of Software']),
    '电子学报': ('电子学报', 'journal', ['Acta Electronica Sinica']),
    '计算机研究与发展': ('计算机研究与发展', 'journal',
                        ['Journal of Computer Research and Development']),
    '计算机科学': ('计算机科学', 'journal', []),
    '模式识别与人工智能': ('模式识别与人工智能', 'journal',
                          ['Pattern Recognition and Artificial Intelligence']),
    '自然言語処理': ('自然言語処理', 'journal',
                    ['Journal of Natural Language Processing']),
}

#: ``venue_key_source`` values.
SOURCE_CANONICAL = 'canonical'
SOURCE_RAW = 'raw'

#: Placeholder venue values: "no venue" gets no key (mirrors
#: ``code.venue_quality.MISSING_VENUE_VALUES``; duplicated here so this module
#: stays dependency-free and usable from a bare data-repair script).
_MISSING_VENUE_VALUES = frozenset([
    '', 'na', 'n/a', 'none', 'null', 'nil', '-', '--', '?', 'unknown', 'n.a.',
])

_VENUE_YEAR_RE = re.compile(r'(?<!\d)((?:19|20)\d{2})(?!\d)')
_YEAR_MIN, _YEAR_MAX = 1990, 2035


def _build_match_table():
    """(match text, canonical name) pairs, longest text first.

    Longest-first is what keeps the venues apart: TACL's full name contains ACL's
    alias, and the Findings full names contain their host conference's, so the
    more specific text must be tried before the shorter one.
    """
    table = []
    for key, (full_name, _kind, aliases) in VENUE_CANONICAL.items():
        for text in [key, full_name] + list(aliases):
            if text:
                table.append((text, key))
    table.sort(key=lambda pair: (-len(pair[0]), pair[0], pair[1]))
    return table


_MATCH_TABLE = _build_match_table()


def venue_match(keyword, text):
    """Word-aware containment test, mirroring stage 3.a.2's matcher.

    Multi-word texts are substring-matched; a short ALL-CAPS acronym needs word
    boundaries (so ``CL`` does not match ``ACL``); other single words must be the
    whole name (so ``Science`` does not match ``Science China``).
    """
    if not keyword or not text:
        return False
    flags = re.IGNORECASE
    if ' ' in keyword:
        return keyword.lower() in text.lower()
    if keyword == keyword.upper() and len(keyword) <= 5:
        return bool(re.search(r'(?:^|\b)' + re.escape(keyword) + r'(?:\b|$)', text, flags))
    return bool(re.search(r'^' + re.escape(keyword) + r'(?:[\s,\.;:\d\(\)\-\[\]]*(?:$|,))',
                          text, flags))


def normalize_venue_text(venue):
    """Collapse whitespace; ``''`` for None."""
    if venue is None:
        return ''
    return ' '.join(str(venue).split())


def is_missing_venue(venue):
    """True when *venue* carries no venue at all (empty or a placeholder)."""
    return normalize_venue_text(venue).lower() in _MISSING_VENUE_VALUES


def canonical_venue(venue):
    """Return the canonical name of a known venue, or None."""
    text = normalize_venue_text(venue)
    if not text:
        return None
    for match_text, key in _MATCH_TABLE:
        if venue_match(match_text, text):
            return key
    return None


def venue_type(venue):
    """``'conference'`` / ``'journal'`` for a known venue, else None."""
    name = canonical_venue(venue)
    if not name:
        return None
    return VENUE_CANONICAL[name][1]


def pick_venue_year(venue, year=None):
    """Return the 4-digit year to use for the key, or None.

    The year inside the venue string wins (it is the conference edition, e.g.
    "Findings of … : ACL 2024, 8411-8423, 2024" → 2024); the record's own year is
    the fallback.
    """
    text = normalize_venue_text(venue)
    for match in _VENUE_YEAR_RE.finditer(text):
        value = match.group(1)
        if _YEAR_MIN <= int(value) <= _YEAR_MAX:
            return value
    fallback = normalize_venue_text(year)
    if _VENUE_YEAR_RE.fullmatch(fallback) and _YEAR_MIN <= int(fallback) <= _YEAR_MAX:
        return fallback
    return None


def resolve_venue_key(venue, year=None):
    """Return ``(venue_key, source)``.

    * known venue → ``('<canonical name><year>', 'canonical')``
    * unknown venue → ``('<venue text>', 'raw')`` so it can be reviewed
    * no venue at all → ``(None, None)``
    """
    if is_missing_venue(venue):
        return None, None
    name = canonical_venue(venue)
    if name:
        if VENUE_CANONICAL[name][1] == 'journal':
            # A journal is a continuous venue, not a yearly edition: its key is
            # the short name alone (TACL, TASLP, Nature Machine Intelligence).
            return name, SOURCE_CANONICAL
        return f"{name}{pick_venue_year(venue, year) or ''}", SOURCE_CANONICAL
    return normalize_venue_text(venue), SOURCE_RAW


def venue_key(venue, year=None):
    """Return only the key (``None`` when the record has no venue)."""
    return resolve_venue_key(venue, year)[0]


def venue_key_source(venue, year=None):
    """Return only the source (``'canonical'`` / ``'raw'`` / ``None``)."""
    return resolve_venue_key(venue, year)[1]


def describe_venue_key(venue, year=None):
    """Return ``{'venue_key': …, 'venue_key_source': …}`` (empty when no venue)."""
    key, source = resolve_venue_key(venue, year)
    if key is None:
        return {}
    return {'venue_key': key, 'venue_key_source': source}
