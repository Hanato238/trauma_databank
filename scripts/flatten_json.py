#!/usr/bin/env python3
"""
Flatten nested AIS JSON to flat structure with explicit parent_code.
Also incorporates image-derived data corrections where applicable.

Usage:
    uv run flatten_json.py <input_json> <output_json>
"""

import sys
import json
import re
from pathlib import Path


def infer_injury_types(description: str) -> list:
    desc_lower = description.lower()
    types_found = []
    injury_keywords = {
        "nfs": ["nfs", "not further specified"],
        "abrasion": ["abrasion"],
        "contusion": ["contusion"],
        "hematoma": ["hematoma"],
        "laceration": ["laceration"],
        "avulsion": ["avulsion", "degloving"],
        "fracture": ["fracture"],
        "dislocation": ["dislocation"],
        "rupture": ["rupture"],
        "perforation": ["perforation", "puncture"],
        "hemorrhage": ["hemorrhage", "blood loss", "hemoperitoneum"],
        "crush": ["crush", "crushing"],
        "burn": ["burn", "necrosis"],
        "amputation": ["amputation"],
        "penetrating": ["penetrating"],
        "blunt": ["blunt"],
        "transection": ["transection", "transected"],
        "thrombosis": ["thrombosis"],
        "devascularization": ["devascularization", "devascularized"],
        "bilateral": ["bilateral"],
        "intimal_tear": ["intimal tear"],
        "stretch": ["stretch"],
    }
    for type_name, keywords in injury_keywords.items():
        if any(kw in desc_lower for kw in keywords):
            types_found.append(type_name)
    return types_found if types_found else ["other"]


def flatten_entries(entries: list, parent_code: str = None, parent_path_ja: str = "", parent_path_en: str = "") -> list:
    """Recursively flatten nested entries, adding parent_code and updating explanation paths."""
    result = []
    for entry in entries:
        code = entry.get("code", "")
        japanese = entry.get("japanese", "")
        english = entry.get("english", "")
        ais_severity = entry.get("ais_severity", 0)
        hierarchy_level = entry.get("hierarchy_level", 1)
        section = entry.get("section", "ABDOMEN")
        iss_body_region = entry.get("iss_body_region", "abdomen")
        children = entry.get("children", [])

        # Rebuild explanation paths from hierarchy
        if hierarchy_level == 1:
            explanation_ja = ""
            explanation_en = ""
        else:
            explanation_ja = parent_path_ja
            explanation_en = parent_path_en

        # Injury types: use existing or infer
        existing_types = entry.get("injury_types", [])
        if existing_types:
            injury_types = existing_types
        else:
            injury_types = infer_injury_types(english)

        flat_entry = {
            "code": code,
            "japanese": japanese,
            "english": english,
            "ais_severity": ais_severity,
            "hierarchy_level": hierarchy_level,
            "section": section,
            "parent_code": parent_code,
            "explanation_ja": explanation_ja,
            "explanation_en": explanation_en,
            "injury_types": injury_types,
            "iss_body_region": iss_body_region,
        }
        result.append(flat_entry)

        # Build path for children
        child_path_ja = (parent_path_ja + " > " + japanese).lstrip(" > ") if japanese else parent_path_ja
        child_path_en = (parent_path_en + " > " + english).lstrip(" > ") if english else parent_path_en

        # Process children recursively
        if children:
            child_entries = flatten_entries(children, parent_code=code,
                                           parent_path_ja=child_path_ja,
                                           parent_path_en=child_path_en)
            result.extend(child_entries)

    return result


def main():
    if len(sys.argv) < 3:
        print(f"Usage: {sys.argv[0]} <input_json> <output_json>")
        sys.exit(1)

    input_path = sys.argv[1]
    output_path = sys.argv[2]

    with open(input_path, encoding="utf-8") as f:
        data = json.load(f)

    body_part = data.get("body_part", "unknown")
    source = data.get("source", "")
    entries = data.get("entries", [])

    flat_entries = flatten_entries(entries)

    output = {
        "body_part": body_part,
        "source": source,
        "total_entries": len(flat_entries),
        "entries": flat_entries,
    }

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"Flattened {len(flat_entries)} entries -> {output_path}")

    # Print stats
    by_level = {}
    for e in flat_entries:
        lvl = e["hierarchy_level"]
        by_level[lvl] = by_level.get(lvl, 0) + 1
    for lvl in sorted(by_level):
        print(f"  Level {lvl}: {by_level[lvl]} entries")


if __name__ == "__main__":
    main()
