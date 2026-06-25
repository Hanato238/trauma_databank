#!/usr/bin/env python3
"""
AIS コードブック JSON に injury_types タグを付与するスクリプト。

タグは各エントリの japanese / english フィールドのパターンマッチで決定し、
`injury_types: [str, ...]` フィールドとして追加する。
既存の injury_types は上書きされる。

使い方:
    uv run tag_injury_types.py [--dry-run]
"""

import json
import re
import sys
from pathlib import Path

DATA_DIR = Path(__file__).parent / "output" / "data" / "json"

# ─── タグ定義 ────────────────────────────────────────────────────────────────
# (tag, japanese_patterns, english_patterns)
# パターンは部分一致（どれか1つでもマッチすればタグ付与）
TAG_RULES: list[tuple[str, list[str], list[str]]] = [
    ("fracture",     ["骨折"],                       ["fracture"]),
    ("dislocation",  ["脱臼"],                       ["dislocation"]),
    ("laceration",   ["裂傷"],                       ["laceration", "tear"]),
    ("contusion",    ["挫傷"],                       ["contusion"]),
    ("rupture",      ["破裂", "断裂"],               ["rupture", "disruption"]),
    ("hematoma",     ["血腫"],                       ["hematoma"]),
    ("hemorrhage",   ["出血"],                       ["hemorrhage", "hemothorax", "hemoperitoneum"]),
    ("perforation",  ["穿孔"],                       ["perforation"]),
    ("crush",        ["挫滅"],                       ["crush"]),
    ("avulsion",     ["剥離", "裂離"],               ["avulsion"]),
    ("sprain",       ["捻挫"],                       ["sprain", "strain"]),
    ("amputation",   ["切断", "離断"],               ["amputation", "transection"]),
    ("burn",         ["熱傷", "火傷"],               ["burn", "scald"]),
    ("pneumothorax", ["気胸"],                       ["pneumothorax"]),
    ("hemothorax",   ["血胸", "血気胸"],             ["hemothorax"]),
    ("abrasion",     ["擦過傷", "擦傷"],             ["abrasion"]),
    ("burn",         ["Ⅱ度", "Ⅲ度"],              ["2nd degree", "3rd degree", "partial thickness", "full thickness"]),
    ("amputation",   ["断頭"],                      ["decapitation"]),
    ("avulsion",     ["デグロービング"],             ["degloving"]),
    ("rupture",      ["靭帯損傷", "靱帯損傷"],      ["ligament injury", "ligament disruption"]),
    ("vascular",     ["静脈", "動脈"],              ["named veins", "named artery"]),
    ("penetrating",  ["吸い込み創", "開放性胸壁"],   ["sucking chest", "open chest"]),
    ("nfs",          ["死亡（詳細な評価なし）", "損傷なし"], ["without further substantiation", "died of"]),
    ("edema",        ["浮腫"],                       ["edema", "swelling"]),
    ("paralysis",    ["麻痺", "麻ひ"],               ["paralysis", "paresis"]),
    ("penetrating",  ["穿通", "穿刺"],               ["penetrating", "puncture"]),
    ("nfs",          ["詳細不明"],                   ["nfs", "not further specified", "unspecified"]),
    ("vascular",     ["血管損傷", "血管裂傷"],       ["vascular injury", "vascular laceration", "devascularization"]),
    ("nerve",        ["神経損傷", "神経麻痺"],        ["nerve injury", "nerve laceration", "spinal cord"]),
]

# 重複タグを排除するため set で扱う
# タグの優先順位（表示・フィルタ用。骨折 > 脱臼骨折 など）
TAG_ORDER = [
    "fracture", "dislocation", "laceration", "contusion", "rupture",
    "hematoma", "hemorrhage", "perforation", "crush", "sprain", "amputation",
    "burn", "pneumothorax", "hemothorax", "avulsion", "edema", "paralysis",
    "penetrating", "vascular", "nerve", "nfs",
]


def assign_tags(japanese: str, english: str) -> list[str]:
    ja = japanese or ""
    en = (english or "").lower()
    tags: set[str] = set()

    for tag, ja_pats, en_pats in TAG_RULES:
        if any(p in ja for p in ja_pats) or any(p in en for p in en_pats):
            tags.add(tag)

    # 順序を保ったリストとして返す
    ordered = [t for t in TAG_ORDER if t in tags]
    extra   = sorted(tags - set(TAG_ORDER))
    return ordered + extra


def tag_entries(entries: list[dict], parent_tags: list[str] | None = None) -> int:
    """再帰的にタグ付与。タグなしの子エントリは親タグを継承する。"""
    count = 0
    for e in entries:
        tags = assign_tags(e.get("japanese", ""), e.get("english", ""))
        if not tags and parent_tags:
            tags = list(parent_tags)  # 親タグを継承
        e["injury_types"] = tags
        count += 1
        count += tag_entries(e.get("children", []), parent_tags=tags or parent_tags)
    return count


def main(dry_run: bool = False) -> None:
    files = sorted(DATA_DIR.glob("*.json"))
    if not files:
        print(f"ERROR: {DATA_DIR} に JSON ファイルが見つかりません")
        sys.exit(1)

    total = 0
    for fpath in files:
        data = json.loads(fpath.read_text(encoding="utf-8"))
        n = tag_entries(data.get("entries", []))
        total += n
        if dry_run:
            print(f"[dry-run] {fpath.name}: {n} エントリ")
        else:
            fpath.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"✓ {fpath.name}: {n} エントリにタグ付与")

    print(f"\n合計 {total} エントリ処理{'（dry-run）' if dry_run else ''}")

    if not dry_run:
        _show_stats()


def _show_stats() -> None:
    from collections import Counter

    def flatten(entries):
        for e in entries:
            yield e
            yield from flatten(e.get("children", []))

    tag_counter: Counter = Counter()
    no_tag = 0

    for fpath in sorted(DATA_DIR.glob("*.json")):
        data = json.loads(fpath.read_text(encoding="utf-8"))
        for e in flatten(data.get("entries", [])):
            tags = e.get("injury_types", [])
            if tags:
                tag_counter.update(tags)
            else:
                no_tag += 1

    print("\n=== タグ分布 ===")
    for tag, cnt in tag_counter.most_common():
        bar = "█" * (cnt // 10)
        print(f"  {tag:<15s} {cnt:4d}  {bar}")
    if no_tag:
        print(f"\n  タグなし: {no_tag} エントリ")


if __name__ == "__main__":
    dry = "--dry-run" in sys.argv
    main(dry_run=dry)
