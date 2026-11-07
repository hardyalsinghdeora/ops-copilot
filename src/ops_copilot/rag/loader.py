"""Load markdown files with YAML-ish front-matter into ``Document`` objects.

We deliberately avoid a YAML dependency: the front-matter in this project is a flat
``key: value`` block (lists written as ``[a, b]``). Keeping the parser tiny makes it
easy to read and easy to swap for Confluence/Notion loaders later.
"""

from __future__ import annotations

import re
from pathlib import Path

from .models import Document

_FRONT_MATTER = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)
_COMMENT = re.compile(r"(?:^|\s)#.*$")  # YAML-style: "title: C# guide" keeps its "#"


def parse_front_matter(raw: str) -> tuple[dict[str, str | list[str]], str]:
    m = _FRONT_MATTER.match(raw)
    if not m:
        return {}, raw
    meta: dict[str, str | list[str]] = {}
    for line in m.group(1).splitlines():
        line = _COMMENT.sub("", line).strip()  # a "#" starts a comment only at the start or after a space
        if not line or ":" not in line:
            continue
        key, value = line.split(":", 1)
        value = value.strip().strip('"').strip("'")
        if value.startswith("[") and value.endswith("]"):
            meta[key.strip()] = [v.strip().strip('"').strip("'") for v in value[1:-1].split(",") if v.strip()]
        else:
            meta[key.strip()] = value
    return meta, raw[m.end() :]


def load_document(path: Path) -> Document:
    raw = path.read_text(encoding="utf-8")
    meta, body = parse_front_matter(raw)
    tags = meta.get("tags", [])
    return Document(
        id=str(meta.get("id") or path.stem),
        title=str(meta.get("title") or path.stem.replace("-", " ").title()),
        kind=str(meta.get("kind") or path.parent.name.rstrip("s")),
        service=str(meta.get("service") or "platform"),
        owner=str(meta.get("owner") or ""),
        updated=str(meta.get("updated") or ""),
        tags=list(tags) if isinstance(tags, list) else [str(tags)],
        text=body.strip(),
        path=str(path),
    )


def load_knowledge_base(root: Path) -> list[Document]:
    docs = [load_document(p) for p in sorted(root.rglob("*.md"))]
    seen: set[str] = set()
    for d in docs:
        if d.id in seen:
            raise ValueError(f"Duplicate document id in knowledge base: {d.id}")
        seen.add(d.id)
    return docs
