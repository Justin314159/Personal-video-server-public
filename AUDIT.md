# Recommender audit (as of 1.4.0)

Written before changing anything, to ground the 1.5.0 recommender rewrite.
File references are to `backend/homelib/`.

## 1. How feed candidates are gathered

`recommender.candidate_queries()` builds at most 6 searches (+1 Shorts search)
per profile, and `fetch_candidates()` runs each as a yt-dlp flat search
(`ytlib.YouTube.search`: `ytsearchN:<query>`, `extract_flat="in_playlist"`, no
download). Results go into `reco_cache` (one row per profile + video, JSON
metadata, `source` label).

How the queries are built:

| Slot | Query | Source label |
|---|---|---|
| up to 2 | the top 4 positive keywords, paired: `"kw1 kw2"`, `"kw3 kw4"` | `topic:` |
| up to 2 | the names of the 2 highest-weighted channels | `channel:` |
| 1 | one keyword from the "long tail" (keywords ranked 7–14) | `explore:` |
| the rest (always ≥ 1) | **starter topics**, rotating daily | `starter:` |
| +1 | the top keyword again, for Shorts | `shorts:` |

The default starter topics are `home cooking, woodworking, japan travel, space
science, guitar lessons`. A reload (`fresh=True`) rotates the keyword pairing and
fetches 20–50 results per query instead of 10, then replaces the profile's pool.

## 2. How ranking works, and where keywords/tags live

* **Keywords** are extracted from the title (+ YouTube tags when known) by
  `text.keywords()` (stop-word filtered words, max 12). They are stored as JSON
  on `videos.keywords`, on every `events.keywords` row, and inside
  `reco_cache.meta`.
* **Taste profile** (`build_profile`): every event adds
  `weight × 0.5^(age/half_life) / sqrt(#keywords)` to each of its keywords and
  `weight × decay` to its channel. Event weights (`library.EVENT_WEIGHTS`):
  finish +1, like +1.5, dislike −2, delete_unwatched −1, skip −1, search +0.2.
  Accumulation is **linear** (10 finishes = 10× the weight of one).
* **Score** (`score`):
  `(1 − exploration) × tanh(familiar / 3) + exploration × novelty + exploration × 0.3 × daily_jitter`,
  where `familiar = (1 − loyalty) × topic + loyalty × channel` and
  `novelty` = share of the candidate's keywords the profile has never seen.
* The feed is the top 40 by score. There is **no per-topic cap and no
  diversity step**.
* Optional `embeddings.py` (sentence-transformers, off by default) adds a
  "similar titles" term; it needs PyTorch and isn't installed on the server.

## 3. The manual editor and the sliders (Settings → Your algorithm)

Stored per profile in `settings(scope='profile:<id>', key='algo')`:

* **Boost** a keyword or channel (`boosts`, default +2, range ±5): added to the
  learned weight. "Add a topic to boost" creates a keyword boost even with no
  history.
* **Mute** a keyword or channel (`mutes`): any candidate containing it is dropped.
* **Forget** (`forgotten`): the term is ignored when building the profile.
* **Starter topics** (`starter_topics`): searched for exploration / cold start.
* **History**: list of events, each deletable. Export JSON, Reset.
* Feed card menu: *Not interested* (= dislike, −2), *More about "kw"* (boost),
  *Never show channel* (mute).

Sliders (three, plus a toggle):

| Slider | Setting | Controls today |
|---|---|---|
| Memory | `half_life_days` 7–90 | decay of all events |
| **Exploration** | `exploration` 0–1 (default 0.2) | weight of novelty vs familiarity in the score |
| Channel loyalty | `channel_loyalty` 0–1 | channel vs keyword share of "familiar" |
| Include Shorts in For you | toggle | whether Shorts appear in the feed |

The Exploration slider is the one that will control the new exploration share.
Memory and Channel loyalty keep their meaning (decay half-life; how much the
channel matters).

## 4. Watch signals recorded per profile

* `watch_state` (per profile + video): `position`, `duration`, `watched`
  (set at ≥ 90 % or when the video ends), `rating` (1/−1/0), `speed`,
  `updated_at`.
* `events` (per profile): `finish` (first time `watched` flips to 1), `like`,
  `dislike` (also "Not interested" on feed cards, via `rate_candidate`),
  `delete_unwatched` (you deleted something you hadn't finished), `skip`
  (left within 2 s: the player and the Shorts feed), `search`, and a neutral
  `deleted` log row.
* **Not recorded**: rewatches (a second finish logs nothing), abandoning a long
  video part-way (only the 2-second skip exists; `position` is there but unused),
  "more/less like this", subscriptions.

## 5. Transcripts

Captions are fetched with every video download (`[downloads] subtitles = true`),
cleaned to WebVTT in `library/subs/` and listed in the `subtitles` table. They
power CC and the transcript view only; the recommender never reads them. They
are deleted together with the video. Nothing is fetched for feed candidates.

## 6. Morning automatic reload and manual reload

* `scheduler.Scheduler` loops every 30 s. When `[recommender] nightly_time`
  (03:30) has passed since `last_nightly`, it runs `nightly()`: set aside what
  every feed was showing (`reco_seen`, 30 days), re-fetch every profile's pool,
  pre-rank. If the pool is empty (first start, offline) it retries every 30 min.
* The reload button (`POST /api/foryou/refresh`) does the same for one feed
  (or both profiles for "Both of us"), **synchronously inside the HTTP request**.
* If every search fails, the old pool is kept (good); a partial failure replaces
  the pool with whatever came back.

## 7. Could the shared queue or the other profile leak into my feed?

The "Just me" feed only reads `reco_cache` rows for my profile and only my own
events, so there's **no direct leak** of the other profile's history. Indirect
paths found:

1. **Starter topics (most likely cooking cause).** `"home cooking"` is a
   default starter topic and at least one starter query runs on *every* refresh
   (usually 2–3, because the topic slots only fill with enough history). Those
   results are "new" to me, so the exploration term (20 %) scores them up.
   A "Not interested" only dislikes that one video's keywords; the next starter
   search brings different cooking titles.
2. **Shared-queue videos count as mine once I watch them.** Finishing (or
   reaching 90 % of) something the other person queued in the Shared queue
   logs a full `finish` for me. Same for liking it.
3. `delete_unwatched`: deleting a shared video I never watched logs −1 for me.
4. Shorts: the Shorts prefetch picks with the blended "Both of us" profile, and
   the Shorts strip shows from that shared pool (Shorts only, not the feed).
5. Profile selection is just the `X-Profile` header/cookie (no logins, by
   design): using the app on the wrong profile records events there.

Why 2 Khan Academy / space videos took over: their keywords become the top 4
keywords, so **both** `topic:` queries are space queries and one `channel:`
query is Khan Academy: 3 of ~6 searches. Weights grow linearly, nothing caps a
topic's share of the feed, and the feed is a plain top-40 by score.

## 8. How profiles are separated in storage

| Data | Per profile? | Where |
|---|---|---|
| Watch progress, rating, speed | yes | `watch_state.profile_id` |
| Signals / history | yes | `events.profile_id` |
| Algorithm settings, boosts, mutes, starters | yes | `settings` scope `profile:<id>` |
| Candidate pool | yes, `0` = "Both of us" | `reco_cache.profile_id` |
| Reloaded-away suggestions | yes, `0` = "Both of us" | `reco_seen.profile_id` |
| Personal queue / playlists | yes | `queue_items` / `playlists` with `scope='personal'` |
| Shared queue, shared playlists, videos, downloads, captions | shared | `queue_items scope='shared'`, `videos.audience='all'` |

Everything lives in one SQLite file (`/var/lib/homelib/homelib.db`, WAL mode);
schema changes are additive `ALTER TABLE`s in `db._migrate()`.

## What this means for the rewrite

* Extend, don't replace: keep `events`, `watch_state`, `reco_cache`,
  `reco_seen`, the reload/nightly flow and the algorithm screen.
* Migrate `boosts` → pinned/weighted manual interests, `mutes` → blocks,
  `starter_topics` → exploration seeds (with "home cooking" no longer forced),
  `forgotten` stays.
* Add: rewatch and partial-watch signals, more/less-like-this, subscriptions,
  embeddings table, interest clusters with sublinear + decaying weights, a fixed
  query budget split across clusters, per-cluster caps and interleaving.
* The 1.4 `EVENT_WEIGHTS` keep their values for compatibility (tests pin them).
