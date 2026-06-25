"""
AIS 2005 Update 2008 コードブック 画像 → Markdown 変換スクリプト

処理フロー:
  1. 各画像をGemini 2.5 Flashで解析 → output/results/{画像名}.json に保存
  2. 全画像処理後、JSONをまとめて部位別Markdownを生成
  3. 中断時はresultsファイルがある画像をスキップして再開可能
"""

import os
import sys
import json
import time
import shutil
from pathlib import Path
from collections import defaultdict
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

IMAGES_DIR    = Path("/workspace/images")
RESULTS_DIR   = Path("/workspace/output/results")
OUTPUT_MD_DIR = Path("/workspace/output/markdown")
OUTPUT_IMG_DIR = Path("/workspace/output/renamed")

BODY_PART_MAP = {
    "head": "head", "頭部": "head",
    "face": "face", "顔面": "face",
    "neck": "neck", "頸部": "neck", "頚部": "neck",
    "thorax": "thorax", "胸部": "thorax",
    "abdomen": "abdomen", "腹部": "abdomen",
    "spine": "spine", "脊椎": "spine", "脊髄": "spine",
    "upper extremity": "extremity_upper", "上肢": "extremity_upper",
    "lower extremity": "extremity_lower", "下肢": "extremity_lower",
    "extremity": "extremity", "四肢": "extremity",
    "surface": "surface", "体表": "surface", "皮膚": "surface", "external": "surface",
    "other": "other", "その他": "other",
    "intro": "intro", "序文": "intro", "preface": "intro",
    "unknown": "unknown",
}

BODY_PART_ORDER = [
    "head", "face", "neck", "thorax", "abdomen",
    "spine", "extremity_upper", "extremity_lower", "extremity", "surface", "other",
]

BODY_PART_NAMES = {
    "head":             "Head / 頭部",
    "face":             "Face / 顔面",
    "neck":             "Neck / 頸部",
    "thorax":           "Thorax / 胸部",
    "abdomen":          "Abdomen / 腹部",
    "spine":            "Spine / 脊椎",
    "extremity":        "Extremity / 四肢",
    "extremity_upper":  "Upper Extremity / 上肢",
    "extremity_lower":  "Lower Extremity / 下肢",
    "surface":          "External (Skin) / 体表（皮膚）",
    "other":            "Other / その他",
}

GEMINI_PROMPT = """\
この画像はAIS 2005 Update 2008（Abbreviated Injury Scale）コードブックのページです。

以下のJSON形式で内容を抽出してください：

{
  "page_type": "code" または "intro",
  "lang": "ja" または "en" または "both",
  "body_part": "head/face/neck/thorax/abdomen/spine/extremity/surface/other/unknown のいずれか",
  "section_title": "ページ上部のセクション名（なければnull）",
  "entries": [
    {
      "code": "AISコード（小数点含む、例：910200.1）",
      "description_ja": "日本語の損傷名（なければnull）",
      "description_en": "英語の損傷名（なければnull）",
      "severity": 数値（コードの小数部の数字、例：910200.1なら1）,
      "level": 1
    }
  ]
}

注意：
- page_typeが"intro"の場合はentries:[]でよい
- levelはインデントの深さ（1=大項目, 2=中項目, 3=小項目）、必ず整数
- severityは必ず整数（null不可）、コードがなければ0
- JSONのみを返すこと（```や説明文は不要）
"""


def call_gemini(client, image_path: Path, retries: int = 3) -> dict:
    from google import genai

    data = image_path.read_bytes()
    for attempt in range(retries):
        try:
            response = client.models.generate_content(
                model="gemini-2.5-flash",
                contents=[
                    genai.types.Part.from_bytes(data=data, mime_type="image/jpeg"),
                    GEMINI_PROMPT,
                ],
            )
            raw = response.text.strip()
            if "```json" in raw:
                raw = raw.split("```json")[1].split("```")[0].strip()
            elif "```" in raw:
                raw = raw.split("```")[1].split("```")[0].strip()
            result = json.loads(raw)
            # 型安全化
            for e in result.get("entries", []):
                e["level"] = int(e.get("level") or 1)
                e["severity"] = int(e.get("severity") or 0)
            return result
        except json.JSONDecodeError:
            return {"page_type": "unknown", "body_part": "unknown", "entries": []}
        except Exception as e:
            err = str(e)
            if attempt < retries - 1:
                wait = 15 * (attempt + 1)
                print(f" [リトライ {attempt+1}/{retries}, {wait}秒待機]", end="", flush=True)
                time.sleep(wait)
            else:
                print(f" [ERROR: {err[:80]}]")
                return {"page_type": "error", "body_part": "unknown", "entries": [], "error": err}


def normalize_body_part(raw: str) -> str:
    if not raw:
        return "unknown"
    raw_lower = raw.lower().strip()
    # 長いキーから先にマッチ（upper/lower extremityを先に）
    for key in sorted(BODY_PART_MAP.keys(), key=len, reverse=True):
        if key.lower() in raw_lower:
            return BODY_PART_MAP[key]
    return "unknown"


def phase1_extract(client, images: list):
    """Phase 1: 各画像をGeminiで解析してJSONを保存"""
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)

    for i, img_path in enumerate(images, 1):
        result_file = RESULTS_DIR / f"{img_path.stem}.json"
        if result_file.exists():
            print(f"[{i:3d}/{len(images)}] {img_path.name} ... スキップ（処理済み）")
            continue

        print(f"[{i:3d}/{len(images)}] {img_path.name}", end=" ... ", flush=True)
        result = call_gemini(client, img_path)
        result["source_file"] = img_path.name

        body_part = normalize_body_part(result.get("body_part", "unknown"))
        if result.get("page_type") == "intro":
            body_part = "intro"
        result["body_part_normalized"] = body_part

        result_file.write_text(json.dumps(result, ensure_ascii=False, indent=2))
        print(f"{body_part} | {len(result.get('entries', []))}件")

        time.sleep(4)


def phase2_build_markdown():
    """Phase 2: 保存されたJSONから部位別Markdownと画像リネームを生成"""
    OUTPUT_MD_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_IMG_DIR.mkdir(parents=True, exist_ok=True)

    results = sorted(RESULTS_DIR.glob("*.json"))
    body_part_pages = defaultdict(list)

    for r in results:
        data = json.loads(r.read_text())
        bp = data.get("body_part_normalized", "unknown")
        if bp != "intro":
            body_part_pages[bp].append(data)

    # 画像リネーム
    print("\n--- 画像リネーム ---")
    for bp in BODY_PART_ORDER + ["unknown", "other"]:
        pages = body_part_pages.get(bp, [])
        for count, data in enumerate(pages, 1):
            src = IMAGES_DIR / data["source_file"]
            dst = OUTPUT_IMG_DIR / f"{bp}_{count:03d}.jpg"
            if src.exists() and not dst.exists():
                shutil.copy2(src, dst)
                print(f"  {src.name} → {dst.name}")

    # Markdown生成
    print("\n--- Markdownファイル生成 ---")
    total_entries = 0
    for bp in BODY_PART_ORDER + ["unknown", "other"]:
        pages = body_part_pages.get(bp, [])
        if not pages:
            continue

        md_path = OUTPUT_MD_DIR / f"{bp}.md"
        title = BODY_PART_NAMES.get(bp, bp)

        with open(md_path, "w", encoding="utf-8") as f:
            f.write(f"# {title} — AISコード一覧\n\n")
            f.write("> AIS 2005 Update 2008 日本語対訳版\n\n")
            f.write("---\n")

            for data in pages:
                section = data.get("section_title") or ""
                entries = data.get("entries", [])
                if not entries and not section:
                    continue
                if section:
                    f.write(f"\n### {section}\n\n")
                if entries:
                    f.write("| コード | 日本語 | English | AIS重症度 | 階層 |\n")
                    f.write("|--------|--------|---------|-----------|------|\n")
                    for e in entries:
                        code = e.get("code") or ""
                        ja   = e.get("description_ja") or ""
                        en   = e.get("description_en") or ""
                        sev  = e.get("severity") or ""
                        lv   = int(e.get("level") or 1)
                        indent = "　" * (lv - 1)
                        f.write(f"| `{code}` | {indent}{ja} | {indent}{en} | {sev} | {'▶' * lv} |\n")
                    f.write("\n")
                    total_entries += len(entries)

        entry_count = sum(len(d.get("entries", [])) for d in pages)
        print(f"  {md_path.name}: {len(pages)}ページ, {entry_count}エントリ")

    print(f"\n総エントリ数: {total_entries}件")


def main():
    print("=== AIS 2005 コードブック → Markdown 変換 ===\n")

    from google import genai

    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        print("ERROR: GEMINI_API_KEY が .env ファイルに設定されていません")
        sys.exit(1)

    client = genai.Client(api_key=api_key)

    images = sorted(
        list(IMAGES_DIR.glob("*.jpg")) +
        list(IMAGES_DIR.glob("*.JPG")) +
        list(IMAGES_DIR.glob("*.jpeg")) +
        list(IMAGES_DIR.glob("*.png"))
    )
    if not images:
        print(f"ERROR: {IMAGES_DIR} に画像が見つかりません")
        sys.exit(1)

    print(f"対象画像: {len(images)}枚")
    done = sum(1 for img in images if (RESULTS_DIR / f"{img.stem}.json").exists())
    print(f"処理済み: {done}枚 / 残り: {len(images) - done}枚\n")

    print("=== Phase 1: Gemini画像解析 ===\n")
    phase1_extract(client, images)

    print("\n=== Phase 2: Markdown生成 ===")
    phase2_build_markdown()

    print("\n=== 完了 ===")
    print(f"  Markdown: {OUTPUT_MD_DIR}/")
    print(f"  リネーム画像: {OUTPUT_IMG_DIR}/")


if __name__ == "__main__":
    main()
