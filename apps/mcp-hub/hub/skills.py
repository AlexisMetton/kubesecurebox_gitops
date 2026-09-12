# Lecture des skills montés depuis ConfigMap (Git → ArgoCD)
import os
from pathlib import Path

import yaml

SKILLS_DIR = Path(os.environ.get("SKILLS_DIR", "/skills"))


def _parse_skill(path: Path) -> dict | None:
    text = path.read_text(encoding="utf-8")
    name = path.parent.name if path.name == "SKILL.md" else path.stem
    description = ""
    body = text
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) >= 3:
            meta = yaml.safe_load(parts[1]) or {}
            name = meta.get("name") or name
            description = meta.get("description") or ""
            body = parts[2].lstrip("\n")
    return {
        "name": str(name),
        "description": str(description),
        "body": body,
        "path": str(path),
    }


def list_skills() -> list[dict]:
    if not SKILLS_DIR.is_dir():
        return []
    out = []
    # Fichiers plats ConfigMap (clé=hello) / hello.md / dossiers hello/SKILL.md
    for p in sorted(SKILLS_DIR.rglob("*")):
        if not p.is_file():
            continue
        # Ignorer éventuels fichiers cachés / non-skill
        if p.name.startswith("."):
            continue
        skill = _parse_skill(p)
        if skill:
            out.append(
                {
                    "name": skill["name"],
                    "description": skill["description"],
                }
            )
    # Dédupliquer par name
    seen = set()
    unique = []
    for s in out:
        if s["name"] in seen:
            continue
        seen.add(s["name"])
        unique.append(s)
    return unique


def get_skill(name: str) -> dict | None:
    if not SKILLS_DIR.is_dir():
        return None
    candidates = [
        SKILLS_DIR / name / "SKILL.md",
        SKILLS_DIR / f"{name}.md",
        SKILLS_DIR / name,
    ]
    for p in candidates:
        if p.is_file():
            return _parse_skill(p)
    # ConfigMap kustomize keys plates montées comme fichiers
    for p in SKILLS_DIR.iterdir():
        if not p.is_file():
            continue
        skill = _parse_skill(p)
        if skill and skill["name"] == name:
            return skill
    return None
