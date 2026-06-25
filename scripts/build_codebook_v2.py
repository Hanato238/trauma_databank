"""
AIS コードブック v2 生成スクリプト（2段階）

Stage 1: フィールド整備
  - output/merged/0*.json から読み込み
  - codebook/json からメタデータ（iss_body_region, section, injury_types）を移植
  - description_ja / description_en を null で追加
  - 不要フィールド（collision, source_files 等）を除去
  - 出力: output/codebook_v2/stage1/{body_part}.json

Stage 2: children ツリー再構築
  - output/codebook_v2/stage1/{body_part}.json から読み込み
  - hierarchy_level を使って children ネスト構造を組み立て
  - 出力: output/codebook_v2/json/{body_part}.json

使用例:
  .venv/bin/python scripts/build_codebook_v2.py --stage 1
  .venv/bin/python scripts/build_codebook_v2.py --stage 2
  .venv/bin/python scripts/build_codebook_v2.py --all
"""

import argparse
import json
from pathlib import Path

MERGED_DIR   = Path("/workspace/output/merged")
CODEBOOK_DIR = Path("/workspace/output/codebook/json")
STAGE1_DIR   = Path("/workspace/output/codebook_v2/stage1")
OUTPUT_DIR   = Path("/workspace/output/codebook_v2/json")

BODY_PART_NAMES = {
    "head":      "Head / 頭部",
    "face":      "Face / 顔面",
    "neck":      "Neck / 頸部",
    "thorax":    "Thorax / 胸部",
    "abdomen":   "Abdomen / 腹部",
    "spine":     "Spine / 脊椎",
    "extremity": "Extremity / 四肢",
    "other":     "Other / その他",
}

# merged ファイル名プレフィックス（ソート順を保証）
MERGED_FILES = [
    ("head",      "01_head.json"),
    ("face",      "02_face.json"),
    ("neck",      "03_neck.json"),
    ("thorax",    "04_thorax.json"),
    ("abdomen",   "05_abdomen.json"),
    ("spine",     "06_spine.json"),
    ("extremity", "07_extremity.json"),
    ("other",     "08_other.json"),
]


# ─── codebook メタデータインデックス ──────────────────────────────────────────

def build_codebook_index() -> dict[str, dict]:
    """
    codebook/json の全エントリを再帰的に走査し、
    code → {iss_body_region, section, injury_types} のインデックスを返す。
    """
    index: dict[str, dict] = {}

    def walk(entries: list[dict]) -> None:
        for e in entries:
            code = e.get("code")
            if code:
                index[code] = {
                    "iss_body_region": e.get("iss_body_region"),
                    "section":         e.get("section"),
                    "injury_types":    list(e.get("injury_types") or []),
                }
            walk(e.get("children") or [])

    for f in CODEBOOK_DIR.glob("*.json"):
        data = json.loads(f.read_text(encoding="utf-8"))
        walk(data.get("entries", []))

    return index


# ─── Stage 1: フィールド整備 ──────────────────────────────────────────────────

def _clean_entry(raw: dict, cb_index: dict, fallback_region: str | None) -> dict:
    """
    merged エントリ1件を整備して返す。
    - codebook に同一コードがあれば iss_body_region / section / injury_types を上書き
    - なければ merged の値をそのまま使用
    - description_ja / description_en を null で追加
    - collision / source_files を除去
    """
    code = raw.get("code", "")
    cb   = cb_index.get(code, {})

    return {
        "code":           code,
        "title_ja":       raw.get("title_ja"),
        "title_en":       raw.get("title_en"),
        "ais_severity":   int(raw.get("ais_severity") or 0),
        "hierarchy_level": int(raw.get("hierarchy_level") or 1),
        "section":        cb.get("section")         or raw.get("section"),
        "iss_body_region": cb.get("iss_body_region") or raw.get("iss_body_region") or fallback_region,
        "injury_types":   cb.get("injury_types")    if code in cb_index else list(raw.get("injury_types") or []),
        "description_ja": None,
        "description_en": None,
    }


def run_stage1(cb_index: dict) -> None:
    STAGE1_DIR.mkdir(parents=True, exist_ok=True)
    print("=== Stage 1: フィールド整備 ===\n")

    for body_part, fname in MERGED_FILES:
        src = MERGED_DIR / fname
        if not src.exists():
            print(f"  {fname}: 見つかりません → スキップ")
            continue

        raw_data = json.loads(src.read_text(encoding="utf-8"))
        fallback_region = raw_data.get("iss_body_region")

        cleaned = [
            _clean_entry(e, cb_index, fallback_region)
            for e in raw_data.get("entries", [])
        ]

        # codebook から上書きされたコードを集計
        overwritten = sum(
            1 for e in raw_data.get("entries", [])
            if e.get("code") in cb_index
        )

        out = {
            "body_part":      body_part,
            "body_part_name": BODY_PART_NAMES.get(body_part, body_part),
            "total_entries":  len(cleaned),
            "entries":        cleaned,
        }

        out_path = STAGE1_DIR / f"{body_part}.json"
        out_path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"  {body_part}: {len(cleaned)}エントリ（codebook照合={overwritten}件） → {out_path.name}")

    print(f"\n  出力先: {STAGE1_DIR}/")


# ─── Stage 2: children ツリー再構築 ──────────────────────────────────────────

def _build_tree(flat_entries: list[dict]) -> list[dict]:
    """
    hierarchy_level をキーにスタックアルゴリズムでネスト構造を組み立てる。

    各エントリに children: [] を付与し、level < 現在エントリの level の
    直近祖先の children に追加する。
    """
    roots: list[dict] = []
    # スタック: (level, entry_with_children)
    stack: list[tuple[int, dict]] = []

    for raw in flat_entries:
        entry = dict(raw)       # shallow copy（children を追加するため）
        entry["children"] = []
        level = entry["hierarchy_level"]

        # 同レベル以上をすべてポップ
        while stack and stack[-1][0] >= level:
            stack.pop()

        if stack:
            stack[-1][1]["children"].append(entry)
        else:
            roots.append(entry)

        stack.append((level, entry))

    return roots


def run_stage2() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    print("=== Stage 2: children ツリー再構築 ===\n")

    for body_part, _ in MERGED_FILES:
        src = STAGE1_DIR / f"{body_part}.json"
        if not src.exists():
            print(f"  {body_part}.json: Stage1 出力が見つかりません → スキップ")
            continue

        data       = json.loads(src.read_text(encoding="utf-8"))
        flat       = data.get("entries", [])
        tree       = _build_tree(flat)

        # ルートエントリ数と全エントリ数を検証
        def count_all(entries: list[dict]) -> int:
            return sum(1 + count_all(e["children"]) for e in entries)

        total_in_tree = count_all(tree)

        out = {
            "body_part":      data["body_part"],
            "body_part_name": data["body_part_name"],
            "total_entries":  total_in_tree,
            "entries":        tree,
        }

        out_path = OUTPUT_DIR / f"{body_part}.json"
        out_path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"  {body_part}: ルート={len(tree)}件 / 全={total_in_tree}件 → {out_path.name}")
        if total_in_tree != len(flat):
            print(f"    ⚠ フラット({len(flat)}) ≠ ツリー合計({total_in_tree}): 件数不一致を確認してください")

    print(f"\n  出力先: {OUTPUT_DIR}/")


# ─── Stage 3: description_ja/en 祖先タイトル結合 ────────────────────────────

def _fill_descriptions(
    entries: list[dict],
    ancestors_ja: list[str],
    ancestors_en: list[str],
) -> None:
    """
    ツリーを再帰的に走査し、各エントリの description_ja/en を
    最上位祖先から自身の title までを " > " で結合した文字列で埋める。
    （自身の title を含む完全パス）
    """
    for entry in entries:
        own_ja = entry.get("title_ja") or ""
        own_en = entry.get("title_en") or ""
        path_ja = ancestors_ja + [own_ja]
        path_en = ancestors_en + [own_en]
        entry["description_ja"] = " > ".join(path_ja)
        entry["description_en"] = " > ".join(path_en)

        children = entry.get("children") or []
        if children:
            _fill_descriptions(children, path_ja, path_en)


def run_stage3() -> None:
    print("=== Stage 3: description_ja/en 祖先タイトル結合 ===\n")

    for body_part, _ in MERGED_FILES:
        src = OUTPUT_DIR / f"{body_part}.json"
        if not src.exists():
            print(f"  {body_part}.json: Stage2 出力が見つかりません → スキップ")
            continue

        data = json.loads(src.read_text(encoding="utf-8"))
        _fill_descriptions(data["entries"], [], [])

        # 埋まったエントリ数を集計
        def count_filled(entries: list[dict]) -> tuple[int, int]:
            total = filled = 0
            for e in entries:
                total += 1
                if e.get("description_ja") is not None:
                    filled += 1
                t2, f2 = count_filled(e.get("children") or [])
                total += t2
                filled += f2
            return total, filled

        total, filled = count_filled(data["entries"])

        src.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"  {body_part}: {filled}/{total}件に description を付与 → {src.name} を上書き")

    print(f"\n  出力先: {OUTPUT_DIR}/（上書き）")


# ─── エントリポイント ─────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        prog="build_codebook_v2",
        description="AIS コードブック v2 生成（merged → codebook_v2）",
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--stage", type=int, choices=[1, 2, 3],
                       help="実行するステージを指定（1, 2, 3）")
    group.add_argument("--all", action="store_true",
                       help="Stage 1 → 2 → 3 を連続実行")
    args = parser.parse_args()

    if args.all or args.stage == 1:
        cb_index = build_codebook_index()
        print(f"codebook インデックス: {len(cb_index)}件\n")
        run_stage1(cb_index)
        print()

    if args.all or args.stage == 2:
        run_stage2()
        print()

    if args.all or args.stage == 3:
        run_stage3()


if __name__ == "__main__":
    main()
