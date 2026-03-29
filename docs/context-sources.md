# Context Sources

Echobox enriches transcripts by injecting relevant context into the LLM prompt. Context sources are pluggable and configured in `echobox.yaml`.

## How Context Works

1. A call ends and a transcript is produced
2. Echobox parses the transcript timestamp
3. Calendar events for that date are fetched
4. The transcript is matched to a calendar event by time
5. The meeting type is classified from the event title
6. Context sources are queried based on meeting type
7. All context is assembled and sent to the LLM with the transcript

## Calendar

The calendar source is the most important. It provides:
- **Event matching** — which calendar event corresponds to this recording
- **Attendee list** — real names for speaker identification
- **Meeting title** — used for classification

### Configuration

```yaml
context_sources:
  calendar:
    enabled: true
    command: "your-calendar-cli events list --date {date} --format json"
```

If the CLI needs nested JSON or shell-sensitive quoting, prefer `command_args` so Echobox can execute it without going through shell parsing:

```yaml
context_sources:
  calendar:
    enabled: true
    command_args:
      - gws
      - calendar
      - events
      - list
      - --params
      - '{"calendarId":"primary","timeMin":"{date}T00:00:00Z","timeMax":"{date}T23:59:59Z","singleEvents":true,"orderBy":"startTime"}'
```

The command must return JSON with an `items` array containing event objects. Each event should have:
- `summary` — event title
- `start.dateTime` — start time in ISO format
- `attendees` — array of `{email, displayName}` objects

### Supported Calendar Tools

| Tool | Command Example |
|------|----------------|
| gws | `command_args: [gws, calendar, events, list, --params, '{"calendarId":"primary","timeMin":"{date}T00:00:00Z","timeMax":"{date}T23:59:59Z","singleEvents":true}']` |
| gcalcli | `gcalcli agenda "{date} 00:00" "{date} 23:59" --details all --tsv` |
| icalBuddy | Requires a wrapper script to convert to JSON |

## Messages

Search a message database for context about attendees and topics. Useful if you have archived Slack, Telegram, or other chat history.

### SQLite Database

```yaml
context_sources:
  messages:
    enabled: true
    type: sqlite
    path: "/path/to/messages.db"
    query: >
      SELECT sender_name, datetime(timestamp, 'unixepoch') as ts,
             substr(text, 1, 200) as snippet
      FROM messages
      WHERE text LIKE '%{term}%'
      AND timestamp > unixepoch('now', '-90 days')
      ORDER BY timestamp DESC
      LIMIT 15
```

### Command-Based

```yaml
context_sources:
  messages:
    enabled: true
    type: command
    command: "grep -rli '{term}' ~/chat-logs/ | head -5 | xargs head -20"
```

## Documents

Search project documents, notes, and knowledge bases for relevant context.

Preferred on macOS because Spotlight is already indexing your files:

```yaml
context_sources:
  documents:
    enabled: true
    command: "mdfind '{term}' | head -5 | xargs -I{} head -20 '{}' 2>/dev/null"
```

If you want to scope document search to a project directory instead, set `PROJECT_DIR` in your environment and use a grep-style command.

## Web

Fallback for unknown attendees. Uses DuckDuckGo Instant Answers API by default.

```yaml
context_sources:
  web:
    enabled: true
```

Override with a custom command:
```yaml
context_sources:
  web:
    enabled: true
    command: "curl -sf 'https://api.search.brave.com/res/v1/web/search?q={query}' -H 'X-Subscription-Token: $API_KEY'"
```

## Meeting Type Classification

Meeting types determine which context sources are queried:

```yaml
meeting_types:
  client_call:
    patterns: ["client", "partner", "investor"]
    context: [calendar, messages, documents, web]
  team_sync:
    patterns: ["sync", "standup", "weekly"]
    internal_only: true
    context: [documents]
```

When a calendar event title matches a pattern, the corresponding meeting type is selected and only the listed context sources are queried.

## Adding Custom Sources

To add a new context source type:

1. Add the configuration to `echobox.yaml` under `context_sources`
2. The `command` field accepts any shell command
3. `{term}` is replaced with the search query (attendee name, topic, etc.)
4. `{date}` is replaced with the transcript date (YYYY-MM-DD)
5. `{query}` is replaced with a URL-encoded search string
6. Output is included in the LLM prompt as-is (truncated to 3000 chars per source)
7. `command_args` is safer than `command` for CLIs that require nested quotes or JSON payloads

See `config/context-sources.example.yaml` for more examples.
