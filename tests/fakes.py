from app.supabase_auth import AuthRefused, AuthUnavailable, AuthUser, Session


class FakeAI:
    """Stands in for AIClient: returns queued responses and records prompts."""

    def __init__(self, *responses, error=None):
        self.responses = list(responses)
        self.error = error
        self.calls = 0
        self.prompts = []

    def complete_json(self, system, user):
        self.calls += 1
        self.prompts.append(user)
        if self.error:
            raise self.error
        return self.responses.pop(0) if self.responses else {}


class FakeTavily:
    def __init__(self, hits=()):
        self.hits = list(hits)
        self.queries = []
        self.include_domains = []

    def search(self, query, include_domains):
        self.queries.append(query)
        self.include_domains.extend(include_domains)
        return self.hits


class FakeAuth:
    """Stands in for SupabaseAuth. Accounts live in memory; each new one is also added to the local
    auth.users table, as Supabase would, so profiles can point at it."""

    def __init__(self, conn):
        self.conn = conn
        self.accounts = {}  # lowercased email -> {"id", "email", "password", "confirmed"}
        self.access = {}  # access token -> lowercased email
        self.refreshes = {}  # refresh token -> lowercased email
        self.links = {}  # token_hash -> (lowercased email, kind)
        self.down = False
        self.made = 0
        self.signups = []
        self.resets_sent = []
        self.verified = []
        self.signed_out = []
        self.others_signed_out = []

    def _up(self):
        if self.down:
            raise AuthUnavailable("down")

    def _user(self, key):
        account = self.accounts[key]
        return AuthUser(account["id"], account["email"])

    def _session(self, key):
        self.made += 1
        access, refresh = f"access-{self.made}", f"refresh-{self.made}"
        self.access[access] = key
        self.refreshes[refresh] = key
        return Session(access, refresh, self._user(key))

    # Test helpers
    def confirm(self, email):
        self.accounts[email.lower()]["confirmed"] = True

    def expire(self, access_token):
        self.access.pop(access_token, None)

    def link_for(self, email, kind):
        token_hash = f"{kind}-link-{len(self.links) + 1}"
        self.links[token_hash] = (email.lower(), kind)
        return token_hash

    # The same methods as SupabaseAuth
    def sign_up(self, email, password):
        self._up()
        self.signups.append(email)
        key = email.lower()
        if key in self.accounts:
            return None
        with self.conn.connection() as c:
            user_id = str(c.execute("insert into auth.users (email) values (%s) returning id", (key,)).fetchone()["id"])
        self.accounts[key] = {"id": user_id, "email": email, "password": password, "confirmed": False}
        self.link_for(email, "email")
        return self._user(key)

    def sign_in(self, email, password):
        self._up()
        account = self.accounts.get(email.lower())
        if not account or account["password"] != password:
            raise AuthRefused("invalid_credentials")
        if not account["confirmed"]:
            raise AuthRefused("email_not_confirmed")
        return self._session(email.lower())

    def refresh(self, refresh_token):
        self._up()
        key = self.refreshes.pop(refresh_token, None)
        if key is None:
            raise AuthRefused("refresh_token_not_found")
        return self._session(key)

    def get_user(self, access_token):
        self._up()
        key = self.access.get(access_token)
        return self._user(key) if key else None

    def send_reset(self, email):
        self._up()
        self.resets_sent.append(email)
        if email.lower() in self.accounts:
            self.link_for(email, "recovery")

    def verify(self, token_hash, kind):
        self._up()
        self.verified.append(token_hash)
        found = self.links.pop(token_hash, None)
        if not found or found[1] != kind:
            raise AuthRefused("otp_expired")
        key = found[0]
        if kind == "email":
            self.accounts[key]["confirmed"] = True
        return self._session(key)

    def set_password(self, access_token, password):
        self._up()
        self.accounts[self.access[access_token]]["password"] = password

    def sign_out(self, access_token, everywhere=False):
        self._up()
        self.signed_out.append(access_token)
        key = self.access.pop(access_token, None)
        if everywhere and key:
            self.access = {t: k for t, k in self.access.items() if k != key}
            self.refreshes = {t: k for t, k in self.refreshes.items() if k != key}

    def sign_out_others(self, access_token):
        self._up()
        self.others_signed_out.append(access_token)
        key = self.access[access_token]
        for token in [t for t, k in self.access.items() if k == key and t != access_token]:
            del self.access[token]
        for token in [t for t, k in self.refreshes.items() if k == key]:
            del self.refreshes[token]


class ChoosingAI:
    """Stands in for AIClient like the real model: picks the first few events it is offered."""

    def __init__(self, how_many=2):
        self.how_many = how_many
        self.calls = 0
        self.offered = []  # the event names offered on each call
        self.error = None

    def complete_json(self, system, user):
        import json

        self.calls += 1
        if self.error:
            raise self.error
        events = json.loads(user.split("Events: ", 1)[1])
        self.offered.append([event["name"] for event in events])
        return {"picks": [{"id": e["id"], "reason": f"Fits: {e['name']}"} for e in events[: self.how_many]]}
