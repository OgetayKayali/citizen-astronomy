from __future__ import annotations

from photometry_app.core.models import CatalogStar, VariableStarDesignationFamily


VARIABLE_STAR_DESIGNATION_LABELS = {
    VariableStarDesignationFamily.NAMED: "Named variables",
    VariableStarDesignationFamily.GAIA: "Gaia designations",
    VariableStarDesignationFamily.ASASSN: "ASAS-SN designations",
    VariableStarDesignationFamily.ATLAS: "ATLAS designations",
    VariableStarDesignationFamily.ZTF: "ZTF designations",
    VariableStarDesignationFamily.OTHER: "Other survey designations",
}


def classify_variable_star_designation(name: str) -> VariableStarDesignationFamily:
    normalized = " ".join(name.strip().lower().replace("_", " ").split())
    if not normalized:
        return VariableStarDesignationFamily.NAMED
    if normalized.startswith("gaia"):
        return VariableStarDesignationFamily.GAIA
    if normalized.startswith("asassn") or normalized.startswith("asas-sn"):
        return VariableStarDesignationFamily.ASASSN
    if normalized.startswith("atlas"):
        return VariableStarDesignationFamily.ATLAS
    if normalized.startswith("ztf"):
        return VariableStarDesignationFamily.ZTF
    if normalized.startswith("["):
        return VariableStarDesignationFamily.OTHER
    if normalized.startswith((
        "2mass",
        "wise",
        "tic",
        "toi",
        "kic",
        "tess",
        "sdss",
        "ucac",
        "usno",
        "ps1",
        "panstarrs",
        "pan-starrs",
        "gsc",
        "tyc",
        "css",
        "nsvs",
    )):
        return VariableStarDesignationFamily.OTHER
    return VariableStarDesignationFamily.NAMED


def filter_variable_stars(
    stars: list[CatalogStar],
    selected_families: list[VariableStarDesignationFamily],
) -> list[CatalogStar]:
    allowed = set(selected_families) or set(VariableStarDesignationFamily)
    return [star for star in stars if classify_variable_star_designation(star.name) in allowed]


def format_designation_family_labels(families: list[VariableStarDesignationFamily]) -> str:
    if not families or len(families) == len(VariableStarDesignationFamily):
        return "all designation families"
    return ", ".join(VARIABLE_STAR_DESIGNATION_LABELS[family] for family in families)