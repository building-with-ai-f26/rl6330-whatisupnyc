# whatisupnyc

> **One-line pitch:** For people new to New York: a short, personal list of NYC events worth going to, on the days they choose, instead of checking a dozen sites.

- **Builder:** Ryandri Lowell (`@rl6330`)
- **Product Page & Living PRD:** https://building-with-ai-f26.github.io/rl6330-whatisupnyc/
- **App:** live at https://whatisupnyc.com behind a pod password while it's tested; see the Product Page for screenshots.

## Repository map

| File / folder | Purpose |
| --- | --- |
| [`docs/index.html`](./docs/index.html) | Required public pitch, living PRD, and weekly build log. Update with actual work and evidence. |
| [`Intent.md`](./Intent.md), [`Plan.md`](./Plan.md), [`journal/`](./journal/) | Optional drafting aids. No duplicate submission is required. |
| [`AGENTS.md`](./AGENTS.md) | Local run commands, project context, and agent rules. Build 2 onward. |
| [`tests/`](./tests/) | Verification guidance. Build 4 offers Track A domain evals **or** Track B user replays. |
| [`SPONSOR_MEMO.md`](./SPONSOR_MEMO.md) | One-page recommendation backed by actual usage, full costs, and risks. Due December 14 at noon. |

[Icerynk](https://www.icerynk.com/c/fa26-tech-gb-2160-dm2-f2/content) holds the assignment instructions and submissions.

## Local setup

You need [uv](https://docs.astral.sh/uv/), free API keys from OpenRouter, Ticketmaster, and Tavily, and a site password of 16 or more characters.

```bash
git clone https://github.com/building-with-ai-f26/rl6330-whatisupnyc.git
cd rl6330-whatisupnyc
cp .env.example .env      # then add your keys and SITE_PASSWORD
uv sync
uv run python scripts/check_keys.py
uv run python -m app.pipeline    # collects events, takes about 10 minutes
uv run uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Then open http://127.0.0.1:8000 and enter your `SITE_PASSWORD` (any name works). Each newsletter has a private link, so bookmark it after you save your preferences. Run the tests with `uv run pytest`.

If your app needs API keys, use a local environment file such as `.env`. Keep private keys out of source code, browser code, screenshots, and commits. The template ignores `.env`; `.env.example` contains placeholders only.
