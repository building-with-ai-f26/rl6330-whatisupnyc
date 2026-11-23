import os
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse

from app import auth, db
from app.ai import AIClient, AIUnavailable, backup_from_env
from app.limits import LIMITS, RateLimiter, client_ip
from app.models import Subscriber
from app.newsletter import _templates, choose_picks, render_newsletter
from app.sites import Sites, load as load_sites
from app.sources.pasted import paste_problem
from app.sources.web import TavilyClient
from app.supabase_auth import AuthRefused, AuthUnavailable, Session, SupabaseAuth, auth_from_env

# Keeps a flood of posts from piling up faster than the morning runs can check them.
MAX_WAITING_PASTES = 200

DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
TEMPLATES = {
    "New to NYC": "I'm new to New York and want to explore classic spots, neighborhoods, and things locals love.",
    "Free and cheap": "Free or low-cost events: outdoor shows, museum free nights, talks, and markets.",
    "Live music and comedy": "Live music and stand-up comedy at good venues, from small rooms to big shows.",
    "Art and culture": "Art openings, museum programs, film screenings, theater, and talks.",
}
AI_BUSY = "The AI model is busy right now. Your events are saved, so please try again in a little while."
AUTH_DOWN = "Sign-in is down right now, please try again in a little while."
TOO_MANY = "Too many tries from your connection. Please wait a while and try again."
CHECK_EMAIL = (
    "Check your email to confirm your account. If you already have an account with this email, "
    "sign in or reset your password instead."
)
SIGNED_UP_BANNER = "Confirm your email to come back and edit your picks."
MIN_PASSWORD = 8

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


@dataclass
class Reader:
    """Who is asking. profile is None for a visitor who isn't signed in (or has no preferences yet)."""

    profile: Subscriber | None = None
    signed_in: bool = False
    first_look: bool = False
    # A login renewed during this request, to store in new cookies.
    renewed: Session | None = None


def _default_deps() -> AppDeps:
    load_dotenv()
    conn = db.connect(db.url_from_env())
    http = httpx.Client()
    return AppDeps(
        conn=conn,
        ai=AIClient(
            http, os.environ["OPENROUTER_API_KEY"], os.environ["OPENROUTER_MODEL"], conn, backup=backup_from_env()
        ),
        tavily=TavilyClient(http, os.environ["TAVILY_API_KEY"], run_budget=100),
        http=http,
        sites=load_sites(),
        clock=lambda: datetime.now(timezone.utc),
        auth=auth_from_env(http),
        posthog_key=os.environ.get("POSTHOG_KEY", ""),
        posthog_host=os.environ.get("POSTHOG_HOST", "https://us.i.posthog.com"),
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
    cache: list[AppDeps] = [deps] if deps else []

    def get_deps() -> AppDeps:
        # Built on first use, so importing this module needs no keys or database.
        if not cache:
            cache.append(_default_deps())
        return cache[0]

    @app.middleware("http")
    async def forms_from_this_site_only(request: Request, call_next):
        # The login is in cookies, so another site's form must not be able to act as the reader.
        if not auth.sent_from_this_site(request):
            return PlainTextResponse("Forms must be sent from this site.", 403)
        return await call_next(request)

    def page(template: str, **context) -> str:
        d = get_deps()
        return _page(template, posthog_key=d.posthog_key, posthog_host=d.posthog_host, **context)

    def notice(title: str, text: str, links: list[tuple[str, str]], status_code: int = 200) -> HTMLResponse:
        return HTMLResponse(page("notice.html", title=title, text=text, links=links), status_code=status_code)

    def over_limit(request: Request, bucket: str) -> bool:
        return not get_deps().limiter.allow(bucket, client_ip(request))

    def too_many(request: Request) -> HTMLResponse | JSONResponse:
        if "application/json" in request.headers.get("accept", ""):
            return JSONResponse({"added": False, "message": TOO_MANY}, status_code=429)
        return notice("Please wait", TOO_MANY, [("/", "Back to the start")], 429)

    def start_page(
        request: Request, message: str = "", status_code: int = 200, paste_message: str = "", form: dict | None = None
    ) -> HTMLResponse:
        # message belongs to the sign-up form; paste_message shows beside the "add an event" form.
        # form holds what the reader already typed (never the password), so a rejected form doesn't make them start over.
        form = {"interests": "", "name": "", "email": "", "look_prompt": "", "days": [], **(form or {})}
        # Only a cookie check, so the start page never waits on Supabase.
        has_login = any(request.cookies.get(name) for name in (ACCESS_COOKIE, REFRESH_COOKIE, FIRST_LOOK_COOKIE))
        html = page(
            "index.html", days=DAYS, templates=TEMPLATES, message=message, paste_message=paste_message, form=form,
            has_login=has_login,
        )
        return HTMLResponse(html, status_code=status_code)

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
            return Reader(db.get_profile(d.conn, user.id), signed_in=True, renewed=renewed)
        token = request.cookies.get(FIRST_LOOK_COOKIE)
        profile = db.profile_for_first_look(d.conn, token, d.clock()) if token else None
        return Reader(profile, first_look=profile is not None)

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
        look_prompt: str = Form(""),
        days: list[str] = Form([]),
    ):
        if over_limit(request, "sign_up"):
            return too_many(request)
        chosen = [day for day in days if day in DAYS]
        typed = {"interests": interests, "name": name, "email": email, "look_prompt": look_prompt, "days": chosen}
        if not name.strip() or not email.strip() or not interests.strip() or not chosen or not password:
            missing = "Please fill in a name, an email, a password, what kind of event, and at least one day."
            return start_page(request, missing, 400, form=typed)
        if len(password) < MIN_PASSWORD:
            return start_page(request, f"Please use a password of at least {MIN_PASSWORD} characters.", 400, form=typed)
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
            d.conn, user.id, name.strip(), interests.strip(), look_prompt.strip(), chosen, d.clock()
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
        if over_limit(request, "sign_in"):
            return too_many(request)
        try:
            session = get_deps().auth.sign_in(email.strip(), password)
        except AuthRefused as refused:
            message = (
                "Please confirm your email first: use the link we sent when you signed up."
                if refused.code == "email_not_confirmed"
                else "Wrong email or password."
            )
            return HTMLResponse(page("signin.html", message=message, email=email), status_code=400)
        except AuthUnavailable:
            return HTMLResponse(page("signin.html", message=AUTH_DOWN, email=email), status_code=503)
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
    def newsletter(request: Request):
        if over_limit(request, "newsletter"):
            return too_many(request)
        try:
            reader = current_reader(request)
        except AuthUnavailable:
            return notice("Please try again", AUTH_DOWN, [("/", "Back to the start")], 503)
        if reader.profile is None:
            if reader.signed_in:
                text = "We couldn't find your preferences. Please start again to tell us what you like."
                return finish(notice("Almost there", text, [("/", "Back to the start")]), reader)
            return RedirectResponse("/signin", status_code=303)
        d = get_deps()
        subscriber = reader.profile
        context = {"subscriber": subscriber, "banner": SIGNED_UP_BANNER if reader.first_look else ""}
        try:
            picks = choose_picks(
                subscriber,
                db.eligible_events(d.conn, d.clock()),
                d.ai,
                now=d.clock(),
                sent_ids=db.sent_event_ids(d.conn, subscriber.id),
            )
        except AIUnavailable:
            return finish(HTMLResponse(page("view.html", error=AI_BUSY, body=None, event_ids=[], **context)), reader)
        html = page(
            "view.html",
            error=None,
            body=render_newsletter(picks, now=d.clock()),
            event_ids=[pick.event.id for pick in picks],
            **context,
        )
        return finish(HTMLResponse(html), reader)

    @app.post("/newsletter/sent")
    def mark_sent(request: Request, event_ids: list[int] = Form([])):
        try:
            reader = current_reader(request)
        except AuthUnavailable:
            return notice("Please try again", AUTH_DOWN, [("/", "Back to the start")], 503)
        if reader.profile is None:
            return RedirectResponse("/signin", status_code=303)
        db.mark_sent(get_deps().conn, reader.profile.id, event_ids)
        return finish(RedirectResponse("/", status_code=303), reader)

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
            # Fail closed: without the new password set, nobody stays signed in, this reader included.
            try:
                d.auth.sign_out(session.access_token, everywhere=True)
            except AuthUnavailable:
                pass
            text = "We couldn't set that password. Please ask for a new link and choose another."
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
