"""``sdd new-feature <NNN> <slug>``: scaffold ``<features_dir>/<NNN>-<slug>/``.

Copies every file under ``paths.templates_dir`` and substitutes the
placeholders ``<NNN>`` / ``{{NNN}}``, ``<slug>`` / ``{{SLUG}}``,
``{{FEATURE}}`` (= ``NNN-slug``), ``{{FID}}`` (= ``F<NNN>``), ``{{BRANCH}}``,
``{{ID_BLOCK}}`` (the requirement-number block this feature owns, see
``requirements.block_size``) and ``{{PROJECT}}``.

Requirement IDs are global across features. To keep parallel features from
defining the same ID, feature ``F<NNN>`` numbers the requirements it defines
within a clause/area key in its own block ``R<NNN*block_size>`` ..
``R<NNN*block_size + block_size-1>``: F013 with block size 10 owns R130-R139.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List, Tuple


def validate_number(number: str, digits: int) -> str:
    if not number.isdigit() or not (digits <= len(number) <= digits + 1):
        raise ValueError(f"feature number must be {digits}-{digits + 1} digits (e.g. {'0' * (digits - 1)}7), got {number!r}")
    return number


def validate_slug(slug: str) -> str:
    if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", slug):
        raise ValueError(f"slug must be lower-case kebab-case (e.g. user-login), got {slug!r}")
    return slug


def id_block(number: str, block_size: int) -> Tuple[int, int]:
    n = int(number)
    return n * block_size, n * block_size + block_size - 1


def substitute(text: str, number: str, slug: str, cfg: Dict[str, Any]) -> str:
    fid = f"{cfg['features']['id_prefix']}{number}"
    lo, hi = id_block(number, int(cfg["requirements"]["block_size"]))
    block = f"R{lo}-R{hi}"
    return (
        text.replace("{{NNN}}", number)
        .replace("<NNN>", number)
        .replace("{{SLUG}}", slug)
        .replace("<slug>", slug)
        .replace("{{FEATURE}}", f"{number}-{slug}")
        .replace("{{FID}}", fid)
        .replace("{{BRANCH}}", f"{cfg['features']['branch_prefix']}{fid}-{slug}")
        .replace("{{ID_BLOCK}}", block)
        .replace("{{PROJECT}}", cfg["project"]["name"])
    )


def roadmap_has(root: Path, cfg: Dict[str, Any], number: str, slug: str) -> bool:
    roadmap = root / cfg["paths"]["roadmap"]
    if not roadmap.is_file():
        return False
    fid = f"{cfg['features']['id_prefix']}{number}"
    rx = re.compile(cfg["features"]["roadmap_feature_regex"])
    for line in roadmap.read_text(encoding="utf-8", errors="replace").splitlines():
        m = rx.match(line)
        if m and m.group(1) == fid and m.group(2) == slug:
            return True
    return False


def create(root: Path, cfg: Dict[str, Any], number: str, slug: str, force: bool = False) -> Tuple[Path, List[str]]:
    number = validate_number(number, int(cfg["features"]["id_digits"]))
    slug = validate_slug(slug)
    templates = root / cfg["paths"]["templates_dir"]
    if not templates.is_dir():
        raise FileNotFoundError(f"template directory {templates} does not exist")
    features = root / cfg["paths"]["features_dir"]
    if features.is_dir():
        for entry in features.iterdir():
            if entry.name.split("-", 1)[0] == number and entry.name != f"{number}-{slug}":
                raise FileExistsError(f"feature number {number} is already used by {entry.relative_to(root)}")
    dest = features / f"{number}-{slug}"
    files = []
    for path in sorted(p for p in templates.rglob("*") if p.is_file()):
        rel = path.relative_to(templates)
        try:
            content = substitute(path.read_text(encoding="utf-8"), number, slug, cfg)
            files.append((rel, content.encode("utf-8")))
        except UnicodeDecodeError:
            files.append((rel, path.read_bytes()))
    if not files:
        raise FileNotFoundError(f"{templates} contains no files")
    written: List[str] = []
    dest.mkdir(parents=True, exist_ok=True)
    for rel, data in files:
        target = dest / rel
        if target.exists() and not force:
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        written.append(str(target.relative_to(root)))
    return dest, written
