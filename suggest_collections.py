import argparse
import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

import httpx
from dotenv import load_dotenv

RAINDROP_API_BASE = "https://api.raindrop.io/rest/v1"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Suggest or apply Raindrop collections for unsorted items (collection 0)."
    )
    parser.add_argument(
        "--collection-map",
        type=Path,
        required=True,
        help="JSON rules mapping tags to collection ids.",
    )
    parser.add_argument(
        "--raindrop-token",
        help="Raindrop personal token (Bearer). If omitted, read RAINDROP_TOKEN env.",
    )
    parser.add_argument(
        "--per-page",
        type=int,
        default=100,
        help="Items per page when fetching unsorted raindrops.",
    )
    parser.add_argument(
        "--max-pages",
        type=int,
        help="Optional max pages to process (each page is per-page items).",
    )
    parser.add_argument(
        "--sleep",
        type=float,
        default=0.2,
        help="Seconds to sleep between update requests.",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="If set, move items to guessed collections. Default: suggest only.",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=30.0,
        help="HTTP timeout in seconds.",
    )
    return parser.parse_args()


def setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s"
    )


def load_collection_map(path: Path) -> List[Dict[str, Any]]:
    with path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, list):
        raise ValueError("collection map must be a JSON list")
    rules: List[Dict[str, Any]] = []
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


def guess_collection(tags: List[str], rules: List[Dict[str, Any]]) -> Optional[int]:
    for rule in rules:
        if any(tag in tags for tag in rule["tags"]):
            return int(rule["collection_id"])
    return None


def suggest_new_collection(tags: List[str], link: str) -> Optional[str]:
    if tags:
        return tags[0]
    host = urlparse(link).netloc
    if host.startswith("www."):
        host = host[4:]
    return host or None


def fetch_unsorted(
    client: httpx.Client, per_page: int, max_pages: Optional[int]
) -> List[Dict[str, Any]]:
    all_items: List[Dict[str, Any]] = []
    page = 0
    while True:
        params = {"perpage": per_page, "page": page}
        resp = client.get("/raindrops/0", params=params)
        resp.raise_for_status()
        payload = resp.json()
        items = payload.get("items", [])
        if not items:
            break
        all_items.extend(items)
        page += 1
        if max_pages is not None and page >= max_pages:
            break
    return all_items


def move_raindrop(client: httpx.Client, item_id: int, collection_id: int) -> bool:
    resp = client.put(f"/raindrop/{item_id}", json={"collection": {"$id": collection_id}})
    try:
        resp.raise_for_status()
        return True
    except httpx.HTTPStatusError as exc:
        logging.error("Failed to move %s: %s", item_id, exc.response.text)
        return False


def main() -> None:
    load_dotenv()
    args = parse_args()
    setup_logging()

    try:
        rules = load_collection_map(args.collection_map)
    except ValueError as exc:
        logging.error("Invalid collection map: %s", exc)
        sys.exit(1)

    raindrop_token = args.raindrop_token or os.getenv("RAINDROP_TOKEN")
    if not raindrop_token:
        logging.error("Missing Raindrop token (set RAINDROP_TOKEN or --raindrop-token).")
        sys.exit(1)

    stats = {
        "total": 0,
        "guessed": 0,
        "moved": 0,
        "unmatched": 0,
    }

    with httpx.Client(
        base_url=RAINDROP_API_BASE,
        headers={"Authorization": f"Bearer {raindrop_token}"},
        timeout=args.timeout,
    ) as client:
        items = fetch_unsorted(client, args.per_page, args.max_pages)
        logging.info("Fetched %s unsorted items", len(items))
        for item in items:
            stats["total"] += 1
            tags = item.get("tags", []) or []
            guess = guess_collection(tags, rules)
            if guess is not None:
                stats["guessed"] += 1
                if args.apply:
                    if move_raindrop(client, int(item["_id"]), guess):
                        stats["moved"] += 1
                    else:
                        logging.error(
                            "Failed to move item %s (%s)", item.get("_id"), item.get("link")
                        )
                    if args.sleep:
                        time.sleep(args.sleep)
                else:
                    logging.info(
                        "[SUGGEST] %s -> collection %s (tags=%s)",
                        item.get("link"),
                        guess,
                        tags,
                    )
            else:
                stats["unmatched"] += 1
                suggestion = suggest_new_collection(tags, item.get("link", ""))
                logging.info(
                    "[UNMATCHED] %s tags=%s suggested-new-collection=%s",
                    item.get("link"),
                    tags,
                    suggestion,
                )

    logging.info(
        "Done. total=%s guessed=%s moved=%s unmatched=%s",
        stats["total"],
        stats["guessed"],
        stats["moved"],
        stats["unmatched"],
    )


if __name__ == "__main__":
    main()

