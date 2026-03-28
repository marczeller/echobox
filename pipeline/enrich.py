#!/usr/bin/env python3
from __future__ import annotations
"""MLX call enrichment with smart context curation.

Matches transcripts to calendar events, classifies meeting type,
curates context per-type, and enriches via local MLX model.

Usage: python3 enrich.py <transcript> [--output <path>] [--config <path>]
"""
import argparse
import json
import os
import re
import shlex
import subprocess
import sys
import time
import urllib.request
from datetime import datetime
from pathlib import Path

DEFAULT_CONFIG = Path(__file__).parent.parent / "config" / "echobox.yaml"
SSH_OPTS = ["-o", "ConnectTimeout=5"]
DEFAULT_PROMPT_TEMPLATE = """You are analyzing a meeting transcript for a project team.

{{known_attendees}}

<meeting_type>{{meeting_type}}</meeting_type>

{{curated_context}}

Now analyze this transcript. Use the known_attendees and meeting_type to correctly identify speakers.

<transcript>
{{transcript}}
</transcript>

Produce a structured analysis with these exact sections:

## Meeting Summary
2-3 sentence executive summary. Name the participants and the purpose of the call.

## Speaker Identification
Map each SPEAKER_XX to a real name. Use the known_attendees list and what each person discusses.

| Speaker Label | Identified As | Confidence |
|---|---|---|
| SPEAKER_00 | Name | high/medium/low |

## Key Decisions
Bullet list of anything decided or committed to. Only include actual decisions, not discussion.

## Action Items
Each action item must have an owner and a deadline (if mentioned). Format:
- **[Owner]** Action item description *(by [date] if mentioned)*

## Follow-ups Needed
What was discussed but not resolved? Who needs to follow up and on what?

## Context for Next Meeting
One paragraph: what should the participants prepare or know before the next conversation?
"""


class ConfigError(RuntimeError):
    """Raised when Echobox config cannot be loaded."""


class StepLogger:
    def __init__(self, verbose: bool = False):
        self.verbose = verbose
        self.start = time.time()

    def emit(self, message: str) -> None:
        if self.verbose:
            elapsed = time.time() - self.start
            print(f"[{elapsed:.1f}s] {message}", file=sys.stderr)
        else:
            print(message, file=sys.stderr)


def load_config(config_path: Path) -> dict:
    """Load YAML config into dotted key-value pairs using PyYAML."""
    try:
        import yaml
    except ModuleNotFoundError as exc:
        raise ConfigError(
            "PyYAML is required to read echobox.yaml. "
            "Install it with: python3 -m pip install --user pyyaml"
        ) from exc

    if not config_path.exists():
        return {}

    try:
        with open(config_path) as f:
            raw = yaml.safe_load(f)
    except Exception as exc:
        raise ConfigError(f"Could not parse config: {config_path} ({exc})") from exc

    if not isinstance(raw, dict):
        return {}

    config = {}

    def _flatten(obj, prefix=""):
        if isinstance(obj, dict):
            for k, v in obj.items():
                key = f"{prefix}.{k}" if prefix else str(k)
                _flatten(v, key)
        elif isinstance(obj, list):
            config[prefix] = ",".join(str(item) for item in obj)
            for idx, item in enumerate(obj):
                item_key = f"{prefix}.{idx}" if prefix else str(idx)
                _flatten(item, item_key)
        elif isinstance(obj, bool):
            config[prefix] = "true" if obj else "false"
        elif obj is not None:
            config[prefix] = str(obj)
        else:
            config[prefix] = ""

    _flatten(raw)
    return config


def get_config(config: dict, key: str, default: str = "") -> str:
    env_key = f"ECHOBOX_{key.upper().replace('.', '_')}"
    return os.environ.get(env_key, config.get(key, default))


def get_config_list(config: dict, key: str) -> list[str]:
    values: list[tuple[int, str]] = []
    prefix = f"{key}."
    for config_key, value in config.items():
        if not config_key.startswith(prefix):
            continue
        suffix = config_key[len(prefix):]
        if suffix.isdigit():
            values.append((int(suffix), value))
    return [value for _, value in sorted(values)]


def _substitute_placeholders(value: str, substitutions: dict[str, str]) -> str:
    rendered = value
    for key, replacement in substitutions.items():
        rendered = rendered.replace(f"{{{key}}}", replacement)
    return rendered


def _build_command(config: dict, key_prefix: str, substitutions: dict[str, str]) -> str | list[str]:
    command_args = get_config_list(config, f"{key_prefix}.command_args")
    if command_args:
        return [_substitute_placeholders(arg, substitutions) for arg in command_args]

    command = get_config(config, f"{key_prefix}.command", "")
    if not command:
        return ""
    return _substitute_placeholders(command, substitutions)


def ssh_run(target: str, cmd: str | list[str], timeout: int = 15) -> str:
    if not target:
        return ""
    remote_cmd = " ".join(shlex.quote(part) for part in cmd) if isinstance(cmd, list) else cmd
    try:
        r = subprocess.run(
            ["ssh"] + SSH_OPTS + [target, remote_cmd],
            capture_output=True, text=True, timeout=timeout
        )
        return r.stdout.strip()
    except Exception:
        return ""


def local_run(cmd: str | list[str], timeout: int = 15) -> str:
    try:
        if isinstance(cmd, list):
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        else:
            r = subprocess.run(
                ["zsh", "-lc", cmd], capture_output=True, text=True, timeout=timeout
            )
        return r.stdout.strip()
    except Exception:
        return ""


def run_command(cmd: str | list[str], workstation: str = "", timeout: int = 15) -> str:
    if workstation:
        return ssh_run(workstation, cmd, timeout)
    return local_run(cmd, timeout)


def parse_transcript_metadata(transcript_path: Path, transcript_text: str) -> dict:
    meta = {"date": None, "time": None, "duration": None}

    filename_match = re.match(r"(\d{4}-\d{2}-\d{2})_(\d{2})-(\d{2})", transcript_path.name)
    if filename_match:
        meta["date"] = filename_match.group(1)
        meta["time"] = f"{filename_match.group(2)}:{filename_match.group(3)}"

    for line in transcript_text[:2000].splitlines():
        date_match = re.match(r"Date:\s*(.+)", line.strip())
        if date_match and not meta["date"]:
            raw = date_match.group(1).strip()
            try:
                parsed = datetime.strptime(raw, "%Y-%m-%d")
                meta["date"] = parsed.strftime("%Y-%m-%d")
            except ValueError:
                pass

        duration_match = re.match(r"Duration:\s*(.+)", line.strip())
        if duration_match:
            meta["duration"] = duration_match.group(1).strip()

    return meta


def prepare_transcript_for_prompt(transcript_text: str) -> str:
    labels = []
    for line in transcript_text.splitlines():
        match = re.match(r"^\[(?:\d{2}:)?\d{2}:\d{2}\]\s+([^:]+):", line.strip())
        if match:
            labels.append(match.group(1).strip())

    unique_labels = {label for label in labels if label}
    if unique_labels and unique_labels != {"[Unknown]"} and unique_labels != {"Unknown"}:
        return transcript_text

    note = (
        "Note: this transcript has no usable diarization. Speaker labels are missing or all marked "
        "as [Unknown]. Treat it as an unlabeled multi-speaker transcript and do not over-claim "
        "speaker identity from turn boundaries alone.\n\n"
    )
    return note + transcript_text


def get_calendar_context(config: dict, workstation: str, transcript_date: str) -> list:
    cmd = _build_command(
        config,
        "context_sources.calendar",
        {"date": transcript_date},
    )
    if not cmd:
        return []

    raw = run_command(cmd, workstation, timeout=20)
    if not raw:
        return []
    try:
        data = json.loads(raw)
        if isinstance(data, list):
            return data
        return data.get("items", [])
    except json.JSONDecodeError:
        return []


def timestamp_match(events: list, transcript_time: str) -> dict:
    if not events or not transcript_time:
        return {}

    target_h, target_m = map(int, transcript_time.split(":"))
    target_minutes = target_h * 60 + target_m

    best_event = None
    best_delta = float("inf")

    for ev in events:
        start_raw = ev.get("start", {}).get("dateTime", "")
        time_part = re.search(r"T(\d{2}):(\d{2})", start_raw)
        if not time_part:
            continue
        ev_minutes = int(time_part.group(1)) * 60 + int(time_part.group(2))
        delta = abs(ev_minutes - target_minutes)
        if delta < best_delta:
            best_delta = delta
            best_event = ev

    if best_event and best_delta <= 60:
        return best_event
    return {}


def load_team_config(config: dict) -> tuple:
    known_emails = {}
    internal_domains = set()
    team_roles = {}
    team_members = set()

    for key, val in config.items():
        if key.startswith("team.members."):
            email = key.split(".", 2)[2]
            known_emails[email] = val
            if val:
                team_members.add(val)
        elif key == "team.internal_domains":
            for domain in val.split(","):
                d = domain.strip()
                if d:
                    internal_domains.add(d)
        elif key.startswith("team.internal_domains."):
            internal_domains.add(val)
        elif key.startswith("team.roles."):
            name = key.split(".", 2)[2]
            team_roles[name] = val

    return known_emails, internal_domains, team_roles, sorted(team_members)


def map_attendees(event: dict, known_emails: dict) -> list:
    attendee_list = []
    for att in event.get("attendees", []):
        email = att.get("email", "")
        name = known_emails.get(email, att.get("displayName", email))
        attendee_list.append({"email": email, "name": name})
    return attendee_list


def load_meeting_types(config: dict) -> dict:
    meeting_types = {}
    type_keys = set()
    for key in config:
        if key.startswith("meeting_types."):
            parts = key.split(".")
            if len(parts) >= 2:
                type_keys.add(parts[1])

    for type_name in type_keys:
        patterns_str = config.get(f"meeting_types.{type_name}.patterns", "[]")
        patterns = re.findall(r'"([^"]*)"', patterns_str)
        if not patterns:
            patterns = [p.strip() for p in patterns_str.strip("[]").split(",") if p.strip()]
        internal = config.get(f"meeting_types.{type_name}.internal_only", "false") == "true"
        meeting_types[type_name] = {"patterns": patterns, "internal_only": internal}

    return meeting_types


def classify_call_type(
    event: dict, attendee_list: list,
    meeting_types: dict, internal_domains: set
) -> dict:
    title = event.get("summary", "")

    for type_name, type_config in meeting_types.items():
        for pattern in type_config["patterns"]:
            if re.search(re.escape(pattern), title, re.IGNORECASE):
                return {"meeting_type": type_name, "matched_pattern": pattern}

    normalized_domains = {
        domain.strip().lstrip("@").lower()
        for domain in internal_domains
        if domain.strip()
    }

    def _is_internal(email: str) -> bool:
        if "@" not in email:
            return False
        email_domain = email.rsplit("@", 1)[1].lower()
        return any(
            email_domain == domain or email_domain.endswith(f".{domain}")
            for domain in normalized_domains
        )

    all_internal = all(
        _is_internal(email)
        for att in attendee_list
        if (email := att.get("email", ""))
    ) if attendee_list and normalized_domains else False

    if all_internal:
        return {"meeting_type": "team_sync", "matched_pattern": "internal-only attendees"}

    return {"meeting_type": "general", "matched_pattern": None}


def _get_allowed_sources(config: dict, classification: dict) -> set:
    """Determine which context sources to query based on meeting type config."""
    meeting_type = classification.get("meeting_type", "general")
    context_str = get_config(config, f"meeting_types.{meeting_type}.context", "")
    if context_str:
        return set(re.findall(r"\w+", context_str))
    return {"calendar", "web"}


def _sanitize_context_term(value: str, allow_at: bool = False) -> str:
    allowed = r"[^\w .,\-@]" if allow_at else r"[^\w .,\-]"
    collapsed = re.sub(r"\s+", " ", value)
    cleaned = re.sub(allowed, "", collapsed)
    return re.sub(r"\s+", " ", cleaned).strip()


def _fetch_documents(config, workstation, event, allowed_sources):
    if "documents" not in allowed_sources:
        return []
    doc_enabled = get_config(config, "context_sources.documents.enabled", "false")
    if doc_enabled != "true":
        return []
    summary = event.get("summary", "")
    if not summary:
        return []
    safe_summary = _sanitize_context_term(summary)
    cmd = _build_command(
        config,
        "context_sources.documents",
        {"term": safe_summary},
    )
    if not cmd:
        return []
    result = run_command(cmd, workstation, timeout=10)
    if result:
        return [f"<document_context>\n{result[:3000]}\n</document_context>"]
    return []


def _fetch_messages(config, workstation, attendee_list, allowed_sources):
    if "messages" not in allowed_sources:
        return []
    msg_enabled = get_config(config, "context_sources.messages.enabled", "false")
    if msg_enabled != "true":
        return []
    msg_type = get_config(config, "context_sources.messages.type", "sqlite").strip().lower()
    external_attendees = [
        att["name"] for att in attendee_list
        if "@" in att.get("email", "")
    ]
    if not external_attendees:
        return []

    if msg_type == "command":
        if not (
            get_config(config, "context_sources.messages.command", "")
            or get_config_list(config, "context_sources.messages.command_args")
        ):
            return []
        sections = []
        for term in external_attendees[:3]:
            safe_term = _sanitize_context_term(term, allow_at=True)
            cmd = _build_command(
                config,
                "context_sources.messages",
                {"term": safe_term},
            )
            result = run_command(cmd, workstation, timeout=10)
            if result and result.strip():
                sections.append(
                    f'<message_context query="{term}">\n{result[:3000]}\n</message_context>'
                )
        return sections

    msg_path = get_config(config, "context_sources.messages.path", "")
    msg_query = get_config(config, "context_sources.messages.query", "")
    if not msg_path or not msg_query:
        return []
    sections = []
    if not workstation:
        import sqlite3 as sqlite3_mod
        for term in external_attendees[:3]:
            try:
                conn = sqlite3_mod.connect(os.path.expanduser(msg_path))
                query = msg_query.replace("{term}", term.replace("'", "''"))
                rows = conn.execute(query).fetchall()
                conn.close()
                result = "\n".join("|".join(str(c) for c in row) for row in rows)
                if result.strip():
                    sections.append(
                        f'<message_context query="{term}">\n{result[:3000]}\n</message_context>'
                    )
            except Exception:
                pass
    else:
        for term in external_attendees[:3]:
            safe_term = term.replace("'", "''").replace('"', '\\"')
            query = msg_query.replace("{term}", safe_term)
            result = ssh_run(workstation, f'sqlite3 "{msg_path}" "{query}"', timeout=10)
            if result and result.strip():
                sections.append(
                    f'<message_context query="{term}">\n{result[:3000]}\n</message_context>'
                )
    return sections


def _fetch_web(config, workstation, attendee_list, allowed_sources):
    if "web" not in allowed_sources:
        return []
    web_enabled = get_config(config, "context_sources.web.enabled", "false")
    if web_enabled != "true":
        return []
    has_web_command = (
        bool(get_config(config, "context_sources.web.command", ""))
        or bool(get_config_list(config, "context_sources.web.command_args"))
    )
    sections = []
    unknown_external = [
        att for att in attendee_list if "@" in att.get("email", "")
    ]
    for att in unknown_external[:2]:
        name = att["name"]
        safe_name = _sanitize_context_term(name)
        from urllib.parse import quote_plus
        query = quote_plus(safe_name)
        if has_web_command:
            cmd = _build_command(
                config,
                "context_sources.web",
                {"query": query},
            )
            result = run_command(cmd, workstation, timeout=8)
        else:
            result = run_command(
                f"curl -sf 'https://api.duckduckgo.com/?q={query}&format=json&no_html=1' "
                "| python3 -c \"import sys,json; d=json.load(sys.stdin); "
                "print(d.get('Abstract','')[:500] or "
                "next((t.get('Text','') for t in d.get('RelatedTopics',[]) if t.get('Text')), ''))\"",
                workstation, timeout=8
            )
        if result and result.strip() and len(result.strip()) > 20:
            sections.append(f'<web_context person="{safe_name}">\n{result[:1000]}\n</web_context>')
    return sections


def fetch_context_by_type(
    config: dict, workstation: str,
    classification: dict, event: dict, attendee_list: list
) -> str:
    allowed = _get_allowed_sources(config, classification)
    sections = []
    sections.extend(_fetch_documents(config, workstation, event, allowed))
    sections.extend(_fetch_messages(config, workstation, attendee_list, allowed))
    sections.extend(_fetch_web(config, workstation, attendee_list, allowed))
    return "\n\n".join(sections)


def build_attendees_block(
    attendee_list: list,
    team_roles: dict,
    fallback_names: list[str] | None = None,
) -> str:
    lines = []
    seen = set()
    for att in attendee_list:
        name = att["name"]
        if name in seen:
            continue
        seen.add(name)
        role = team_roles.get(name, "")
        if role:
            lines.append(f"{name} ({role})")
        else:
            lines.append(f"{name} ({att['email']})")

    if not lines:
        for name in fallback_names or []:
            if name in seen:
                continue
            seen.add(name)
            role = team_roles.get(name, "")
            lines.append(f"{name} ({role or 'team member'})")
        for name, role in team_roles.items():
            if name in seen:
                continue
            seen.add(name)
            lines.append(f"{name} ({role})")

    if not lines:
        lines = ["Unknown attendees (calendar match unavailable)"]

    return "<known_attendees>\n" + "\n".join(lines) + "\n</known_attendees>"


def load_prompt_template(config: dict) -> str:
    template_path = get_config(config, "prompt.template", "").strip()
    if not template_path:
        return DEFAULT_PROMPT_TEMPLATE

    path = Path(os.path.expandvars(os.path.expanduser(template_path)))
    if not path.exists():
        raise FileNotFoundError(
            f"Prompt template not found: {path}. "
            "Set prompt.template to an existing file or remove it to use the built-in template."
        )
    return path.read_text()


def render_prompt_template(template: str, values: dict[str, str]) -> str:
    rendered = template
    for key, value in values.items():
        rendered = rendered.replace(f"{{{{{key}}}}}", value)

    unresolved = sorted(set(re.findall(r"{{\s*([a-zA-Z0-9_]+)\s*}}", rendered)))
    if unresolved:
        raise ValueError(
            "Prompt template contains unknown placeholders: "
            + ", ".join(unresolved)
            + ". Supported placeholders: transcript, known_attendees, meeting_type, curated_context."
        )
    return rendered


def build_prompt(
    transcript_text: str,
    attendees_block: str,
    classification: dict,
    curated_context: str,
    template_text: str | None = None,
) -> str:
    return render_prompt_template(
        template_text or DEFAULT_PROMPT_TEMPLATE,
        {
            "transcript": transcript_text,
            "known_attendees": attendees_block,
            "meeting_type": classification["meeting_type"],
            "curated_context": curated_context,
        },
    )


def extract_structured_data(enrichment_md: str, meta: dict, classification: dict, attendee_list: list) -> dict:
    """Parse enrichment markdown into structured JSON for downstream integrations."""
    data = {
        "date": meta.get("date"),
        "time": meta.get("time"),
        "duration": meta.get("duration"),
        "meeting_type": classification.get("meeting_type"),
        "participants": [{"name": a["name"], "email": a.get("email", "")} for a in attendee_list],
        "summary": "",
        "speakers": [],
        "decisions": [],
        "action_items": [],
        "follow_ups": [],
    }

    current_section = ""
    for line in enrichment_md.splitlines():
        section_match = re.match(r"^##\s+(.+)", line)
        if section_match:
            current_section = section_match.group(1).strip().lower()
            continue

        stripped = line.strip()
        if not stripped:
            continue

        if "summary" in current_section:
            if not stripped.startswith("|") and not stripped.startswith("#"):
                data["summary"] += stripped + " "

        elif "speaker" in current_section:
            if stripped.startswith("|") and "SPEAKER" in stripped:
                cells = [c.strip() for c in stripped.strip("|").split("|")]
                if len(cells) >= 3:
                    data["speakers"].append({
                        "label": cells[0],
                        "name": cells[1],
                        "confidence": cells[2] if len(cells) > 2 else "",
                    })

        elif "decision" in current_section:
            li = re.match(r"^[-*]\s+(.+)", stripped)
            if li:
                data["decisions"].append(li.group(1))

        elif "action" in current_section:
            li = re.match(r"^[-*]\s+\*{0,2}\[(.+?)\]\*{0,2}\s+(.+)", stripped)
            if li:
                owner = li.group(1).strip()
                task = li.group(2).strip()
                deadline_match = re.search(r"\*?\((?:by\s+)?(.+?)\)\*?$", task)
                deadline = deadline_match.group(1) if deadline_match else ""
                if deadline:
                    task = task[:deadline_match.start()].strip()
                data["action_items"].append({
                    "owner": owner,
                    "task": task,
                    "deadline": deadline,
                })

        elif "follow" in current_section:
            if not stripped.startswith("#"):
                data["follow_ups"].append(stripped)

    data["summary"] = data["summary"].strip()
    return data


def call_mlx(prompt: str, config: dict, logger: StepLogger | None = None) -> str:
    url = get_config(config, "mlx_url", "http://localhost:8090/v1/chat/completions")
    model = get_config(config, "mlx_model", "mlx-community/Qwen3-Next-80B-A3B-Instruct-6bit")
    timeout = int(get_config(config, "mlx_timeout_seconds", "600") or "600")

    if logger:
        logger.emit(f"Calling LLM ({len(prompt)} char prompt)...")
    else:
        print(f"Calling LLM: {model}", file=sys.stderr)
        print(f"  endpoint: {url}", file=sys.stderr)
        print(f"  prompt: {len(prompt)} chars", file=sys.stderr)

    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 4096,
        "temperature": 0.3,
    }

    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            result = json.loads(resp.read())
            elapsed = time.time() - t0
            content = result["choices"][0]["message"]["content"]
            if logger:
                logger.emit(f"LLM response: {len(content)} chars")
            else:
                print(f"  response: {len(content)} chars in {elapsed:.1f}s", file=sys.stderr)
            return content
    except Exception as e:
        elapsed = time.time() - t0
        print(
            f"LLM enrichment failed after {elapsed:.1f}s: {e} (timeout={timeout}s)",
            file=sys.stderr,
        )
        sys.exit(1)


def main():
    parser = argparse.ArgumentParser(
        description="Enrich call transcript via MLX with smart context curation"
    )
    parser.add_argument("transcript", help="Path to raw transcript file")
    parser.add_argument("--output", "-o", help="Output path (default: stdout)")
    parser.add_argument("--config", "-c", help="Config file path",
                        default=str(DEFAULT_CONFIG))
    parser.add_argument("--verbose", action="store_true", help="Show timed pipeline steps")
    args = parser.parse_args()

    transcript_path = Path(args.transcript)
    if not transcript_path.exists():
        print(f"Error: {transcript_path} not found", file=sys.stderr)
        sys.exit(1)

    logger = StepLogger(verbose=args.verbose)
    logger.emit("Loading config...")
    try:
        config = load_config(Path(args.config))
    except ConfigError as exc:
        print(f"Config error: {exc}", file=sys.stderr)
        sys.exit(1)
    workstation = os.environ.get(
        "ECHOBOX_WORKSTATION",
        get_config(config, "workstation_ssh", "")
    )

    if not workstation:
        url = get_config(config, "mlx_url", "http://localhost:8090/v1/chat/completions")
        models_url = url.replace("/chat/completions", "/models")
        try:
            urllib.request.urlopen(models_url, timeout=3)
        except Exception:
            print(f"Error: LLM server not reachable at {url}", file=sys.stderr)
            print("  Start your server first:", file=sys.stderr)
            model = get_config(config, "mlx_model", "")
            if model:
                print(f"    mlx_lm.server --model {model} --port 8090", file=sys.stderr)
            print("  Or check: echobox status", file=sys.stderr)
            sys.exit(1)

    transcript_text = transcript_path.read_text()
    prepared_transcript_text = prepare_transcript_for_prompt(transcript_text)

    logger.emit("Parsing transcript metadata...")
    meta = parse_transcript_metadata(transcript_path, transcript_text)

    cal_enabled = get_config(config, "context_sources.calendar.enabled", "true")
    events = []
    if cal_enabled == "true" and meta["date"]:
        logger.emit("Fetching calendar events...")
        events = get_calendar_context(config, workstation, meta["date"])

    matched_event = timestamp_match(events, meta["time"]) if events else {}

    known_emails, internal_domains, team_roles, team_members = load_team_config(config)
    attendee_list = map_attendees(matched_event, known_emails) if matched_event else []
    meeting_types = load_meeting_types(config)

    logger.emit("Classifying meeting type...")
    classification = (
        classify_call_type(matched_event, attendee_list, meeting_types, internal_domains)
        if matched_event
        else {"meeting_type": "general", "matched_pattern": None}
    )

    attendees_block = build_attendees_block(
        attendee_list,
        team_roles,
        fallback_names=team_members,
    )

    logger.emit("Curating context...")
    curated_context = fetch_context_by_type(
        config, workstation, classification, matched_event, attendee_list
    )

    try:
        template_text = load_prompt_template(config)
        prompt = build_prompt(
            prepared_transcript_text,
            attendees_block,
            classification,
            curated_context,
            template_text=template_text,
        )
    except (FileNotFoundError, ValueError) as exc:
        print(f"Prompt template error: {exc}", file=sys.stderr)
        sys.exit(1)

    result = call_mlx(prompt, config, logger=logger if args.verbose else None)

    if args.output:
        logger.emit("Extracting structured data...")
        sidecar = extract_structured_data(result, meta, classification, attendee_list)

        logger.emit("Writing enrichment + JSON sidecar")
        output_path = Path(args.output)
        output_path.write_text(result)
        json_path = output_path.with_suffix(".json")
        json_path.write_text(json.dumps(sidecar, indent=2))
        if not args.verbose:
            print(f"Written to {args.output}", file=sys.stderr)
            print(f"Structured data: {json_path}", file=sys.stderr)
    else:
        print(result)


if __name__ == "__main__":
    main()
