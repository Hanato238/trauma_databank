"""
AIS 2005 Update 2008 コードブック 画像 → JSON 変換スクリプト（renamed版）

output/renamed/ ディレクトリ構造:
  00_intro/       ← スキップ
  01_head/        ← head_001.jpg, head_002.jpg, ...
  02_face/        ← face_001.jpg, ...
  03_neck/
  04_thorax/
  05_abdomen/
  06_spine/
  07_extremity/
  08_other/
  99_index/       ← スキップ

処理フロー:
  1. 各サブディレクトリの画像をGemini 2.5 Flashで解析
  2. 結果を output/results_renamed/{部位}/{画像名}.json に保存
  3. 中断時は既存JSONがある画像をスキップして再開可能
"""

import os
import sys
import json
import time
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

IMAGES_DIR  = Path("/workspace/output/renamed")
RESULTS_DIR = Path("/workspace/output/results_renamed")

# スキップ対象のディレクトリ（接頭辞で判定）
SKIP_DIRS = {"00_intro", "99_index"}

# ディレクトリ名 → body_part キー
DIR_TO_BODY_PART = {
    "01_head":      "head",
    "02_face":      "face",
    "03_neck":      "neck",
    "04_thorax":    "thorax",
    "05_abdomen":   "abdomen",
    "06_spine":     "spine",
    "07_extremity": "extremity",
    "08_other":     "other",
}

BODY_PART_NAMES = {
    "head":       "Head / 頭部",
    "face":       "Face / 顔面",
    "neck":       "Neck / 頸部",
    "thorax":     "Thorax / 胸部",
    "abdomen":    "Abdomen / 腹部",
    "spine":      "Spine / 脊椎",
    "extremity":  "Extremity / 四肢",
    "other":      "Other / その他",
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
                print(f"  [リトライ {attempt+1}/{retries}, {wait}秒待機]", end="", flush=True)
                time.sleep(wait)
            else:
                print(f"  [ERROR: {err[:80]}]")
                return {"page_type": "error", "body_part": "unknown", "entries": [], "error": err}


def collect_images() -> list[tuple[str, str, Path]]:
    """
    画像を収集して (dir_name, body_part, image_path) のリストを返す。
    00_intro, 99_index はスキップ。
    """
    images = []
    for subdir in sorted(IMAGES_DIR.iterdir()):
        if not subdir.is_dir():
            continue
        if subdir.name in SKIP_DIRS:
            continue
        body_part = DIR_TO_BODY_PART.get(subdir.name)
        if body_part is None:
            print(f"WARNING: 未知のディレクトリ {subdir.name} をスキップ")
            continue
        for img_path in sorted(subdir.glob("*.jpg")):
            images.append((subdir.name, body_part, img_path))
    return images


def process_images(client, images: list[tuple[str, str, Path]]):
    """各画像をGeminiで解析してJSONを保存"""
    total = len(images)

    for i, (dir_name, body_part, img_path) in enumerate(images, 1):
        # 部位ごとのサブディレクトリに結果を保存
        result_dir = RESULTS_DIR / body_part
        result_dir.mkdir(parents=True, exist_ok=True)
        result_file = result_dir / f"{img_path.stem}.json"

        if result_file.exists():
            print(f"[{i:3d}/{total}] {body_part}/{img_path.name} ... スキップ（処理済み）")
            continue

        print(f"[{i:3d}/{total}] {body_part}/{img_path.name}", end=" ... ", flush=True)
        result = call_gemini(client, img_path)

        # メタデータを付与
        result["source_dir"] = dir_name
        result["source_file"] = img_path.name
        result["body_part_normalized"] = body_part

        result_file.write_text(json.dumps(result, ensure_ascii=False, indent=2))
        entry_count = len(result.get("entries", []))
        print(f"{result.get('page_type', '?')} | {entry_count}件")

        time.sleep(4)


def print_summary():
    """処理結果のサマリーを表示"""
    print("\n--- 処理結果サマリー ---")
    total_files = 0
    total_entries = 0

    for body_part in DIR_TO_BODY_PART.values():
        result_dir = RESULTS_DIR / body_part
        if not result_dir.exists():
            continue
        json_files = list(result_dir.glob("*.json"))
        entries = 0
        for jf in json_files:
            data = json.loads(jf.read_text())
            entries += len(data.get("entries", []))
        name = BODY_PART_NAMES.get(body_part, body_part)
        print(f"  {name}: {len(json_files)}ページ, {entries}エントリ")
        total_files += len(json_files)
        total_entries += entries

    print(f"\n  合計: {total_files}ページ, {total_entries}エントリ")


def main():
    print("=== AIS 2005 コードブック（renamed）→ JSON 変換 ===\n")

    from google import genai

    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        print("ERROR: GEMINI_API_KEY が .env ファイルに設定されていません")
        sys.exit(1)

    client = genai.Client(api_key=api_key)

    # 画像収集
    images = collect_images()
    if not images:
        print(f"ERROR: {IMAGES_DIR} に処理対象の画像が見つかりません")
        sys.exit(1)

    # スキップ対象を表示
    for skip_dir in sorted(SKIP_DIRS):
        skip_path = IMAGES_DIR / skip_dir
        if skip_path.exists():
            count = len(list(skip_path.glob("*.jpg")))
            print(f"スキップ: {skip_dir}/ ({count}枚)")

    print(f"対象画像: {len(images)}枚")

    # 処理済みカウント
    done = 0
    for _, body_part, img_path in images:
        result_file = RESULTS_DIR / body_part / f"{img_path.stem}.json"
        if result_file.exists():
            done += 1
    print(f"処理済み: {done}枚 / 残り: {len(images) - done}枚\n")

    print("=== Gemini画像解析 ===\n")
    process_images(client, images)

    print_summary()

    print(f"\n=== 完了 ===")
    print(f"  結果JSON: {RESULTS_DIR}/")


if __name__ == "__main__":
    main()
