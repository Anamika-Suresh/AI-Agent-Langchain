import asyncio
import json
import logging
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Callable, Optional, Dict, Any

from langchain.agents import create_agent
from langchain.chat_models import init_chat_model
from langchain_core.tools import tool
from playwright.sync_api import sync_playwright
from pydantic import BaseModel, Field

from config import LLM_MODEL, MAX_CHARS, HEADLESS
from database import SessionLocal, ExtractionJob, FounderModel, AgentLogModel

logger = logging.getLogger("founder_agent.service")


# Pydantic schemas for LLM structured output
class FounderSchema(BaseModel):
    name: str
    role: Optional[str] = Field(None, description="Title as written on the page, e.g. 'CEO & Founder'")
    bio: Optional[str] = Field(None, description="2-4 sentence summary of the person, using only facts on the page")
    previous_experience: Optional[str] = Field(None, description="Past companies, roles or education, only if stated")
    linkedin_url: Optional[str] = Field(None, description="Personal LinkedIn URL, ONLY if it is in the PERSON LINKEDIN LINKS list")
    evidence: str = Field(description="Exact sentence from the page that names this person as a founder")


class FounderResultSchema(BaseModel):
    company: Optional[str] = None
    founders: list[FounderSchema] = Field(default_factory=list)


class FounderAgentRunner:
    def __init__(self, job_id: str, target_url: str, ws_callback: Optional[Callable[[Dict[str, Any]], None]] = None, event_loop=None):
        self.job_id = job_id
        self.target_url = target_url
        self.ws_callback = ws_callback
        self.executor = ThreadPoolExecutor(max_workers=1)
        self.state = {}
        self.results = {}  # key -> merged dict
        self.llm = None
        self.extractor = None
        try:
            self.event_loop = event_loop or asyncio.get_running_loop()
        except RuntimeError:
            self.event_loop = None

    def _send_ws(self, payload: dict):
        """Safely dispatch payload to WebSocket callback without raising exceptions."""
        if not self.ws_callback:
            return
        try:
            if asyncio.iscoroutinefunction(self.ws_callback):
                if self.event_loop and self.event_loop.is_running():
                    asyncio.run_coroutine_threadsafe(self.ws_callback(payload), self.event_loop)
                else:
                    asyncio.run(self.ws_callback(payload))
            else:
                self.ws_callback(payload)
        except Exception as e:
            logger.error(f"Failed to emit WS payload: {e}")

    def _emit_log(self, step_type: str, message: str, details: Optional[Dict[str, Any]] = None):
        """Save log to database and stream via WebSocket callback."""
        timestamp = datetime.now(timezone.utc).isoformat()
        log_payload = {
            "job_id": self.job_id,
            "step_type": step_type,
            "message": message,
            "details": details,
            "timestamp": timestamp
        }

        # Persist log to DB
        try:
            db = SessionLocal()
            log_entry = AgentLogModel(
                job_id=self.job_id,
                step_type=step_type,
                message=message,
                details_json=json.dumps(details) if details else None
            )
            db.add(log_entry)
            db.commit()
            db.close()
        except Exception as e:
            logger.error(f"Failed to persist agent log to DB: {e}")

        # Send to WebSocket callback
        self._send_ws({"type": "agent_log", **log_payload})

    def _run_in_browser_thread(self, fn, *args):
        return self.executor.submit(fn, *args).result()

    def _get_page(self):
        if "page" not in self.state:
            self._emit_log("INFO", "Initializing Playwright Chromium headless browser...")
            self.state["pw"] = sync_playwright().start()
            launchers = [{}, {"channel": "chrome"}, {"channel": "msedge"}]
            last_error = None
            for opts in launchers:
                try:
                    self.state["browser"] = self.state["pw"].chromium.launch(headless=HEADLESS, **opts)
                    break
                except Exception as e:
                    last_error = e
            else:
                raise last_error
            
            page = self.state["browser"].new_page()
            # Block images, fonts, media, & ad trackers for 3x-5x faster page loads
            page.route(
                "**/*.{png,jpg,jpeg,gif,svg,webp,ico,woff,woff2,ttf,otf,mp4,webm,css}",
                lambda route: route.abort()
            )
            self.state["page"] = page
        return self.state["page"]

    def _close_browser(self):
        if "browser" in self.state:
            self.state["browser"].close()
            self.state["pw"].stop()
            self.state.clear()
            self._emit_log("INFO", "Closed browser session.")

    def _settle(self, page):
        """Wait briefly for late JS content to load without stalling for 10+ seconds."""
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

    def _page_text(self, page) -> str:
        return " ".join(page.inner_text("body").split())

    def _person_linkedin_links(self, page) -> list[dict]:
        return page.eval_on_selector_all(
            "a[href*='linkedin.com/in/']",
            """els => els.map(e => {
                let c = e;
                for (let i = 0; i < 3 && c.parentElement; i++) c = c.parentElement;
                return {href: e.href, context: (c.innerText || '').trim().slice(0, 300)};
            })""",
        )

    def _snapshot(self, page, preview_chars: int = 3000) -> str:
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
            + f"\n\nTEXT PREVIEW:\n{self._page_text(page)[:preview_chars]}"
        )

    def _open_page_impl(self, url: str) -> str:
        self._emit_log("TOOL_CALL", f"Navigating browser to URL: {url}", {"url": url})
        page = self._get_page()
        
        # Normalize protocol if missing
        if not (url.startswith("http://") or url.startswith("https://")):
            url = "https://" + url

        last_error = None
        # Try primary URL then fallback to http if https times out
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
            msg = f"Error loading URL {url}: {last_error}"
            self._emit_log("ERROR", msg)
            return f"ERROR opening page: {last_error}. Try another link from LINKS list."

        self._settle(page)
        snapshot = self._snapshot(page)
        self._emit_log("TOOL_RESULT", f"Successfully loaded {page.url}", {"current_url": page.url})
        return snapshot

    def _click_text_impl(self, text: str) -> str:
        self._emit_log("TOOL_CALL", f"Clicking page element matching text: '{text}'", {"text": text})
        page = self._get_page()
        
        # Try multiple locator strategies with fast 3-second timeouts
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
            msg = f"Element matching '{text}' not clickable or not visible on page. Pick a link from LINKS."
            self._emit_log("ERROR", f"Error clicking text '{text}': {last_err or 'Element not visible'}")
            return msg

        self._settle(page)
        snapshot = self._snapshot(page)
        self._emit_log("TOOL_RESULT", f"Clicked '{text}'. Current URL: {page.url}", {"current_url": page.url})
        return snapshot

    def _merge(self, founder: FounderSchema, source_url: str):
        key = founder.name.strip().lower()
        data = founder.model_dump()
        evidence = data.pop("evidence")
        entry = self.results.setdefault(key, {
            "name": founder.name,
            "role": None,
            "bio": None,
            "previous_experience": None,
            "linkedin_url": None,
            "evidence": [],
            "sources": []
        })
        for field in ("role", "linkedin_url", "previous_experience"):
            if not entry[field] and data.get(field):
                entry[field] = data[field]
        if data.get("bio") and len(data["bio"]) > len(entry["bio"] or ""):
            entry["bio"] = data["bio"]
        if evidence and evidence not in entry["evidence"]:
            entry["evidence"].append(evidence)
        if source_url not in entry["sources"]:
            entry["sources"].append(source_url)

    def _extract_impl(self) -> str:
        page = self._get_page()
        self._emit_log("TOOL_CALL", f"Extracting founders from current page ({page.url})...", {"url": page.url})
        text = self._page_text(page)[:MAX_CHARS]
        li_links = self._person_linkedin_links(page)
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

        try:
            result = self.extractor.invoke(prompt)
            real = {l["href"].rstrip("/") for l in li_links}
            found_names = []
            for f in result.founders:
                if f.linkedin_url and f.linkedin_url.rstrip("/") not in real:
                    f.linkedin_url = None
                self._merge(f, page.url)
                found_names.append(f.name)

            self._emit_log("MERGE", f"Extracted {len(found_names)} founder(s) from {page.url}: {', '.join(found_names) if found_names else 'None'}", {
                "founders_found": found_names,
                "current_total_founders": len(self.results)
            })

            # Stream intermediate founder data to WS
            current_founders = list(self.results.values())
            self._send_ws({"type": "founders_update", "job_id": self.job_id, "founders": current_founders})

            return json.dumps({
                "page": page.url,
                "founders_found_here": found_names,
                "person_linkedin_links_on_page": len(li_links)
            })
        except Exception as e:
            err_msg = f"LLM Extraction failed on page {page.url}: {e}"
            self._emit_log("ERROR", err_msg)
            return json.dumps({"error": str(e)})

    def run(self):
        """Execute the LangChain agent for founder extraction."""
        # Update job status to RUNNING
        db = SessionLocal()
        job = db.query(ExtractionJob).filter(ExtractionJob.id == self.job_id).first()
        if job:
            job.status = "RUNNING"
            db.commit()
        db.close()

        self._emit_log("INFO", f"Starting Founder Agent extraction workflow for: {self.target_url}")

        try:
            self.llm = init_chat_model(LLM_MODEL, temperature=0)
            self.extractor = self.llm.with_structured_output(FounderResultSchema)
        except Exception as e:
            err_msg = f"Failed to initialize LLM model '{LLM_MODEL}': {e}"
            self._emit_log("ERROR", err_msg)
            db = SessionLocal()
            job = db.query(ExtractionJob).filter(ExtractionJob.id == self.job_id).first()
            if job:
                job.status = "FAILED"
                job.completed_at = datetime.now(timezone.utc)
                job.error_message = err_msg
                db.commit()
            db.close()
            payload = {"type": "failed", "job_id": self.job_id, "status": "FAILED", "error": err_msg}
            self._send_ws(payload)
            return

        # Tools setup
        @tool
        def open_page(url: str) -> str:
            """Open a URL in the browser. Returns links, clickable labels and a text preview."""
            return self._run_in_browser_thread(self._open_page_impl, url)

        @tool
        def click_text(text: str) -> str:
            """Click a menu item, tab or button by a short distinctive part of its visible label (from CLICKABLE LABELS)."""
            return self._run_in_browser_thread(self._click_text_impl, text)

        @tool
        def extract_founders() -> str:
            """Extract founder details from the page or section currently showing in the browser and save them."""
            return self._run_in_browser_thread(self._extract_impl)

        agent = create_agent(
            model=self.llm,
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
            )
        )

        try:
            out = agent.invoke(
                {"messages": [{"role": "user", "content": f"Find the founders for: {self.target_url}"}]},
                config={"recursion_limit": 40}
            )

            # Execution finished cleanly
            db = SessionLocal()
            job = db.query(ExtractionJob).filter(ExtractionJob.id == self.job_id).first()
            
            founders_list = list(self.results.values())
            if job:
                job.status = "COMPLETED"
                job.completed_at = datetime.now(timezone.utc)
                job.total_founders = len(founders_list)

                # Persist extracted founders
                for f_data in founders_list:
                    founder_obj = FounderModel(
                        job_id=self.job_id,
                        name=f_data["name"],
                        role=f_data.get("role"),
                        bio=f_data.get("bio"),
                        previous_experience=f_data.get("previous_experience"),
                        linkedin_url=f_data.get("linkedin_url"),
                        evidence_json=json.dumps(f_data.get("evidence", [])),
                        sources_json=json.dumps(f_data.get("sources", []))
                    )
                    db.add(founder_obj)
                db.commit()
            db.close()

            self._emit_log("INFO", f"Extraction completed! Total founders found: {len(founders_list)}")

            self._send_ws({
                "type": "completed",
                "job_id": self.job_id,
                "status": "COMPLETED",
                "total_founders": len(founders_list),
                "founders": founders_list
            })

        except Exception as e:
            logger.error(f"Error during extraction job {self.job_id}: {e}", exc_info=True)
            db = SessionLocal()
            job = db.query(ExtractionJob).filter(ExtractionJob.id == self.job_id).first()
            if job:
                job.status = "FAILED"
                job.completed_at = datetime.now(timezone.utc)
                job.error_message = str(e)
                db.commit()
            db.close()

            self._emit_log("ERROR", f"Extraction job failed: {e}")

            self._send_ws({
                "type": "failed",
                "job_id": self.job_id,
                "status": "FAILED",
                "error": str(e)
            })

        finally:
            self._run_in_browser_thread(self._close_browser)


def start_extraction_job(job_id: str, target_url: str, ws_callback: Optional[Callable[[Dict[str, Any]], None]] = None, loop=None):
    """Factory helper to start an extraction job runner in a background worker thread."""
    if loop is None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
    runner = FounderAgentRunner(job_id=job_id, target_url=target_url, ws_callback=ws_callback, event_loop=loop)
    if loop and loop.is_running():
        loop.run_in_executor(None, runner.run)
    else:
        runner.run()
