"""Cross-platform path policy shared by client-side project resolution."""

from __future__ import annotations

import re

STOPLIST_BASENAMES: frozenset[str] = frozenset({
    "home",
    "tmp",
    "temp",
    "root",
    "usr",
    "var",
    "etc",
    "opt",
    "users",
    "desktop",
    "downloads",
    "documents",
    "pictures",
    "videos",
    "music",
    "library",
    "applications",
    "appdata",
    "programdata",
    "program_files",
    "program_files_x86",
    "windows",
    "system32",
    "mnt",
    "media",
    "dev",
    "proc",
    "sys",
    "projects",
    "repos",
    "workspace",
})

MIN_SLUG_LENGTH = 2

_NON_SLUG_CHARS = re.compile(r"[^a-z0-9_-]+")
_RUNS = re.compile(r"-{2,}")
_SEPARATORS = re.compile(r"[/\\]+")

PathShape = tuple[str, tuple[str, ...]]


def normalize_slug(raw: str) -> str:
    """Normalize a candidate project slug."""
    s = raw.strip().lower()
    s = _NON_SLUG_CHARS.sub("-", s)
    s = _RUNS.sub("-", s)
    return s.strip("-")


def path_shape(p: str) -> PathShape:
    """Return a cross-platform lexical root and path segments."""
    if p.startswith("\\\\"):
        parts = tuple(part.casefold() for part in _SEPARATORS.split(p) if part)
        server = parts[0] if parts else ""
        share = parts[1] if len(parts) > 1 else ""
        return f"unc:{server}/{share}", parts[2:]

    drive = re.match(r"^([A-Za-z]):", p)
    if drive:
        remainder = p[2:]
        absolute = remainder.startswith(("/", "\\"))
        prefix = "win" if absolute else "win-rel"
        root = f"{prefix}:{drive.group(1).casefold()}"
        parts = tuple(
            part.casefold() for part in _SEPARATORS.split(remainder) if part
        )
        return root, parts

    root = "posix-abs" if p.startswith(("/", "\\")) else "posix-rel"
    return root, tuple(part for part in _SEPARATORS.split(p) if part)


def is_contained_by(child: str, ancestor: str) -> bool:
    """Return whether ancestor is a comparable strict lexical prefix."""
    child_root, child_parts = path_shape(child)
    ancestor_root, ancestor_parts = path_shape(ancestor)
    if child_root != ancestor_root or child_root.startswith("win-rel:"):
        return False
    if ".." in child_parts or ".." in ancestor_parts:
        return False
    return (
        len(ancestor_parts) < len(child_parts)
        and child_parts[:len(ancestor_parts)] == ancestor_parts
    )


def derive_slug_from_cwd(cwd: str) -> tuple[str | None, str | None]:
    """Derive a slug, or return the path-policy rejection reason."""
    if not cwd or not cwd.strip():
        return None, "empty cwd"

    stripped = cwd.rstrip("/\\")
    if not stripped or stripped in {"/", "C:", "C:/", "C:\\"}:
        return None, "root-level cwd"
    if re.fullmatch(r"[A-Za-z]:", stripped):
        return None, "root-level cwd"

    parts = [part for part in _SEPARATORS.split(stripped) if part]
    base = parts[-1] if parts else ""
    if not base:
        return None, "empty basename"

    _, shape_parts = path_shape(stripped)
    folded = tuple(part.casefold() for part in shape_parts)
    if any(
        part in {"tmp", "temp", ".tmp"} or part.startswith("tmp.")
        for part in folded
    ):
        return None, "path contains temp directory"
    if any(len(part) > 1 and part.startswith(".") for part in shape_parts):
        return None, "path contains dot-directory"
    if (
        (len(folded) == 2 and folded[0] in {"home", "users"})
        or (len(folded) == 1 and folded[0] == "root")
    ):
        return None, "home-directory cwd"

    slug = normalize_slug(base)
    if len(slug) < MIN_SLUG_LENGTH:
        return None, "slug too short after normalization"
    if slug in STOPLIST_BASENAMES:
        return None, f"basename '{slug}' is in the stoplist"
    return slug, None


def rejection_reason(path: str) -> str | None:
    """Return the path-policy rejection reason, if any."""
    return derive_slug_from_cwd(path)[1]


def is_rejected_path(path: str) -> bool:
    """Return whether auto-registration would reject path."""
    return rejection_reason(path) is not None
