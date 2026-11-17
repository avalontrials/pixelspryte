"""
PixelSpryte B-roll Library
Matches planner queries against a tagged clip library.

Library layout:
    assets/broll/           <- clips (any subfolder structure)
    assets/tags.csv         <- "relative/path.mp4,tag1 tag2 tag3"

Untagged clips are still searchable by their filename words.
"""

import csv
import os
from typing import List, Optional, Tuple

VIDEO_EXTENSIONS = (".mp4", ".mov", ".mkv", ".m4v")


def load_library(broll_dir: str, tags_csv: str) -> List[Tuple[str, set]]:
    """
    Return [(absolute_path, {tags...}), ...] for every clip in the library.
    Tags come from tags.csv plus the clip's own filename words.
    """
    library = []
    if not os.path.isdir(broll_dir):
        return library

    tag_map = {}
    if os.path.exists(tags_csv):
        with open(tags_csv, "r", encoding="utf-8") as f:
            for row in csv.reader(f):
                if not row or row[0].strip().startswith("#") or len(row) < 2:
                    continue
                rel = row[0].strip().replace("\\", "/").lower()
                tag_map[rel] = set(row[1].lower().split())

    for root, _dirs, files in os.walk(broll_dir):
        for name in files:
            if not name.lower().endswith(VIDEO_EXTENSIONS):
                continue
            path = os.path.join(root, name)
            rel = os.path.relpath(path, broll_dir).replace("\\", "/").lower()
            # Filename words double as tags: "kids_beach_sunset.mp4"
            stem = os.path.splitext(name)[0].lower()
            tags = set(stem.replace("-", "_").split("_"))
            tags |= tag_map.get(rel, set())
            library.append((path, tags))

    return library


def find_clip(query: str, library: List[Tuple[str, set]]) -> Optional[str]:
    """
    Return the library clip best matching the query (most tag-word
    overlaps), or None if nothing matches at all.
    """
    words = set(query.lower().replace(",", " ").split())
    if not words or not library:
        return None

    best, best_score = None, 0
    for path, tags in library:
        score = len(words & tags)
        if score > best_score:
            best, best_score = path, score
    return best
