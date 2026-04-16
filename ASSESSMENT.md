# Type Consolidation Assessment

## Investigation summary

Echobox is a deliberately dict-first Python codebase: shapes are passed as
plain dicts, never validated at runtime, and routinely parsed from external
sources (LLM output, calendar JSON, Whisper segments). I scanned all
~25 Python modules under `pipeline/`, `echobox_recorder/`, `echobox.py`,
and `tests/` for type definitions, ad-hoc aliases, and shape-polymorphic
dicts that cross file boundaries.

### What I did NOT find
- **No literal duplicate type definitions** across files. The
  `@dataclass`es that exist (`AppContext` in `echobox.py:43`,
  `DetectionResult` in `watcher.py:89`, `RecordingSession` in
  `recorder.py:233`, `SwiftHelperSession` in `swift_helper.py:78`) are
  each scoped to one module and not re-declared elsewhere.
- No `TypedDict`, `NamedTuple`, or `Protocol` declarations anywhere — so
  there is nothing to merge, only shapes to **name** for the first time.

### What I found: shape polymorphism

Six dict shapes flow through multiple modules with the same keys but
without a shared name. Each one is a high-confidence target for a
lightweight `TypedDict` because the producer is single, the keys are
stable, and the consumers are few.

| Shape | Producer | Consumers | Sites |
|-------|----------|-----------|-------|
| `Config` (flattened YAML, `dict[str, str]`) | `enrich.load_config` (`pipeline/enrich.py:96`) | `enrich.py` (~15 sigs), `read_config.py:20`, `fit.py`, `setup.py` | ~20 |
| `TranscriptMeta` (`date/time/duration`) | `parse_transcript_metadata` (`pipeline/enrich.py:232`) | `extract_structured_data` (`enrich.py:958`) | 2 |
| `Classification` (`meeting_type/matched_pattern`) | `classify_call_type` (`pipeline/enrich.py:459`) | `_get_allowed_sources`, `extract_structured_data`, `build_prompt`, `fetch_context_by_type` | 4 |
| `Attendee` (`email/name`) | `map_attendees` (`pipeline/enrich.py:430`) | `classify_call_type`, `fetch_context_by_type`, `_fetch_prior_meetings`, `extract_structured_data`, `build_attendees_block` | 5 |
| `ActionItem` (`owner/task/deadline`) | `extract_structured_data` (`enrich.py:1019`) and `parse_markdown_actions` (`actions.py:32`) | `actions.print_items`, `summary.parse_json_sidecar` | 4 |
| `EnrichmentSidecar` (full JSON sidecar) | `extract_structured_data` (`enrich.py:958-970`) | `list_calls.py:74-107`, `slug_from_enrichment.derive_slug` (`:41`), `summary.parse_json_sidecar` (`:64`), `actions.py:69`, `echobox.py:218-222` | 5 |

The bare `-> dict:` and `-> list:` return annotations on public functions
in `enrich.py` (15+ instances) hide the actual shape and force readers
to chase definitions across files.

## High-confidence changes (implemented)

1. **New file `pipeline/echobox_types.py`** with one `Config` alias and
   six `TypedDict`s (all `total=False` because LLM/markdown parsers
   routinely omit fields). Named `echobox_types.py`, not `types.py`, to
   avoid shadowing the stdlib `types` module — `pipeline/` is on
   `sys.path` for several scripts.
2. **`pipeline/enrich.py`** — replaced `dict` / `list` with the new
   aliases on every public function plus a few private helpers whose
   shapes were obvious:
   - `load_config -> Config`
   - `get_config(config: Config, …)`, `get_config_list(config: Config, …)`
   - `_build_command(config: Config, …)`
   - `parse_transcript_metadata -> TranscriptMeta`
   - `get_calendar_context(config: Config, …) -> list[dict]`
   - `timestamp_match(events: list[dict], …)`
   - `load_team_config(config: Config) -> tuple[dict[str, str], set[str], dict[str, str], list[str]]`
   - `map_attendees(…) -> list[Attendee]`
   - `load_meeting_types(config: Config) -> dict[str, dict]`
   - `classify_call_type(…) -> Classification`
   - `_get_allowed_sources(config: Config, classification: Classification) -> set[str]`
   - `_fetch_calendar_context(event: dict) -> list[str]`
   - `_extract_key_terms(…) -> list[str]`
   - `_fetch_prior_meetings(…, attendee_list: list[Attendee], …) -> list[str]`
   - `fetch_context_by_type(config: Config, …, classification: Classification, …, attendee_list: list[Attendee], …)`
   - `build_attendees_block(attendee_list: list[Attendee], team_roles: dict[str, str], …)`
   - `load_prompt_template(config: Config) -> str`
   - `build_prompt(…, classification: Classification, …)`
   - `extract_structured_data(…, meta: TranscriptMeta, classification: Classification, attendee_list: list[Attendee]) -> EnrichmentSidecar`
   - `call_mlx(…, config: Config, …)`
3. **`pipeline/read_config.py`** — `safe_load_config(...) -> Config`.
4. **`pipeline/actions.py`** — `parse_markdown_actions(...) -> list[ActionItem]`,
   `print_items(name, items: list[ActionItem])`.
5. **`pipeline/summary.py`** — `parse_json_sidecar` annotates the loaded
   sidecar as `EnrichmentSidecar`.
6. **`pipeline/slug_from_enrichment.py`** — `derive_slug(sidecar: EnrichmentSidecar, …)`.
7. **`pipeline/list_calls.py`** — `sidecar_data: EnrichmentSidecar` (was
   `dict[str, object]`).

Total: **7 files edited + 1 new file = 8 files**, well within the
15-file budget.

## Uncertain / deferred

These were considered and intentionally NOT changed:

- **`event` dicts from calendars** (Google API / TSV / Apple Calendar)
  are external JSON with wildly different shapes per source. Typing
  them risks misleading downstream code; left as `dict`.
- **Recorder / Whisper / sounddevice types** in
  `echobox_recorder/recorder.py` — explicitly out of scope per the task
  prompt; the messy `Any`s there reflect upstream API shapes.
- **`fit.py` model dicts** (`get_hardware_info -> dict`,
  `detect_*_models -> list`) are internal to fit and never cross
  module boundaries; not worth a TypedDict.
- **Private helpers** `_fetch_documents`, `_fetch_messages`, `_fetch_web`
  in `enrich.py` have no annotations at all today. Per the task
  guardrail "Do not add types speculatively to private helpers,"
  I left their signatures alone.
- **Converting any of these dicts to dataclass / Pydantic** is NOT
  trivial here. The sidecar shape is round-tripped through `json.dumps`
  / `json.loads` and would require reviver/serializer code at every
  boundary. `TypedDict` was the right escape valve — it documents the
  shape without changing runtime behavior.
- **`team_roles` and `known_emails` `dict[str, str]`** could be alias'd
  but each only appears in 1-2 signatures; aliases would add indirection
  without clarity.

## Test results

`./echobox test`:

```
[ok]   test_artifact_fallbacks
[ok]   test_cli_paths
[ok]   test_cli_reliability
[ok]   test_config_parser
[ok]   test_enrich              (47 passed, 0 failed)
[ok]   test_enrich_verbose
[ok]   test_fit
[ok]   test_markdown_preview
[ok]   test_orchestrator
[ok]   test_read_config
[FAIL] test_recorder            (PRE-EXISTING — sounddevice not installed in env)
[ok]   test_report_render
[ok]   test_serve
[ok]   test_smart_setup
[ok]   test_swift_helper
[ok]   test_watcher
```

The single `test_recorder` failure is a pre-existing environment issue
(`RuntimeError: sounddevice is required for recording`) — present on
`main` before any of my edits, unchanged by them, and unrelated to the
type work.
