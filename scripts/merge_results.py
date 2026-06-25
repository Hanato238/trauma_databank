"""
AIS 2005 results_renamed/ の各部位JSONを統合するスクリプト

処理概要:
  - output/results_renamed/{body_part}/*.json をすべて読み込む
  - AISコードをキーとしてエントリをマージ（英語ページ + 日本語ページを結合）
  - 同一コードでデータが衝突する場合は collision フラグと collision_note を付与（日本語優先）
  - 出力: output/merged/{body_part}.json
"""

import json
from pathlib import Path

RESULTS_DIR = Path("/workspace/output/results_renamed")
OUTPUT_DIR  = Path("/workspace/output/merged")

BODY_PARTS = [
    "head", "face", "neck", "thorax",
    "abdomen", "spine", "extremity", "other",
]

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


def merge_entry(existing: dict, incoming: dict) -> dict:
    """
    existing に incoming をマージして返す。
    衝突（同一コードで矛盾するデータ）が発生した場合は collision=True を立てる。
    日本語フィールドを優先する。
    """
    collision_reasons = []

    # description_ja: 両方に値があり内容が異なる場合
    ja_ex = existing.get("description_ja")
    ja_in = incoming.get("description_ja")
    if ja_in and ja_ex and ja_in != ja_ex:
        collision_reasons.append(
            f"description_ja 衝突: [{ja_ex}] vs [{ja_in}] → 既存値を使用"
        )
        # 既存値を保持（何もしない）
    elif ja_in and not ja_ex:
        existing["description_ja"] = ja_in

    # description_en: 両方に値があり内容が異なる場合
    en_ex = existing.get("description_en")
    en_in = incoming.get("description_en")
    if en_in and en_ex and en_in != en_ex:
        collision_reasons.append(
            f"description_en 衝突: [{en_ex}] vs [{en_in}] → 既存値を使用"
        )
        # 既存値を保持
    elif en_in and not en_ex:
        existing["description_en"] = en_in

    # severity: 数値が異なる場合（日本語ページを優先 → incoming が ja 由来なら上書き）
    sev_ex = existing.get("severity", 0)
    sev_in = incoming.get("severity", 0)
    if sev_ex != sev_in and sev_in != 0 and sev_ex != 0:
        # incoming のほうが日本語ページ由来かどうかを lang で判定
        if incoming.get("_lang") == "ja":
            collision_reasons.append(
                f"severity 衝突: {sev_ex} → {sev_in}（日本語ページを優先して上書き）"
            )
            existing["severity"] = sev_in
        else:
            collision_reasons.append(
                f"severity 衝突: {sev_ex} vs {sev_in}（既存値を保持）"
            )

    # level: 異なる場合
    lv_ex = existing.get("level", 1)
    lv_in = incoming.get("level", 1)
    if lv_ex != lv_in:
        if incoming.get("_lang") == "ja":
            collision_reasons.append(
                f"level 衝突: {lv_ex} → {lv_in}（日本語ページを優先して上書き）"
            )
            existing["level"] = lv_in
        else:
            collision_reasons.append(
                f"level 衝突: {lv_ex} vs {lv_in}（既存値を保持）"
            )

    # ソースファイル記録
    sources = existing.setdefault("source_files", [])
    src = incoming.get("_source_file")
    if src and src not in sources:
        sources.append(src)

    # 衝突記録
    if collision_reasons:
        existing["collision"] = True
        prev = existing.get("collision_note", "")
        new_note = "; ".join(collision_reasons)
        existing["collision_note"] = f"{prev}; {new_note}" if prev else new_note
    else:
        if "collision" not in existing:
            existing["collision"] = False

    return existing


def merge_body_part(body_part: str) -> dict:
    src_dir = RESULTS_DIR / body_part
    if not src_dir.exists():
        print(f"  {body_part}: ディレクトリなし、スキップ")
        return {}

    json_files = sorted(src_dir.glob("*.json"))
    if not json_files:
        print(f"  {body_part}: JSONなし、スキップ")
        return {}

    # code → entry のマッピング（出現順を保持するため dict を使用）
    code_map: dict[str, dict] = {}
    # コードなしエントリ（section_title のみなど）は別途収集
    no_code_entries: list[dict] = []
    source_files: list[str] = []
    page_types_seen: set[str] = set()

    for jf in json_files:
        data = json.loads(jf.read_text(encoding="utf-8"))
        source_files.append(jf.name)
        page_types_seen.add(data.get("page_type", "unknown"))

        lang = data.get("lang", "unknown")

        for entry in data.get("entries", []):
            code = entry.get("code") or ""
            # コードなし（空文字 or None）はスキップ対象リストへ
            if not code or code.strip() == "":
                no_code_entries.append({
                    "code": code,
                    "description_ja": entry.get("description_ja"),
                    "description_en": entry.get("description_en"),
                    "severity": int(entry.get("severity") or 0),
                    "level": int(entry.get("level") or 1),
                    "collision": False,
                    "source_files": [jf.name],
                })
                continue

            # _lang と _source_file は内部用タグ（最終出力から除去）
            incoming = {
                "code": code,
                "description_ja": entry.get("description_ja"),
                "description_en": entry.get("description_en"),
                "severity": int(entry.get("severity") or 0),
                "level": int(entry.get("level") or 1),
                "collision": False,
                "_lang": lang,
                "_source_file": jf.name,
            }

            if code not in code_map:
                code_map[code] = {
                    "code": code,
                    "description_ja": entry.get("description_ja"),
                    "description_en": entry.get("description_en"),
                    "severity": int(entry.get("severity") or 0),
                    "level": int(entry.get("level") or 1),
                    "collision": False,
                    "source_files": [jf.name],
                    "_first_lang": lang,
                }
            else:
                merge_entry(code_map[code], incoming)

    # 内部タグを除去
    entries = []
    for e in code_map.values():
        e.pop("_lang", None)
        e.pop("_first_lang", None)
        entries.append(e)

    # コードなしエントリを末尾に追加
    entries.extend(no_code_entries)

    collision_count = sum(1 for e in entries if e.get("collision"))

    return {
        "body_part": body_part,
        "body_part_name": BODY_PART_NAMES.get(body_part, body_part),
        "source_file_count": len(source_files),
        "source_files": source_files,
        "total_entries": len(entries),
        "collision_count": collision_count,
        "entries": entries,
    }


def main():
    print("=== AIS コードブック JSON 統合 ===\n")
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    grand_total = 0
    grand_collision = 0

    for body_part in BODY_PARTS:
        print(f"処理中: {body_part} ...", end=" ", flush=True)
        merged = merge_body_part(body_part)
        if not merged:
            continue

        out_file = OUTPUT_DIR / f"{body_part}.json"
        out_file.write_text(
            json.dumps(merged, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        n = merged["total_entries"]
        c = merged["collision_count"]
        grand_total += n
        grand_collision += c
        print(f"{n}エントリ, 衝突={c}件 → {out_file.name}")

    print(f"\n合計: {grand_total}エントリ, 衝突={grand_collision}件")
    print(f"出力先: {OUTPUT_DIR}/")


if __name__ == "__main__":
    main()
