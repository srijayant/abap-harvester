"""
SAP ABAP Knowledge Deep Scraper
================================
Scrapes full page text + sub-links from:
  - GitHub (repos, READMEs, .abap / .cds source files)
  - SAP Community Blogs (RSS + full post body)
  - SAP Help Portal (documentation pages + sub-links)
  - SAP Developer Tutorials
  - ABAP open-source orgs (SAP, SAP-samples, etc.)

Output: abap_knowledge.jsonl  (one JSON record per line)
        abap_knowledge_summary.md (human-readable summary)
"""

import os, json, time, re, hashlib, logging
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

# ── Logging ────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("abap-scraper")

# ── Config ─────────────────────────────────────────────────────────────────
GH_TOKEN      = os.environ.get("GH_TOKEN", "")
OUTPUT_JSONL  = Path("abap_knowledge.jsonl")
OUTPUT_MD     = Path("abap_knowledge_summary.md")
DELAY         = 1.2          # seconds between HTTP requests (be polite)
GH_DELAY      = 0.5          # seconds between GitHub API calls
MAX_SUBLINKS  = 40           # max sub-links to follow per seed page
MAX_TEXT_LEN  = 12000        # max chars of body text per record
MAX_GH_REPOS  = 15           # repos per search query
MAX_GH_FILES  = 8            # .abap/.cds files to fetch per repo

SEEN_URLS: set[str] = set()
records: list[dict] = []

# ── HTTP session ───────────────────────────────────────────────────────────
session = requests.Session()
session.headers.update({
    "User-Agent": "Mozilla/5.0 (compatible; ABAP-Knowledge-Bot/1.0; +https://github.com/srijayant/abap-harvester)",
    "Accept-Language": "en-US,en;q=0.9",
})

def gh_headers() -> dict:
    h = {"Accept": "application/vnd.github+json",
         "X-GitHub-Api-Version": "2022-11-28"}
    if GH_TOKEN:
        h["Authorization"] = f"Bearer {GH_TOKEN}"
    return h

def get(url: str, timeout: int = 20, delay: float = DELAY) -> requests.Response | None:
    try:
        resp = session.get(url, timeout=timeout)
        resp.raise_for_status()
        time.sleep(delay)
        return resp
    except Exception as e:
        log.warning(f"GET failed {url[:80]}  → {e}")
        time.sleep(delay)
        return None

def url_id(url: str) -> str:
    return hashlib.md5(url.encode()).hexdigest()[:12]

def clean_text(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script","style","nav","footer","header","aside","noscript"]):
        tag.decompose()
    text = soup.get_text(separator="\n")
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    return "\n".join(lines)[:MAX_TEXT_LEN]

def add_record(**kwargs):
    url = kwargs.get("url","")
    if url and url in SEEN_URLS:
        return
    if url:
        SEEN_URLS.add(url)
    rec = {
        "id":         url_id(url) if url else url_id(str(len(records))),
        "scraped_at": datetime.now(timezone.utc).isoformat(),
        **kwargs,
    }
    records.append(rec)
    log.info(f"  ✓ [{rec['source']}] {rec.get('title','')[:70]}")

# ══════════════════════════════════════════════════════════════════════════════
# 1. GITHUB
# ══════════════════════════════════════════════════════════════════════════════

GH_QUERIES = [
    "ABAP language:ABAP",
    "SAP CDS view language:ABAP",
    "RAP ABAP BTP",
    "ABAP OData V4",
    "ABAP clean core",
    "SAP EWM ABAP",
    "ABAP unit test",
    "ABAP function module",
    "ABAP RESTful programming",
    "SAP BTP ABAP Cloud",
    "ABAP RAP behavior definition",
    "ABAP CDS annotation",
    "ABAP ALV report",
    "ABAP smartforms",
    "ABAP BAPI wrapper",
]

SAP_GH_ORGS = ["SAP", "SAP-samples", "SAP-archive"]

def fetch_readme(full_name: str) -> str:
    resp = get(f"https://api.github.com/repos/{full_name}/readme",
               delay=GH_DELAY)
    if not resp:
        return ""
    try:
        import base64
        data = resp.json()
        return base64.b64decode(data["content"]).decode("utf-8", errors="replace")[:MAX_TEXT_LEN]
    except Exception:
        return ""

def fetch_abap_files(full_name: str) -> list[dict]:
    """Fetch actual .abap and .cds source files from the repo."""
    results = []
    for ext in ["abap", "cds"]:
        resp = get(
            f"https://api.github.com/search/code?q=extension:{ext}+repo:{full_name}&per_page={MAX_GH_FILES}",
            delay=GH_DELAY,
        )
        if not resp:
            continue
        try:
            items = resp.json().get("items", [])
        except Exception:
            continue
        for item in items[:MAX_GH_FILES]:
            raw_url = item.get("html_url","").replace(
                "github.com","raw.githubusercontent.com"
            ).replace("/blob/","/")
            content_resp = get(raw_url, delay=GH_DELAY)
            if content_resp:
                results.append({
                    "filename": item.get("name",""),
                    "path":     item.get("path",""),
                    "content":  content_resp.text[:MAX_TEXT_LEN],
                    "raw_url":  raw_url,
                    "html_url": item.get("html_url",""),
                })
    return results

def scrape_github():
    log.info("═══ GitHub ═══")

    # Search queries
    for query in GH_QUERIES:
        log.info(f"  Searching: {query}")
        resp = get(
            f"https://api.github.com/search/repositories?q={requests.utils.quote(query)}&sort=stars&per_page={MAX_GH_REPOS}",
            delay=GH_DELAY,
        )
        if not resp:
            continue

        try:
            items = resp.json().get("items", [])
        except Exception:
            continue

        for repo in items:
            full_name = repo["full_name"]
            readme    = fetch_readme(full_name)
            abap_files = fetch_abap_files(full_name)

            add_record(
                source      = "github",
                type        = "repository",
                title       = full_name,
                url         = repo["html_url"],
                description = repo.get("description") or "",
                stars       = repo.get("stargazers_count", 0),
                language    = repo.get("language") or "",
                topics      = repo.get("topics", []),
                updated     = repo.get("updated_at",""),
                readme      = readme,
                abap_files  = abap_files,
                text        = "\n\n".join(filter(None, [
                    full_name,
                    repo.get("description",""),
                    " ".join(repo.get("topics",[])),
                    readme,
                    *[f["content"] for f in abap_files],
                ])),
            )
        time.sleep(1)

    # SAP org repos
    for org in SAP_GH_ORGS:
        log.info(f"  Org: {org}")
        page = 1
        while page <= 3:
            resp = get(
                f"https://api.github.com/orgs/{org}/repos?type=public&sort=updated&per_page=30&page={page}",
                delay=GH_DELAY,
            )
            if not resp:
                break
            try:
                repos = resp.json()
            except Exception:
                break
            if not repos:
                break

            abap_repos = [r for r in repos
                          if r.get("language") in ("ABAP", None)
                          and any(kw in (r.get("description") or "").upper()
                                  for kw in ["ABAP","CDS","RAP","BTP","SAP"])]

            for repo in abap_repos[:10]:
                full_name  = repo["full_name"]
                readme     = fetch_readme(full_name)
                abap_files = fetch_abap_files(full_name)
                add_record(
                    source      = "github_sap_org",
                    type        = "repository",
                    title       = full_name,
                    url         = repo["html_url"],
                    description = repo.get("description") or "",
                    stars       = repo.get("stargazers_count", 0),
                    language    = repo.get("language") or "",
                    topics      = repo.get("topics", []),
                    readme      = readme,
                    abap_files  = abap_files,
                    text        = "\n\n".join(filter(None, [
                        full_name,
                        repo.get("description",""),
                        readme,
                        *[f["content"] for f in abap_files],
                    ])),
                )
            page += 1
            time.sleep(1)

# ══════════════════════════════════════════════════════════════════════════════
# 2. SAP COMMUNITY BLOGS
# ══════════════════════════════════════════════════════════════════════════════

COMMUNITY_RSS_URLS = [
    "https://community.sap.com/t5/application-development-blog-posts/bg-p/application-developmentblog-board/label-name/abap/rss",
    "https://community.sap.com/t5/technology-blog-posts/bg-p/technology-blog-posts/label-name/abap/rss",
    "https://community.sap.com/t5/enterprise-resource-planning-blog-posts/bg-p/enterprise-resource-planning-blog-posts/label-name/abap/rss",
]

def fetch_full_post(url: str) -> str:
    resp = get(url)
    if not resp:
        return ""
    soup = BeautifulSoup(resp.text, "html.parser")
    # SAP Community article body
    body = (soup.find("div", class_="lia-message-body-content") or
            soup.find("article") or
            soup.find("main"))
    if body:
        for tag in body(["script","style"]):
            tag.decompose()
        return body.get_text(separator="\n").strip()[:MAX_TEXT_LEN]
    return clean_text(resp.text)

def scrape_sap_community():
    log.info("═══ SAP Community ═══")
    import xml.etree.ElementTree as ET

    for rss_url in COMMUNITY_RSS_URLS:
        log.info(f"  RSS: {rss_url[:60]}...")
        resp = get(rss_url)
        if not resp:
            continue
        try:
            root = ET.fromstring(resp.text)
        except Exception as e:
            log.warning(f"  RSS parse error: {e}")
            continue

        ns = {"dc": "http://purl.org/dc/elements/1.1/"}
        items = root.findall(".//item")
        log.info(f"  Found {len(items)} posts")

        for item in items:
            title   = (item.findtext("title") or "").strip()
            link    = (item.findtext("link")  or "").strip()
            pub     = (item.findtext("pubDate") or "").strip()
            desc    = (item.findtext("description") or "").strip()
            desc    = re.sub(r"<[^>]+>", "", desc)

            if not title or not link:
                continue
            if link in SEEN_URLS:
                continue

            # Fetch full post body
            full_text = fetch_full_post(link)

            add_record(
                source      = "sap_community",
                type        = "blog_post",
                title       = title,
                url         = link,
                description = desc[:500],
                pub_date    = pub,
                text        = full_text or f"{title}\n{desc}",
            )

# ══════════════════════════════════════════════════════════════════════════════
# 3. SAP HELP PORTAL  (deep crawl with sub-links)
# ══════════════════════════════════════════════════════════════════════════════

SAP_HELP_SEEDS = [
    "https://help.sap.com/docs/abap-cloud/abap-rap/abap-restful-application-programming-model",
    "https://help.sap.com/docs/abap-cloud/abap-data-models/cds-view-entities",
    "https://help.sap.com/docs/btp/sap-business-technology-platform/abap-environment",
    "https://help.sap.com/doc/abapdocu_latest_index_htm/latest/en-US/index.htm",
    "https://help.sap.com/docs/SAP_NETWEAVER_AS_ABAP_752/68bf513362174d54b58cddec28794093/b5a6485c0a1541e1a556b4cde9ca8555.html",
    "https://help.sap.com/docs/ABAP_PLATFORM_NEW/b5670aaaa2364a29935f40b16499972d/4ec2ff5b6e391014adc9fffe4e204223.html",
    "https://help.sap.com/docs/SAP_EXTENDED_WAREHOUSE_MANAGEMENT",
    "https://help.sap.com/docs/abap-cloud/abap-rap/behavior-definition",
    "https://help.sap.com/docs/abap-cloud/abap-rap/business-events",
    "https://help.sap.com/docs/abap-cloud/abap-rap/late-numbering",
]

def is_sap_help_link(url: str) -> bool:
    parsed = urlparse(url)
    return parsed.netloc in ("help.sap.com",) and len(parsed.path) > 5

def extract_sublinks(html: str, base_url: str) -> list[str]:
    soup = BeautifulSoup(html, "html.parser")
    links = []
    for a in soup.find_all("a", href=True):
        href = urljoin(base_url, a["href"])
        parsed = urlparse(href)
        # strip fragments and query strings for dedup
        clean = parsed._replace(fragment="", query="").geturl()
        if is_sap_help_link(clean) and clean not in SEEN_URLS:
            links.append(clean)
    return list(dict.fromkeys(links))[:MAX_SUBLINKS]  # dedup, cap

def scrape_page(url: str, source: str, depth: int = 0, max_depth: int = 2):
    if url in SEEN_URLS:
        return
    resp = get(url)
    if not resp:
        return

    soup = BeautifulSoup(resp.text, "html.parser")
    title_tag = soup.find("title")
    title = title_tag.get_text().strip() if title_tag else url

    # Extract main content
    main = (soup.find("article") or
            soup.find("main") or
            soup.find("div", {"id": "content"}) or
            soup.find("div", class_=re.compile(r"content|body|article", re.I)))
    if main:
        for tag in main(["script","style","nav"]):
            tag.decompose()
        text = main.get_text(separator="\n").strip()[:MAX_TEXT_LEN]
    else:
        text = clean_text(resp.text)

    add_record(
        source = source,
        type   = "documentation",
        title  = title,
        url    = url,
        depth  = depth,
        text   = text,
    )

    # Follow sub-links up to max_depth
    if depth < max_depth:
        sublinks = extract_sublinks(resp.text, url)
        log.info(f"    Following {len(sublinks)} sub-links from {url[:60]}...")
        for sub in sublinks:
            if sub not in SEEN_URLS:
                scrape_page(sub, source, depth + 1, max_depth)

def scrape_sap_help():
    log.info("═══ SAP Help Portal ═══")
    for seed in SAP_HELP_SEEDS:
        log.info(f"  Seed: {seed[:70]}")
        scrape_page(seed, source="sap_help", depth=0, max_depth=2)

# ══════════════════════════════════════════════════════════════════════════════
# 4. SAP DEVELOPER TUTORIALS  (deep crawl)
# ══════════════════════════════════════════════════════════════════════════════

SAP_TUTORIAL_SEEDS = [
    "https://developers.sap.com/mission.abap-env-trial.html",
    "https://developers.sap.com/mission.sds-abap-cloud.html",
    "https://developers.sap.com/group.abap-rap-build-app.html",
    "https://developers.sap.com/group.abap-env-abap-rap.html",
    "https://developers.sap.com/tutorials/abap-create-project.html",
    "https://developers.sap.com/tutorials/abap-dev-adt-create-new-source.html",
    "https://developers.sap.com/tutorials/abap-environment-console-application.html",
    "https://developers.sap.com/tutorials/abap-environment-rap-business-events.html",
    "https://developers.sap.com/tutorials/abap-environment-create-cds-view.html",
]

def scrape_tutorial_page(url: str):
    if url in SEEN_URLS:
        return
    resp = get(url)
    if not resp:
        return

    soup = BeautifulSoup(resp.text, "html.parser")
    title_tag = soup.find("h1") or soup.find("title")
    title = title_tag.get_text().strip() if title_tag else url

    # Tutorial steps
    steps = soup.find_all(class_=re.compile(r"step|tutorial-content|content", re.I))
    if steps:
        text = "\n\n".join(s.get_text(separator="\n").strip() for s in steps)[:MAX_TEXT_LEN]
    else:
        text = clean_text(resp.text)

    # Find code blocks
    code_blocks = []
    for pre in soup.find_all(["pre","code"]):
        code = pre.get_text().strip()
        if len(code) > 50:
            code_blocks.append(code[:2000])

    add_record(
        source       = "sap_tutorials",
        type         = "tutorial",
        title        = title,
        url          = url,
        text         = text,
        code_samples = code_blocks[:5],
    )

    # Follow tutorial sub-links on same domain
    for a in soup.find_all("a", href=True):
        href = urljoin(url, a["href"])
        if "developers.sap.com/tutorials" in href and href not in SEEN_URLS:
            scrape_tutorial_page(href)

def scrape_tutorials():
    log.info("═══ SAP Developer Tutorials ═══")
    for seed in SAP_TUTORIAL_SEEDS:
        log.info(f"  Seed: {seed[:70]}")
        scrape_tutorial_page(seed)

# ══════════════════════════════════════════════════════════════════════════════
# 5. ABAP OPEN SOURCE DOCS  (Clean ABAP styleguide, etc.)
# ══════════════════════════════════════════════════════════════════════════════

OPEN_SOURCE_PAGES = [
    {
        "url": "https://raw.githubusercontent.com/SAP/styleguides/main/clean-abap/CleanABAP.md",
        "title": "Clean ABAP Style Guide (SAP Official)",
        "source": "sap_styleguides",
        "type": "style_guide",
    },
    {
        "url": "https://raw.githubusercontent.com/SAP/styleguides/main/clean-abap/sub-sections/Formatting.md",
        "title": "Clean ABAP – Formatting",
        "source": "sap_styleguides",
        "type": "style_guide",
    },
    {
        "url": "https://raw.githubusercontent.com/SAP/abap-cheat-sheets/main/01_Internal_Tables.md",
        "title": "ABAP Cheat Sheet – Internal Tables",
        "source": "sap_cheatsheets",
        "type": "cheat_sheet",
    },
    {
        "url": "https://raw.githubusercontent.com/SAP/abap-cheat-sheets/main/02_String_Processing.md",
        "title": "ABAP Cheat Sheet – String Processing",
        "source": "sap_cheatsheets",
        "type": "cheat_sheet",
    },
    {
        "url": "https://raw.githubusercontent.com/SAP/abap-cheat-sheets/main/03_ABAP_SQL.md",
        "title": "ABAP Cheat Sheet – ABAP SQL",
        "source": "sap_cheatsheets",
        "type": "cheat_sheet",
    },
    {
        "url": "https://raw.githubusercontent.com/SAP/abap-cheat-sheets/main/04_ABAP_Object_Orientation.md",
        "title": "ABAP Cheat Sheet – Object Orientation",
        "source": "sap_cheatsheets",
        "type": "cheat_sheet",
    },
    {
        "url": "https://raw.githubusercontent.com/SAP/abap-cheat-sheets/main/05_Constructor_Expressions.md",
        "title": "ABAP Cheat Sheet – Constructor Expressions",
        "source": "sap_cheatsheets",
        "type": "cheat_sheet",
    },
    {
        "url": "https://raw.githubusercontent.com/SAP/abap-cheat-sheets/main/08_RAP.md",
        "title": "ABAP Cheat Sheet – RAP",
        "source": "sap_cheatsheets",
        "type": "cheat_sheet",
    },
    {
        "url": "https://raw.githubusercontent.com/SAP/abap-cheat-sheets/main/09_CDS.md",
        "title": "ABAP Cheat Sheet – CDS",
        "source": "sap_cheatsheets",
        "type": "cheat_sheet",
    },
]

def scrape_open_source():
    log.info("═══ SAP Open Source Docs ═══")
    for page in OPEN_SOURCE_PAGES:
        resp = get(page["url"])
        if not resp:
            continue
        add_record(
            source = page["source"],
            type   = page["type"],
            title  = page["title"],
            url    = page["url"],
            text   = resp.text[:MAX_TEXT_LEN],
        )

# ══════════════════════════════════════════════════════════════════════════════
# OUTPUT
# ══════════════════════════════════════════════════════════════════════════════

def save_outputs():
    log.info(f"═══ Saving {len(records)} records ═══")

    # JSONL
    with open(OUTPUT_JSONL, "w", encoding="utf-8") as f:
        for rec in records:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    log.info(f"  ✓ {OUTPUT_JSONL}  ({OUTPUT_JSONL.stat().st_size // 1024} KB)")

    # Markdown summary
    counts: dict[str, int] = {}
    for rec in records:
        counts[rec["source"]] = counts.get(rec["source"], 0) + 1

    with open(OUTPUT_MD, "w", encoding="utf-8") as f:
        f.write(f"# SAP ABAP Knowledge Base\n\n")
        f.write(f"**Last scraped:** {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}\n\n")
        f.write(f"**Total records:** {len(records)}\n\n")
        f.write("## Records by source\n\n")
        for src, cnt in sorted(counts.items(), key=lambda x: -x[1]):
            f.write(f"- `{src}`: {cnt}\n")
        f.write("\n## Download\n\n")
        f.write(f"The full dataset is in [`abap_knowledge.jsonl`](./abap_knowledge.jsonl) — ")
        f.write("one JSON record per line, ready for RAG / LLM fine-tuning.\n\n")
        f.write("## Record schema\n\n```json\n")
        if records:
            sample = {k: v for k, v in list(records[0].items()) if k != "text"}
            sample["text"] = "... full page text (up to 12,000 chars) ..."
            f.write(json.dumps(sample, indent=2, ensure_ascii=False)[:1000])
        f.write("\n```\n")

    log.info(f"  ✓ {OUTPUT_MD}")

# ══════════════════════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    start = time.time()
    log.info("🚀  SAP ABAP Knowledge Deep Scraper — starting")
    log.info(f"    GitHub token: {'✓ present' if GH_TOKEN else '✗ missing (60 req/hr limit)'}")

    scrape_open_source()   # fastest — raw markdown files
    scrape_github()        # repos + READMEs + source files
    scrape_sap_community() # blog posts RSS + full body
    scrape_sap_help()      # documentation + sub-links (depth 2)
    scrape_tutorials()     # tutorial pages + code samples

    save_outputs()

    elapsed = int(time.time() - start)
    log.info(f"✅  Done in {elapsed // 60}m {elapsed % 60}s — {len(records)} records")
