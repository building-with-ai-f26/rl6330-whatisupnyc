# NYC Events Newsletter: Design

**Date:** 2026-11-02
**Status:** Approved by the builder on 2026-11-02
**Context:** Class project for NYU Stern's Building with AI. Build 1 is due Monday, November 9, 2026 at noon New York time, with a two-minute demo on November 10.
**Product name:** whatisupnyc, from the builder's domain, whatisupnyc.com.

Items marked **[my call]** were chosen by the design author and have not been explicitly confirmed by the builder. Everything else came from the builder.

## 1. Intent

New York has a lot to do, but the information is scattered across many sources. Even on platforms like Secret NYC and Luma, many events are not right for a given person. This product sends each subscriber a curated newsletter of NYC events based on their own preferences, so they get a short list they trust instead of checking many sites.

## 2. Target user

NYU classmates and friends around the city. Most are not from New York, so it is a new city for them to explore. The builder believes this about classmates but has not confirmed it, and should ask 5 classmates how they found the last event they attended.

## 3. Goals

- A person can describe what they want in one bar ("what kind of event?") or start from a template.
- They choose which days they receive the newsletter.
- They can use a prompt to shape how the newsletter looks, while it stays beginner-friendly.
- Every event shown is real, checked, credible, and linked to a reliable page.
- Reputable events rank above unknown ones.
- Event collection is automatic, with no hand-curated event list.
- Everything is free to run and free to subscribers.

**Success signal [my call]:** a subscriber attends an event they would not otherwise have found. Track opens, clicks, events attended, and whether they would pay $5 a month.

## 4. Non-goals

**[my call, for PRD v1]**

- Multiple cities. NYC only.
- Full design control. Prompts change simple things such as length, tone, and categories, not layout.
- Selling tickets or social features. The newsletter links out to the event's page.

## 5. Anti-goals

The product has failed, even if it technically works, if:

- It shows an event the AI invented, or a wrong date, venue, or link.
- It fills issues with weak picks to reach a set length.
- Its setup is too complicated for a beginner.
- It tells readers an event came from TikTok, Instagram, RedNote, or Luma because of how it was discovered (see section 7 for the Luma exception).

## 6. User experience

1. **Start page:** one bar asking "what kind of event?", plus templates for people who do not know how to start.
2. **Preferences:** the person picks their delivery days and can add a simple prompt about how the newsletter should look.
3. **Newsletter:** a short list of picks. Each has an event name, date, venue, one-line reason it fits, and a link to a reliable page.
4. **Feedback [my call]:** a one-click "not for me" button on each pick, used to improve later picks.

If there are too few good new events for a chosen day, the issue is shorter or says so instead of padding **[my call]**.

## 7. Event sources

| Source | Access | Status |
|---|---|---|
| Ticketmaster Discovery API | Official API, free key | Automatic. Must follow Ticketmaster's branding and linking terms. |
| NYC Open Data, permitted events (dataset `tvpp-9vvx`) | Official city data | Automatic. May lack event pages, so each event needs a link found for it. |
| Public web pages via Tavily search: Luma, Secret NYC, Time Out, doNYC, The Skint, NYC Parks, Eventbrite, and museum, library, and venue calendars | Search and fetching public pages | Automatic. Check each site's terms first and drop any that do not allow this. |
| Instagram | Official Hashtag Search API (30 hashtags per rolling 7 days) | Automatic once Meta approves it. Needs a Business or Creator account and app review. Approval may be slow or denied. |
| TikTok and RedNote | Pasted links | The builder or a subscriber pastes in a post. |
| Meetup | Official API | Later, only if free access is enough. |

**Not allowed:** logging in with bot accounts, proxies, getting around anti-bot blocks, or scraping tools that use a platform's internal API. Build 1 must not depend on Instagram approval, and the product must work without any social source.

**Site terms [my call]:** a site is dropped if its terms prohibit automated access or if its robots rules disallow the pages we need. Terms that say nothing about it count as allowed. Getting a page through Tavily does not change this. If this leaves mostly Ticketmaster and city data, the plan stops and reports it to the builder, because the product would skew toward big ticketed shows.

**Social finds:** the person pastes the post's link and its caption or a short description **[my call]**, because a plain fetch of a post may return little. AI reads that text and pulls out the event name, date, and venue. Screenshots and video-only posts are out of scope for Build 1 unless the person types the details. The app then looks for a reliable page for that event and checks the details there. The newsletter shows and links only that page. If no reliable page is found, the event is dropped. The product does not misrepresent how it works in class write-ups or the sponsor memo.

**Luma exception:** a public Luma event page can be the page a newsletter links to and can pass the checks in section 8, as the builder confirmed, when the event has no better page. It does not count as credibility on its own (see section 9).

## 8. How one newsletter gets made

1. **Collect:** on a schedule, the app gathers candidate events from the sources. Official APIs supply structured records. For web pages, code first reads any event data embedded in the page (schema.org markup). Where there is none, AI extracts the name, date, and venue, with many pages batched into one request to save the daily limit **[my call]**.
2. **Check:** plain code, not AI, confirms the following. Anything that fails is dropped and logged with the reason.
   - The event's page opens.
   - The date and venue on the page match the collected details. AI-extracted values must appear in the page text.
   - The event is in NYC, has not happened yet, and is not a duplicate.
   - **API exception [my call]:** a record from an official API counts as confirmed for date and venue, because that page may refuse plain requests or load its details with JavaScript. Its link is still tested where the site allows it. No headless browsers or block workarounds. Web pages that cannot be read are dropped.
3. **Score credibility:** see section 9.
4. **Choose:** AI receives the person's preferences and only the events that passed, then picks the best matches and writes a one-line reason for each.
5. **Confirm:** code verifies every pick came from the checked list. Any pick that did not is removed. The AI cannot invent an event.
6. **Deliver:** in Build 1, the app shows the finished newsletter and the builder copies it into an email from their own account.

The builder does not review each issue. Automated checks can miss things such as sold-out shows or outdated pages, so test them on a sample of events and record the error rate in the build log.

## 9. Credibility and interest scoring

Events are not weighted equally. Events from popular brands, known organizers, and reputable series rank higher. A new event that nobody knows gets little weight. Every event needs some credibility and good social sentiment.

**Signals [my call for the points and exact definitions]:**

| Signal | Points | How it is measured |
|---|---|---|
| Organizer or brand reputation | up to 40 | Organizer or venue is on a reputable list, or has several past events covered by reliable sources |
| Independent coverage | up to 30 | Number of separate reliable sources mentioning this event |
| Interest | up to 20 | Available signs of demand, such as Instagram engagement once approved, or the number of pasted posts about it |
| Social sentiment | up to 10 | AI rates available posts or coverage as positive, mixed, or negative |

**Rules [my call]:**

- An event needs at least one credibility signal: reputation or independent coverage. With neither, it is excluded.
- Clearly negative sentiment excludes an event.
- Unknown sentiment does not exclude an event, as the builder confirmed. It earns no sentiment points and the system must not guess a sentiment.
- Scoring stays on the backend, as the builder confirmed. Readers never see scores, sentiment ratings, or an "unknown" label.
- The AI chooses for fit, considering higher-scored events first. When two events fit equally, the higher score wins.
- Score once per event and reuse it, to save free AI requests.
- Plain code adds up the points, so the ranking is explainable and repeatable.

**Reputable list [my call]:** a short starting list of organizers and venues in a file, separate from the event list. It is not hand-curating events, but it does take a small amount of setup.

**What counts as reliable [my call]:** editorial sources such as Time Out, Secret NYC, doNYC, and The Skint, plus official venue, organizer, and city sites. Open listing platforms where anyone can post, such as Luma and Eventbrite, do not count as coverage. So a Luma-only event from an organizer not on the reputable list is excluded.

**Brand:** a brand counts toward organizer or brand reputation, as the builder confirmed. A brand's name on a page is not enough **[my call]**. Its involvement must be confirmed on the brand's official site or by a reliable source.

**How sentiment is checked [my call]:**

1. On shortlisted events only, gather what people outside the event have said about it, its past editions, or its organizer: editorial reviews and write-ups found through Tavily, pasted posts, and Instagram captions once approved.
2. Exclude the event's own page, ticket listings, and posts by the organizer, because those are marketing.
3. AI rates the text as positive, mixed, or negative, and must quote the words behind its rating. Code confirms each quote appears in the gathered text and discards the rating if it does not.
4. With fewer than two separate outside sources, the sentiment is "unknown".
5. Test the ratings against about 20 examples the builder labels by hand, and record the accuracy in the build log.

This measures public reaction in available text. It is not a full reading of social media.

**Saving search credits [my call]:** coverage is measured first from pages already collected. Extra Tavily searches run only on a shortlist, within a set budget per run.

**Known limits:**

- Social data will be thin at first, so sentiment and interest will have little influence until Instagram access is approved.
- Favoring established events will reduce small insider finds, which is the tradeoff the builder chose.

## 10. Tools

All of these are **[my call]**, because the builder asked for simple defaults.

| Part | Choice | Notes |
|---|---|---|
| App | Python, FastAPI, plain HTML page | Few moving parts and readable |
| Saved data | SQLite | One file, no account |
| AI model | An OpenRouter free model, easy to swap | Reportedly 50 requests a day, 20 a minute, for accounts with under $10 in purchases. Free models can slow down or disappear. |
| Web search | Tavily free tier | Reportedly 1,000 credits a month |
| Email | Builder's own email for Build 1, later Resend | Resend's free plan allows 100 emails a day. The builder's domain, whatisupnyc.com, can be the verified sending domain. |
| Analytics | PostHog from Build 2 | Required by the class |
| Hosting | Local until Build 5, then to be decided | A free host may lose saved data on restart |

**Constraints:**

- Free limits fit a small class group, not a large subscriber list. Plain code does the checks to save AI requests.
- Do not send names or email addresses to free AI models, because how they handle data is unconfirmed.
- Secrets stay in `.env`. Add new variables to `.env.example`. `OPENROUTER_API_KEY` is already set in `.env`, but has not been tested.
- Confirm all limits and terms on each provider's own site before relying on them, because most figures here came from third-party pages.
- Follow `AGENTS.md`, including small batches and a plan before multi-file changes.

## 11. Build 1 scope

Build 1 is a rough local prototype that serves real people **[my call]**. Because the app runs locally, the builder enters preferences on behalf of about 5 classmates or friends:

- The start page with the bar, templates, day picker, and the simple "look" prompt. Each person's name, email, and preferences are saved, with no automatic sending.
- Automatic collection from Ticketmaster and at least one other source whose terms allow it.
- The check, score, and choose steps, producing each person's newsletter on screen.
- A pasted-link box with a caption field for social finds.
- The builder sends issues from their own email.

The "not for me" button waits until the app is hosted, because a link in an email cannot reach a local app. In Build 1, people reply by email.

Build 1 also requires, outside this app: the public Product Page, PRD v1, a screenshot or GIF, an honest build log with costs and hours, commits on at least two separate days each week, and a PostHog account (installed in Build 2). The official requirements are in Icerynk. The repo must be public and named `<netid>-<project-slug>`. The project stays in this repo, renamed from `rl6330-sandbox` to `rl6330-whatisupnyc` **[my call for the exact slug]**, before the Product Page URL is submitted. The local Git remote and the GitHub Pages address change with the rename.

## 12. Left to the builder

- Module and file layout, database tables, and collection schedule.
- Which free model to use, chosen by testing pick quality.
- Number of templates and picks per issue.
- Which hashtags to search once Instagram is approved.
- Which of Build 4's Track A or Track B to follow.
