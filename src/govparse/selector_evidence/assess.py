"""Check that candidate table fragments contain content and the native picture assets."""

from pathlib import Path

from PIL import Image, ImageStat

from .pictures import score_table_png_fidelity


def has_content(paths):
    """Require visible contrast in every candidate fragment."""
    if not paths:
        return False
    for path in paths:
        try:
            with Image.open(path) as image:
                image.thumbnail((450, 450))
                if ImageStat.Stat(image.convert("L")).stddev[0] < 1:
                    return False
        except (OSError, ValueError):
            return False
    return True


def assess(ir, table_id, paths, grid_complete, *, relative_to=None):
    """Check rendered file content, grid completeness, and native picture matches.

    Paths are opened as provided. Set relative_to to record them relative to a
    caller-owned output directory in the returned evidence.
    """
    if isinstance(paths, (str, Path)):
        paths = [paths]
    files = [Path(path) for path in paths]
    if relative_to is None:
        recorded = [path.as_posix() for path in files]
    else:
        root = Path(relative_to).resolve()
        recorded = [path.resolve().relative_to(root).as_posix() for path in files]
    evidence = {"paths": recorded, "complete_grid": grid_complete}
    if not has_content(files):
        return dict(evidence, eligible=False, reason="empty_or_missing")
    if not grid_complete:
        return dict(evidence, eligible=False, reason="partial_grid")
    scores = score_table_png_fidelity(ir, table_id, files)
    if scores["status"] == "unknown_table":
        return dict(evidence, eligible=False, reason="unknown_table", pictures=scores)
    eligible = (
        scores["expected_asset_count"] == scores["matched_asset_count"]
        and scores["missing_source_asset_count"] == 0
        and all(p["status"] != "invalid_source_asset" for p in scores["placements"])
    )
    return dict(
        evidence,
        eligible=eligible,
        reason="passed" if eligible else "missing_picture",
        pictures=scores,
    )
