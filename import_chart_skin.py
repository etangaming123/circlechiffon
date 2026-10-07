"""
Import a MajdataPlay note skin for /cc-chart's local renderer.

    python import_chart_skin.py <path to MajdataPlay checkout> [--skin Deluxe]

Copies the skin's PNGs, a few of MajdataPlay's effect sprites and its tap
hit sound (used by the "game" render mode), and extracts every slide shape's
arrow layout from MajdataPlay's slide prefabs into
`assets/chart_skin/slides.json`.

**Nothing this writes may be committed.** MajdataPlay is GPL-3.0 and its
"Deluxe" skin isn't even in MajdataPlay's own repository (it's game art);
this repo is MIT and public. `assets/chart_skin/` is gitignored - each host
runs this once against its own MajdataPlay checkout. Without it, /cc-chart
falls back to drawing mai-notes-style vector notes.
"""

import argparse
import json
import math
import re
import shutil
import sys
from pathlib import Path

OUT_DIR = Path(__file__).resolve().parent / "assets" / "chart_skin"

_SKIN_SUBDIRS = (
    "TapSkins", "StarSkins", "HoldSkins", "SlideSkins", "WifiSkins", "TouchSkins",
    "TouchHoldSkins", "JudgeTextSkins", "SlideOKSkins", "NoteGuideSkins",
)
_SKIN_FILES = ("outline.png",)
_EFFECT_SPRITES = (
    "Sprites/Game/EffectSprites/Star.png",
    "Sprites/Game/EffectSprites/StarWhite.png",
    "Sprites/Game/CircleMask.png",
    "Sprites/Game/Firework_new.png",
)
_SOUNDS = ("StreamingAssets/SFX/answer.wav",)

# NoteLoader.cs SLIDE_PREFAB_MAP: shape name -> index into Game.unity's
# `slidePrefab` array. ("Ex" is the extended-slide prefab; not a shape.)
_SLIDE_PREFAB_MAP = {
    "line3": 0, "line4": 1, "line5": 2, "line6": 3, "line7": 4,
    **{f"circle{k}": 4 + k for k in range(1, 9)},
    "v1": 41, "v2": 13, "v3": 14, "v4": 15, "v6": 16, "v7": 17, "v8": 18,
    **{f"ppqq{k}": 18 + k for k in range(1, 9)},
    **{f"pq{k}": 26 + k for k in range(1, 9)},
    "s": 35, "wifi": 36, "L2": 37, "L3": 38, "L4": 39, "L5": 40,
}

_DOC_RE = re.compile(r"^--- !u!(\d+) &(-?\d+)( stripped)?\s*$", re.M)


def _z_angle(z: float, w: float) -> float:
    """Degrees, counter-clockwise (Unity convention), from a pure-Z quaternion."""
    return math.degrees(2.0 * math.atan2(z, w))


def _vec(text: str, key: str) -> dict[str, float] | None:
    m = re.search(key + r":\s*\{([^}]*)\}", text)
    if not m:
        return None
    out = {}
    for part in m.group(1).split(","):
        k, _, v = part.partition(":")
        try:
            out[k.strip()] = float(v)
        except ValueError:
            pass
    return out


def _parse_unity_yaml(text: str) -> dict[str, dict]:
    """fileID -> {class, stripped, body} for every document in the file."""
    docs = {}
    matches = list(_DOC_RE.finditer(text))
    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        docs[m.group(2)] = {"class": int(m.group(1)), "stripped": bool(m.group(3)), "body": text[m.end():end]}
    return docs


def _modifications(body: str) -> list[tuple[str, str, str]]:
    """(target fileID, propertyPath, value) from a PrefabInstance."""
    mods = []
    for m in re.finditer(
        r"- target: \{fileID: (-?\d+)[^}]*\}\s*\n\s*propertyPath: (\S+)\s*\n\s*value: ?(.*)", body
    ):
        mods.append((m.group(1), m.group(2), m.group(3).strip()))
    return mods


def _transform_info(docs: dict, file_id: str) -> dict:
    doc = docs[file_id]
    body = doc["body"]
    if not doc["stripped"]:
        pos = _vec(body, "m_LocalPosition") or {}
        rot = _vec(body, "m_LocalRotation") or {}
        scale = _vec(body, "m_LocalScale") or {}
        go = re.search(r"m_GameObject: \{fileID: (-?\d+)\}", body)
        name = ""
        if go and go.group(1) in docs:
            nm = re.search(r"m_Name: (.*)", docs[go.group(1)]["body"])
            name = nm.group(1).strip() if nm else ""
        return {
            "name": name, "x": pos.get("x", 0.0), "y": pos.get("y", 0.0),
            "angle": _z_angle(rot.get("z", 0.0), rot.get("w", 1.0)),
            "sx": scale.get("x", 1.0), "sy": scale.get("y", 1.0),
        }

    # A stripped Transform belongs to a nested prefab; its overrides live in
    # the PrefabInstance's modification list.
    src = re.search(r"m_CorrespondingSourceObject: \{fileID: (-?\d+)", body).group(1)
    inst = re.search(r"m_PrefabInstance: \{fileID: (-?\d+)\}", body).group(1)
    mods = _modifications(docs[inst]["body"])
    vals: dict[str, str] = {}
    name = ""
    for target, path, value in mods:
        if target == src:
            vals[path] = value
        if path == "m_Name":
            name = value
    f = lambda k, d=0.0: float(vals.get(k, d))  # noqa: E731
    return {
        "name": name, "x": f("m_LocalPosition.x"), "y": f("m_LocalPosition.y"),
        "angle": _z_angle(f("m_LocalRotation.z"), f("m_LocalRotation.w", 1.0)),
        "sx": f("m_LocalScale.x", 1.0), "sy": f("m_LocalScale.y", 1.0),
    }


def _parse_slide_prefab(path: Path) -> dict:
    docs = _parse_unity_yaml(path.read_text(encoding="utf-8"))
    root = None
    for fid, doc in docs.items():
        if doc["class"] == 4 and not doc["stripped"] and re.search(r"m_Father: \{fileID: 0\}", doc["body"]):
            root = fid
            break
    if root is None:
        raise ValueError(f"{path.name}: no root Transform")
    children_block = re.search(r"m_Children:\s*\n((?:\s*- \{fileID: -?\d+\}\s*\n)*)", docs[root]["body"])
    child_ids = re.findall(r"fileID: (-?\d+)", children_block.group(1)) if children_block else []

    arrows, text = [], None
    for cid in child_ids:
        info = _transform_info(docs, cid)
        if info["name"].startswith("Just"):
            text = {"x": info["x"], "y": info["y"], "angle": info["angle"], "kind": info["name"]}
        else:
            arrows.append([round(info["x"], 5), round(info["y"], 5), round(info["angle"], 4),
                           round(info["sx"], 4)])
    return {"prefab": path.name, "arrows": arrows, "text": text}


def _guid_index(assets: Path) -> dict[str, Path]:
    index = {}
    for meta in (assets / "Prefabs").rglob("*.prefab.meta"):
        m = re.search(r"^guid: ([0-9a-f]+)", meta.read_text(encoding="utf-8"), re.M)
        if m:
            index[m.group(1)] = meta.with_suffix("")
    return index


def _slide_prefab_paths(assets: Path) -> list[Path]:
    scene = (assets / "Scenes" / "Game.unity").read_text(encoding="utf-8")
    block = re.search(r"\n  slidePrefab:\s*\n((?:  - \{[^\n]*\}\s*\n)+)", scene)
    if not block:
        raise ValueError("couldn't find slidePrefab in Scenes/Game.unity")
    guids = re.findall(r"guid: ([0-9a-f]+)", block.group(1))
    index = _guid_index(assets)
    missing = [g for g in guids if g not in index]
    if missing:
        raise ValueError(f"slidePrefab GUIDs with no prefab: {missing}")
    return [index[g] for g in guids]


def extract_slides(assets: Path) -> dict:
    paths = _slide_prefab_paths(assets)
    shapes = {}
    for name, idx in _SLIDE_PREFAB_MAP.items():
        data = _parse_slide_prefab(paths[idx])
        if not data["arrows"]:
            raise ValueError(f"{name} ({paths[idx].name}) has no arrows")
        if data["text"] is None:
            raise ValueError(f"{name} ({paths[idx].name}) has no judge-text anchor")
        shapes[name] = data
    return {"source": "MajdataPlay slide prefabs", "unit_ring_radius": 4.8, "shapes": shapes}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("majdataplay", type=Path, help="path to a MajdataPlay checkout")
    ap.add_argument("--skin", default="Deluxe", help="skin folder under StreamingAssets/Skins (default: Deluxe)")
    args = ap.parse_args()

    assets = args.majdataplay / "Assets"
    skin = assets / "StreamingAssets" / "Skins" / args.skin
    if not skin.is_dir():
        print(f"No skin at {skin}", file=sys.stderr)
        return 1

    slides = extract_slides(assets)

    if OUT_DIR.exists():
        shutil.rmtree(OUT_DIR)
    OUT_DIR.mkdir(parents=True)
    copied = 0
    for sub in _SKIN_SUBDIRS:
        src = skin / sub
        if not src.is_dir():
            print(f"warning: skin has no {sub}/", file=sys.stderr)
            continue
        for png in src.glob("*.png"):
            (OUT_DIR / sub).mkdir(exist_ok=True)
            shutil.copy2(png, OUT_DIR / sub / png.name)
            copied += 1
    for name in _SKIN_FILES:
        if (skin / name).exists():
            shutil.copy2(skin / name, OUT_DIR / name)
            copied += 1
    (OUT_DIR / "Effects").mkdir()
    for rel in _EFFECT_SPRITES:
        src = assets / rel
        if src.exists():
            shutil.copy2(src, OUT_DIR / "Effects" / src.name)
            copied += 1
        else:
            print(f"warning: missing effect sprite {rel}", file=sys.stderr)
    (OUT_DIR / "SFX").mkdir()
    for rel in _SOUNDS:
        src = assets / rel
        if src.exists():
            shutil.copy2(src, OUT_DIR / "SFX" / src.name)
            copied += 1
        else:
            print(f"warning: missing sound {rel} (game mode will use mai-notes' tap sound)", file=sys.stderr)

    (OUT_DIR / "slides.json").write_text(json.dumps(slides, indent=1), encoding="utf-8")
    (OUT_DIR / "SOURCE.txt").write_text(
        f"Imported from {args.majdataplay.resolve()} (skin: {args.skin}).\n"
        "MajdataPlay is GPL-3.0; the skin art is not ours. Do not commit this folder.\n",
        encoding="utf-8",
    )

    counts = {k: len(v["arrows"]) for k, v in slides["shapes"].items()}
    print(f"Copied {copied} files, extracted {len(counts)} slide shapes into {OUT_DIR}")
    print("  " + ", ".join(f"{k}:{n}" for k, n in counts.items()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
