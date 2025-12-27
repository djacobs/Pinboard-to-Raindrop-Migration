from types import SimpleNamespace

from pinboard_to_raindrop import (
    guess_collection_from_tags,
    normalize_tags,
    pinboard_to_raindrop_payload,
)


def make_args(**overrides):
    defaults = {
        "lowercase_tags": True,
        "toread_tag": "toread",
        "collection_id": 0,
        "readlater_collection_id": 123,
        "merge_tags": False,
        "skip_existing": True,
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def test_tag_normalization_and_collection_match():
    entry = {
        "href": "https://example.com",
        "description": "Example",
        "extended": "Notes",
        "tags": "Work project-x",
        "toread": "no",
        "time": "2021-01-01T00:00:00Z",
    }
    rules = [{"collection_id": 555, "tags": ["work", "project-x"], "name": "Work"}]
    args = make_args()

    payload = pinboard_to_raindrop_payload(entry, args, rules)

    assert payload["collection"]["$id"] == 555
    assert payload["tags"] == ["project-x", "work"]
    assert payload["title"] == "Example"
    assert payload["note"] == "Notes"
    assert payload["created"] == "2021-01-01T00:00:00Z"
    assert payload["important"] is False


def test_toread_overrides_collection_and_adds_tag():
    entry = {
        "href": "https://later.example",
        "description": "",
        "tags": "Read",
        "toread": "yes",
    }
    rules = [{"collection_id": 999, "tags": ["read"], "name": "Read"}]
    args = make_args(readlater_collection_id=42, toread_tag="later", lowercase_tags=True)

    payload = pinboard_to_raindrop_payload(entry, args, rules)

    assert payload["collection"]["$id"] == 42  # read-later override wins
    assert payload["tags"] == ["later", "read"]
    assert payload["title"] == "https://later.example"


def test_starred_flag_sets_important():
    tags = normalize_tags("starred dev", lowercase=True, toread=False, toread_tag="toread")
    rules = []
    args = make_args()
    entry = {
        "href": "https://star.example",
        "description": "",
        "tags": "starred dev",
        "toread": "no",
    }

    payload = pinboard_to_raindrop_payload(entry, args, rules)

    assert "starred" in tags
    assert payload["important"] is True


if __name__ == "__main__":
    test_tag_normalization_and_collection_match()
    test_toread_overrides_collection_and_adds_tag()
    test_starred_flag_sets_important()
    print("smoke_test: PASS")

