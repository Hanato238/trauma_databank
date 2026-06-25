#!/usr/bin/env python3
"""
AIS Codebook Image Analyzer
Analyzes AIS codebook page images using Gemini API and generates flat JSON structure.

Usage:
    uv run --with google-genai analyze_codebook.py <section> <image_dir> <output_dir>

Example:
    uv run --with google-genai analyze_codebook.py abdomen /workspace/output/renamed/05_abdomen /workspace/output/codebook/json_v2
"""

import sys
import os
import json
import base64
import time
import re
import subprocess
from pathlib import Path

SECTION_MAP = {
    "abdomen": "ABDOMEN",
    "extremity": "EXTREMITY",
    "face": "FACE",
    "head": "HEAD",
    "neck": "NECK",
    "other": "OTHER",
    "spine": "SPINE",
    "surface": "SURFACE",
    "thorax": "THORAX",
}

ISS_REGION_MAP = {
    "ABDOMEN": "abdomen",
    "EXTREMITY": "extremity",
    "FACE": "face",
    "HEAD": "head",
    "NECK": "neck",
    "OTHER": "other",
    "SPINE": "spine",
    "SURFACE": "external",
    "THORAX": "thorax",
}

PROMPT_TEMPLATE = """You are analyzing a page from the AIS (Abbreviated Injury Scale) 2005 codebook.
Extract ALL injury codes visible on this page and return them as a JSON array.

Rules for extraction:
1. Each entry must have these fields:
   - code: AIS code (e.g. "500099.9") - the number before the description
   - english: English description of the injury
   - ais_severity: integer severity (0-6 or 9), extracted from the last digit after the decimal point
   - hierarchy_level: 1 for main entries (leftmost/bold), 2 for sub-entries (indented once), 3 for sub-sub-entries (indented twice), etc.
   - parent_code: the AIS code of the parent entry (null if hierarchy_level=1). This must be the actual AIS code string of the immediate parent.

2. Hierarchy is determined by visual indentation in the codebook:
   - Bold/leftmost entries = level 1
   - Indented once = level 2
   - Indented twice = level 3

3. Do NOT include:
   - AIS98 cross-reference columns (the numbers in the right columns labeled "AIS98")
   - FCI column values
   - Footnote text
   - Section headers like "WHOLE AREA", "ABDOMEN" titles

4. If a code appears with multiple sub-descriptions that share a parent, make sure parent_code is set correctly.

Return ONLY a valid JSON array like:
[
  {
    "code": "500099.9",
    "english": "Injuries to the Whole Abdomen NFS",
    "ais_severity": 9,
    "hierarchy_level": 1,
    "parent_code": null
  },
  {
    "code": "500999.9",
    "english": "Died of abdominal injury without further substantiation of injuries or no autopsy confirmation of specific injuries",
    "ais_severity": 9,
    "hierarchy_level": 2,
    "parent_code": "500099.9"
  }
]

Do not include markdown code fences. Return only the raw JSON array.
"""


def encode_image(image_path: str) -> str:
    """Encode image to base64."""
    with open(image_path, "rb") as f:
        return base64.b64encode(f.read()).decode("utf-8")


def analyze_image_with_gemini(image_path: str, api_key: str = None) -> list:
    """Analyze a single image using Gemini API."""
    try:
        from google import genai
        from google.genai import types

        if api_key:
            client = genai.Client(api_key=api_key)
        else:
            # Try to use OAuth credentials
            import google.oauth2.credentials
            creds_path = os.path.expanduser("~/.gemini/oauth_creds.json")
            with open(creds_path) as f:
                creds_data = json.load(f)
            creds = google.oauth2.credentials.Credentials(
                token=creds_data["access_token"],
                refresh_token=creds_data["refresh_token"],
                token_uri="https://oauth2.googleapis.com/token",
                client_id=creds_data.get("client_id", ""),
                client_secret=creds_data.get("client_secret", ""),
            )
            client = genai.Client(credentials=creds)

        image_data = encode_image(image_path)
        response = client.models.generate_content(
            model="gemini-2.5-pro",
            contents=[
                types.Part.from_bytes(
                    data=base64.b64decode(image_data),
                    mime_type="image/jpeg",
                ),
                PROMPT_TEMPLATE,
            ],
        )

        text = response.text.strip()
        # Remove markdown fences if present
        text = re.sub(r'^```(?:json)?\s*', '', text)
        text = re.sub(r'\s*```$', '', text)

        return json.loads(text)

    except Exception as e:
        print(f"  Error analyzing {image_path}: {e}", file=sys.stderr)
        return []


def build_flat_entries(raw_entries: list, section: str, existing_entries: list) -> list:
    """
    Convert raw Gemini output to the target flat JSON structure.
    Adds explanation_ja/en fields and iss_body_region.
    """
    iss_region = ISS_REGION_MAP.get(section, section.lower())

    # Build a lookup of parent codes to their english descriptions
    # Use existing entries plus new ones
    code_to_entry = {e["code"]: e for e in existing_entries}
    for e in raw_entries:
        code_to_entry[e["code"]] = e

    result = []
    for entry in raw_entries:
        if not entry.get("code") or not entry.get("english"):
            continue

        # Build explanation chain (parent breadcrumb)
        explanation_parts = []
        parent_code = entry.get("parent_code")
        # Walk up the parent chain
        visited = set()
        current_parent = parent_code
        parent_chain = []
        while current_parent and current_parent not in visited:
            visited.add(current_parent)
            p = code_to_entry.get(current_parent, {})
            if p.get("english"):
                parent_chain.insert(0, p["english"])
            current_parent = p.get("parent_code")

        explanation_en = " > ".join(parent_chain) if parent_chain else ""

        flat = {
            "code": entry["code"],
            "japanese": "",  # Will be filled by translation step
            "english": entry["english"],
            "ais_severity": entry.get("ais_severity", 0),
            "hierarchy_level": entry.get("hierarchy_level", 1),
            "section": section,
            "parent_code": entry.get("parent_code"),
            "explanation_ja": "",
            "explanation_en": explanation_en,
            "injury_types": infer_injury_types(entry["english"]),
            "iss_body_region": iss_region,
        }
        result.append(flat)

    return result


def infer_injury_types(description: str) -> list:
    """Infer injury type tags from description text."""
    desc_lower = description.lower()
    types_found = []

    injury_keywords = {
        "nfs": ["nfs", "not further specified"],
        "abrasion": ["abrasion"],
        "contusion": ["contusion"],
        "hematoma": ["hematoma"],
        "laceration": ["laceration"],
        "avulsion": ["avulsion"],
        "fracture": ["fracture"],
        "dislocation": ["dislocation"],
        "rupture": ["rupture"],
        "perforation": ["perforation"],
        "hemorrhage": ["hemorrhage", "blood loss", "hemoperitoneum"],
        "crush": ["crush", "crushing"],
        "burn": ["burn"],
        "amputation": ["amputation"],
        "penetrating": ["penetrating"],
        "blunt": ["blunt"],
        "minor": ["minor"],
        "major": ["major"],
        "complete": ["complete"],
        "partial": ["partial"],
        "transection": ["transection", "transected"],
        "thrombosis": ["thrombosis"],
        "devascularization": ["devascularization", "devascularized"],
    }

    for type_name, keywords in injury_keywords.items():
        if any(kw in desc_lower for kw in keywords):
            types_found.append(type_name)

    return types_found if types_found else ["other"]


def process_section(section_name: str, image_dir: str, output_dir: str, api_key: str = None):
    """Process all images for a section and create the JSON output."""
    section = SECTION_MAP.get(section_name.lower(), section_name.upper())
    image_dir_path = Path(image_dir)
    output_dir_path = Path(output_dir)
    output_dir_path.mkdir(parents=True, exist_ok=True)

    images = sorted(image_dir_path.glob("*.jpg")) + sorted(image_dir_path.glob("*.png"))
    print(f"Found {len(images)} images in {image_dir}")

    all_raw_entries = []
    all_flat_entries = []

    for i, image_path in enumerate(images):
        print(f"  Processing {image_path.name} ({i+1}/{len(images)})...")
        raw = analyze_image_with_gemini(str(image_path), api_key)
        if raw:
            print(f"    Found {len(raw)} entries")
            all_raw_entries.extend(raw)
            flat = build_flat_entries(raw, section, all_raw_entries)
            all_flat_entries.extend(flat)
        else:
            print(f"    No entries found (may be title page)")

        # Small delay to avoid rate limits
        if i < len(images) - 1:
            time.sleep(1)

    # Deduplicate by code
    seen_codes = set()
    deduped = []
    for entry in all_flat_entries:
        if entry["code"] not in seen_codes:
            seen_codes.add(entry["code"])
            deduped.append(entry)

    output = {
        "body_part": section_name.lower(),
        "source": f"{section_name.lower()}_images",
        "total_entries": len(deduped),
        "entries": deduped,
    }

    output_file = output_dir_path / f"{section_name.lower()}.json"
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"\nSaved {len(deduped)} entries to {output_file}")
    return deduped


if __name__ == "__main__":
    if len(sys.argv) < 4:
        print(__doc__)
        sys.exit(1)

    section = sys.argv[1]
    image_dir = sys.argv[2]
    output_dir = sys.argv[3]
    api_key = sys.argv[4] if len(sys.argv) > 4 else os.environ.get("GEMINI_API_KEY")

    process_section(section, image_dir, output_dir, api_key)
