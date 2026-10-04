import json
import sys
from concurrent.futures import ThreadPoolExecutor
from typing import Optional

from langchain.agents import create_agent
from langchain.chat_models import init_chat_model
from langchain_core.tools import tool
from playwright.sync_api import sync_playwright
from pydantic import BaseModel, Field

DEFAULT_URL = "https://globalwebproduction.com/"
MODEL = "groq:openai/gpt-oss-120b"   # change this one line to use another LLM
MAX_CHARS = 12000                    # limit text sent to the LLM (controls cost)
HEADLESS = False                      # set False to watch the browser work


class Founder(BaseModel):
    name: str
    role: Optional[str] = Field(None, description="Title as written on the page, e.g. 'CEO & Founder'")
    bio: Optional[str] = Field(None, description="2-4 sentence summary of the person, using only facts on the page")
    previous_experience: Optional[str] = Field(None, description="Past companies, roles or education, only if stated")
    linkedin_url: Optional[str] = Field(None, description="Personal LinkedIn URL, ONLY if it is in the PERSON LINKEDIN LINKS list")
    evidence: str = Field(description="Exact sentence from the page that names this person as a founder")


class FounderResult(BaseModel):
    company: Optional[str] = None
    founders: list[Founder] = Field(default_factory=list)


try:
    llm = init_chat_model(MODEL, temperature=0)
    extractor = llm.with_structured_output(FounderResult)
except Exception:
    llm = None
    extractor = None

# Playwright's sync API must always be used from ONE thread. LangGraph runs tools
# in worker threads, so all browser work goes through this single-thread executor.
_executor = ThreadPoolExecutor(max_workers=1)
_state = {}
_results = {}   # merged founder details collected across all pages


def _run_in_browser_thread(fn, *args):
    return _executor.submit(fn, *args).result()


def _get_page():
    if "page" not in _state:
        _state["pw"] = sync_playwright().start()
        launchers = [{}, {"channel": "chrome"}, {"channel": "msedge"}]
        last_error = None
        for opts in launchers:
            try:
                _state["browser"] = _state["pw"].chromium.launch(headless=HEADLESS, **opts)
                break
            except Exception as e:
                last_error = e
        else:
            raise last_error
        _state["page"] = _state["browser"].new_page()
        # Block heavy assets for fast loads
        _state["page"].route(
            "**/*.{png,jpg,jpeg,gif,svg,webp,ico,woff,woff2,ttf,otf,mp4,webm,css}",
            lambda route: route.abort()
        )
    return _state["page"]


def _close_browser():
    if "browser" in _state:
        _state["browser"].close()
        _state["pw"].stop()
        _state.clear()


def _settle(page):
    """JavaScript sites build content late: wait briefly without 10+ second delays."""
    try:
        page.wait_for_load_state("domcontentloaded", timeout=5000)
        page.wait_for_load_state("networkidle", timeout=1500)
    except Exception:
        pass
    try:
        page.evaluate("window.scrollTo(0, document.body.scrollHeight / 2)")
        page.wait_for_timeout(200)
        page.evaluate("window.scrollTo(0, 0)")
    except Exception:
        pass


def _page_text(page) -> str:
    return " ".join(page.inner_text("body").split())


def _person_linkedin_links(page) -> list[dict]:
    """Personal LinkedIn links (linkedin.com/in/...) with the text around each one."""
    return page.eval_on_selector_all(
        "a[href*='linkedin.com/in/']",
        """els => els.map(e => {
            let c = e;
            for (let i = 0; i < 3 && c.parentElement; i++) c = c.parentElement;
            return {href: e.href, context: (c.innerText || '').trim().slice(0, 300)};
        })""",
    )


def _snapshot(page, preview_chars: int = 3000) -> str:
    anchors = page.eval_on_selector_all(
        "a[href]", "els => els.map(e => ({href: e.href, text: (e.innerText || '').trim()}))"
    )
    keywords = ("about", "team", "leadership", "management", "executive",
                "company", "founder", "people", "story", "contact")
    links = sorted({
        a["href"] for a in anchors
        if any(k in a["href"].lower() or k in a["text"].lower() for k in keywords)
    })[:20]

    labels = page.eval_on_selector_all(
        "a, button, [role=button], [role=tab], [role=link]",
        "els => els.map(e => (e.innerText || '').replace(/\\s+/g, ' ').trim())",
    )
    clickable = []
    for t in labels:
        if t and len(t) <= 40 and t not in clickable:
            clickable.append(t)

    return (
        f"URL: {page.url}\n\nLINKS:\n" + "\n".join(links)
        + "\n\nCLICKABLE LABELS:\n" + " | ".join(clickable[:40])
        + f"\n\nTEXT PREVIEW:\n{_page_text(page)[:preview_chars]}"
    )


def _open_page_impl(url: str) -> str:
    page = _get_page()
    if not (url.startswith("http://") or url.startswith("https://")):
        url = "https://" + url

    last_error = None
    urls_to_try = [url]
    if url.startswith("https://"):
        urls_to_try.append("http://" + url[8:])

    loaded = False
    for target in urls_to_try:
        for wait_strategy in ("domcontentloaded", "commit"):
            try:
                page.goto(target, wait_until=wait_strategy, timeout=25000)
                loaded = True
                break
            except Exception as e:
                last_error = e
        if loaded:
            break

    if not loaded:
        return f"ERROR opening page: {last_error}. Pick a link from LINKS."

    _settle(page)
    return _snapshot(page)


def _click_text_impl(text: str) -> str:
    page = _get_page()
    clicked = False
    last_err = None
    locators = [
        lambda: page.get_by_role("link", name=text, exact=False).first,
        lambda: page.get_by_role("button", name=text, exact=False).first,
        lambda: page.get_by_text(text, exact=False).first,
        lambda: page.locator(f"a:has-text('{text}'), button:has-text('{text}')").first,
    ]

    for loc_factory in locators:
        try:
            loc = loc_factory()
            if loc.is_visible(timeout=1000):
                loc.click(timeout=3000)
                clicked = True
                break
        except Exception as e:
            last_err = e

    if not clicked:
        return f"ERROR clicking '{text}': {last_err or 'Element not visible'}. Try another link from LINKS."

    _settle(page)
    return _snapshot(page)


def _merge(founder: Founder, source_url: str):
    """Combine details for the same person found on different pages."""
    key = founder.name.strip().lower()
    data = founder.model_dump()
    evidence = data.pop("evidence")
    entry = _results.setdefault(key, {"name": founder.name, "role": None, "bio": None,
                                      "previous_experience": None, "linkedin_url": None,
                                      "evidence": [], "sources": []})
    for field in ("role", "linkedin_url", "previous_experience"):
        if not entry[field] and data.get(field):
            entry[field] = data[field]
    if data.get("bio") and len(data["bio"]) > len(entry["bio"] or ""):
        entry["bio"] = data["bio"]
    if evidence and evidence not in entry["evidence"]:
        entry["evidence"].append(evidence)
    if source_url not in entry["sources"]:
        entry["sources"].append(source_url)


def _extract_impl() -> str:
    page = _get_page()
    text = _page_text(page)[:MAX_CHARS]
    li_links = _person_linkedin_links(page)
    li_block = "\n".join(f"- {l['href']}  (near text: {l['context']!r})" for l in li_links) or "(none)"

    prompt = (
        "Extract the FOUNDERS of the company from the text below.\n"
        "Rules:\n"
        "- Only include people the text clearly names as founder or co-founder.\n"
        "- If the text does not name founders, return an empty list. Do NOT guess.\n"
        "- 'evidence' must be an exact sentence copied from the text.\n"
        "- role, bio and previous_experience: use only facts stated in the text, else null.\n"
        "- linkedin_url: use ONLY a URL from the PERSON LINKEDIN LINKS list, and only if the "
        "'near text' shows it belongs to that founder. Never make up a URL. Otherwise null.\n\n"
        f"PERSON LINKEDIN LINKS:\n{li_block}\n\n"
        f"TEXT:\n{text}"
    )
    result = extractor.invoke(prompt)

    # Safety check in code: drop any LinkedIn URL that is not a real link on the page
    real = {l["href"].rstrip("/") for l in li_links}
    for f in result.founders:
        if f.linkedin_url and f.linkedin_url.rstrip("/") not in real:
            f.linkedin_url = None
        _merge(f, page.url)

    return json.dumps({"page": page.url, "founders_found_here": [f.name for f in result.founders],
                       "person_linkedin_links_on_page": len(li_links)})


@tool
def open_page(url: str) -> str:
    """Open a URL in the browser. Returns links, clickable labels and a text preview."""
    return _run_in_browser_thread(_open_page_impl, url)


@tool
def click_text(text: str) -> str:
    """Click a menu item, tab or button by a short distinctive part of its visible label
    (from CLICKABLE LABELS), e.g. 'LEADERSHIP'. Returns the new view."""
    return _run_in_browser_thread(_click_text_impl, text)


@tool
def extract_founders() -> str:
    """Extract founder details from the page or section currently showing in the browser
    and save them. No arguments. Call it on EACH page that may describe the founder."""
    return _run_in_browser_thread(_extract_impl)


agent = create_agent(
    model=llm,
    tools=[open_page, click_text, extract_founders],
    system_prompt=(
        "You collect company founder details using a real browser. Steps:\n"
        "1. open_page on the given URL.\n"
        "2. Open the About page. Call extract_founders.\n"
        "3. Then look for a leadership / team / founder / 'meet' page or button and open it "
        "(open_page for a link in LINKS, or click_text with a short word from CLICKABLE LABELS). "
        "Call extract_founders again there. Details from each page are merged automatically.\n"
        "4. If a founder has no bio or LinkedIn yet, check the Contact page and the leadership section once.\n"
        "Rules:\n"
        "- Only open URLs that appeared in LINKS, or the user's URL. Never guess URLs.\n"
        "- Never use proxy, cache or scraper services. Never open linkedin.com pages.\n"
        "- Call one tool at a time. Use at most 12 tool calls.\n"
        "- Never invent names, bios or LinkedIn URLs.\n"
        "When finished, reply with one short sentence."
    ),
)


def show_steps(messages):
    print("\n===== STEPS =====")
    for m in messages:
        calls = getattr(m, "tool_calls", None)
        if m.type == "ai" and calls:
            for c in calls:
                print(f"[AGENT CALLED] {c['name']} {c['args']}")
        elif m.type == "tool":
            print(f"[TOOL RESULT] {str(m.content)[:400]}\n")
    print("===== END STEPS =====\n")


if __name__ == "__main__":
    url = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_URL
    try:
        out = agent.invoke(
            {"messages": [{"role": "user", "content": f"Find the founders for: {url}"}]},
            config={"recursion_limit": 40},
        )
        show_steps(out["messages"])
        print("COLLECTED FOUNDER DETAILS:")
        print(json.dumps(list(_results.values()), indent=2, ensure_ascii=False)
              if _results else "No founders found on the site.")
    finally:
        _run_in_browser_thread(_close_browser)