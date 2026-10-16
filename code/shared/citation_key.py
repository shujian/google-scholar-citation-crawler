"""引用**身份键**的唯一实体（跨仓库共享，宿主＝阶段 1）。

**为什么宿主在阶段 1**（`KNOWN_ISSUES.md` Y-7 + K144）：`citations_meta.json` 的**主体**
（含 `cites_id`）自 2026-10-16 起由阶段 1 产出（Y-9）—— 而"没有真实 `cites_id` 的条目该用什么键"
正是这份文件里的规则，所以它必须跟着数据产生者走 ✓。此前它只存在于阶段 2 的 `code/utils.py`
（键的**最早**使用者），阶段 2 的 `build_index_map()` / `progress` / 一次性迁移工具都依赖它。

**内容 = 阶段 2 `code/utils.py` 里那一簇逐字搬移**（`normalize_url_for_key` / `url_fingerprint` /
`normalize_title_for_key` / `first_author_surname` / `is_real_cites_id` / `citation_disambiguator` /
`paper_dir_slug` / `_paper_meta_fields` / `paper_identity_key` / `synthetic_cites_id` /
`synthetic_keys_for` / `resolve_cites_id` / `extract_doi` / `extract_arxiv_id`），
**一个字符都没改** ✗ —— 键的每个字节都被写进了 `download_progress.json` 与下游产物，改语义等于
让存量漂移。阶段 2 的 `code/utils.py` 现在是"再导出 + 其余工具函数"的薄壳 ✓。

键的形态（用户 2026-10-13 / 2026-10-15 定的口径，见根 `AGENTS.md`）：
`no_id:<归一化被引标题>|<年份>|<首作者姓>`，同基础键多条再追加
`#doi:…` / `#arxiv:…` / `#url:<sha8>`，全缺才退 `#pos<组内序号>`（并告警）。
**键里不含输入下标、不含源论文段** —— 论文重排 / 引文倒序 / 上游增删都不改键 ✓。

只用标准库 ✓（阶段 1 是宿主，直接 `from code.shared.citation_key import …`；阶段 2 经
`code/_shared.py` 注入后 `from shared.citation_key import …` 消费同一份）。
"""

import hashlib
import json
import os
import re


#: URL 查询串里**与身份无关**的跟踪参数（归一化时丢掉）。刻意不包含
#: `citation_for_view` / `user` / `id` / `doi` 这类真正标识内容的参数。
_TRACKING_QUERY_KEYS = frozenset({
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content", "utm_id",
    "gclid", "dclid", "fbclid", "msclkid", "yclid", "twclid", "igshid",
    "hl", "lr", "oi", "oe", "ref", "referrer", "source", "src", "spm",
    "pagesize", "scisdf", "_ga", "_gl", "mc_cid", "mc_eid",
})


def normalize_url_for_key(url):
    """URL → 身份用的规范化串：去 scheme、去末尾斜杠、去 fragment、去常见跟踪参数。

    只用于**派生身份键**（不是去访问网络）：域名小写、路径与其余查询参数原样保留
    （路径可能大小写敏感，`10.1145/AbC` 与 `.../abc` 不保证同一篇）。取不到任何内容时返回 ""。
    """
    text = str(url or "").strip()
    if not text:
        return ""
    text = re.sub(r"^[a-zA-Z][a-zA-Z0-9+.\-]*://", "", text)
    text = text.lstrip("/") if text.startswith("//") else text
    text = text.split("#", 1)[0]
    head, _, query = text.partition("?")
    head = head.rstrip("/")
    if query:
        keep = [p for p in query.split("&")
                if p and p.split("=", 1)[0].strip().lower() not in _TRACKING_QUERY_KEYS]
        head = f"{head}?{'&'.join(keep)}" if keep else head
    # 域名部分小写（hostname 大小写不敏感），其余原样
    head = re.sub(r"^([^/?#]+)", lambda m: m.group(1).lower(), head, count=1)
    return head


def url_fingerprint(url, length=8):
    """归一化 URL 的短哈希（sha1 前 *length* 位）；取不到内容时返回 ""。"""
    norm = normalize_url_for_key(url)
    if not norm:
        return ""
    return hashlib.sha1(norm.encode("utf-8")).hexdigest()[:length]


def normalize_title_for_key(text):
    """归一化标题（**Unicode 安全**）：只留字母/数字/CJK 等 word 字符并小写。

    `re.UNICODE` 下 `\\W` 保留中文/日文/韩文等字母，所以 `相似度增强` 不会被清空
    （过去用 `[^a-z0-9]` 会把中文标题清成空串 —— 那种"所有中文标题彼此相等"的坑见
    `repair_citation_filenames` 的注释）。
    """
    return re.sub(r"[\W_]+", "", str(text or "").lower(), flags=re.UNICODE)


def first_author_surname(authors):
    """"AB Recommender, K Wang, L Xu" → "recommender"（**首作者名的最后一个词**，小写）。

    作者串按 `,` / `;` / ` and ` 切分，取第一段、再取该段最后一个词；只保留 word 字符，
    所以 `J. Smith Jr.` → `jr`。作者为空 → 空串（键里就是空字段）。
    """
    text = str(authors or "").strip()
    if not text:
        return ""
    head = re.split(r"[,;]|\band\b", text, maxsplit=1, flags=re.IGNORECASE)[0]
    words = head.split()
    if not words:
        return ""
    return normalize_title_for_key(words[-1])


def is_real_cites_id(cites_id):
    """真实身份（`cites_id`）还是"无真实 id"（空 / `no_id:*` 合成键）。

    与 `citation_downloader._is_real_cites_id()` 同义（那份保持在下载器内，供历史调用点使用）。
    """
    text = str(cites_id or "").strip()
    return bool(text) and not text.startswith("no_id:")


def citation_disambiguator(cit):
    """同一「标题+年+首作者」下的**与位置无关**稳定消歧项：`doi:` / `arxiv:` / `url:<短哈希>`。

    用于区分"同一作品的出版社页 vs arXiv 预印本"这类基础键相同的不同条目。取不到 URL
    或 URL 归一化后为空 → 返回 ""（调用方退到 `#pos<组内序号>` 并告警）。
    """
    url = str((cit or {}).get("url") or "").strip()
    if not url:
        return ""
    doi = extract_doi(url)
    if doi:
        return "doi:" + re.sub(r"\s+", "", doi.lower())
    arxiv_id = extract_arxiv_id(url)
    if arxiv_id:
        return "arxiv:" + arxiv_id.lower()
    fingerprint = url_fingerprint(url)
    return f"url:{fingerprint}" if fingerprint else ""


def paper_dir_slug(paper_dir):
    """`.../papers/117-Some_Title` → `Some_Title`（去数字前缀；取不到返回 ""）。"""
    if not paper_dir:
        return ""
    name = os.path.basename(os.path.normpath(str(paper_dir)))
    if name in (".", "..", os.sep):
        return ""
    return re.sub(r"^\d+[-_]", "", name)


#: `paper_meta.json` 缓存：路径 → (mtime, dict)
_PAPER_META_CACHE = {}


def _paper_meta_fields(paper_dir):
    """读论文目录里的 `paper_meta.json`（缺/坏 → {}），按 mtime 缓存。"""
    if not paper_dir:
        return {}
    path = os.path.join(str(paper_dir), "paper_meta.json")
    try:
        mtime = os.stat(path).st_mtime
    except OSError:
        return {}
    cached = _PAPER_META_CACHE.get(path)
    if cached and cached[0] == mtime:
        return cached[1]
    data = {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            loaded = json.load(f)
        if isinstance(loaded, dict):
            data = loaded
    except (OSError, ValueError):
        data = {}
    _PAPER_META_CACHE[path] = (mtime, data)
    return data


def paper_identity_key(paper_data=None, paper_dir=None, paper_index=None):
    """源论文的**稳定标识**（合成键的第一段）—— **绝不用它在输入里的下标**。

    回退链（用户 2026-10-13 定的口径）：
      ① 该论文在输入里的 URL（`normalize_url_for_key()`：去 scheme/末尾斜杠/跟踪参数）→ `url:…`
      ② 该论文自身的 `cites_id` → `cites:…`
      ③ 归一化论文标题（`normalize_title_for_key()`）→ `title:…`
      ④ 论文目录 slug（`papers/<NNN>-<slug>` 的 slug）→ `slug:…`

    `paper_data` 是输入里的论文对象（或其 `pub` 段）；只在它缺字段时才去读 `paper_dir` 下的
    `paper_meta.json`（那里面存着同一个 `url`/`title`）。连目录都没有的退化情形才用下标，
    只为"不返回空" —— 正常路径永远走不到。
    """
    pub = {}
    if isinstance(paper_data, dict):
        inner = paper_data.get("pub")
        pub = inner if isinstance(inner, dict) else paper_data

    for source in (pub, _paper_meta_fields(paper_dir)):
        norm_url = normalize_url_for_key(source.get("url"))
        if norm_url:
            return "url:" + norm_url
        cites_id = str(source.get("cites_id") or "").strip()
        if cites_id:
            return "cites:" + cites_id
        norm_title = normalize_title_for_key(source.get("title"))
        if norm_title:
            return "title:" + norm_title

    slug = paper_dir_slug(paper_dir)
    if slug:
        return "slug:" + slug
    return f"paper_{paper_index}" if paper_index is not None else "paper_unknown"


def synthetic_cites_id(cit, paper_key=None):
    """合成键的**基础形态**（2026-10-15 用户定：**不带源论文标识**）。

    `no_id:<归一化标题>|<年份>|<首作者姓>`

    **为什么去掉源论文段**：这个键标识的是"**出现了一条引用**"，应当只描述**被引用的那篇作品**
    自己；源论文的信息由**文件位置**承担（引用本来就存在某一篇论文目录的 `citations_meta.json` /
    `download_progress.json` 里 ✓），塞进键里既冗余、又让"同一作品被多篇论文引用"永远无法相同。
    去掉之后：同一作品在不同论文里键相同 ✓ —— 在**按论文存放**的文件里完全安全 ✓；
    全局汇总 `download_summary.json` **只记计数、不记键** ✓，所以也不会撞键 ✓。
    下游四处（3.a.1 / 3.a.2 / 3.b / 阶段 4）**从不解析键的形状** ✓，身份判定取条目自身字段 ✓。

    `paper_key` 参数**保留但不再参与键**（调用方签名不变；也因此不再要求它必须存在 ——
    键只依赖被引作品自己的标题/年份/首作者 ✓）。标题归一化后为空 → 返回 None（算不出身份 ✓）。
    消歧（同标题同年同姓多条）仍由 `synthetic_keys_for()` 追加 `#doi:`/`#arxiv:`/`#url:<sha8>` ✓。
    """
    norm_title = normalize_title_for_key((cit or {}).get("title"))
    if not norm_title:
        return None
    year = str((cit or {}).get("year") or "").strip()
    surname = first_author_surname((cit or {}).get("authors"))
    return f"no_id:{norm_title}|{year}|{surname}"


def synthetic_keys_for(entries, paper_key, on_fallback=None):
    """给一串引用算出**最终**的合成键，返回与 `entries` 等长的列表（有真实 id 或算不出的位置为 None）。

    这是"与列表位置无关"的落点，**下载器与一次性迁移工具共用同一份实现**，保证两边算出的键
    逐字相同：

    * 基础键相同的多条（同标题 + 同作者 + 同年 —— 实测"出版社页 vs arXiv 预印本"就是这种）
      一律追加**与位置无关**的消歧项：DOI → arXiv id → 归一化 URL 短哈希；
    * 消歧项缺失或**彼此相同**（连 URL 都相同/缺失）时才退到 `#pos<组内序号>`（0 起），
      并通过 `on_fallback(i, entry)` 告警 —— 这是最后手段，位置一变键就变。

    只读，绝不修改 `entries`（写回 `_cites_id` 由调用方决定）。
    """
    keys = [None] * len(entries)
    order = []
    for i, entry in enumerate(entries):
        if not isinstance(entry, dict) or is_real_cites_id(entry.get("cites_id")):
            continue
        keys[i] = synthetic_cites_id(entry, paper_key)
        if keys[i]:
            order.append(i)

    groups = {}
    for i in order:
        groups.setdefault(keys[i], []).append(i)
    for base, members in groups.items():
        if len(members) < 2:
            continue
        by_disc = {}
        for i in members:
            disc = citation_disambiguator(entries[i])
            if disc:
                by_disc.setdefault(disc, []).append(i)
        for nth, i in enumerate(members):
            disc = citation_disambiguator(entries[i])
            if disc and len(by_disc.get(disc, [])) == 1:
                keys[i] = f"{base}#{disc}"
            else:
                keys[i] = f"{base}#pos{nth}"
                if on_fallback is not None:
                    on_fallback(i, entries[i])
    return keys


def resolve_cites_id(cit, paper_index=None, paper_key=None):
    """
    Return the best available identifier for a citation.
    Uses cites_id if present; otherwise scopes the title fallback to its paper.

    `paper_key`（见 `paper_identity_key()`）给了就生成**与列表位置无关**的新格式合成键；
    不给则保持历史格式 `no_id:paper_<下标>:<标题>`（兼容老调用点与老数据）。

    `_cites_id` 是 `build_index_map()` → `_disambiguate_cites_ids()` 写回的**最终消歧键**，
    优先返回（否则同标题的重复条目会在 progress 里互相覆盖）。它带着写入时的 `paper_key`，
    只有 `paper_key` 一致（或调用方没给 `paper_key`）时才认，避免"用 A 篇的纸算出的键被当成
    B 篇的键"。
    """
    scoped = cit.get("_cites_id")
    if scoped and (paper_key is None or cit.get("_cites_id_key") == paper_key):
        return scoped
    cites_id = cit.get("cites_id", "")
    if cites_id:
        return cites_id
    title = cit.get("title", "")
    if not title:
        return None
    if paper_key:
        new_key = synthetic_cites_id(cit, paper_key)
        if new_key:
            return new_key
    scope = f"paper_{paper_index}" if paper_index is not None else "paper_unknown"
    return f"no_id:{scope}:{title}"


def extract_doi(url):
    """Extract DOI from a URL. Returns the DOI string or None."""
    if not url:
        return None
    m = re.search(r'10\.\d{4,}/[^\s?#"<>]+', url)
    if m:
        doi = m.group(0).rstrip('.')
        doi = re.sub(r'[,;:)]+$', '', doi)
        return doi
    return None


def extract_arxiv_id(url):
    """Extract arXiv ID from a URL. Returns the ID string or None."""
    if not url:
        return None
    m = re.search(r'arxiv\.org/(?:abs|pdf)/([\d.]+)', url, re.IGNORECASE)
    return m.group(1) if m else None


# ===================================================================
