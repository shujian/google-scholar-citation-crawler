"""引用身份判定 —— **唯一实体**（宿主：阶段 2）。

消费方：3.a.1 / 3.a.2 / 3.b / 阶段 4（各自 `code/_shared.py` 注入路径后导入本模块）。
2026-10-14 之前这四个仓库各存一份**逐字节相同**的副本（sha256 `b58b45d4…`），靠
"改一处必须同步四处"的人工约定维持；现在只有这一份 —— **改这里就是改四个消费方的行为**。

判定规则（决策树，与阶段 2 的 `cites_id` / K125 合成键语义配套）：

1. 两侧都有真实 `cites_id` 且相等 → 同一（id 一级定案，其余字段不参与）
2. 否则 Tier1 强标识（DOI / arXiv id）两侧都给得出：相等 → 同一；不等 → 不同
   （**DOI 与同一作品的 arXiv 预印本算不同资源** = K123 的场景）
3. 否则比归一化标题；两侧都提供年份 / 首作者姓时**互相矛盾即判不同**
4. 否则 Tier2 弱 URL 资源键（折叠同一资源的已知不同形状后）相等 → 同一
5. 其余 → 不同（无法判定一律按"不同"，安全侧）

`no_id:*` 只作"没有真实 id"的前缀判断，**绝不解析合成键的形状**（新格式含 `|` 与 `#`，
连键尾的 `#doi:` / `#url:<sha8>` 也不当 URL 凭据）；标题/年份/作者/URL 一律取条目自身字段
（作者优先 `authors_raw`，K47）。URL 归一化与阶段 2 `utils.normalize_url_for_key()` 同口径，
但**自带一份**：本模块要能被没有阶段 2 `code` 包在 `sys.path` 上的进程导入。
"""

import re
import unicodedata

# 引用身份：判定"两个条目是不是同一条引用"
#
# 本段在四个仓库里**逐字节相同**（3.a.1 / 3.a.2 / 3.b / 阶段 4；改一处必须同步四处，
# 见根 AGENTS.md「编号必须与身份一致」）。各文件的 `same_citation()` 只是"取身份段 +
# 调用 `same_citation_identity()`"的薄封装。
#
# 上游阶段 2 的合成键（K125，2026-10-13）已改成**与列表位置无关**：
#   no_id:<源论文稳定标识>|<归一化被引标题>|<年份>|<首作者姓>[#doi:…|#arxiv:…|#url:<sha8>|#pos<N>]
# 旧格式 `no_id:paper_<下标>:<标题>` 仍可能留在历史文件里。因此**绝不解析合成键的形状**
# （`split(':')[-1]` 取标题、假定键里不含 `|` 或 `#` 都会取错）：标题/年份/作者/URL 一律取
# 条目**自身的字段**（`title` / `year` / `authors` / `authors_raw` / `url`）；连键尾的
# `#doi:` / `#arxiv:` / `#url:<sha8>` 消歧后缀也**不当作 URL 凭据**，`no_id:*` 只用来判
# "没有真实 id"。
#
# URL / DOI / arXiv 凭据（K128，2026-10-13）分**两级**，只在"两侧都能给出"时参与：
#   Tier 1 **强标识**（`identity_strong_identifier()`）：DOI 与 arXiv id。两侧都有且不等
#     → 判**不同**（**DOI 与同一作品的 arXiv 预印本 = 不同资源**，这正是 K123 的场景）。
#   Tier 2 **弱 URL 资源键**（`identity_url_resource_key()`）：只在两侧拿不到强标识时比；
#     先把"同一个资源的已知不同形状"折叠成同一个键（IEEE `/abstract/document/` vs
#     `/document/`、OSF `files.osf.io` vs `osf.io/preprints`、Research Square 的
#     `rs-<数字>`、DSpace 的 item 页 vs bitstream 直链、DOI 版本后缀 `/v1`、`.pdf`、
#     `http`/`https`、`www.`、末尾斜杠、fragment、`utm_*` 等跟踪参数），**折叠后仍不同**才判不同。
#   任一级任一侧给不出凭据 → 该级不参与（保守原则；`N/A` / `javascript:void(0)` 这类占位值
#   不算 URL）。两级都**只会判"不同"、绝不会判"同一"** —— 所以加 URL 这一维不可能把原本
#   分开的两条合并，只会把原本被复用的情况改为重算。
# ---------------------------------------------------------------------------


def _identity_get(entry, name) -> str:
    """条目字段读取：dict 或带属性的对象（CitationEntry / Citation 等）；缺失 → ""。"""
    if isinstance(entry, dict):
        value = entry.get(name)
    else:
        value = getattr(entry, name, None)
    return "" if value is None else str(value)


def identity_norm_title(text) -> str:
    """身份用的标题归一化（与阶段 2 `normalize_title_for_key()` 同口径）。

    **Unicode 安全**：只留字母/数字/CJK 等 word 字符并小写。过去用 `[^a-z0-9]` 会把中文/日文
    标题清成空串 —— 那等于"所有中文标题都判不出身份"，只能无谓重算。
    """
    return re.sub(r"[\W_]+", "", str(text or "").lower(), flags=re.UNICODE)


def identity_first_author_surname(authors) -> str:
    """首作者名的**最后一个词**（小写；与阶段 2 `first_author_surname()` 同口径）。

    "AB Recommender, K Wang, L Xu" → "recommender"；空作者串 → ""（= "提供不了"）。
    """
    text = str(authors or "").strip()
    if not text:
        return ""
    head = re.split(r"[,;]|\band\b", text, maxsplit=1, flags=re.IGNORECASE)[0]
    words = head.split()
    if not words:
        return ""
    return identity_norm_title(words[-1])


def identity_year(entry) -> str:
    """条目里的 4 位年份（"2018" / "2018." / "Proceedings of 2018" → "2018"）；取不到 → ""。"""
    m = re.search(r"(?:19|20)\d{2}", _identity_get(entry, "year"))
    return m.group(0) if m else ""


def identity_is_real_cites_id(identifier) -> bool:
    """True = 真实 `cites_id`（空值、`no_id:*` 合成键都不算）；只看前缀、不解析键的内容。"""
    text = str(identifier or "").strip()
    return bool(text) and not text.startswith("no_id:")


#: URL 查询串里**与身份无关**的跟踪参数（与阶段 2 `utils._TRACKING_QUERY_KEYS` 同一张表；
#: 刻意不含 `citation_for_view` / `user` / `id` / `doi` 这类真正标识内容的参数）。
_IDENTITY_TRACKING_QUERY_KEYS = frozenset({
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content", "utm_id",
    "gclid", "dclid", "fbclid", "msclkid", "yclid", "twclid", "igshid",
    "hl", "lr", "oi", "oe", "ref", "referrer", "source", "src", "spm",
    "pagesize", "scisdf", "_ga", "_gl", "mc_cid", "mc_eid",
})

#: 规范化后的 URL 必须形如**有点的 host**（可带端口）才算"可识别"。`N/A`、`javascript:void(0)`
#: 这类占位值不是 URL —— 必须当作"给不出凭据"（保守原则；真实库 180 例）。
_IDENTITY_URL_HOST_RE = re.compile(
    r"^[a-z0-9]([a-z0-9\-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9\-]*[a-z0-9])?)+(:\d+)?$"
)

#: DOI（与阶段 2 `extract_doi()` 同口径）：`10.<4-9 位数字>/…`。
_IDENTITY_DOI_RE = re.compile(r'10\.\d{4,9}/[^\s?#"<>]+')

#: arXiv id：新式 `2311.03191` 与旧式 `cs/0701001` 都认；`v<N>` 版本后缀随后去掉。
_IDENTITY_ARXIV_ID = r"([A-Za-z][A-Za-z\-]*/\d{7}|\d{4}\.\d{4,5})"
_IDENTITY_ARXIV_RES = (
    re.compile(r"arxiv\.org/(?:abs|pdf)/" + _IDENTITY_ARXIV_ID, re.IGNORECASE),
    re.compile(r"arxiv[:\s]+" + _IDENTITY_ARXIV_ID, re.IGNORECASE),
    re.compile(r"10\.48550/arxiv\." + _IDENTITY_ARXIV_ID, re.IGNORECASE),
)

#: 弱 URL 里"不是资源身份"的路径末段（去掉后才是同一个资源）：`/full`、`/abstract`、`/latest`、`/v1`。
_IDENTITY_URL_NOISE_SEGMENTS = frozenset({"full", "abstract", "latest"})


def identity_normalize_url(url) -> str:
    """URL → 身份用的规范化串（与阶段 2 `normalize_url_for_key()` 等价）。

    去 scheme、去 fragment、去末尾斜杠、去常见跟踪参数（`utm_*` / `gclid` / `fbclid` / `hl` /
    `ref` / `pagesize` 等，与阶段 2 同一张表）；域名部分小写，路径与其余查询参数**原样保留**
    （路径可能大小写敏感）。因此 `http`/`https`、末尾斜杠、`?utm_*` 都归一成同一个串。
    **只派生身份键，不访问网络**；取不到内容 → ""。

    与阶段 2 的唯一差异：**额外去掉开头的 `www.`**（`www.acm.org/x` 与 `acm.org/x` 视为同一
    站点）。这个方向只会让更多 URL 判为"同一"，即偏保守，不可能凭空造成"不同"。
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
                if p and p.split("=", 1)[0].strip().lower() not in _IDENTITY_TRACKING_QUERY_KEYS]
        head = f"{head}?{'&'.join(keep)}" if keep else head
    # 域名部分小写（hostname 大小写不敏感），其余原样；再去掉开头的 www.
    head = re.sub(r"^([^/?#]+)", lambda m: m.group(1).lower(), head, count=1)
    return re.sub(r"^www\.", "", head)


def identity_strong_identifier(url) -> str:
    """URL → **Tier 1 强标识**：`arxiv:<id>` 或 `doi:<DOI>`；都没有 → ""。

    优先级：**arXiv**（含 `10.48550/arXiv.<id>` 这种 DOI 形态，它与 `arxiv.org/abs/<id>` 归一成
    同一个）→ **DOI**。归一：小写、去 `doi:` / `https://doi.org/` 前缀（直接在串里搜 `10.` 即
    天然去掉）、去 `v<N>` 版本后缀与 `/full`、`.pdf`、末尾标点。

    **DOI 与同一作品的 arXiv 预印本前缀不同 ⇒ 永远不相等 = 不同资源**（K123 的落点）。
    """
    text = str(url or "").strip()
    if not text:
        return ""
    for pattern in _IDENTITY_ARXIV_RES:
        m = pattern.search(text)
        if m:
            return "arxiv:" + re.sub(r"v\d+$", "", m.group(1), flags=re.IGNORECASE).lower()
    m = _IDENTITY_DOI_RE.search(text)
    if not m:
        return ""
    doi = re.sub(r"[,;:)]+$", "", m.group(0).rstrip("."))
    doi = re.sub(r"/(?:v\d+|full|abstract|latest)$", "", doi, flags=re.IGNORECASE)
    doi = re.sub(r"\.(?:pdf|html?)$", "", doi, flags=re.IGNORECASE)
    return "doi:" + re.sub(r"\s+", "", doi.lower())


def identity_url_resource_key(url) -> str:
    """URL → **Tier 2 弱资源键**（只在两侧都拿不到强标识时才比）；不可识别 → ""。

    作用是把"**同一个资源的已知不同形状**"折叠成同一个键，**折叠后仍不同**才判不同：

      ① **IEEE Xplore**：`/abstract/document/<id>`、`/document/<id>`、`/iel8/<id>` → `ieee:<id>`；
      ② **OSF**：`files.osf.io/v1/resources/<id>[_v1]/…`、`osf.io/preprints/<provider>/<id>`、
         `osf.io/<id>` → `osf:<id>`；
      ③ **Research Square**：任意 `*researchsquare.com` 主机 + URL 里的 `rs-<数字>` →
         `researchsquare:<id>`（`/article/rs-123/latest` 与 `assets-eu…/files/rs-123/…pdf` 同一）；
      ④ **DSpace / 机构仓库**（路径含 `/bitstream(s)/`、`/handle/`、`/items/`、`/server/api/core/`）
         → `repository:<host>`。**刻意只折到"仓库"粒度**：item 页与 bitstream 直链的 uuid 不同
         （实测 `jscholarship…/items/b69d938e…` vs `…/bitstreams/3fdc2fb3…/content`），URL 层面
         拿不到共同 id；
      ⑤ **通用**：`identity_normalize_url()`（去 scheme / `www.` / 末尾斜杠 / fragment / 跟踪参数、
         host 小写）之后，再去掉路径末尾的 `/v<N>`、`/full`、`/abstract`、`/latest` 与 `.pdf`。

    `N/A`、`javascript:void(0)`、空串等"不是 URL"的值一律返回 ""（该维度不参与）。
    """
    text = str(url or "").strip()
    if not text:
        return ""
    norm = identity_normalize_url(text)
    if not norm:
        return ""
    host = norm.split("?", 1)[0].split("/", 1)[0]
    if not _IDENTITY_URL_HOST_RE.match(host):
        return ""
    host_bare = re.sub(r":\d+$", "", host)
    path = norm.split("?", 1)[0][len(host):]
    query = norm.split("?", 1)[1] if "?" in norm else ""
    if not path.startswith("/"):
        path = "/" + path

    # ① IEEE Xplore：三种落地形式都取同一个文献号
    if host_bare == "ieeexplore.ieee.org":
        m = (re.search(r"/(?:abstract/)?document/(\d+)", path)
             or re.search(r"/iel[a-z0-9]*/(\d+)", path))
        if m:
            return "ieee:" + m.group(1)
    # ② OSF：下载直链 / 预印本落地页 / 裸 id 三形状取同一个资源 id
    if host_bare == "osf.io" or host_bare.endswith(".osf.io"):
        m = re.search(r"/resources/([^/]+)", path)
        cand = m.group(1) if m else path.strip("/").rsplit("/", 1)[-1]
        cand = re.sub(r"_v\d+$", "", cand)
        if cand:
            return "osf:" + cand.lower()
    # ③ Research Square：`rs-<数字>` 是资源号，`/article/.../latest` 与 assets 直链同一
    # （与 ② 同口径：**标签对齐**，不用 `"researchsquare.com" in host` —— 那会误配
    #   `notresearchsquare.com` 这类别人的域，K118）
    if host_bare == "researchsquare.com" or host_bare.endswith(".researchsquare.com"):
        m = re.search(r"(?:^|[/_\-])rs[-_]?(\d{4,})", path)
        if m:
            return "researchsquare:" + m.group(1)
    # ④ DSpace / 机构仓库：item 页与 bitstream 直链没有共同 id，只能折到仓库粒度
    if re.search(r"/(?:bitstreams?|handle|items?|server/api/core)(?:/|$)", path):
        return "repository:" + host_bare

    # ⑤ 通用：host + 路径（去掉版本/形状噪声段）
    clean = re.sub(r"\.(?:pdf|html?)$", "", path, flags=re.IGNORECASE)
    parts = [p for p in clean.split("/") if p]
    while parts and (re.fullmatch(r"v\d+", parts[-1], flags=re.IGNORECASE)
                     or parts[-1].lower() in _IDENTITY_URL_NOISE_SEGMENTS):
        parts.pop()
    key = host_bare + "/" + "/".join(parts)
    return key + ("?" + query if query else "")


def citation_identity_of(entry) -> tuple:
    """条目 → 身份 `(cites_id, 归一化标题, 年份, 首作者姓, 原始标题, 强标识, 弱 URL 键)`。

    缺失字段一律 ""。`entry` 可以是 dict，也可以是带属性的对象。作者串两种字段名都认，且
    **优先 `authors_raw`**：按契约（K47）它始终是 `citations_meta.json` 的原始作者串；而
    `authors` 在 3.a.1 的 resume 路径上可能是**补全后的全名**（派生值，不该拿来当身份）。
    阶段 2 的 `citations_meta.json` 与 3.b 用 `authors`，3.a.1 / 3.a.2 落盘的 `citation` 段用
    `authors_raw`。

    URL 凭据只取条目自身的 `url` 字段 —— **绝不去解析合成键里的 `#doi:` / `#url:<sha8>` 后缀**
    （那是阶段 2 的键形状，不是身份字段）。
    """
    url = _identity_get(entry, "url")
    return (
        _identity_get(entry, "cites_id"),
        identity_norm_title(_identity_get(entry, "title")),
        identity_year(entry),
        identity_first_author_surname(
            _identity_get(entry, "authors_raw") or _identity_get(entry, "authors")
        ),
        _identity_get(entry, "title"),
        identity_strong_identifier(url),
        identity_url_resource_key(url),
    )


def same_citation_identity(a, b) -> bool:
    """两个身份条目是否**同一条引用**（四个仓库逐字节相同，见本段头部）。

    规则（对历史文件保守）：
      1. 两侧都是真实 `cites_id` → **必须相等**（不等即不同作品；有真实 id 时与升级前完全一致）；
      2. 否则比**归一化标题**：任一侧标题为空 → 无法判定 → 判为**不同**（安全侧：宁可重算）；
      3. 标题相同，且**两侧都提供了**年份 / 首作者姓时，这些信息**互相矛盾**才判不同 ——
         年份都非空且不同，或首作者姓都非空且不同（K123：上游 Google 认为是不同的两条
         （同标题、不同年份/作者/URL）必须当作两条独立引用处理，任何环节都不按标题合并）；
      4. 任一侧缺年份或首作者姓 → 该维度不参与，只按标题判 —— **保持对历史产物的兼容**，
         不因为信息缺失就把大批旧产物判成不符；
      5. **Tier 1 强标识**（K128）：两侧都有 DOI / arXiv 强标识时，不等即判**不同** ——
         **DOI 与同一作品的 arXiv 预印本算不同资源**（"如果 Google 认为是两条，我们就该认为是
         两条"）。相等时它**直接给出"同一"**（同一资源；不再看 Tier 2，否则 `doi.org/10.x` 与
         出版社落地页会被误判为不同）；
      6. **Tier 2 弱 URL 资源键**（K128，只在两侧拿不到强标识时比）：先把"同一资源的已知不同
         形状"折叠（IEEE `/abstract/document/` vs `/document/`、OSF `files.osf.io` vs
         `osf.io/preprints`、Research Square `rs-<数字>`、DSpace item vs bitstream、`/v1`、`.pdf`、
         `http`/`https`、`www.`、末尾斜杠、fragment、跟踪参数），**折叠后仍不同**才判不同；
         任一侧不可识别（`N/A` 这类占位值）→ 该级不参与。

    两级 URL 凭据都**只会判"不同"、绝不会判"同一"**：加这一维不可能把原本分开的两条合并，
    只会把原本被复用的情况改为重算。
    """
    a_id, a_title, a_year, a_surname, _, a_strong, a_weak = citation_identity_of(a)
    b_id, b_title, b_year, b_surname, _, b_strong, b_weak = citation_identity_of(b)
    if identity_is_real_cites_id(a_id) and identity_is_real_cites_id(b_id):
        return a_id == b_id
    if not a_title or a_title != b_title:
        return False
    if a_year and b_year and a_year != b_year:
        return False
    if a_surname and b_surname and a_surname != b_surname:
        return False
    if a_strong and b_strong:
        return a_strong == b_strong
    if a_weak and b_weak and a_weak != b_weak:
        return False
    return True


__all__ = [
    "identity_norm_title",
    "identity_first_author_surname",
    "identity_year",
    "identity_is_real_cites_id",
    "identity_normalize_url",
    "identity_strong_identifier",
    "identity_url_resource_key",
    "citation_identity_of",
    "same_citation_identity",
]

