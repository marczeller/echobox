# Echobox audit — 2026-05-12

## Symptom

No meetings recorded since 2026-05-08 22:38. Three days of calls lost (CMT
follow-up on May 11 was the trigger). Watcher process is alive; mlx-server is
alive; pruning sweeps run on schedule; **zero `Recording started` events** in
watcher.log since 2026-05-08.

## Hypothesis from operator (Marc)

> "We changed the infra to use ScreenCaptureKit and the audio settings are
> now completely weird."

**Falsified by primary evidence:**

- Only two commits since 2026-04-25, both on **2026-05-09** (one day after
  the last good recording, not before):
  - `cfd80bd  Improve Echobox capture reliability (+1120/-117)`
  - `137f231  experiment: add coverage for PortAudio -9986 self-heal path`
- SCK is added in `cfd80bd` but is **opt-in** via `capture.backend:
  screencapturekit`. The live `config/echobox.yaml` has no `backend:` field,
  so it falls through to the default `sounddevice` path. **The audio
  capture path has not changed.**
- BlackHole + Multi-Output Device routing is unchanged. `auto_switch_output`
  is still true. Audio-side regression can be ruled out without further
  investigation.

## Actual root cause

`cfd80bd` introduces **two independent regressions in the meeting-detection
watcher**. Either alone would cause the symptom.

### Regression 1: browser tab query narrowed from "all tabs" to "active tab of front window"

`echobox_recorder/watcher.py` `BROWSER_SCRIPTS` was rewritten. Before:

```applescript
tell application "Arc"
    set urls to {}
    repeat with w in every window
        repeat with t in every tab of w
            set end of urls to (URL of t as text)
        end repeat
    end repeat
    return urls as string
end tell
```

After:

```applescript
tell application "Arc"
    if (count of windows) is 0 then return ""
    return URL of active tab of front window as text
end tell
```

**Why this breaks the workflow:** Marc uses Arc. A meeting tab in Arc is
typically **not** the active tab of the front window when work is happening.
Common cases that silently lose detection:

- Meet/Zoom tab open in a pinned sidebar slot, focus on a different tab.
- Split view, Meet on one side but focus on the other.
- Workspace switch while the call is live.
- Any second app window front (Slack, Telegram, terminal) — Arc is not the
  front app at all, AppleScript still queries Arc but finds nothing useful.

The old "all tabs in all windows" pattern survived all of these.

### Regression 2: URL match changed from substring → strict anchored regex

Before:

```python
MEETING_PATTERNS = (
    ("meet.google.com", "google-meet"),
    ("zoom.us/j/", "zoom"),
    ...
)
# matched anywhere in concatenated tab dump (case-insensitive substring)
```

After:

```python
MEETING_PATTERNS = (
    (re.compile(r"^https://meet\.google\.com/[a-z]{3}-[a-z]{4}-[a-z]{3}(?:$|[/?#])"), "google-meet"),
    (re.compile(r"^https://(?:[\w.-]+\.)?zoom\.us/(?:j|wc)/\d+(?:/[^?#]*)?(?:$|[?#])"), "zoom"),
    (re.compile(r"^https://teams\.microsoft\.com/(?:l/meetup-join|meet|v2/)(?:$|[/?#])"), "teams"),
    ...
)
```

**Why this breaks** (in addition to Regression 1):

- Anchored `^https://` — fails on any URL prefixed with whitespace, a query
  fragment, or an embedded concatenation (which was the OLD behavior of
  returning all tabs as one string — though Regression 1 made that moot).
- Meet regex requires exact `xxx-yyyy-zzz` shape. Meet supports
  alternate URL shapes (`/lookup/...`, `/landing`, dial-in landing, mobile
  redirects) that the substring match caught and the regex does not.
- Teams regex matches only three exact path prefixes. Teams meeting URLs
  in practice come in many forms (different tenants, embedded SharePoint
  redirects, `/_#/` paths, mobile share links). High false-negative risk.
- Zoom regex requires a digit-only meeting ID immediately after `/j/` or
  `/wc/`. Zoom sometimes serves intermediate redirect URLs first
  (`zoom.us/join`, `zoom.us/launch/...`) — substring caught these, regex
  does not.

## Out of scope (deliberate)

These are NOT touched by this audit. The evidence does not implicate them.

- ScreenCaptureKit code path (not active in config).
- Audio routing, BlackHole, Multi-Output Device.
- PortAudio -9986 self-heal logic.
- mlx-server, whisper model selection, enrichment pipeline.
- Teams **desktop-app** detection (Marc joins Teams via browser; confirmed).

Open these as separate workstreams if their need is later proven by evidence.

## Plan

### Phase 0 — Freeze baseline and add a discriminating test

Goal: write a regression test that captures **what worked on May 8 and what
fails today**. This becomes the gate for every fix below: green test means
the fix solved the right problem.

Tasks:

1. Branch `audit/2026-05-12-watcher-detection` off `main`.
2. Inventory real-world URLs from the May 4 to May 8 watcher.log
   `Meeting detected via Arc:` lines. These are GROUND-TRUTH positive
   examples that the old code matched. About 12 distinct URLs.
3. Add `tests/test_watcher_url_fixtures.py` with two fixture sets:
   - **`SHOULD_MATCH`** — extracted real Meet/Zoom/Teams meeting URLs from
     ground truth, plus a small curated set covering tenant variations.
   - **`SHOULD_NOT_MATCH`** — `meet.google.com/landing`,
     `meet.google.com/lookup/...`, `zoom.us/download`, generic teams.microsoft.com
     navigation. These should not trigger recording.
4. Run test against current code. Expect failures on SHOULD_MATCH. Commit
   the failing test (TDD baseline).

Verification gate: test file exists, runs, fails in the expected places.

### Phase 1 — Fix browser-tab query

Restore the "all tabs in all windows" enumeration. Keep the new return
value clean (split on `[\r\n,]+` already in place handles list output).

Tasks:

1. Revert `BROWSER_SCRIPTS` to all-tabs enumeration for all four browsers.
2. Keep the `if (count of windows) is 0 then return ""` guard added in
   `cfd80bd` — that one is a legit fix, not part of the regression.
3. Add a unit test that mocks `_run_osascript` to return a multi-line URL
   list and asserts every meeting URL in the list is found, regardless of
   position.

Verification gate: new unit test green. No live test yet.

### Phase 2 — Loosen URL matchers

Two design options, decide before coding (see Decision below):

**Option A — Drop back to substring matching.** Trivially correct, matches
exactly what worked May 8. Cost: re-introduces past false-positive risk
(matched `meet.google.com/landing` and similar). Mitigation: explicit
deny-list of known-bad paths.

**Option B — Keep regex but loosen each pattern.** Drop `^https://` anchor
(allow `https?` and protocol-relative), drop strict path shape on Meet
(any path after `meet.google.com/`), broaden Teams to `teams\.microsoft\.com`
anywhere, add explicit deny-list for landing/download/lookup pages.

**Recommendation: Option B.** Costs a few hours of regex work, preserves
the legitimate goal of `cfd80bd` (no recording on landing pages), and the
deny-list is explicit rather than emergent.

Tasks:

1. Revise `MEETING_PATTERNS` per chosen option.
2. Add an explicit `EXCLUDE_PATTERNS` list with reasons inline.
3. Make `_match_meeting_url` return None if any exclude pattern hits before
   meeting patterns are tried.
4. All fixture tests from Phase 0 must pass green.

Verification gate: `pytest tests/test_watcher*.py` all green.

### Phase 3 — Live smoke test on real Meet/Zoom/Teams

Tasks:

1. Deploy fix on mbp via `git pull` in `~/echobox`.
2. `launchctl kickstart -k gui/$(id -u)/com.echobox.watcher`.
3. Open a Google Meet test room. Wait 90s. Close.
4. Check `~/echobox-data/logs/watcher.log` for matching
   `Recording started` / `Recording finished` pair.
5. Check `~/echobox-data/audio/` for the corresponding `.wav` file.
6. Check `~/echobox-data/transcripts/` for the matching `.txt`.
7. Repeat on Zoom and Teams-in-browser.

Verification gate: all three platforms produce a full pipeline run end to
end (audio + transcript + enrichment file).

### Phase 4 — Monitoring and handoff

Tasks:

1. Add a one-line healthcheck to the morning brief: count of recordings in
   the last 24h. Loud alarm if zero on a weekday.
2. Document the regression and fix in `docs/troubleshooting.md` so the next
   "echobox is dead" debugging session is one log search away.
3. Open a follow-up ticket on the Teams-desktop-app gap (deferred from
   this audit, but real risk for future calls).

Verification gate: monitoring fires when watcher is paused. Doc commit
lands. Follow-up ticket exists.

## Decisions for Marc before execution

1. **Phase 2 Option A vs B**: substring with deny-list, or regex with
   deny-list? Recommendation = B (keeps the intent of `cfd80bd`).
2. **Merge strategy**: squash the fix into one commit on `main`, or keep
   the per-phase commits? Recommendation = per-phase, this audit document
   makes the history navigable.
3. **Should the audit doc itself be committed to the repo?** Useful as a
   permanent record next to `docs/design-decisions.md`. Recommendation =
   yes, commit at end as `docs/audit-2026-05-12-watcher-detection.md`.

## Time estimate

- Phase 0: 30 min (fixture extraction + failing test)
- Phase 1: 20 min (AppleScript revert + test)
- Phase 2: 45 min (regex tuning + deny-list + tests)
- Phase 3: 30 min (live smoke across three platforms)
- Phase 4: 30 min (monitoring + doc + follow-up ticket)

Total: ~2.5 hours, gated at each phase. Stop at the first failed
verification.
