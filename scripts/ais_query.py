"""
AIS外傷コード照会システム
損傷の記述から AIS 2005 Update 2008 コードを返す CLI ツール
"""

import os
import json
import glob
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

RESULTS_DIR = Path("/workspace/output/results")

BODY_PART_KEYWORDS = {
    "head":            ["頭", "脳", "頭蓋", "硬膜", "くも膜", "頭皮", "head", "brain", "skull", "cranial", "cerebral", "intracranial"],
    "face":            ["顔", "眼", "目", "鼻", "口", "歯", "顎", "耳", "face", "eye", "nose", "mouth", "jaw", "ear", "orbit", "dental"],
    "neck":            ["頸", "頚", "咽", "喉", "気管", "食道", "neck", "cervical", "trachea", "esophagus", "larynx", "pharynx"],
    "thorax":          ["胸", "肺", "肋骨", "心", "胸骨", "横隔膜", "thorax", "chest", "lung", "rib", "heart", "sternum", "diaphragm", "aorta"],
    "abdomen":         ["腹", "肝", "脾", "腎", "膵", "腸", "胃", "膀胱", "骨盤", "abdomen", "liver", "spleen", "kidney", "pancreas", "bowel", "stomach", "bladder", "pelvis"],
    "spine":           ["脊椎", "脊髄", "腰椎", "胸椎", "頸椎", "椎", "spine", "spinal", "vertebra", "lumbar", "thoracic vertebra", "cord"],
    "extremity_upper": ["上肢", "肩", "上腕", "肘", "前腕", "手首", "手", "指", "鎖骨", "肩甲骨", "upper extremity", "shoulder", "humerus", "elbow", "forearm", "wrist", "hand", "finger", "clavicle"],
    "extremity_lower": ["下肢", "股関節", "大腿", "膝", "下腿", "足首", "足", "趾", "腓骨", "脛骨", "大腿骨", "lower extremity", "hip", "femur", "knee", "tibia", "fibula", "ankle", "foot", "toe"],
    "extremity":       ["四肢", "骨折", "脱臼", "extremity", "fracture", "dislocation"],
    "surface":         ["皮膚", "裂傷", "挫傷", "擦過傷", "熱傷", "火傷", "表皮", "surface", "skin", "laceration", "contusion", "abrasion", "burn"],
    "other":           ["血管", "神経", "多発", "その他", "vessel", "vascular", "nerve", "multiple"],
}


def load_codebook() -> dict:
    """全JSONを読み込んでbody_part別に索引化"""
    codebook = {}
    for f in sorted(RESULTS_DIR.glob("*.json")):
        data = json.loads(f.read_text(encoding="utf-8"))
        bp = data.get("body_part_normalized", "unknown")
        if bp == "intro":
            continue
        if bp not in codebook:
            codebook[bp] = []
        for entry in data.get("entries", []):
            if entry.get("code"):
                codebook[bp].append({
                    "code": entry["code"],
                    "ja": entry.get("description_ja") or "",
                    "en": entry.get("description_en") or "",
                    "severity": entry.get("severity", 0),
                    "level": entry.get("level", 1),
                    "section": data.get("section_title") or "",
                })
    return codebook


def guess_body_parts(description: str) -> list[str]:
    """損傷記述から関連する部位を推定"""
    desc_lower = description.lower()
    matched = []
    for bp, keywords in BODY_PART_KEYWORDS.items():
        for kw in keywords:
            if kw in desc_lower or kw in description:
                if bp not in matched:
                    matched.append(bp)
                break
    # upper/lower extremity → extremity も含める
    if "extremity_upper" in matched or "extremity_lower" in matched:
        if "extremity" not in matched:
            matched.append("extremity")
    return matched if matched else list(BODY_PART_KEYWORDS.keys())


def keyword_filter(entries: list[dict], description: str, top_n: int = 150) -> list[dict]:
    """損傷記述のキーワードでエントリをスコアリングして絞り込む"""
    desc_lower = description.lower()
    words = [w for w in desc_lower.replace("　", " ").split() if len(w) >= 2]
    # 日本語は文字単位でも分割
    ja_chars = [description[i:i+2] for i in range(len(description) - 1)]
    all_terms = words + ja_chars

    scored = []
    for e in entries:
        text = (e["ja"] + " " + e["en"]).lower()
        score = sum(1 for t in all_terms if t in text)
        scored.append((score, e))

    scored.sort(key=lambda x: -x[0])
    # スコアが0のものも一定数含める（コードブック網羅性のため）
    matched = [e for s, e in scored if s > 0]
    unmatched = [e for s, e in scored if s == 0]
    return matched[:top_n] + unmatched[: max(0, top_n - len(matched))]


def build_context(codebook: dict, body_parts: list[str], description: str = "") -> str:
    """関連部位のAISコード一覧をコンテキスト文字列に変換（キーワード絞り込み付き）"""
    lines = []
    for bp in body_parts:
        entries = codebook.get(bp, [])
        if not entries:
            continue
        filtered = keyword_filter(entries, description) if description else entries[:150]
        lines.append(f"\n## {bp.upper()} ({len(filtered)}/{len(entries)}件)")
        lines.append("コード | 日本語 | English | AIS重症度")
        lines.append("-------|--------|---------|----------")
        for e in filtered:
            indent = "　" * (e["level"] - 1)
            lines.append(f"{e['code']} | {indent}{e['ja']} | {indent}{e['en']} | {e['severity']}")
    return "\n".join(lines)


SYSTEM_PROMPT = """\
あなたはAIS（Abbreviated Injury Scale）2005 Update 2008の専門家です。
外傷患者の損傷記述を受け取り、最も適切なAISコードを特定してください。

以下のルールに従って回答してください：
1. 提供されたAISコードブック（コンテキスト）からのみコードを選ぶ
2. 複数の損傷がある場合はそれぞれコードを提示
3. 確信度が低い場合は候補を複数挙げ、その理由を述べる
4. 回答形式：
   - AISコード: `XXXXXXX.X`
   - 損傷名（日本語/英語）
   - AIS重症度: X
   - 根拠・補足

コードブックに該当なければ「コードブックに該当なし」と明示すること。
"""


def query(description: str, codebook: dict, client) -> str:
    from google import genai

    body_parts = guess_body_parts(description)
    context = build_context(codebook, body_parts, description)

    prompt = f"""【損傷記述】
{description}

【参照AISコードブック（関連部位: {', '.join(body_parts)}）】
{context}

上記の損傷記述に最も適合するAISコードを特定してください。"""

    from google.genai import types as gtypes
    response = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=prompt,
        config=gtypes.GenerateContentConfig(
            system_instruction=SYSTEM_PROMPT,
        ),
    )
    return response.text


def main():
    from google import genai

    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        print("ERROR: GEMINI_API_KEY が .env に設定されていません")
        return

    print("AISコードブックを読み込み中...", end="", flush=True)
    codebook = load_codebook()
    total = sum(len(v) for v in codebook.values())
    print(f" 完了（{total}件のコードエントリ）\n")

    client = genai.Client(api_key=api_key)

    print("AIS外傷コード照会システム（終了: Ctrl+C または 'quit'）")
    print("=" * 60)

    while True:
        try:
            desc = input("\n損傷を入力してください > ").strip()
        except (KeyboardInterrupt, EOFError):
            print("\n終了します")
            break

        if desc.lower() in ("quit", "exit", "q", "終了"):
            print("終了します")
            break
        if not desc:
            continue

        parts = guess_body_parts(desc)
        print(f"  → 推定部位: {', '.join(parts)}")
        print("  → Gemini照会中...\n")

        result = query(desc, codebook, client)
        print(result)
        print("-" * 60)


if __name__ == "__main__":
    main()
