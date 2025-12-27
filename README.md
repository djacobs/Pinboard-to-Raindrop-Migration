# Pinboard ➜ Raindrop.io (API migration)

Python CLI to migrate your Pinboard bookmarks to Raindrop.io using only APIs. Keeps URLs, titles, tags, extended descriptions, and optional to-read tagging; can skip or merge existing bookmarks in Raindrop.

## Prerequisites
- Python 3.10+
- Pinboard API token (`username:token`)
- Raindrop.io personal token (Settings → Integrations → API)

## Setup
```bash
python -m venv .venv
. .venv/Scripts/activate  # Windows PowerShell: .\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

Copy `env.sample` to `.env` and fill in tokens:
```
PINBOARD_TOKEN=yourusername:apitoken
RAINDROP_TOKEN=your_raindrop_personal_token
```

### Using uv (optional)
If you prefer uv over virtualenv/pip:
```bash
uv pip install -r requirements.txt
uv run pinboard_to_raindrop.py --fetch-pinboard --dry-run --limit 20
```

## Usage
Fetch directly from Pinboard and dry-run the first 20 items:
```bash
python pinboard_to_raindrop.py --fetch-pinboard --dry-run --limit 20
```

Use an existing export file:
```bash
curl -o pinboard.json "https://api.pinboard.in/v1/posts/all?format=json&auth_token=$PINBOARD_TOKEN"
python pinboard_to_raindrop.py --pinboard-json pinboard.json
```

Common flags:
- `--collection-id 0` (default) target collection (`0` = Unsorted)
- `--readlater-collection-id <id>` send Pinboard `toread=yes` items to a different collection
- `--toread-tag toread` add a tag to to-read items (set empty string to disable)
- `--skip-existing / --no-skip-existing` skip creating if link already exists (default: skip)
- `--merge-tags` when an item exists, merge tags and update notes instead of skipping
- `--limit N` process only N items
- `--sleep 0.2` seconds between Raindrop requests
- `--dry-run` log what would happen without writing

Examples:
```bash
# Real run, fetch from API, merge tags into existing items, 250 ms pacing
python pinboard_to_raindrop.py --fetch-pinboard --merge-tags --sleep 0.25

# Import from file, send to collection 123456, do not skip existing (will update)
python pinboard_to_raindrop.py --pinboard-json pinboard.json --collection-id 123456 --no-skip-existing --merge-tags
```

### Smoke test (offline)
Quick local check of tag → collection logic:
```bash
python smoke_test.py
```

### Tag→collection mapping (optional)
Provide a JSON rules file (ordered) to place items into collections based on tags:
```json
[
  {"collection_id": 123456, "tags": ["work", "project-x"], "name": "Work"},
  {"collection_id": 234567, "tags": ["python", "coding"], "name": "Dev"},
  {"collection_id": 345678, "tags": ["recipes", "cooking"]}
]
```
First matching rule wins. Use with `--collection-map path/to/map.json`. `--readlater-collection-id` still overrides when `toread=yes`.

### Suggest or move unsorted items
Guess collections for existing Unsorted items (collection 0) using the same mapping:
```bash
# Suggest only (no writes)
python suggest_collections.py --collection-map path/to/map.json

# Apply moves based on guesses
python suggest_collections.py --collection-map path/to/map.json --apply --sleep 0.25
```
Unmatched items will log a suggested new collection name (based on first tag or domain).

## CI
GitHub Actions runs ruff lint and the smoke test on push/PR (`.github/workflows/lint.yml`).

## Notes and assumptions
- Pinboard “starred” state is not included in `posts/all`; if you use a `starred` tag, it will be preserved and mapped to Raindrop’s `important` flag when present.
- Raindrop privacy is driven by collection; new items go to your personal (private) collections by default. If you need public collections, create/select the appropriate collection ID.
- `toread=yes` items can be tagged and/or routed to a dedicated collection via CLI flags.
- The script dedupes via Raindrop search (`/raindrops/0?search=link:<url>`). Keep pacing to avoid rate limits.
- Logs are written to `migration.log`.

