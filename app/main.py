import hashlib
import hmac
import json
import os
from pathlib import Path
import secrets
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone

import httpx
import psycopg
from dotenv import load_dotenv
from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool

from app import auth, db, intake, saved_lists
from app import emailer
from app.ai import AIClient, AIUnavailable, client_from_env
from app.limits import LIMITS, RateLimiter, client_ip
from app.models import Subscriber
from app.newsletter import NY
from app.newsletter import _templates, card, choose_picks, dashboard_groups, render_newsletter
from app.sites import Sites, load as load_sites
from app.sources.pasted import paste_problem
from app.sources.web import TavilyClient
from app.supabase_auth import AuthRefused, AuthUnavailable, Session, SupabaseAuth, auth_from_env

# Keeps a flood of posts from piling up faster than the morning runs can check them.
MAX_WAITING_PASTES = 200

DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
# When readers can have their list emailed, New York time: (hour, label). Lists are made after the 6:30 collection.
SEND_TIMES = [(7, "7 AM"), (8, "8 AM"), (9, "9 AM"), (12, "Noon"), (17, "5 PM")]


def send_hour_from(text: str) -> int:
    """The chosen hour, or 8 AM for anything that isn't one of the choices."""
    hours = [hour for hour, _ in SEND_TIMES]
    return int(text) if text.strip().isdigit() and int(text) in hours else 8
TEMPLATES = {
    "New to NYC": "Classic NYC spots, neighborhoods, and things locals love.",
    "Free and cheap": "Free or low-cost events: outdoor shows, museum free nights, talks, and markets.",
    "Live music": "Live music at good venues, from small jazz rooms to big concerts.",
    "Comedy": "Stand-up and improv comedy shows.",
    "Art and museums": "Art openings, gallery shows, and museum programs.",
    "Theater and dance": "Theater, Broadway and off-Broadway shows, and dance performances.",
    "Film": "Film screenings, premieres, and movie nights.",
    "Sports": "Live sports games, races, and watch parties.",
    "Food and drink": "Food festivals, tastings, food markets, and pop-up dinners.",
    "Markets and shopping": "Holiday markets, flea markets, sample sales, and pop-up shops.",
    "Outdoors": "Parks, walks, tours, and outdoor activities.",
    "Fitness": "Group runs, yoga, and free fitness classes.",
    "Talks and books": "Talks, author readings, book events, and lectures.",
    "Nightlife": "Parties, DJ nights, and late-night events.",
    "Family and kids": "Family-friendly events and things to do with kids.",
    "Meet people": "Social events, meetups, and ways to meet new people.",
}
AI_BUSY = "The AI model is busy right now. Your events are saved, so please try again in a little while."
AUTH_DOWN = "Sign-in is down right now, please try again in a little while."
# The list page's second step: it makes the new list while the reader sees "Making your list…".
MAKE_LIST = "/newsletter?make=1"
TOO_MANY = "Too many tries from your connection. Please wait a while and try again."
CHECK_EMAIL = (
    "Check your email to confirm your account. If you already have an account with this email, "
    "sign in or reset your password instead."
)
SIGNED_UP_BANNER = "Confirm your email to come back and edit your picks."
MIN_PASSWORD = 8
# Longest name a list can have ("Music", "Weekend sport").
MAX_TITLE = 40

# The login lives in cookies that page scripts can't read, sent only over https, and not sent
# when another site posts a form here.
ACCESS_COOKIE, REFRESH_COOKIE, FIRST_LOOK_COOKIE = "wnyc_access", "wnyc_refresh", "wnyc_first_look"
SESSION_SECONDS = 30 * 24 * 3600
COOKIE_FLAGS = {"httponly": True, "secure": True, "samesite": "lax"}


@dataclass
class AppDeps:
    conn: object
    ai: AIClient
    tavily: TavilyClient
    http: httpx.Client
    sites: Sites
    clock: Callable[[], datetime]
    auth: SupabaseAuth
    limiter: RateLimiter = field(default_factory=lambda: RateLimiter(LIMITS))
    # PostHog analytics. The project key is public by design; no key means no tracking.
    posthog_key: str = ""
    posthog_host: str = "https://us.i.posthog.com"
    # Resend key for emailing readers their own list; empty means preview only.
    resend_key: str = ""
    # SHA-256 of the key another tool uses for POST /api/intake; empty turns intake off.
    intake_hash: str = ""


@dataclass
class Reader:
    """Who is asking. profile is None for a visitor who isn't signed in (or has no preferences yet)."""

    profile: Subscriber | None = None
    signed_in: bool = False
    user_id: str | None = None
    email: str | None = None
    first_look: bool = False
    # A login renewed during this request, to store in new cookies.
    renewed: Session | None = None


def _default_deps() -> AppDeps:
    load_dotenv()
    conn = db.connect(db.url_from_env())
    http = httpx.Client()
    return AppDeps(
        conn=conn,
        ai=client_from_env(http, conn),
        tavily=TavilyClient(http, os.environ["TAVILY_API_KEY"], run_budget=100),
        http=http,
        sites=load_sites(),
        clock=lambda: datetime.now(timezone.utc),
        auth=auth_from_env(http),
        posthog_key=os.environ.get("POSTHOG_KEY", ""),
        posthog_host=os.environ.get("POSTHOG_HOST", "https://us.i.posthog.com"),
        resend_key=os.environ.get("RESEND_API_KEY", ""),
        intake_hash=os.environ.get("INTAKE_TOKEN_SHA256", ""),
    )


def _page(template: str, **context) -> str:
    return _templates.get_template(template).render(**context)


def set_session_cookies(response, session: Session) -> None:
    response.set_cookie(ACCESS_COOKIE, session.access_token, max_age=SESSION_SECONDS, **COOKIE_FLAGS)
    response.set_cookie(REFRESH_COOKIE, session.refresh_token, max_age=SESSION_SECONDS, **COOKIE_FLAGS)
    # A real login replaces the just-signed-up preview.
    response.delete_cookie(FIRST_LOOK_COOKIE, **COOKIE_FLAGS)


def clear_session_cookies(response) -> None:
    for name in (ACCESS_COOKIE, REFRESH_COOKIE, FIRST_LOOK_COOKIE):
        response.delete_cookie(name, **COOKIE_FLAGS)


def create_app(deps: AppDeps | None = None) -> FastAPI:
    # No automatic pages that list every route.
    app = FastAPI(title="whatisupnyc", docs_url=None, redoc_url=None, openapi_url=None)
    # The site's own pictures for events that have none.
    app.mount("/static", StaticFiles(directory=Path(__file__).parent / "static"), name="static")
    cache: list[AppDeps] = [deps] if deps else []

    def get_deps() -> AppDeps:
        # Built on first use, so importing this module needs no keys or database.
        if not cache:
            cache.append(_default_deps())
        return cache[0]

    @app.middleware("http")
    async def forms_from_this_site_only(request: Request, call_next):
        # The login is in cookies, so another site's form must not be able to act as the reader.
        # /api/ routes use their own key, never cookies, so this check doesn't apply to them.
        if not request.url.path.startswith("/api/") and not auth.sent_from_this_site(request):
            return PlainTextResponse("Forms must be sent from this site.", 403)
        return await call_next(request)

    def page(template: str, **context) -> str:
        d = get_deps()
        return _page(template, posthog_key=d.posthog_key, posthog_host=d.posthog_host, **context)

    def notice(title: str, text: str, links: list[tuple[str, str]], status_code: int = 200) -> HTMLResponse:
        return HTMLResponse(page("notice.html", title=title, text=text, links=links), status_code=status_code)

    def over_limit(request: Request, bucket: str) -> bool:
        return not get_deps().limiter.allow(bucket, client_ip(request))

    def over_account_limit(bucket: str, account: str) -> bool:
        return not get_deps().limiter.allow(bucket, account.strip().lower())

    def failed_sign_in_keys(request: Request, email: str) -> list[tuple[str, str]]:
        account = email.strip().lower()
        return [("failed_sign_in", f"{account} {client_ip(request)}"), ("failed_sign_in_account", account)]

    def too_many(request: Request) -> HTMLResponse | JSONResponse:
        if "application/json" in request.headers.get("accept", ""):
            return JSONResponse({"added": False, "message": TOO_MANY}, status_code=429)
        return notice("Please wait", TOO_MANY, [("/", "Back to the start")], 429)

    def start_page(
        request: Request, message: str = "", status_code: int = 200, paste_message: str = "", form: dict | None = None
    ) -> HTMLResponse:
        # message belongs to the sign-up form; paste_message shows beside the "add an event" form.
        # form holds what the reader already typed (never the password), so a rejected form doesn't make them start over.
        form = {"interests": "", "name": "", "email": "", "days": [], "send_hour": 8, **(form or {})}
        # Only a cookie check, so the start page never waits on Supabase.
        has_login = any(request.cookies.get(name) for name in (ACCESS_COOKIE, REFRESH_COOKIE, FIRST_LOOK_COOKIE))
        html = page(
            "index.html", days=DAYS, templates=TEMPLATES, message=message, paste_message=paste_message, form=form,
            has_login=has_login, send_hours=SEND_TIMES,
        )
        return HTMLResponse(html, status_code=status_code)

    def chosen_list(request: Request) -> int | None:
        """The list the reader is on (?list=ID). A list that isn't theirs falls back to their first one."""
        text = request.query_params.get("list", "")
        return int(text) if text.isdigit() and len(text) < 19 else None

    def current_reader(request: Request) -> Reader:
        """The signed-in reader, renewing an expired login, or a brand-new reader's first look."""
        d = get_deps()
        access, refresh = request.cookies.get(ACCESS_COOKIE), request.cookies.get(REFRESH_COOKIE)
        user = d.auth.get_user(access) if access else None
        renewed = None
        if user is None and refresh:
            try:
                renewed = d.auth.refresh(refresh)
                user = renewed.user
            except AuthRefused:
                pass  # The login has ended; treat them as signed out.
        if user is not None:
            profile = db.get_profile(d.conn, user.id, chosen_list(request))
            return Reader(profile, signed_in=True, user_id=user.id, email=user.email, renewed=renewed)
        token = request.cookies.get(FIRST_LOOK_COOKIE)
        profile = db.profile_for_first_look(d.conn, token, d.clock()) if token else None
        return Reader(profile, first_look=profile is not None)

    def list_query(reader: Reader) -> str:
        """"?list=ID" for any list but the reader's first, so their first keeps the plain address."""
        p = reader.profile
        if p is None or p.list_id is None or not p.lists or p.list_id == p.lists[0][0]:
            return ""
        return f"?list={p.list_id}"

    def finish(response, reader: Reader):
        if reader.renewed:
            set_session_cookies(response, reader.renewed)
        return response

    @app.get("/", response_class=HTMLResponse)
    def index(request: Request):
        return start_page(request)

    @app.post("/subscribers")
    def add_subscriber(
        request: Request,
        name: str = Form(""),
        email: str = Form(""),
        password: str = Form(""),
        interests: str = Form(""),
        days: list[str] = Form([]),
        send_hour: str = Form(""),
    ):
        chosen = [day for day in days if day in DAYS]
        hour = send_hour_from(send_hour)
        typed = {"interests": interests, "name": name, "email": email, "days": chosen, "send_hour": hour}
        if not name.strip() or not email.strip() or not interests.strip() or not chosen or not password:
            missing = "Please fill in a name, an email, a password, what kind of event, and at least one day."
            return start_page(request, missing, 400, form=typed)
        if len(password) < MIN_PASSWORD:
            return start_page(request, f"Please use a password of at least {MIN_PASSWORD} characters.", 400, form=typed)
        # Counted only once the form is complete, so fixing a typo doesn't use up anyone's tries.
        if over_limit(request, "sign_up"):
            return too_many(request)
        d = get_deps()
        try:
            user = d.auth.sign_up(email.strip(), password)
        except AuthRefused as refused:
            problem = {
                "weak_password": "Please choose a longer or less common password.",
                "email_address_invalid": "That email doesn't look right. Please check it.",
            }.get(refused.code, "We couldn't make your account. Please check your email and password.")
            return start_page(request, problem, 400, form=typed)
        except AuthUnavailable:
            return start_page(request, AUTH_DOWN, 503, form=typed)
        token = user and db.create_profile(
            d.conn, user.id, name.strip(), interests.strip(), "", chosen, d.clock(), send_hour=hour
        )
        if not token:
            # The email already has an account. Say the same thing either way, and change nothing.
            return notice("Check your email", CHECK_EMAIL, [("/signin", "Sign in"), ("/forgot", "Reset my password")])
        response = RedirectResponse("/newsletter", status_code=303)
        response.set_cookie(FIRST_LOOK_COOKIE, token, max_age=int(db.FIRST_LOOK.total_seconds()), **COOKIE_FLAGS)
        return response

    @app.get("/signin", response_class=HTMLResponse)
    def signin_page():
        return page("signin.html", message="", email="")

    @app.post("/signin")
    def signin(request: Request, email: str = Form(""), password: str = Form("")):
        limiter = get_deps().limiter
        if over_limit(request, "sign_in"):
            return too_many(request)
        # Reserve a wrong-password try before asking Supabase, so guesses sent at the same moment are
        # all counted. It's given back below unless the password really was wrong.
        reserved = []
        for key in failed_sign_in_keys(request, email):
            if not limiter.allow(*key):
                for taken in reserved:
                    limiter.refund(*taken)
                return too_many(request)
            reserved.append(key)
        try:
            session = get_deps().auth.sign_in(email.strip(), password)
        except AuthRefused as refused:
            if refused.code != "invalid_credentials":
                for key in reserved:
                    limiter.refund(*key)
            message = (
                "Please confirm your email first: use the link we sent when you signed up."
                if refused.code == "email_not_confirmed"
                else "Wrong email or password."
            )
            return HTMLResponse(page("signin.html", message=message, email=email), status_code=400)
        except AuthUnavailable:
            for key in reserved:
                limiter.refund(*key)
            return HTMLResponse(page("signin.html", message=AUTH_DOWN, email=email), status_code=503)
        for key in reserved:
            limiter.refund(*key)
        response = RedirectResponse("/newsletter", status_code=303)
        set_session_cookies(response, session)
        return response

    @app.post("/signout")
    def signout(request: Request):
        access = request.cookies.get(ACCESS_COOKIE)
        if access:
            try:
                get_deps().auth.sign_out(access)
            except AuthUnavailable:
                pass  # The cookies still go; the login on Supabase's side expires on its own.
        response = RedirectResponse("/", status_code=303)
        clear_session_cookies(response)
        return response

    @app.get("/newsletter", response_class=HTMLResponse)
    def newsletter(request: Request, make: str = ""):
        # make=1 is the request that may make a new list; plain /newsletter answers quickly either way.
        if over_limit(request, "newsletter"):
            return too_many(request)
        try:
            reader = current_reader(request)
        except AuthUnavailable:
            return notice("Please try again", AUTH_DOWN, [("/", "Back to the start")], 503)
        q = list_query(reader)
        make_url = f"/newsletter{q}{'&' if q else '?'}make=1"
        if reader.renewed:
            # Supabase refresh tokens work once, so store the new ones before the slow AI call.
            return finish(RedirectResponse(make_url if make else f"/newsletter{q}", status_code=303), reader)
        if reader.profile is None:
            # Signed in but no preferences saved yet: ask for them.
            return finish(RedirectResponse("/preferences" if reader.signed_in else "/signin", status_code=303), reader)
        d = get_deps()
        subscriber = reader.profile
        now = d.clock()
        latest = db.latest_issue(d.conn, subscriber.id, subscriber.list_id)
        if not saved_lists.needs_new_list(latest, subscriber, now):
            return finish(dashboard(request, reader, saved_lists.saved_picks(d.conn, latest), latest.made_at), reader)
        if not make:
            # Show "Making your list…" right away, and only now that a list really is being made.
            # The page goes straight on to MAKE_LIST, which does the slow AI work below.
            # It sends nothing to PostHog, so each list visit still counts as one page view.
            return finish(HTMLResponse(_page("making_page.html", next_url=make_url)), reader)
        # Only making a new list calls the AI, so only that counts toward the reader's limit.
        if over_account_limit("newsletter_account", subscriber.id):
            return too_many(request)
        try:
            picks = saved_lists.make_list(d.conn, d.ai, subscriber, now)
        except AIUnavailable:
            # Their last list, if any, still shows under the message.
            old = saved_lists.saved_picks(d.conn, latest) if latest else []
            return finish(dashboard(request, reader, old, latest.made_at if latest else now, error=AI_BUSY), reader)
        return finish(dashboard(request, reader, picks, now), reader)

    def dashboard(request: Request, reader: Reader, picks, made_at, error: str | None = None) -> HTMLResponse:
        """The website version of the list: today's picks, saved events, and events to rate."""
        d = get_deps()
        now, user_id = d.clock(), reader.profile.id
        saved_ids, rated, history = db.dashboard_data(d.conn, user_id)
        events = db.events_by_ids(d.conn, list(set(saved_ids) | history))
        saved = [card(events[i], now=now) for i in saved_ids
                 if i in events and events[i].score.eligible and not events[i].event.is_over(now)]
        went = sorted((events[i] for i in history if i in events and events[i].event.is_over(now)),
                      key=lambda e: e.event.start, reverse=True)[:12]
        base = str(request.base_url).rstrip("/")
        groups = [(title, [{**card(p.event, p.reason, now), "extra": p.extra} for p in group])
                  for title, group in dashboard_groups(picks, now)]
        html = page(
            "dashboard.html",
            subscriber=reader.profile,
            banner=SIGNED_UP_BANNER if reader.first_look else "",
            error=error,
            list_day=made_at.astimezone(NY).strftime("%A, %B %-d"),
            groups=groups,
            pick_count=sum(1 for _, cards in groups for c in cards if not c["extra"]),
            extra_count=sum(1 for _, cards in groups for c in cards if c["extra"]),
            saved=saved,
            to_rate=[card(e, now=now) for e in went],
            saved_ids=set(saved_ids),
            ratings=rated,
            share_base=base,
            list_query=list_query(reader),
            lists=reader.profile.lists,
            list_id=reader.profile.list_id,
            can_add_list=reader.signed_in and len(reader.profile.lists) < db.MAX_LISTS,
        )
        return HTMLResponse(html)

    def event_on_readers_lists(reader: Reader, event_id: int):
        """The event, if it was on one of this reader's lists; otherwise None."""
        d = get_deps()
        if event_id not in db.history_event_ids(d.conn, reader.profile.id):
            return None
        return db.events_by_ids(d.conn, [event_id]).get(event_id)

    def from_page_script(request: Request) -> bool:
        """Sent by the dashboard's own buttons, which update the page in place instead of reloading it."""
        return request.headers.get("x-requested-with") == "fetch"

    def not_on_your_lists(reader: Reader) -> HTMLResponse:
        return finish(notice("Not found", "That event isn't on your lists.", [("/newsletter", "Back to my list")], 404), reader)

    @app.post("/events/{event_id}/save")
    def save_event_for_later(request: Request, event_id: int):
        reader = listed_reader(request)
        if not isinstance(reader, Reader):
            return reader
        if over_limit(request, "event_actions"):
            return finish(too_many(request), reader)
        d = get_deps()
        # The list history alone answers this (the events table guards that the event exists).
        if event_id not in db.history_event_ids(d.conn, reader.profile.id):
            return not_on_your_lists(reader)
        try:
            db.save_for_later(d.conn, reader.profile.id, event_id, d.clock())
        except psycopg.errors.ForeignKeyViolation:
            return not_on_your_lists(reader)  # on an old list, but the event itself is gone
        if from_page_script(request):
            return finish(JSONResponse({"saved": True}), reader)
        return finish(RedirectResponse(f"/newsletter#event-{event_id}", status_code=303), reader)

    @app.post("/events/{event_id}/unsave")
    def unsave_event(request: Request, event_id: int):
        reader = listed_reader(request)
        if not isinstance(reader, Reader):
            return reader
        if over_limit(request, "event_actions"):
            return finish(too_many(request), reader)
        db.unsave(get_deps().conn, reader.profile.id, event_id)
        if from_page_script(request):
            return finish(JSONResponse({"saved": False}), reader)
        return finish(RedirectResponse(f"/newsletter#event-{event_id}", status_code=303), reader)

    @app.post("/events/{event_id}/rate")
    def rate_event(request: Request, event_id: int, rating: str = Form("")):
        reader = listed_reader(request)
        if not isinstance(reader, Reader):
            return reader
        if over_limit(request, "event_actions"):
            return finish(too_many(request), reader)
        event = event_on_readers_lists(reader, event_id)
        if event is None:
            return not_on_your_lists(reader)
        d = get_deps()
        stars = int(rating) if rating.isdigit() else 0
        if not 1 <= stars <= 5 or not event.event.is_over(d.clock()):
            text = "Ratings go from 1 to 5, for events that have already happened."
            return finish(notice("Can't rate that", text, [("/newsletter", "Back to my list")], 400), reader)
        db.rate_event(d.conn, reader.profile.id, event_id, stars, d.clock())
        if from_page_script(request):
            return finish(JSONResponse({"rating": stars}), reader)
        return finish(RedirectResponse(f"/newsletter#rate-{event_id}", status_code=303), reader)

    def listed_reader(request: Request) -> Reader | Response:
        """A reader with preferences, signed in or on their first look; anyone else goes to sign in."""
        try:
            reader = current_reader(request)
        except AuthUnavailable:
            return notice("Please try again", AUTH_DOWN, [("/", "Back to the start")], 503)
        if reader.profile is None:
            return finish(RedirectResponse("/signin", status_code=303), reader)
        return reader

    @app.post("/newsletter/shown")
    def picks_shown(request: Request, event_ids: list[int] = Form([])):
        """Show more was tapped: those picks now count as seen, so later lists don't repeat them."""
        reader = listed_reader(request)
        if not isinstance(reader, Reader):
            return reader
        if over_limit(request, "event_actions"):
            return finish(too_many(request), reader)
        d = get_deps()
        latest = db.latest_issue(d.conn, reader.profile.id, reader.profile.list_id)
        on_list = {saved.event_id for saved in latest.picks} if latest else set()
        chosen = list(dict.fromkeys(event_ids))[:50]
        if not chosen or not set(chosen) <= on_list:
            return not_on_your_lists(reader)
        db.reveal_picks(d.conn, reader.profile.id, chosen, reader.profile.list_id)
        return finish(JSONResponse({"ok": True}), reader)

    @app.post("/newsletter/more")
    def more_like_these(request: Request, event_ids: list[int] = Form([])):
        if over_limit(request, "newsletter"):
            return too_many(request)
        reader = listed_reader(request)
        if not isinstance(reader, Reader):
            return reader
        d = get_deps()
        chosen = list(dict.fromkeys(event_ids))[:10]
        if not chosen:
            return finish(notice("Pick an event", "Tick at least one event you'd like more of.", [("/newsletter", "Back to my list")], 400), reader)
        if not set(chosen) <= db.history_event_ids(d.conn, reader.profile.id):
            # Only events from the reader's own lists can steer their next list.
            return finish(notice("Not found", "That event isn't on your lists.", [("/newsletter", "Back to my list")], 404), reader)
        if over_account_limit("newsletter_account", reader.profile.id):
            return finish(too_many(request), reader)
        try:
            saved_lists.make_list(d.conn, d.ai, reader.profile, d.clock(), more_like_ids=chosen)
        except AIUnavailable:
            return finish(notice("Please try again", AI_BUSY, [("/newsletter", "Back to my list")], 503), reader)
        back = f"/newsletter{list_query(reader)}"
        if from_page_script(request):
            # The page then says "Opening your list…" instead of still "Making your list…".
            return finish(JSONResponse({"next": back}), reader)
        return finish(RedirectResponse(back, status_code=303), reader)

    def signed_in_reader(request: Request) -> Reader | Response:
        """The signed-in reader, or the response that sends anyone else to sign in."""
        try:
            reader = current_reader(request)
        except AuthUnavailable:
            return notice("Please try again", AUTH_DOWN, [("/", "Back to the start")], 503)
        if not reader.signed_in:
            # A just-signed-up reader confirms their email first; the banner on their list says so.
            return RedirectResponse("/signin", status_code=303)
        return reader

    def preferences_page(reader: Reader, form: dict, message: str = "", status_code: int = 200) -> HTMLResponse:
        p = reader.profile
        html = page("preferences.html", days=DAYS, form=form, message=message, send_hours=SEND_TIMES,
                    list_query=list_query(reader), several=bool(p and len(p.lists) > 1))
        return finish(HTMLResponse(html, status_code=status_code), reader)

    @app.get("/preferences", response_class=HTMLResponse)
    def preferences(request: Request):
        reader = signed_in_reader(request)
        if not isinstance(reader, Reader):
            return reader
        p = reader.profile
        form = {"name": "", "title": "", "interests": "", "days": [], "send_hour": 8, "email_on": True}
        if p:
            hour, on = db.email_settings(get_deps().conn, p.id, p.list_id)
            form = {"name": p.name, "title": p.title, "interests": p.interests, "days": p.days, "send_hour": hour,
                    "email_on": on}
        return preferences_page(reader, form)

    @app.post("/preferences")
    def save_preferences(
        request: Request,
        name: str = Form(""),
        title: str = Form(""),
        interests: str = Form(""),
        days: list[str] = Form([]),
        send_hour: str = Form(""),
        email_on: str = Form(""),
    ):
        reader = signed_in_reader(request)
        if not isinstance(reader, Reader):
            return reader
        chosen = [day for day in days if day in DAYS]
        hour, wants_email = send_hour_from(send_hour), bool(email_on)
        form = {"name": name, "title": title, "interests": interests, "days": chosen, "send_hour": hour,
                "email_on": wants_email}
        if not name.strip() or not interests.strip() or not chosen:
            message = "Please fill in a name, what kind of event, and at least one day."
            return preferences_page(reader, form, message, 400)
        d = get_deps()
        values = (name.strip(), interests.strip(), "", chosen, d.clock())
        list_id = reader.profile.list_id if reader.profile else None
        if reader.profile is not None:
            db.update_profile(d.conn, reader.profile.id, *values, list_id=list_id,
                              title=title.strip()[:MAX_TITLE] or None)
        else:
            db.create_profile(d.conn, reader.user_id, *values)
        db.set_email_settings(d.conn, reader.user_id, hour, wants_email, list_id)
        return finish(RedirectResponse(f"/newsletter{list_query(reader)}", status_code=303), reader)

    def new_list_page(reader: Reader, form: dict, message: str = "", status_code: int = 200) -> HTMLResponse:
        html = page("new_list.html", days=DAYS, form=form, message=message, send_hours=SEND_TIMES,
                    templates=TEMPLATES)
        return finish(HTMLResponse(html, status_code=status_code), reader)

    @app.get("/lists/new", response_class=HTMLResponse)
    def new_list(request: Request):
        reader = signed_in_reader(request)
        if not isinstance(reader, Reader):
            return reader
        if reader.profile is None:
            return finish(RedirectResponse("/preferences", status_code=303), reader)
        return new_list_page(reader, {"title": "", "interests": "", "days": [], "send_hour": 8})

    @app.post("/lists/new")
    def add_list(
        request: Request,
        title: str = Form(""),
        interests: str = Form(""),
        days: list[str] = Form([]),
        send_hour: str = Form(""),
    ):
        reader = signed_in_reader(request)
        if not isinstance(reader, Reader):
            return reader
        if reader.profile is None:
            return finish(RedirectResponse("/preferences", status_code=303), reader)
        chosen = [day for day in days if day in DAYS]
        hour = send_hour_from(send_hour)
        form = {"title": title, "interests": interests, "days": chosen, "send_hour": hour}
        if not title.strip() or not interests.strip() or not chosen:
            message = "Please give the list a name, say what kind of events, and pick at least one day."
            return new_list_page(reader, form, message, 400)
        d = get_deps()
        new_id = db.add_list(d.conn, reader.user_id, title.strip()[:MAX_TITLE], interests.strip(), chosen, hour,
                             d.clock())
        if new_id is None:
            message = f"You can keep up to {db.MAX_LISTS} lists. Delete one on its preferences page to add another."
            return new_list_page(reader, form, message, 400)
        return finish(RedirectResponse(f"/newsletter?list={new_id}", status_code=303), reader)

    @app.post("/lists/delete")
    def remove_list(request: Request):
        reader = signed_in_reader(request)
        if not isinstance(reader, Reader):
            return reader
        if reader.profile is not None and reader.profile.list_id is not None:
            db.delete_list(get_deps().conn, reader.user_id, reader.profile.list_id)
        return finish(RedirectResponse("/newsletter", status_code=303), reader)

    def email_version(request: Request, reader: Reader) -> tuple[str | None, str]:
        """The reader's latest list as the compact email, and its date. None when they have no list yet."""
        d = get_deps()
        latest = db.latest_issue(d.conn, reader.profile.id, reader.profile.list_id)
        if latest is None:
            return None, ""
        site = str(request.base_url).rstrip("/")
        html = render_newsletter(saved_lists.saved_picks(d.conn, latest), now=latest.made_at, site_url=site)
        return html, latest.made_at.astimezone(NY).strftime("%A, %B %-d")

    @app.get("/newsletter/email", response_class=HTMLResponse)
    def email_preview(request: Request):
        reader = listed_reader(request)
        if not isinstance(reader, Reader):
            return reader
        body, _ = email_version(request, reader)
        can_send = bool(get_deps().resend_key) and reader.signed_in
        html = page("email_preview.html", body=body, can_send=can_send, list_query=list_query(reader))
        return finish(HTMLResponse(html), reader)

    @app.post("/newsletter/email")
    def email_me(request: Request):
        d = get_deps()
        if not d.resend_key:
            return notice("Not available", "Emailing lists isn't switched on yet.", [("/newsletter", "Back to my list")], 404)
        reader = signed_in_reader(request)
        if not isinstance(reader, Reader):
            return reader
        if reader.profile is None:
            return finish(RedirectResponse("/preferences", status_code=303), reader)
        if over_account_limit("email_account", reader.profile.id):
            return too_many(request)
        body, day = email_version(request, reader)
        if body is None:
            return finish(RedirectResponse("/newsletter/email", status_code=303), reader)
        # Always the signed-in reader's own address, never one from the form.
        title = reader.profile.title
        named = "whatisupnyc" if title in ("", db.FIRST_LIST_TITLE) else title
        if not emailer.send_list(d.http, d.resend_key, reader.email, f"Your {named} list for {day}", body):
            text = "We couldn't send the email just now. Please try again in a little while."
            return finish(notice("Couldn't send", text, [("/newsletter", "Back to my list")], 502), reader)
        text = f"Sent to {reader.email}. It can take a minute to arrive."
        return finish(notice("On its way", text, [("/newsletter", "Back to my list")]), reader)

    @app.post("/api/intake")
    async def intake_events(request: Request):
        """Events another tool found, stored for the morning collection to check on their own pages."""
        d = get_deps()
        if not d.intake_hash:
            return JSONResponse({"error": "intake is not switched on"}, status_code=404)
        if over_limit(request, "intake"):
            return JSONResponse({"error": "too many requests from this address"}, status_code=429)
        given = request.headers.get("authorization", "")
        token = given[7:] if given.lower().startswith("bearer ") else ""
        if not token or not hmac.compare_digest(hashlib.sha256(token.encode()).hexdigest(), d.intake_hash):
            return JSONResponse({"error": "wrong or missing key"}, status_code=401)
        declared = request.headers.get("content-length", "")
        if declared.isdigit() and int(declared) > intake.MAX_BODY_BYTES:
            # Refused before reading, so a huge upload never sits in memory.
            return JSONResponse({"error": f"body larger than {intake.MAX_BODY_BYTES} bytes"}, status_code=413)
        body = await request.body()
        if len(body) > intake.MAX_BODY_BYTES:
            return JSONResponse({"error": f"body larger than {intake.MAX_BODY_BYTES} bytes"}, status_code=413)
        try:
            items = json.loads(body).get("events")
        except (ValueError, AttributeError):
            items = None
        if not isinstance(items, list):
            return JSONResponse({"error": "send a JSON object with an events list"}, status_code=400)
        return await run_in_threadpool(store_intake, items)

    def store_intake(items: list) -> JSONResponse:
        d = get_deps()
        now = d.clock()
        room = intake.MAX_PER_DAY - db.count_intake_today(d.conn, now)
        if room <= 0:
            return JSONResponse({"error": f"the daily limit of {intake.MAX_PER_DAY} events is reached"}, status_code=429)
        usable, rejected = [], []
        for index, item in enumerate(items):
            found, reason = intake.check(item, now)
            if found is None:
                rejected.append({"index": index, "reason": reason})
            elif len(usable) >= room:
                rejected.append({"index": index, "reason": f"over the daily limit of {intake.MAX_PER_DAY} events"})
            else:
                usable.append(found)
        accepted, updated = db.save_intake(d.conn, usable, now)
        return JSONResponse({"accepted": accepted, "updated": updated, "rejected": rejected})

    @app.get("/e/{event_id}", response_class=HTMLResponse)
    def shared_event(request: Request, event_id: int):
        """A public page for one event, for sharing with friends. Nothing about who shared it."""
        event = db.events_by_ids(get_deps().conn, [event_id]).get(event_id)
        # Only events the site would recommend; event numbers can be walked.
        if event is None or not event.score.eligible:
            return notice("Event not found", "That event isn't on whatisupnyc.", [("/", "Make your own list")], 404)
        item = card(event, now=get_deps().clock())
        # Link previews need the picture's full address.
        preview = item["image"] if item["image"].startswith("https://") else str(request.base_url).rstrip("/") + item["image"]
        return HTMLResponse(page("event.html", item=item, preview_image=preview))

    @app.get("/newsletter/{token}", response_class=HTMLResponse)
    def old_newsletter_link(token: str):
        text = "Lists now open when you sign in. If you never set a password, reset it with the email you signed up with."
        return notice("This link has moved", text, [("/signin", "Sign in"), ("/forgot", "Set or reset my password")], 410)

    @app.get("/forgot", response_class=HTMLResponse)
    def forgot_page():
        return page("forgot.html", message="")

    @app.post("/forgot")
    def forgot(request: Request, email: str = Form("")):
        if over_limit(request, "reset"):
            return too_many(request)
        try:
            # Past the per-account limit, send nothing but answer the same, so nobody sees a lockout.
            if not over_account_limit("reset_account", email):
                get_deps().auth.send_reset(email.strip())
        except AuthRefused:
            pass  # Same reply either way, so this page doesn't reveal who has an account.
        except AuthUnavailable:
            return HTMLResponse(page("forgot.html", message=AUTH_DOWN), status_code=503)
        text = "If that email has an account, we've sent it a link to set a new password. It can take a minute or two."
        return notice("Check your email", text, [("/signin", "Back to sign in")])

    def expired_link(kind: str) -> HTMLResponse:
        if kind == "recovery":
            return notice(
                "This link has expired",
                "This link has expired or was already used. Please ask for a new one.",
                [("/forgot", "Send me a new link")],
                400,
            )
        return notice(
            "This link has expired",
            "This link has expired or was already used. If you already confirmed your email, just sign in.",
            [("/signin", "Sign in"), ("/forgot", "Reset my password")],
            400,
        )

    def take_over_with_password(kind: str, token_hash: str, password: str, template: str) -> HTMLResponse:
        """Uses an emailed link: signs the reader in, sets the password they typed, and ends every
        other login on the account. A confirm link does this too, so whoever made an account with
        someone else's email loses it the moment its real owner confirms."""
        if len(password) < MIN_PASSWORD:
            message = f"Please use a password of at least {MIN_PASSWORD} characters."
            return HTMLResponse(page(template, token_hash=token_hash, message=message), status_code=400)
        d = get_deps()
        try:
            session = d.auth.verify(token_hash, kind)
        except AuthRefused:
            return expired_link(kind)
        except AuthUnavailable:
            return HTMLResponse(page(template, token_hash=token_hash, message=AUTH_DOWN), status_code=503)
        # Only the email's owner gets here, so anything someone else started on this account ends now.
        db.end_first_look(d.conn, session.user.id)
        try:
            try:
                d.auth.set_password(session.access_token, password)
            except AuthRefused as refused:
                if refused.code != "same_password":
                    raise
            d.auth.sign_out_others(session.access_token)
        except (AuthRefused, AuthUnavailable):
            # Fail closed: the link has confirmed the email, so lock out whatever password the account
            # had with one nobody knows, then end every login, this reader's included.
            for step in (
                lambda: d.auth.set_password(session.access_token, secrets.token_urlsafe(32)),
                lambda: d.auth.sign_out(session.access_token, everywhere=True),
            ):
                try:
                    step()
                except (AuthRefused, AuthUnavailable):
                    pass
            text = "We couldn't set that password. Please reset your password now to finish."
            failed = notice("Password not set", text, [("/forgot", "Send me a new link")], 400)
            clear_session_cookies(failed)
            return failed
        response = RedirectResponse("/newsletter", status_code=303)
        set_session_cookies(response, session)
        return response

    # Emailed links open a page with a form. Only the form's button uses the link's token, so a mail
    # scanner that opens links before the reader does can't use it up.
    @app.get("/reset", response_class=HTMLResponse)
    def reset_page(token_hash: str = ""):
        if not token_hash:
            return expired_link("recovery")
        return page("reset.html", token_hash=token_hash, message="")

    @app.post("/reset")
    def reset(request: Request, token_hash: str = Form(""), password: str = Form("")):
        if over_limit(request, "sign_in"):
            return too_many(request)
        return take_over_with_password("recovery", token_hash, password, "reset.html")

    @app.get("/confirm", response_class=HTMLResponse)
    def confirm_page(token_hash: str = ""):
        if not token_hash:
            return expired_link("email")
        return page("confirm.html", token_hash=token_hash, message="")

    @app.post("/confirm")
    def confirm(request: Request, token_hash: str = Form(""), password: str = Form("")):
        if over_limit(request, "sign_in"):
            return too_many(request)
        return take_over_with_password("email", token_hash, password, "confirm.html")

    @app.post("/paste", response_class=HTMLResponse)
    def paste(request: Request, link: str = Form(""), caption: str = Form("")):
        if over_limit(request, "paste"):
            return too_many(request)
        # Only saved here, so the reader never waits. The next collection run checks it.
        problem = paste_problem(link.strip(), caption)
        if not problem and db.count_waiting_pastes(get_deps().conn) >= MAX_WAITING_PASTES:
            problem = "We have a lot of posts to check right now. Please try again tomorrow."
        if not problem:
            db.save_waiting_paste(get_deps().conn, link.strip(), caption)
        message = problem or "Thanks! We'll check it and add the event if we can confirm it on a page we can check."
        if "application/json" in request.headers.get("accept", ""):
            # The start page's script sends posts this way, so the reader keeps their place in the steps.
            return JSONResponse({"added": not problem, "message": message}, status_code=400 if problem else 200)
        return start_page(request, paste_message=message)

    return app


app = create_app()
