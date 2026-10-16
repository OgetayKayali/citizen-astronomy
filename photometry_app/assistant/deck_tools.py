"""Observation Deck tools: look at the library, find duplicate images, and leave chosen images out of the statistics."""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable
from pathlib import Path
import threading
import time
from typing import Protocol

from photometry_app.assistant.image_tools import SCAN_LIMIT, iter_image_files, resolve_folder
from photometry_app.assistant.tools import ActionPlan, ToolAccess, ToolContext, ToolDefinition, ToolFailure
from photometry_app.core.duplicate_images import EXACT_COPY, DuplicateReport, find_duplicate_images
from photometry_app.core.models import AppMode
from photometry_app.core.observation_deck import (
    KIND_LABELS,
    ObservationDeckLibrary,
    build_deck_stats,
    classify_image_kind,
    path_is_inside,
    path_key,
)


DECK_NAME = "Observation Deck"
MAX_PATHS_PER_CALL = 5000
_MAX_LISTED = 8
_POLL_SECONDS = 0.25
_SCAN_TIMEOUT_SECONDS = 6 * 3600


class DeckBridge(Protocol):
    """Implemented by the main window; every call runs on the UI thread."""

    def current_mode(self, cancel_event: threading.Event) -> AppMode: ...

    def mode_switch_notes(self, mode: AppMode, cancel_event: threading.Event) -> tuple[str, ...]: ...

    def deck_library(self, cancel_event: threading.Event) -> ObservationDeckLibrary | None: ...

    def deck_busy(self, cancel_event: threading.Event) -> bool: ...

    def open_deck(self, cancel_event: threading.Event) -> None: ...

    def add_deck_folder(self, folder: Path, cancel_event: threading.Event) -> None: ...

    def set_deck_exclusions(self, paths: tuple[Path, ...], excluded: bool, cancel_event: threading.Event) -> int: ...


def deck_tools(bridge: DeckBridge, *, sleep: Callable[[float], None] = time.sleep) -> tuple[ToolDefinition, ...]:
    def wait_until_idle(context: ToolContext, message: str, timeout: float = _SCAN_TIMEOUT_SECONDS) -> None:
        started = time.monotonic()
        announced = False
        while bridge.deck_busy(context.cancel_event):
            context.check_cancelled()
            if not announced:
                context.progress(message)
                announced = True
            if time.monotonic() - started > timeout:
                raise ToolFailure("The Observation Deck is still busy. Try again when the scan finishes.")
            sleep(_POLL_SECONDS)

    def library(context: ToolContext) -> ObservationDeckLibrary | None:
        wait_until_idle(context, "Waiting for the Observation Deck library to load")
        found = bridge.deck_library(context.cancel_event)
        if found is not None:
            for root in found.root_paths:
                context.writer.protect(root)
        return found

    def get_deck(context: ToolContext, arguments: dict[str, object], _plan: ActionPlan | None) -> object:
        found = library(context)
        if found is None or not found.root_paths:
            return {"master_folders": [], "images": 0, "note": "The Observation Deck is empty. Add a master folder first."}
        return deck_summary(found)

    def find_duplicates(context: ToolContext, arguments: dict[str, object], _plan: ActionPlan | None) -> object:
        max_groups = int(arguments.get("max_groups", 40))
        folder_text = str(arguments.get("folder") or "").strip()
        excluded: frozenset[str] = frozenset()
        if folder_text:
            folder = resolve_folder(folder_text)
            context.writer.protect(folder)
            recursive = bool(arguments.get("recursive", True))
            paths: list[Path] = []
            for path in iter_image_files(folder, recursive=recursive, check_cancelled=context.check_cancelled):
                paths.append(path)
                if len(paths) >= SCAN_LIMIT:
                    break
            stage_of = lambda path, header: classify_image_kind(path, header, root=folder).value
            scope = str(folder)
            deck = bridge.deck_library(context.cancel_event) if not bridge.deck_busy(context.cancel_event) else None
        else:
            deck = library(context)
            if deck is None or not deck.images:
                raise ToolFailure("The Observation Deck is empty. Give a folder to check, or add a master folder to the deck first.")
            paths = [image.path for image in deck.images]
            kinds = {path_key(image.path): image.kind.value for image in deck.images}
            stage_of = lambda path, _header: kinds.get(path_key(path), "")
            scope = DECK_NAME
        if deck is not None:
            excluded = deck.excluded_keys
        context.progress(f"Checking {len(paths):,} images for copies")
        report = find_duplicate_images(paths, stage_of=stage_of, check_cancelled=context.check_cancelled, progress=context.progress)
        return duplicate_summary(report, scope=scope, excluded=excluded, max_groups=max_groups, hit_limit=len(paths) >= SCAN_LIMIT)

    def plan_add(context: ToolContext, arguments: dict[str, object]) -> ActionPlan:
        folder = resolve_folder(arguments["folder"])
        context.writer.protect(folder)
        wait_until_idle(context, "Waiting for the Observation Deck library to load", timeout=120)
        found = bridge.deck_library(context.cancel_event)
        if found is not None:
            for root in found.root_paths:
                if path_is_inside(folder, root):
                    raise ToolFailure(f"{folder} is already in the Observation Deck (inside {root}).")
        details = [
            f"CAst reads the header of every image under {folder}.",
            "Your images are not changed. CAst saves the library list in its own database, not in your folders.",
        ]
        if bridge.current_mode(context.cancel_event) != AppMode.ASTRO_TOOLS:
            details.insert(0, f"CAst switches to {DECK_NAME}.")
            details.extend(bridge.mode_switch_notes(AppMode.ASTRO_TOOLS, context.cancel_event))
        return ActionPlan(
            title=f"Add {folder.name or folder} to the Observation Deck",
            summary="Scans the folder and adds its images to the deck's totals, nights, and targets.",
            details=tuple(details),
            payload=folder,
        )

    def add_folder(context: ToolContext, arguments: dict[str, object], plan: ActionPlan | None) -> object:
        assert plan is not None and isinstance(plan.payload, Path)
        bridge.open_deck(context.cancel_event)
        wait_until_idle(context, "Waiting for the Observation Deck library to load")
        bridge.add_deck_folder(plan.payload, context.cancel_event)
        context.progress(f"Scanning {plan.payload.name or plan.payload}")
        wait_until_idle(context, "Scanning the folder")
        found = bridge.deck_library(context.cancel_event)
        if found is None or not any(path_key(root) == path_key(plan.payload) or path_is_inside(plan.payload, root) for root in found.root_paths):
            raise ToolFailure("The scan did not finish. Check the Observation Deck for an error message.")
        added = sum(1 for image in found.images if path_is_inside(image.path, plan.payload))
        return {"status": "added", "folder": str(plan.payload), "images_in_folder": added, "deck": deck_summary(found)}

    def plan_exclusion(excluded: bool) -> Callable[[ToolContext, dict[str, object]], ActionPlan]:
        def plan(context: ToolContext, arguments: dict[str, object]) -> ActionPlan:
            found = library(context)
            if found is None or not found.images:
                raise ToolFailure("The Observation Deck is empty.")
            by_key = {path_key(image.path): image for image in found.images}
            wanted: dict[str, Path] = {}
            unknown: list[str] = []
            for raw in arguments["paths"]:  # type: ignore[union-attr]
                text = str(raw).strip().strip('"')
                image = by_key.get(path_key(text)) if text else None
                if image is None:
                    unknown.append(text)
                elif found.is_excluded(image.path) != excluded:
                    wanted[path_key(image.path)] = image.path
            if not wanted:
                if unknown and len(unknown) == len(arguments["paths"]):  # type: ignore[arg-type]
                    raise ToolFailure("None of those paths are images in the Observation Deck. Use the full paths it reported.")
                state = "already excluded" if excluded else "already counted"
                raise ToolFailure(f"Every listed image is {state}.")
            paths = tuple(sorted(wanted.values(), key=lambda path: str(path).casefold()))
            details = [str(path) for path in paths[:_MAX_LISTED]]
            if len(paths) > _MAX_LISTED:
                details.append(f"...and {len(paths) - _MAX_LISTED:,} more.")
            if unknown:
                details.append(f"{len(unknown):,} listed path(s) are not in the deck and are skipped.")
            reason = str(arguments.get("reason") or "").strip()
            count = f"{len(paths):,} image{'s' if len(paths) != 1 else ''}"
            if excluded:
                title = f"Leave {count} out of Observation Deck statistics"
                summary = "The files stay where they are. They are only skipped in totals, nights, and targets."
                if reason:
                    summary = f"{reason} {summary}"
                details.append("You can include them again from the Library tree (right-click > Include in Statistics).")
            else:
                title = f"Count {count} in Observation Deck statistics again"
                summary = "These images go back into totals, nights, and targets."
            return ActionPlan(title=title, summary=summary, details=tuple(details), payload=paths)

        return plan

    def apply_exclusion(excluded: bool):
        def run(context: ToolContext, arguments: dict[str, object], plan: ActionPlan | None) -> object:
            assert plan is not None and isinstance(plan.payload, tuple)
            changed = bridge.set_deck_exclusions(plan.payload, excluded, context.cancel_event)
            found = bridge.deck_library(context.cancel_event)
            result: dict[str, object] = {"status": "excluded" if excluded else "included", "changed": changed}
            if found is not None:
                result["deck"] = deck_summary(found)
            return result

        return run

    paths_schema = {
        "type": "array",
        "items": {"type": "string"},
        "maxItems": MAX_PATHS_PER_CALL,
        "description": "Full image paths exactly as reported by find_duplicate_images or get_observation_deck.",
    }
    return (
        ToolDefinition(
            name="get_observation_deck",
            description=(
                "The Observation Deck library: master folders, image counts by type, total exposure, nights, the top "
                "targets, and images the user left out of the statistics."
            ),
            access=ToolAccess.READ,
            run=get_deck,
            activity="Reading the Observation Deck",
        ),
        ToolDefinition(
            name="find_duplicate_images",
            description=(
                "Find duplicate image files, read-only. 'exact_copy' groups have identical bytes. 'same_exposure' "
                "groups record the same exposure (same DATE-OBS, exposure, filter, camera, and size) but differ in "
                "bytes, for example the same sub saved twice in another format. Each group lists a suggested file to "
                "keep (the one nearest the top of the folder tree) and the copies. Leave out 'folder' to check the "
                "Observation Deck library. Files are never changed; to stop copies inflating the deck, offer "
                "exclude_from_observation_deck."
            ),
            access=ToolAccess.READ,
            run=find_duplicates,
            parameters={
                "type": "object",
                "properties": {
                    "folder": {"type": "string", "description": "Full path of a folder to check instead of the deck."},
                    "recursive": {"type": "boolean", "description": "Include subfolders when a folder is given. Default true."},
                    "max_groups": {"type": "integer", "minimum": 1, "maximum": 200, "description": "Most groups to list. Default 40."},
                },
                "additionalProperties": False,
            },
            activity="Looking for duplicate images",
        ),
        ToolDefinition(
            name="add_folder_to_observation_deck",
            description=(
                "Add a master imaging folder to the Observation Deck and scan it, switching to the deck if needed. "
                "The user approves it first. Images are only read."
            ),
            access=ToolAccess.SESSION,
            run=add_folder,
            plan=plan_add,
            parameters={
                "type": "object",
                "properties": {"folder": {"type": "string", "description": "Full path of the master folder."}},
                "required": ["folder"],
                "additionalProperties": False,
            },
            activity="Adding a folder to the Observation Deck",
        ),
        ToolDefinition(
            name="exclude_from_observation_deck",
            description=(
                "Leave images out of the Observation Deck statistics, for example duplicate copies. The user approves "
                "it first. The files are not moved, renamed, or deleted."
            ),
            access=ToolAccess.SESSION,
            run=apply_exclusion(True),
            plan=plan_exclusion(True),
            parameters={
                "type": "object",
                "properties": {
                    "paths": paths_schema,
                    "reason": {"type": "string", "maxLength": 200, "description": "One short sentence shown on the approval card."},
                },
                "required": ["paths"],
                "additionalProperties": False,
            },
            activity="Excluding images from the deck",
        ),
        ToolDefinition(
            name="include_in_observation_deck",
            description="Count images the user excluded earlier in the Observation Deck statistics again. The user approves it first.",
            access=ToolAccess.SESSION,
            run=apply_exclusion(False),
            plan=plan_exclusion(False),
            parameters={
                "type": "object",
                "properties": {"paths": paths_schema},
                "required": ["paths"],
                "additionalProperties": False,
            },
            activity="Including images in the deck again",
        ),
    )


def deck_summary(library: ObservationDeckLibrary, *, max_targets: int = 30, max_excluded: int = 50) -> dict[str, object]:
    stats = build_deck_stats(library.counted(), scope_label="All targets")
    kinds = Counter(KIND_LABELS[image.kind] for image in library.images)
    excluded = library.excluded_images
    summary: dict[str, object] = {
        "master_folders": [str(root) for root in library.root_paths],
        "images": len(library.images),
        "unreadable": library.unreadable_files,
        "by_type": dict(kinds.most_common()),
        "counted_subframes": stats.subframe_count,
        "total_exposure_hours": round(stats.total_exposure_hours, 2),
        "nights": stats.night_count,
        "first_night": stats.first_date.isoformat() if stats.first_date else None,
        "last_night": stats.last_date.isoformat() if stats.last_date else None,
        "targets": [
            {"name": target.name, "master": target.root_name, "hours": round(target.exposure_seconds / 3600.0, 2), "subframes": target.frame_count}
            for target in stats.targets[:max_targets]
        ],
        "excluded_images": len(excluded),
    }
    if len(stats.targets) > max_targets:
        summary["note"] = f"Showing the top {max_targets} of {len(stats.targets)} targets by exposure."
    if excluded:
        summary["excluded"] = [str(image.path) for image in excluded[:max_excluded]]
    return summary


def duplicate_summary(
    report: DuplicateReport,
    *,
    scope: str,
    excluded: frozenset[str],
    max_groups: int,
    hit_limit: bool = False,
) -> dict[str, object]:
    exact = report.groups_of(EXACT_COPY)
    groups: list[dict[str, object]] = []
    for group in report.groups[:max_groups]:
        entry: dict[str, object] = {
            "kind": group.kind,
            "detail": group.detail,
            "keep": str(group.keep),
            "copies": [str(path) for path in group.copies],
        }
        already = [str(path) for path in group.copies if path_key(path) in excluded]
        if already:
            entry["copies_already_excluded"] = already
        groups.append(entry)
    result: dict[str, object] = {
        "checked": scope,
        "images_checked": report.checked_files,
        "exact_copy_groups": len(exact),
        "same_exposure_groups": len(report.groups) - len(exact),
        "copies": report.copy_count,
        "groups": groups,
    }
    notes: list[str] = []
    if len(report.groups) > max_groups:
        notes.append(f"Listing {max_groups} of {len(report.groups)} groups. Ask for more with max_groups.")
    if hit_limit:
        notes.append(f"Only the first {SCAN_LIMIT:,} images were checked. Try a narrower folder.")
    if report.unreadable:
        result["unreadable"] = [{"file": str(path), "reason": reason} for path, reason in report.unreadable[:20]]
        notes.append(f"{len(report.unreadable)} file(s) could not be read and were not compared.")
    if not report.groups:
        notes.append("No duplicates were found.")
    notes.append("Frames at different processing stages, such as a raw light and its calibrated version, are not counted as copies.")
    result["notes"] = notes
    return result
