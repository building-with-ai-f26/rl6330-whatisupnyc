import os
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from app import auth, db
from app.ai import AIClient, AIUnavailable
from app.newsletter import _templates, choose_picks, render_newsletter
from app.sites import Sites, load as load_sites
from app.sources.pasted import process_paste
from app.sources.web import TavilyClient

DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
TEMPLATES = {
    "New to NYC": "I'm new to New York and want to explore classic spots, neighborhoods, and things locals love.",
    "Free and cheap": "Free or low-cost events: outdoor shows, museum free nights, talks, and markets.",
    "Live music and comedy": "Live music and stand-up comedy at good venues, from small rooms to big shows.",
    "Art and culture": "Art openings, museum programs, film screenings, theater, and talks.",
}
AI_BUSY = "The AI model is busy right now. Your events are saved, so please try again in a little while."


@dataclass
class AppDeps:
    conn: object
    ai: AIClient
    tavily: TavilyClient
    http: httpx.Client
    sites: Sites
    clock: Callable[[], datetime]
    site_password: str


def _default_deps() -> AppDeps:
    load_dotenv()
    conn = db.connect(os.environ.get("DATABASE_PATH", "whatisupnyc.db"))
    http = httpx.Client()
    return AppDeps(
        conn=conn,
        ai=AIClient(http, os.environ["OPENROUTER_API_KEY"], os.environ["OPENROUTER_MODEL"], conn),
        tavily=TavilyClient(http, os.environ["TAVILY_API_KEY"], run_budget=100),
        http=http,
        sites=load_sites(),
        clock=lambda: datetime.now(timezone.utc),
        site_password=os.environ.get("SITE_PASSWORD", ""),
    )


def _page(template: str, **context) -> str:
    return _templates.get_template(template).render(**context)


def create_app(deps: AppDeps | None = None) -> FastAPI:
    # No automatic pages that list every route.
    app = FastAPI(title="whatisupnyc", docs_url=None, redoc_url=None, openapi_url=None)
    cache: list[AppDeps] = [deps] if deps else []

    def get_deps() -> AppDeps:
        # Built on first use, so importing this module needs no keys or database.
        if not cache:
            cache.append(_default_deps())
        return cache[0]

    @app.middleware("http")
    async def require_password(request: Request, call_next):
        refusal = auth.check(request, get_deps().site_password)
        return refusal or await call_next(request)

    def start_page(message: str = "", status_code: int = 200) -> HTMLResponse:
        html = _page("index.html", days=DAYS, templates=TEMPLATES, message=message)
        return HTMLResponse(html, status_code=status_code)

    def subscriber_for(token: str):
        subscriber = db.find_subscriber_by_token(get_deps().conn, token)
        if subscriber is None:
            raise HTTPException(status_code=404, detail="Subscriber not found")
        return subscriber

    @app.get("/", response_class=HTMLResponse)
    def index():
        return start_page()

    @app.post("/subscribers")
    def add_subscriber(
        name: str = Form(""),
        email: str = Form(""),
        interests: str = Form(""),
        look_prompt: str = Form(""),
        days: list[str] = Form([]),
    ):
        chosen = [day for day in days if day in DAYS]
        if not name.strip() or not email.strip() or not interests.strip() or not chosen:
            return start_page("Please fill in a name, an email, what kind of event, and at least one day.", 400)
        conn = get_deps().conn
        new_id = db.save_subscriber(conn, name.strip(), email.strip(), interests.strip(), look_prompt.strip(), chosen)
        return RedirectResponse(f"/newsletter/{db.subscriber_token(conn, new_id)}", status_code=303)

    @app.get("/newsletter/{token}", response_class=HTMLResponse)
    def newsletter(token: str):
        d = get_deps()
        subscriber = subscriber_for(token)
        events = db.eligible_events(d.conn, d.clock())
        try:
            picks = choose_picks(
                subscriber, events, d.ai, now=d.clock(), sent_ids=db.sent_event_ids(d.conn, subscriber.id)
            )
        except AIUnavailable:
            return _page("view.html", subscriber=subscriber, token=token, error=AI_BUSY, body=None, event_ids=[])
        return _page(
            "view.html",
            subscriber=subscriber,
            token=token,
            error=None,
            body=render_newsletter(picks, now=d.clock()),
            event_ids=[pick.event.id for pick in picks],
        )

    @app.post("/newsletter/{token}/sent")
    def mark_sent(token: str, event_ids: list[int] = Form([])):
        db.mark_sent(get_deps().conn, subscriber_for(token).id, event_ids)
        return RedirectResponse("/", status_code=303)

    @app.post("/paste", response_class=HTMLResponse)
    def paste(link: str = Form(""), caption: str = Form("")):
        d = get_deps()
        result = process_paste(link.strip(), caption, d.tavily, d.http, d.sites, d.ai, d.clock())
        if result.event:
            db.save_paste(d.conn, link.strip(), caption, result.event)
        return start_page(result.message)

    return app


app = create_app()
