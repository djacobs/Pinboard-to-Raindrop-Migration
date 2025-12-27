import argparse
import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import httpx
from dotenv import load_dotenv

PINBOARD_EXPORT_URL = "https://api.pinboard.in/v1/posts/all"
RAINDROP_API_BASE = "https://api.raindrop.io/rest/v1"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Migrate Pinboard bookmarks to Raindrop.io via API."
    )
    parser.add_argument("--pinboard-token", help="Pinboard API token (username:token).")
    parser.add_argument(
        "--pinboard-json",
        type=Path,
        help="Path to Pinboard JSON export (optional if --fetch-pinboard is used).",
    )
    parser.add_argument(
        "--fetch-pinboard",
        action="store_true",
        help="Fetch fresh data from Pinboard API instead of using a file.",
    )
    parser.add_argument(
        "--raindrop-token", help="Raindrop.io personal token (Bearer token)."
    )
    parser.add_argument(
        "--collection-id",
        type=int,
        default=0,
        help="Default Raindrop collection id (0 = Unsorted).",
    )
    parser.add_argument(
        "--collection-map",
        type=Path,
        help="Path to JSON rules for mapping tags to collection ids.",
    )
    parser.add_argument(
        "--readlater-collection-id",
        type=int,
        help="Optional collection id for Pinboard toread=yes items.",
    )
    parser.add_argument(
        "--toread-tag",
        default="toread",
        help="Tag to add when Pinboard toread=yes (set empty string to disable).",
    )
    parser.add_argument(
        "--lowercase-tags",
        action="store_true",
        help="Lowercase all tags during import.",
    )
    parser.add_argument(
        "--skip-existing",
        dest="skip_existing",
        action="store_true",
        default=True,
        help="Skip creating if a bookmark with the same URL exists (default: on).",
    )
    parser.add_argument(
        "--no-skip-existing",
        dest="skip_existing",
        action="store_false",
        help="Do not skip existing bookmarks (may create duplicates).",
    )
    parser.add_argument(
        "--merge-tags",
        action="store_true",
        help="When a bookmark exists, merge tags/update note instead of skipping.",
    )
    parser.add_argument(
        "--sleep",
        type=float,
        default=0.2,
        help="Seconds to sleep between Raindrop requests (to avoid rate limits).",
    )
    parser.add_argument("--limit", type=int, help="Limit number of items processed.")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print intended actions without writing to Raindrop.",
    )
    parser.add_argument(
        "--log-file",
        type=Path,
        default=Path("migration.log"),
        help="Path to log file (also logs to stdout).",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=30.0,
        help="HTTP timeout in seconds for API requests.",
    )
    return parser.parse_args()


def setup_logging(log_file: Path) -> None:
    log_file.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(log_file, encoding="utf-8"),
        ],
    )


def load_pinboard_from_file(path: Path) -> List[Dict[str, Any]]:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def load_collection_map(path: Optional[Path]) -> List[Dict[str, Any]]:
    if not path:
        return []
    with path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    rules: List[Dict[str, Any]] = []
    if not isinstance(data, list):
        raise ValueError("collection map must be a JSON list")
    for idx, rule in enumerate(data):
        if not isinstance(rule, dict):
            raise ValueError(f"rule {idx} must be an object")
        if "collection_id" not in rule or "tags" not in rule:
            raise ValueError(f"rule {idx} must include collection_id and tags")
        tags = rule["tags"]
        if not isinstance(tags, list) or not all(isinstance(t, str) for t in tags):
            raise ValueError(f"rule {idx} tags must be a list of strings")
        rules.append(
            {
                "collection_id": int(rule["collection_id"]),
                "tags": tags,
                "name": rule.get("name"),
            }
        )
    return rules


def fetch_pinboard(token: str, timeout: float) -> List[Dict[str, Any]]:
    params = {"format": "json", "auth_token": token}
    with httpx.Client(timeout=timeout) as client:
        response = client.get(PINBOARD_EXPORT_URL, params=params)
        response.raise_for_status()
        return response.json()


def normalize_tags(
    tag_str: str, lowercase: bool, toread: bool, toread_tag: str
) -> List[str]:
    tags = [t for t in tag_str.split(" ") if t]
    if toread and toread_tag:
        tags.append(toread_tag)
    if lowercase:
        tags = [t.lower() for t in tags]
    return sorted(set(tags))


def guess_collection_from_tags(
    tags: List[str], rules: List[Dict[str, Any]], default_id: int
) -> int:
    for rule in rules:
        if any(tag in tags for tag in rule["tags"]):
            return int(rule["collection_id"])
    return default_id


def select_collection_id(
    entry: Dict[str, Any],
    tags: List[str],
    default_id: int,
    readlater_id: Optional[int],
    rules: List[Dict[str, Any]],
) -> int:
    if readlater_id is not None and entry.get("toread") == "yes":
        return readlater_id
    return guess_collection_from_tags(tags, rules, default_id)


def pinboard_to_raindrop_payload(
    entry: Dict[str, Any], args: argparse.Namespace, rules: List[Dict[str, Any]]
) -> Dict[str, Any]:
    toread_flag = entry.get("toread") == "yes"
    tags = normalize_tags(
        entry.get("tags", ""), lowercase=args.lowercase_tags, toread=toread_flag, toread_tag=args.toread_tag
    )

    payload: Dict[str, Any] = {
        "link": entry["href"],
        "title": entry.get("description") or entry["href"],
        "tags": tags,
        "important": "starred" in tags,
        "collection": {
            "$id": select_collection_id(
                entry,
                tags,
                args.collection_id,
                args.readlater_collection_id,
                rules,
            )
        },
        "pleaseParse": {"mode": "tags"},
    }

    extended = entry.get("extended")
    if extended:
        payload["note"] = extended
        payload["excerpt"] = extended
    created = entry.get("time")
    if created:
        payload["created"] = created
        payload["lastUpdate"] = created
    return payload


def find_existing_raindrop(
    client: httpx.Client, link: str
) -> Tuple[Optional[Dict[str, Any]], Optional[int]]:
    try:
        response = client.get("/raindrops/0", params={"search": f"link:{link}", "perpage": 1})
        response.raise_for_status()
        items = response.json().get("items", [])
        if items:
            item = items[0]
            return item, int(item["_id"])
    except httpx.HTTPError as exc:
        logging.error("Failed to search existing raindrop for %s: %s", link, exc)
    return None, None


def create_raindrop(client: httpx.Client, payload: Dict[str, Any]) -> bool:
    response = client.post("/raindrop", json=payload)
    try:
        response.raise_for_status()
        return True
    except httpx.HTTPStatusError as exc:
        logging.error("Create failed (%s): %s", payload.get("link"), exc.response.text)
        return False


def update_raindrop(
    client: httpx.Client,
    raindrop_id: int,
    payload: Dict[str, Any],
    existing: Dict[str, Any],
    merge_tags: bool,
) -> bool:
    update_body: Dict[str, Any] = {}
    if merge_tags:
        update_body["tags"] = sorted(set(existing.get("tags", [])) | set(payload.get("tags", [])))
    if payload.get("note"):
        update_body["note"] = payload["note"]
    if payload.get("excerpt"):
        update_body["excerpt"] = payload["excerpt"]
    if payload.get("important"):
        update_body["important"] = True

    if not update_body:
        return True  # nothing to change

    response = client.put(f"/raindrop/{raindrop_id}", json=update_body)
    try:
        response.raise_for_status()
        return True
    except httpx.HTTPStatusError as exc:
        logging.error("Update failed (%s): %s", payload.get("link"), exc.response.text)
        return False


def main() -> None:
    load_dotenv()
    args = parse_args()
    setup_logging(args.log_file)

    pinboard_token = args.pinboard_token or os.getenv("PINBOARD_TOKEN")
    raindrop_token = args.raindrop_token or os.getenv("RAINDROP_TOKEN")

    if not args.pinboard_json and not args.fetch_pinboard:
        logging.error("Provide --pinboard-json or --fetch-pinboard.")
        sys.exit(1)

    if not pinboard_token and args.fetch_pinboard:
        logging.error("Missing Pinboard token (set PINBOARD_TOKEN or --pinboard-token).")
        sys.exit(1)

    if not args.dry_run and not raindrop_token:
        logging.error("Missing Raindrop token (set RAINDROP_TOKEN or --raindrop-token).")
        sys.exit(1)

    # Load optional collection map
    try:
        collection_rules = load_collection_map(args.collection_map)
    except ValueError as exc:
        logging.error("Invalid collection map: %s", exc)
        sys.exit(1)

    # Load Pinboard data
    if args.pinboard_json:
        if not args.pinboard_json.exists():
            logging.error("Pinboard JSON file not found: %s", args.pinboard_json)
            sys.exit(1)
        pinboard_posts = load_pinboard_from_file(args.pinboard_json)
        logging.info("Loaded %d bookmarks from %s", len(pinboard_posts), args.pinboard_json)
    else:
        pinboard_posts = fetch_pinboard(pinboard_token, timeout=args.timeout)
        logging.info("Fetched %d bookmarks from Pinboard API", len(pinboard_posts))

    # Avoid duplicate work within the export itself
    seen_links = set()

    stats = {
        "processed": 0,
        "created": 0,
        "skipped_existing": 0,
        "updated": 0,
        "failed": 0,
        "duplicate_input": 0,
    }

    if args.dry_run:
        logging.info("DRY RUN: no changes will be made to Raindrop.")

    with httpx.Client(
        base_url=RAINDROP_API_BASE,
        headers={"Authorization": f"Bearer {raindrop_token}"} if raindrop_token else {},
        timeout=args.timeout,
    ) as raindrop_client:
        for entry in pinboard_posts:
            if args.limit and stats["processed"] >= args.limit:
                break

            href = entry.get("href")
            if not href:
                logging.warning("Skipping item without href: %s", entry)
                continue

            if href in seen_links:
                stats["duplicate_input"] += 1
                continue
            seen_links.add(href)

            payload = pinboard_to_raindrop_payload(entry, args, collection_rules)

            if args.dry_run:
                logging.info("[DRY] Would create/update %s with tags=%s", href, payload.get("tags"))
                stats["processed"] += 1
                continue

            existing_item = None
            existing_id = None
            if args.skip_existing or args.merge_tags:
                existing_item, existing_id = find_existing_raindrop(raindrop_client, href)

            if existing_item and args.skip_existing and not args.merge_tags:
                stats["skipped_existing"] += 1
            elif existing_item and args.merge_tags and existing_id:
                if update_raindrop(raindrop_client, existing_id, payload, existing_item, merge_tags=True):
                    stats["updated"] += 1
                else:
                    stats["failed"] += 1
            else:
                if create_raindrop(raindrop_client, payload):
                    stats["created"] += 1
                else:
                    stats["failed"] += 1

            stats["processed"] += 1
            if args.sleep:
                time.sleep(args.sleep)

    logging.info(
        "Done. Processed=%s created=%s updated=%s skipped=%s duplicates_in_export=%s failed=%s",
        stats["processed"],
        stats["created"],
        stats["updated"],
        stats["skipped_existing"],
        stats["duplicate_input"],
        stats["failed"],
    )


if __name__ == "__main__":
    main()

