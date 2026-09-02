"""Human-gated cleanup proposals for the project registry."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Any, Literal
from urllib.parse import quote

from hydra_cli import api
from hydra_cli.paths import is_contained_by, path_shape, rejection_reason

Action = Literal["delete", "keep"]
_NON_ALNUM = re.compile(r"[^a-z0-9]")


class PruneError(RuntimeError):
    pass


@dataclass(frozen=True)
class Assessment:
    slug: str
    action: Action
    reason: str
    paths: tuple[dict[str, Any], ...]
    memory_count: int


@dataclass(frozen=True)
class MergeCandidate:
    source: str
    target: str


@dataclass(frozen=True)
class PrunePlan:
    assessments: tuple[Assessment, ...]
    merge_candidates: tuple[MergeCandidate, ...]
    ambiguous_twins: tuple[tuple[str, ...], ...]


def _slug_key(slug: str) -> str:
    return _NON_ALNUM.sub("", slug.lower())


def _shape_compatible(left: str, right: str) -> bool:
    left_root, left_parts = path_shape(left)
    right_root, right_parts = path_shape(right)
    return (
        left_root == right_root
        and not left_root.startswith("win-rel:")
        and bool(left_parts)
        and bool(right_parts)
        and ".." not in left_parts
        and ".." not in right_parts
        and _slug_key(left_parts[-1]) == _slug_key(right_parts[-1])
    )


def _find_twins(
    projects: list[dict[str, Any]],
) -> tuple[list[MergeCandidate], list[tuple[str, ...]], set[str]]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for project in projects:
        groups[_slug_key(project["slug"])].append(project)

    candidates: list[MergeCandidate] = []
    ambiguous: list[tuple[str, ...]] = []
    protected: set[str] = set()
    for key, group in sorted(groups.items()):
        if len(group) < 2:
            continue
        protected.update(project["slug"] for project in group)
        confirmed = [p for p in group if p.get("auto_registered_at") is None]
        unconfirmed = [p for p in group if p.get("auto_registered_at") is not None]
        if (
            key
            and len(group) == 2
            and len(confirmed) == 1
            and len(unconfirmed) == 1
            and len(confirmed[0].get("paths", [])) == 1
            and len(unconfirmed[0].get("paths", [])) == 1
            and _shape_compatible(
                confirmed[0]["paths"][0]["path"],
                unconfirmed[0]["paths"][0]["path"],
            )
        ):
            candidates.append(
                MergeCandidate(
                    source=unconfirmed[0]["slug"], target=confirmed[0]["slug"]
                )
            )
        else:
            ambiguous.append(tuple(sorted(project["slug"] for project in group)))
    return candidates, ambiguous, protected


def _path_reason(path: str, anchors: list[tuple[str, str]]) -> str | None:
    matches = [
        (slug, anchor)
        for slug, anchor in anchors
        if is_contained_by(path, anchor)
    ]
    if matches:
        deepest = max(len(path_shape(anchor)[1]) for _, anchor in matches)
        slugs = sorted({
            slug
            for slug, anchor in matches
            if len(path_shape(anchor)[1]) == deepest
        })
        return f"descendant of confirmed project {', '.join(slugs)}"
    return rejection_reason(path)


def build_prune_plan(
    projects: list[dict[str, Any]], memories: list[dict[str, Any]]
) -> PrunePlan:
    """Classify the live registry without consulting the local filesystem."""
    memory_counts = Counter(
        memory.get("project_slug")
        for memory in memories
        if memory.get("project_slug") is not None
    )
    anchors = [
        (project["slug"], entry["path"])
        for project in projects
        if project.get("auto_registered_at") is None
        for entry in project.get("paths", [])
    ]
    candidates, ambiguous, protected = _find_twins(projects)
    candidate_by_slug = {
        candidate.source: candidate for candidate in candidates
    } | {
        candidate.target: candidate for candidate in candidates
    }
    ambiguous_slugs = {
        slug: group for group in ambiguous for slug in group
    }

    assessments: list[Assessment] = []
    for project in sorted(projects, key=lambda item: item["slug"]):
        slug = project["slug"]
        paths = tuple(project.get("paths", []))
        memory_count = memory_counts[slug]
        if slug in candidate_by_slug:
            candidate = candidate_by_slug[slug]
            reason = f"merge candidate {candidate.source} -> {candidate.target}"
            action: Action = "keep"
        elif slug in ambiguous_slugs:
            reason = (
                f"slug twins {', '.join(ambiguous_slugs[slug])} are ambiguous;"
                " handle manually"
            )
            action = "keep"
        elif project.get("auto_registered_at") is None:
            reason = "confirmed project"
            action = "keep"
        elif memory_count:
            noun = "memory" if memory_count == 1 else "memories"
            reason = f"holds {memory_count} pinned {noun}"
            action = "keep"
        elif not paths:
            reason = "no registered paths"
            action = "keep"
        else:
            reasons = [_path_reason(entry["path"], anchors) for entry in paths]
            if all(reason is not None for reason in reasons):
                unique = sorted({reason for reason in reasons if reason is not None})
                reason = "every path qualifies: " + "; ".join(unique)
                action = "delete"
            elif any(reason is not None for reason in reasons):
                reason = "mixed qualifying and non-qualifying paths"
                action = "keep"
            else:
                reason = "no path qualifies for cleanup"
                action = "keep"
        assessments.append(Assessment(slug, action, reason, paths, memory_count))

    assert protected == set(candidate_by_slug) | set(ambiguous_slugs)
    return PrunePlan(tuple(assessments), tuple(candidates), tuple(ambiguous))


def _api_error(status: int, body: str) -> str:
    try:
        return json.loads(body).get("detail", body)
    except (json.JSONDecodeError, AttributeError):
        return body


def _fetch_list(path: str, label: str) -> list[dict[str, Any]]:
    status, body = api.get(path)
    if status != 200:
        raise PruneError(f"failed to fetch {label}: {_api_error(status, body)}")
    try:
        value = json.loads(body)
    except json.JSONDecodeError as exc:
        raise PruneError(f"failed to decode {label}: {exc}") from exc
    if not isinstance(value, list):
        raise PruneError(f"failed to decode {label}: expected a list")
    return value


def _path_liveness(entry: dict[str, Any]) -> str:
    local_instance = os.environ.get("HYDRA_INSTANCE_ID", "").strip()
    if not local_instance or entry.get("instance_id") != local_instance:
        return "unknown"
    try:
        return "yes" if os.path.exists(entry["path"]) else "no"
    except OSError:
        return "unknown"


def _print_plan(plan: PrunePlan, *, apply: bool) -> None:
    print(f"Project prune ({'apply' if apply else 'dry-run'})")
    for candidate in plan.merge_candidates:
        print(f"MERGE CANDIDATE {candidate.source} -> {candidate.target} (report only)")
    for group in plan.ambiguous_twins:
        print(f"AMBIGUOUS {', '.join(group)} - handle manually")
    for assessment in plan.assessments:
        print(
            f"{assessment.action.upper():6} {assessment.slug}"
            f" paths={len(assessment.paths)} memories={assessment.memory_count}"
            f" reason={assessment.reason}"
        )
        for entry in assessment.paths:
            print(
                f"       path instance={entry.get('instance_id', '?')}"
                f" exists={_path_liveness(entry)} {entry['path']}"
            )


def run_prune(*, apply: bool = False) -> PrunePlan:
    projects = _fetch_list("/api/projects", "projects")
    memories = _fetch_list("/api/memory", "memories")
    plan = build_prune_plan(projects, memories)
    _print_plan(plan, apply=apply)
    if not apply:
        print("Dry run only; pass --apply to delete eligible projects.")
        return plan

    for assessment in plan.assessments:
        if assessment.action != "delete":
            continue
        current_memories = _fetch_list("/api/memory", "memories before delete")
        if any(
            memory.get("project_slug") == assessment.slug
            for memory in current_memories
        ):
            print(f"SKIP   {assessment.slug}: acquired a pinned memory")
            continue
        status, body = api.delete(
            f"/api/projects/{quote(assessment.slug, safe='')}"
        )
        if status != 204:
            raise PruneError(
                f"failed to delete {assessment.slug} ({status}):"
                f" {_api_error(status, body)}"
            )
        print(f"DELETED {assessment.slug}")
    return plan


def cmd_project_prune(args: argparse.Namespace) -> None:
    try:
        run_prune(apply=args.apply)
    except PruneError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
