#!/usr/bin/env python3
"""
Phase 3: AISコード付与・ISS/TRISS計算モジュール

使い方（単体実行）:
  uv run jtdb_ais_coder.py output/patients/20260621_abc12345.json
  uv run jtdb_ais_coder.py --session <session_id>
"""

import os
import sys
import json
import math
from collections import Counter
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")        # scripts/.env（開発時）
load_dotenv(Path(__file__).parent.parent / ".env") # プロジェクトルートの .env

DATA_JSON_DIR = Path(__file__).parent.parent / "output" / "codebook" / "json"
PATIENTS_DIR  = Path(__file__).parent.parent / "output" / "patients"
SESSIONS_DIR  = Path(__file__).parent.parent / "output" / "sessions"
GEMINI_MD     = Path(__file__).parent.parent / "GEMINI.md"


def _load_system_prompt() -> str:
    """GEMINI.md からシステムプロンプトを読み込む。なければデフォルトを使用。"""
    if GEMINI_MD.exists():
        return GEMINI_MD.read_text(encoding="utf-8")
    return (
        "あなたはAIS 2005 Update 2008の専門コーダーです。"
        "提供されたコードブックからのみコードを選び、JSON配列で返してください。"
    )


AIS_SYSTEM = _load_system_prompt()

# JTDB損傷フィールド → AISコードブック部位名
JTDB_TO_BODY_PARTS: dict[str, list[str]] = {
    "70": ["head", "neck"],
    "71": ["face"],
    "72": ["thorax"],
    "73": ["abdomen"],
    "74": ["extremity", "spine"],
    "75": ["surface"],
}

JTDB_FIELD_LABELS: dict[str, str] = {
    "70": "頭頸部（頭部・頸部）",
    "71": "顔面",
    "72": "胸部",
    "73": "腹部",
    "74": "四肢・骨盤",
    "75": "体表",
}

# TRISS係数（AIS90, 1990 MTOS）
TRISS_COEFFS = {
    "blunt":        {"b0": -1.2470, "b1":  0.9544, "b2": -0.0768, "b3": -1.9052},
    "penetrating":  {"b0": -0.6029, "b1":  1.1430, "b2": -0.1516, "b3": -2.6676},
}


# ─── コードブック ─────────────────────────────────────────────────────────────

def load_codebook() -> dict[str, list[dict]]:
    """output/data/json/ から全AISコードブックを読み込む"""
    codebook: dict[str, list[dict]] = {}
    for f in DATA_JSON_DIR.glob("*.json"):
        data = json.loads(f.read_text(encoding="utf-8"))
        bp = data.get("body_part", f.stem)
        codebook[bp] = data.get("entries", [])
    return codebook


def _flatten_entries(entries: list[dict]):
    """入れ子になったエントリを再帰的にフラット化するジェネレータ"""
    for e in entries:
        yield e
        yield from _flatten_entries(e.get("children", []))


def _is_spine_code(code: str, codebook: dict) -> bool:
    """コードが脊椎チャプターに属するかどうかを判定する"""
    return any(e.get("code") == code
               for e in _flatten_entries(codebook.get("spine", [])))


# ─── 構造化検索（案2: 損傷形態検出 + 案3: 階層スコア継承） ─────────────────────

# クエリ記述 → injury_types タグ の対応（Stage 1: 損傷形態検出）
_INJURY_TYPE_QUERY: dict[str, list[str]] = {
    "fracture":     ["骨折"],
    "dislocation":  ["脱臼"],
    "laceration":   ["裂傷", "裂創"],
    "contusion":    ["挫傷"],
    "rupture":      ["破裂", "断裂"],
    "hematoma":     ["血腫"],
    "hemorrhage":   ["出血", "血胸", "血気胸"],
    "perforation":  ["穿孔"],
    "crush":        ["挫滅"],
    "avulsion":     ["剥離", "裂離", "デグロービング"],
    "sprain":       ["捻挫"],
    "amputation":   ["切断", "離断", "断頭"],
    "burn":         ["熱傷", "火傷"],
    "pneumothorax": ["気胸"],
    "hemothorax":   ["血胸", "血気胸"],
    "abrasion":     ["擦過傷", "擦傷"],
    "edema":        ["浮腫"],
    "paralysis":    ["麻痺", "麻ひ"],
    "penetrating":  ["穿通", "穿刺", "吸い込み"],
    "vascular":     ["血管損傷", "血管裂傷"],
    "nerve":        ["神経損傷", "神経麻痺"],
}

# Stage 2: フィールド重み
_FIELD_WEIGHTS: dict[str, float] = {
    "japanese":       3.0,
    "english":        2.0,
    "section":        1.0,
    "explanation_ja": 0.5,
    "explanation_en": 0.5,
}

_TYPE_MATCH_BONUS   = 2.0  # 損傷形態一致時のスコア倍率
_PARENT_BOOST_RATIO = 0.3  # 親スコアの子への継承率（案3）


def build_idf_index(codebook: dict) -> dict[str, float]:
    """コードブック全体のjapaense/englishからIDF重みを計算する（起動時1回）"""
    all_entries = [e for entries in codebook.values()
                   for e in _flatten_entries(entries)]
    N = len(all_entries)
    if N == 0:
        return {}
    df: Counter = Counter()
    for e in all_entries:
        df.update(_entry_terms(e))  # set なので1エントリ1カウント
    return {t: math.log((N + 1) / (cnt + 1)) for t, cnt in df.items()}


def _entry_terms(e: dict) -> set[str]:
    """エントリからIDF計算用term集合を抽出"""
    terms: set[str] = set()
    ja = e.get("japanese") or ""
    en = (e.get("english") or "").lower()
    terms.update(ja[i:i+2] for i in range(len(ja) - 1))
    terms.update(w for w in en.split() if len(w) >= 2)
    terms.update(en[i:i+2] for i in range(len(en) - 1))
    return terms


def _query_terms(description: str) -> list[str]:
    """クエリ記述からスコアリング用termリストを抽出"""
    words   = [w for w in description.replace("　", " ").split() if len(w) >= 2]
    bigrams = [description[i:i+2] for i in range(len(description) - 1)]
    en_lower = description.lower()
    en_bg   = [en_lower[i:i+2] for i in range(len(en_lower) - 1)]
    return words + bigrams + en_bg


def _detect_injury_types(description: str) -> set[str]:
    """クエリ記述から損傷形態タグを検出する（Stage 1）"""
    return {tag for tag, pats in _INJURY_TYPE_QUERY.items()
            if any(p in description for p in pats)}


def _score_entry(
    entry: dict,
    terms: list[str],
    idf: dict[str, float],
    detected_types: set[str],
) -> float:
    """1エントリのIDF重み付き多フィールドスコアを計算する（Stage 2）"""
    score = 0.0
    field_texts: dict[str, str] = {
        "japanese":       entry.get("japanese") or "",
        "english":        (entry.get("english") or "").lower(),
        "section":        (entry.get("section") or "").lower(),
        "explanation_ja": entry.get("explanation_ja") or "",
        "explanation_en": (entry.get("explanation_en") or "").lower(),
    }
    for field, text in field_texts.items():
        w = _FIELD_WEIGHTS[field]
        for t in terms:
            if t in text:
                score += w * idf.get(t, 1.0)

    # 損傷形態タグ一致ボーナス（Stage 1 の反映）
    if detected_types:
        entry_types = set(entry.get("injury_types") or [])
        if entry_types & detected_types:
            score *= _TYPE_MATCH_BONUS

    return score


def _score_tree(
    entries: list[dict],
    terms: list[str],
    idf: dict[str, float],
    detected_types: set[str],
    parent_boost: float = 0.0,
) -> list[tuple[float, dict]]:
    """階層構造を考慮したスコアリング（親スコアの一部を子に継承: 案3）"""
    results: list[tuple[float, dict]] = []
    for e in entries:
        own   = _score_entry(e, terms, idf, detected_types)
        total = own + parent_boost
        results.append((total, e))
        children = e.get("children") or []
        if children:
            results.extend(
                _score_tree(children, terms, idf, detected_types,
                            parent_boost=total * _PARENT_BOOST_RATIO)
            )
    return results


def structural_filter(
    entries: list[dict],
    description: str,
    idf: dict[str, float],
    top_n: int = 60,
) -> tuple[list[dict], set[str]]:
    """
    2段階+階層スコアリングでエントリを絞り込む。

    Stage 1: 損傷形態タグ検出 → 一致エントリにボーナス付与
    Stage 2: IDF重み付き多フィールドスコアリング
    Stage 3: 親スコアを子に継承（_PARENT_BOOST_RATIO）

    戻り値: (絞り込み済みエントリリスト, 検出された損傷形態タグ)
    """
    terms          = _query_terms(description)
    detected_types = _detect_injury_types(description)

    scored = _score_tree(entries, terms, idf, detected_types)
    scored.sort(key=lambda x: -x[0])

    matched   = [e for s, e in scored if s > 0]
    unmatched = [e for s, e in scored if s == 0]
    filtered  = matched[:top_n] + unmatched[:max(0, top_n - len(matched))]
    return filtered, detected_types


def build_ais_context(
    codebook: dict,
    body_parts: list[str],
    description: str,
    idf: dict[str, float],
) -> str:
    lines: list[str] = []
    for bp in body_parts:
        entries = codebook.get(bp, [])
        if not entries:
            continue
        total    = sum(1 for _ in _flatten_entries(entries))
        filtered, detected = structural_filter(entries, description, idf)
        type_hint = "/".join(sorted(detected)) if detected else "-"
        lines.append(f"\n## {bp.upper()} ({len(filtered)}/{total}件, 検出形態: {type_hint})")
        lines.append("コード | 日本語 | English | AIS重症度")
        lines.append("-------|--------|---------|----------")
        for e in filtered:
            lines.append(
                f"{e.get('code','')} | {e.get('japanese','')} | "
                f"{e.get('english','')} | {e.get('ais_severity','')}"
            )
    return "\n".join(lines)


# ─── 対話確認ヘルパー ──────────────────────────────────────────────────────────

def _confirm_severity(entry: dict) -> dict:
    """
    重症度が不明（0）、重症度不明コード（.9）、または確信度が低い場合に
    ユーザーへ確認を求め、必要に応じて severity を更新する。
    """
    sev  = entry.get("severity", 0)
    conf = entry.get("confidence", "")
    code = (entry.get("code") or "")
    ja   = entry.get("japanese", "")

    if sev == 0:
        reason = "重症度が未設定（AIS 0）"
    elif code.endswith(".9"):
        reason = "重症度不明コード（.9 — ISS計算に使用不可）"
    elif conf == "low":
        reason = "確信度が低い候補（confidence: low）"
    else:
        return entry  # 確認不要

    print(f"\n  ⚠  {reason}")
    print(f"     コード: [{code}] AIS{sev}  {ja}")
    try:
        ans = input(
            f"     AIS重症度を確認してください"
            f"（Enter=AIS{sev}のまま / 1-6=変更 / s=スキップ）> "
        ).strip()
    except (KeyboardInterrupt, EOFError):
        ans = ""

    if ans.lower() == "s":
        print("     → スキップ（AIS重症度を0に設定）")
        return {**entry, "severity": 0, "confidence": "skipped"}
    if ans.isdigit() and 1 <= int(ans) <= 6:
        new_sev = int(ans)
        print(f"     → AIS重症度を {new_sev} に変更しました")
        return {**entry, "severity": new_sev, "confidence": "confirmed"}
    print(f"     → AIS{sev} のまま確定")
    return entry


# ISS身体部位の選択肢: (JTDB field ID相当, 表示ラベル)
_SPINE_ISS_OPTIONS: list[tuple[str, str]] = [
    ("70", "頭部・頸部（頸椎: C1-7）"),
    ("72", "胸部    （胸椎: T1-12）"),
    ("73", "腹部・骨盤内臓器（腰椎: L1-5）"),
    ("74", "四肢・骨盤（レベル不明 / その他）"),
]


def _lookup_codebook_entry(code: str, body_parts: list[str], codebook: dict) -> dict | None:
    """コードブックからコードに一致するエントリ（children含む）を返す"""
    for bp in body_parts:
        for e in _flatten_entries(codebook.get(bp, [])):
            if e.get("code") == code:
                return e
    return None


def _drill_down(entry: dict, cb_entry: dict | None) -> dict:
    """
    選択されたエントリに子セクションがある場合、詳細を対話確認する。
    子が選択されればその子に対して再帰的に実行する。

    children=1 → 「該当しますか？」形式
    children≥2 → リスト選択形式
    """
    if not cb_entry:
        return entry

    children = [c for c in cb_entry.get("children", []) if c.get("code")]
    if not children:
        return entry  # リーフ = 確定

    cur_code = entry.get("code", "")
    cur_sev  = entry.get("severity", 0)
    cur_ja   = entry.get("japanese", "")

    if len(children) == 1:
        child     = children[0]
        child_code = child.get("code", "")
        child_sev  = child.get("ais_severity", cur_sev)
        child_ja   = (child.get("japanese") or "")[:50]
        print(f"\n  🔍 詳細分類があります")
        print(f"     現在: [{cur_code}] AIS{cur_sev}  {cur_ja}")
        print(f"     該当しますか?: [{child_code}] AIS{child_sev}  {child_ja}")
        try:
            ans = input("     (y=該当する / Enter=詳細不明で確定) > ").strip().lower()
        except (KeyboardInterrupt, EOFError):
            ans = ""
        if ans == "y":
            next_entry = {**entry,
                          "code": child_code, "japanese": child.get("japanese", ""),
                          "english": child.get("english", ""),
                          "severity": child_sev, "confidence": "confirmed"}
            print(f"     → [{child_code}] AIS{child_sev}  {child_ja}")
            return _drill_down(next_entry, child)
        print(f"     → [{cur_code}] で確定")
        return entry

    # children ≥ 2
    print(f"\n  🔍 詳細分類があります（{len(children)}件）")
    print(f"     現在: [{cur_code}] AIS{cur_sev}  {cur_ja}")
    print(f"     判明している場合は選択してください（不明なら Enter で確定）:")
    for i, child in enumerate(children, 1):
        num       = str(i % 10)
        child_sev = child.get("ais_severity", cur_sev)
        child_ja  = (child.get("japanese") or "")[:45]
        print(f"       {num}. [{child.get('code','')}] AIS{child_sev}  {child_ja}")
    try:
        ans = input(f"     選択（1-{min(len(children), 9)} / Enter=詳細不明で確定）> ").strip()
    except (KeyboardInterrupt, EOFError):
        ans = ""

    if not ans:
        print(f"     → [{cur_code}] で確定")
        return entry

    if ans.isdigit():
        n   = int(ans)
        idx = (n - 1) if n > 0 else 9
        if 0 <= idx < len(children):
            child      = children[idx]
            child_code = child.get("code", "")
            child_sev  = child.get("ais_severity", cur_sev)
            child_ja   = (child.get("japanese") or "")[:45]
            next_entry = {**entry,
                          "code": child_code, "japanese": child.get("japanese", ""),
                          "english": child.get("english", ""),
                          "severity": child_sev, "confidence": "confirmed"}
            print(f"     → [{child_code}] AIS{child_sev}  {child_ja}")
            return _drill_down(next_entry, child)

    print(f"     → [{cur_code}] で確定")
    return entry


def _ask_clarifying(
    description: str,
    body_parts: list[str],
    codebook: dict,
    idf: dict,
    client,
) -> str:
    """
    損傷記述でサブカテゴリが判別できない場合、Gemini に臨床質問を生成させ
    ユーザーから回答を収集して記述を補完する。
    記述が十分な場合や子セクションがない場合は元の記述をそのまま返す。
    2択: 1/y または 2/n でも回答可
    3択以上: 数字（1-9）のみ
    """
    from google.genai import types as gtypes

    # ローカルでサブカテゴリの有無を確認（Gemini呼び出し前の高速フィルタ）
    candidates_with_children: list[dict] = []
    for bp in body_parts:
        filtered, _ = structural_filter(codebook.get(bp, []), description, idf, top_n=15)
        for e in filtered:
            if e.get("children"):
                candidates_with_children.append(e)
        if len(candidates_with_children) >= 4:
            break

    if not candidates_with_children:
        return description

    # Gemini へ渡すサブカテゴリのコンテキストを構築
    ctx_lines: list[str] = []
    for e in candidates_with_children[:4]:
        ctx_lines.append(
            f"[{e.get('code')}] {e.get('japanese')} / {e.get('english')}"
        )
        for child in (e.get("children") or [])[:8]:
            ctx_lines.append(
                f"  └ [{child.get('code')}] AIS{child.get('ais_severity')} "
                f"{child.get('japanese')} / {child.get('english')}"
            )
    context = "\n".join(ctx_lines)

    print("  詳細分類を確認中...", end="", flush=True)
    try:
        resp = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=f"""損傷記述: 「{description}」

AISコードブックに以下のサブカテゴリがあります:
{context}

正確なAISコードを決定するために追加確認が必要な場合、
医師・コーダーに確認すべき最小限の臨床質問を最大3個、以下のJSON形式で返してください:
[
  {{
    "question": "質問文（日本語）",
    "options": ["選択肢1", "選択肢2"],
    "keywords": ["キーワード1", "キーワード2"]
  }}
]
- "options": 選択肢のラベル（2択: 所見の有無など / 3択以上: 分類選択）
- "keywords": 選択時に記述へ追記するキーワード（optionsと同数、不要な選択肢はnullも可）
- 例（2択）: {{"question": "頸髄損傷はありますか？", "options": ["あり", "なし"], "keywords": ["頸髄損傷あり", "頸髄損傷なし"]}}
- 例（3択）: {{"question": "骨折タイプは？", "options": ["単純骨折", "粉砕骨折", "開放骨折"], "keywords": ["単純骨折", "粉砕骨折", "開放骨折"]}}
記述がすでに十分な場合は空配列 [] を返してください。JSONのみ。""",
            config=gtypes.GenerateContentConfig(system_instruction=AIS_SYSTEM),
        )
        print()
        raw = resp.text.strip()
    except Exception as e:
        print(f" エラー: {e}")
        return description

    if "```json" in raw:
        raw = raw.split("```json")[1].split("```")[0].strip()
    elif "```" in raw:
        raw = raw.split("```")[1].split("```")[0].strip()

    try:
        questions = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return description

    if not isinstance(questions, list) or not questions:
        return description

    additions: list[str] = []
    print("  📋 詳細分類のために追加情報を確認します")
    for q_data in questions:
        if not isinstance(q_data, dict):
            continue
        q        = q_data.get("question", "")
        options  = q_data.get("options") or []
        keywords = q_data.get("keywords") or []

        if not q or not options:
            continue

        print(f"\n     ❓ {q}")
        for i, opt in enumerate(options, 1):
            print(f"        {i % 10}. {opt}")

        try:
            if len(options) <= 2:
                ans = input("        (1/y=1番目 / 2/n=2番目 / Enter=不明) > ").strip().lower()
                if ans in ("1", "y"):
                    idx = 0
                elif ans in ("2", "n"):
                    idx = 1
                else:
                    idx = -1
            else:
                ans = input(f"        選択（1-{min(len(options), 9)} / Enter=不明）> ").strip()
                if ans.isdigit():
                    n = int(ans)
                    idx = (n - 1) if n > 0 else 9
                    if idx >= len(options):
                        idx = -1
                else:
                    idx = -1
        except (KeyboardInterrupt, EOFError):
            break

        if idx >= 0:
            print(f"        → {options[idx]}")
            kw = keywords[idx] if idx < len(keywords) else None
            if kw:
                additions.append(kw)

    if additions:
        enriched = description + "、" + "、".join(additions)
        print(f"  → 記述を補完: {enriched}")
        return enriched
    return description


def _confirm_spine_iss_region(code: str, ja: str) -> str:
    """
    脊椎損傷コードのISS身体部位を確認する。
    AIS章は「脊椎」で統一されているが、ISS計算では椎体レベルにより部位が異なる:
      頸椎 C1-7  → ISS「頭部・頸部」
      胸椎 T1-12 → ISS「胸部」
      腰椎 L1-5  → ISS「腹部・骨盤内臓器」
    戻り値: JTDB field ID相当の文字列
    """
    print(f"\n  📍 脊椎損傷のISS部位を確認してください")
    print(f"     コード : [{code}] {ja}")
    print(f"     ISS計算ではレベルにより割り当て部位が異なります:")
    for i, (_, label) in enumerate(_SPINE_ISS_OPTIONS, 1):
        print(f"       {i}. {label}")
    try:
        ans = input("     脊椎レベルを選択してください（1-4 / Enter=4 不明）> ").strip()
    except (KeyboardInterrupt, EOFError):
        ans = "4"
    idx = int(ans) - 1 if ans.isdigit() and 1 <= int(ans) <= 4 else 3
    chosen_fid, chosen_label = _SPINE_ISS_OPTIONS[idx]
    print(f"     → ISS部位: {chosen_label}")
    return chosen_fid


# ─── Gemini AISコード照会 ─────────────────────────────────────────────────────


def suggest_ais_codes(description: str, body_parts: list[str],
                      codebook: dict, client,
                      idf: dict[str, float] | None = None) -> list[dict]:
    """損傷記述に対してAISコード候補リストを返す"""
    from google.genai import types as gtypes

    context = build_ais_context(codebook, body_parts, description, idf or {})

    prompt = f"""【損傷記述】
{description}

【AISコードブック（関連部位: {', '.join(body_parts)}）】
{context}

上記の損傷記述に適合する可能性のあるAISコードをすべてJSON配列で返してください:
[
  {{
    "code": "XXXXXXX.X",
    "japanese": "コードブックの損傷名（日本語）",
    "english": "Injury name",
    "description_ja": "この損傷コードが表す病態・特徴の説明（1〜2文、日本語）",
    "severity": 数値,
    "confidence": "high" | "medium" | "low"
  }}
]
確信度順に最大9件。コードブックに該当がなければ空配列。noteは不要。JSONのみ。"""

    resp = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=prompt,
        config=gtypes.GenerateContentConfig(system_instruction=AIS_SYSTEM),
    )
    raw = resp.text.strip()
    if "```json" in raw:
        raw = raw.split("```json")[1].split("```")[0].strip()
    elif "```" in raw:
        raw = raw.split("```")[1].split("```")[0].strip()
    result = json.loads(raw)
    return result if isinstance(result, list) else []


# ─── スコア計算 ───────────────────────────────────────────────────────────────

def calculate_iss(
    max_ais_by_region: dict[str, int],
    spine_iss: dict[str, list[int]] | None = None,
) -> int | None:
    """
    ISS = 上位3部位のAIS²の和（AIS=6→75）
    spine_iss: 脊椎損傷をレベル別に振り分けたISS部位 → AIS値リスト
    """
    region_max = dict(max_ais_by_region)
    if spine_iss:
        for iss_fid, sevs in spine_iss.items():
            if sevs:
                region_max[iss_fid] = max(region_max.get(iss_fid, 0), max(sevs))
    scores = [v for v in region_max.values() if v > 0]
    if not scores:
        return None
    if 6 in region_max.values():
        return 75
    top3 = sorted(scores, reverse=True)[:3]
    return sum(s * s for s in top3)


def _code_gcs(gcs: int) -> int:
    if gcs >= 13: return 4
    if gcs >= 9:  return 3
    if gcs >= 6:  return 2
    if gcs >= 4:  return 1
    return 0

def _code_sbp(sbp: int) -> int:
    if sbp > 89:  return 4
    if sbp >= 76: return 3
    if sbp >= 50: return 2
    if sbp >= 1:  return 1
    return 0

def _code_rr(rr: int) -> int:
    if 10 <= rr <= 29: return 4
    if rr > 29:        return 3
    if 6 <= rr <= 9:   return 2
    if 1 <= rr <= 5:   return 1
    return 0

def calculate_rts(gcs: int | None, sbp: int | None, rr: int | None) -> float | None:
    if gcs is None or sbp is None or rr is None:
        return None
    return round(0.9368 * _code_gcs(gcs) + 0.7326 * _code_sbp(sbp) + 0.2908 * _code_rr(rr), 4)


def calculate_triss(rts: float | None, iss: int | None,
                    age: int | None, trauma_type: int = 1) -> float | None:
    """TRISS Ps計算（trauma_type: 1=鈍的, 2=穿通性）"""
    if rts is None or iss is None or age is None:
        return None
    key = "penetrating" if trauma_type == 2 else "blunt"
    c = TRISS_COEFFS[key]
    age_group = 1 if age >= 55 else 0
    b = c["b0"] + c["b1"] * rts + c["b2"] * iss + c["b3"] * age_group
    return round(1.0 / (1.0 + math.exp(-b)), 4)


# ─── 対話型AISコーディング ────────────────────────────────────────────────────

def run_ais_coding(patient_data: dict, client) -> dict:
    """
    patient_data: jtdb_collector.py が生成した患者JSONデータ
    戻り値: AISコード・ISS・TRISS追記済みのpatient_data
    """
    codebook = load_codebook()
    idf      = build_idf_index(codebook)
    injuries = patient_data.get("injuries", {})
    ais_results: dict[str, list[dict]] = {}
    max_ais: dict[str, int] = {}

    print()
    print("=" * 60)
    print("Phase 3: AISコーディング")
    print("（各部位の損傷に最適なAISコードを確定します）")
    print("=" * 60)

    # 脊椎損傷のISS部位別最高スコア（ISS計算時にmax_aisと合算）
    spine_iss: dict[str, list[int]] = {}

    for fid in ("70", "71", "72", "73", "74", "75"):
        descs = [d for d in injuries.get(fid, []) if d]
        label = JTDB_FIELD_LABELS.get(fid, fid)
        body_parts = JTDB_TO_BODY_PARTS.get(fid, [])

        if not descs:
            print(f"\n【{label}】: 損傷なし → スキップ")
            max_ais[fid] = 0
            ais_results[fid] = []
            continue

        print(f"\n{'─'*50}")
        print(f"【{label}】")
        coded_entries: list[dict] = []

        for injury_desc in descs:
            print(f"\n  損傷: {injury_desc}")

            # サブカテゴリ判別に必要な情報をコーディング前に収集
            enriched_desc = _ask_clarifying(injury_desc, body_parts, codebook, idf, client)

            print("  AISコードを検索中...", end="", flush=True)

            try:
                candidates = suggest_ais_codes(enriched_desc, body_parts, codebook, client, idf)
                print(f" {len(candidates)}件候補")
            except Exception as e:
                print(f" エラー: {e}")
                candidates = []

            if not candidates:
                print("  → コードブックに該当なし")
                try:
                    ans = input("  手動入力（スキップはEnter）> ").strip()
                except (KeyboardInterrupt, EOFError):
                    ans = ""
                if ans:
                    manual_entry: dict = {
                        "description": injury_desc,
                        "code": ans,
                        "japanese": "",
                        "english": "",
                        "severity": 0,
                        "confidence": "manual",
                    }
                    manual_entry = _confirm_severity(manual_entry)
                    coded_entries.append(manual_entry)
                continue

            # 候補表示（全件、1桁番号）
            print()
            for i, c in enumerate(candidates, 1):
                badge = {"high": "◎", "medium": "○", "low": "△"}.get(
                    c.get("confidence", ""), " "
                )
                num = str(i % 10)  # 1-9、10番目→0
                print(f"  {badge}{num}. [{c.get('code','')}] AIS{c.get('severity','')}  "
                      f"{c.get('japanese','')}")
                c_desc = (c.get("description_ja") or "").strip()
                if c_desc:
                    print(f"       {c_desc}")

            print()
            try:
                ans = input(
                    "  選択 (1-9 / 0=10番目 / Enter=1番目 / コード直入力 / s=スキップ) > "
                ).strip()
            except (KeyboardInterrupt, EOFError):
                ans = ""

            if ans.lower() == "s":
                coded_entries.append({
                    "description": injury_desc,
                    "code": None,
                    "japanese": "（コーディング保留）",
                    "english": "",
                    "severity": 0,
                    "confidence": "skipped",
                })
                continue

            selected: dict | None = None
            if not ans and candidates:
                selected = candidates[0]
            elif ans.isdigit():
                n   = int(ans)
                idx = (n - 1) if n > 0 else 9  # 1→0, …, 9→8, 0→9
                if 0 <= idx < len(candidates):
                    selected = candidates[idx]
            elif "." in ans:
                # コードブックから直接検索（入れ子エントリを含む）
                found_entry = None
                for bp in body_parts:
                    for e in _flatten_entries(codebook.get(bp, [])):
                        if e.get("code") == ans:
                            found_entry = {
                                "description": injury_desc,
                                "code": ans,
                                "japanese": e.get("japanese", ""),
                                "english": e.get("english", ""),
                                "severity": e.get("ais_severity", 0),
                                "confidence": "manual",
                            }
                            break
                    if found_entry:
                        break
                if found_entry:
                    selected = found_entry
                else:
                    print(f"  ⚠ コード {ans} がコードブックにありません（手動登録）")
                    try:
                        sev_str = input("  AIS重症度 (0-6) > ").strip()
                    except (KeyboardInterrupt, EOFError):
                        sev_str = "0"
                    selected = {
                        "description": injury_desc,
                        "code": ans,
                        "japanese": "",
                        "english": "",
                        "severity": int(sev_str) if sev_str.isdigit() else 0,
                        "confidence": "manual",
                    }

            if selected:
                entry = {
                    "description": injury_desc,
                    "code": selected.get("code"),
                    "japanese": selected.get("japanese", ""),
                    "description_ja": (selected.get("description_ja") or "").strip(),
                    "english": selected.get("english", ""),
                    "severity": selected.get("severity", 0),
                    "confidence": selected.get("confidence", ""),
                }

                # 子セクションがある場合は詳細確認（詳細不明 → 詳細あり へのドリルダウン）
                cb_entry = _lookup_codebook_entry(
                    entry.get("code", ""), body_parts, codebook
                )
                entry = _drill_down(entry, cb_entry)

                # 重症度不明・低確信度の場合は対話確認
                entry = _confirm_severity(entry)

                # 脊椎コードはISS計算用の椎体レベルを確認する
                code = entry.get("code") or ""
                if code and _is_spine_code(code, codebook):
                    iss_fid = _confirm_spine_iss_region(code, entry.get("japanese", ""))
                    entry["iss_region"] = iss_fid
                    # spine_iss に記録（ISS計算時に正しい部位で加算）
                    spine_iss.setdefault(iss_fid, []).append(entry["severity"])

                coded_entries.append(entry)
                print(f"  ✓ 確定: [{entry['code']}] AIS{entry['severity']}  {entry['japanese']}")
                if entry["description_ja"]:
                    print(f"         {entry['description_ja']}")

        ais_results[fid] = coded_entries
        # spine_iss に記録済みの脊椎損傷はmax_aisから除外（二重計上防止）
        severities = [
            e["severity"] for e in coded_entries
            if e.get("severity") and "iss_region" not in e
        ]
        max_ais[fid] = max(severities) if severities else 0

    # ── スコア計算 ──
    fields = patient_data.get("patient", {})

    def fval(fid_str: str):
        entry = fields.get(fid_str, {})
        if entry.get("unknown"):
            return None
        return entry.get("value")

    gcs_e, gcs_v, gcs_m = fval("45"), fval("46"), fval("47")
    gcs: int | None = None
    if (isinstance(gcs_e, int) and isinstance(gcs_v, int) and isinstance(gcs_m, int)):
        gcs = gcs_e + gcs_v + gcs_m

    sbp = fval("38")
    rr  = fval("40")
    rts = calculate_rts(gcs, sbp, rr)

    iss = calculate_iss(max_ais, spine_iss)

    age = fval("2")
    trauma_type = fval("8") or 1
    triss_ps = calculate_triss(rts, iss, age, trauma_type)

    # ── 結果表示 ──
    print()
    print("=" * 60)
    print("スコア計算結果")
    print(f"  GCS:      {gcs if gcs is not None else '計算不可'}")
    print(f"  RTS:      {rts if rts is not None else '計算不可'}")
    print(f"  ISS:      {iss if iss is not None else '計算不可（AIS未確定）'}")
    if triss_ps is not None:
        print(f"  TRISS Ps: {triss_ps}  （生存確率 {round(triss_ps * 100, 1)}%）")
    else:
        print(f"  TRISS Ps: 計算不可")
    _ISS_REGION_LABELS = {
        "70": "頭部・頸部", "71": "顔面", "72": "胸部",
        "73": "腹部・骨盤内臓器", "74": "四肢・骨盤", "75": "体表",
    }
    # ISS計算に使用した実効最大値（spine_iss反映後）
    iss_effective: dict[str, int] = dict(max_ais)
    for iss_fid, sevs in spine_iss.items():
        if sevs:
            iss_effective[iss_fid] = max(iss_effective.get(iss_fid, 0), max(sevs))

    print("  AIS最大値（ISS計算部位別）:")
    for fid, label in _ISS_REGION_LABELS.items():
        sev = iss_effective.get(fid, 0)
        bar = "■" * sev + "□" * (6 - sev) if sev <= 6 else "■" * 6
        note = " ← 脊椎損傷含む" if fid in spine_iss and spine_iss[fid] else ""
        print(f"    {label:18s}: AIS{sev} {bar}{note}")
    print("=" * 60)

    # ── patient_data を更新 ──
    patient_data["ais_codes"] = ais_results
    patient_data["scores"] = {
        "gcs": gcs,
        "rts": rts,
        "iss": iss,
        "triss_ps": triss_ps,
        "ais_coding_status": "complete",
        "max_ais_by_region": max_ais,
    }
    patient_data["meta"]["ais_coding_status"] = "complete"

    return patient_data


# ─── 出力更新（MD再生成） ──────────────────────────────────────────────────────

def regenerate_md(patient_data: dict) -> str:
    """AISコード・スコアを含む患者MDを再生成"""
    meta   = patient_data.get("meta", {})
    fields = patient_data.get("patient", {})
    injuries = patient_data.get("injuries", {})
    ais_codes = patient_data.get("ais_codes", {})
    scores = patient_data.get("scores", {})

    sid = meta.get("session_id", "")
    completed = meta.get("completed_at", "")[:16].replace("T", " ")

    lines = [
        "# JTDB患者記録",
        "",
        f"**セッションID:** {sid}  ",
        f"**記録日時:** {completed}  ",
        f"**AISコーディング:** {'完了' if meta.get('ais_coding_status') == 'complete' else '未完了'}",
        "",
    ]

    # セクション別フィールド
    section_order = ["I", "II", "III", "IV", "V", "VI", "VII"]
    section_names = {
        "I": "I. 患者初期情報", "II": "II. 病院前情報",
        "III": "III. 転送情報", "IV": "IV. 来院時病態",
        "V": "V. 初療時の検査と処置", "VI": "VI. 診断名と損傷重症度",
        "VII": "VII. 退院情報",
    }
    by_section: dict[str, list] = {s: [] for s in section_order}
    for fid, fdata in sorted(fields.items(), key=lambda x: int(x[0])):
        sec = fdata.get("section", "")
        if sec == "VI":
            continue  # VI は損傷セクションで別途出力
        display = fdata.get("display") or str(fdata.get("value", ""))
        if fdata.get("unknown"):
            display = "不明"
        if sec in by_section:
            by_section[sec].append((fdata.get("label", fid), display))

    for sec_id in section_order:
        if sec_id == "VI":
            continue
        items = by_section.get(sec_id, [])
        if not items:
            continue
        lines += [f"## {section_names[sec_id]}", ""]
        for name, display in items:
            lines.append(f"- **{name}:** {display}")
        lines.append("")

    # VI. 損傷・AISコード
    any_coded = any(ais_codes.get(fid) for fid in JTDB_FIELD_LABELS)
    lines += ["## VI. 診断名と損傷重症度", ""]
    for fid, label in JTDB_FIELD_LABELS.items():
        coded = ais_codes.get(fid, [])
        raw_descs = injuries.get(fid, [])
        if not raw_descs and not coded:
            continue
        lines.append(f"### {label}")
        if coded:
            for e in coded:
                code    = e.get("code") or "未確定"
                sev     = e.get("severity", 0)
                ja      = e.get("japanese", "")
                desc_ja = (e.get("description_ja") or "").strip()
                raw_d   = e.get("description", "")
                conf    = e.get("confidence", "")
                badge   = {"high": "◎", "medium": "○", "low": "△", "manual": "✍"}.get(conf, " ")
                lines.append(f"- {badge} `{code}` AIS**{sev}** — {ja}")
                if desc_ja:
                    lines.append(f"  {desc_ja}")
                if raw_d and raw_d != ja:
                    lines.append(f"  （原記述: {raw_d}）")
        elif raw_descs:
            for d in raw_descs:
                lines.append(f"- {d}（AIS未確定）")
        lines.append("")

    # スコア
    gcs      = scores.get("gcs")
    rts      = scores.get("rts")
    iss      = scores.get("iss")
    triss_ps = scores.get("triss_ps")
    lines += [
        "## スコア",
        "",
        f"| スコア | 値 |",
        f"|--------|-----|",
        f"| GCS    | {gcs if gcs is not None else '計算不可'} |",
        f"| RTS    | {rts if rts is not None else '計算不可'} |",
        f"| ISS    | {iss if iss is not None else '計算不可'} |",
        f"| TRISS Ps | {f'{triss_ps} ({round(triss_ps*100,1)}%)' if triss_ps is not None else '計算不可'} |",
        "",
    ]

    if scores.get("max_ais_by_region"):
        lines += ["### AIS最大値（部位別）", ""]
        for fid, label in JTDB_FIELD_LABELS.items():
            sev = scores["max_ais_by_region"].get(fid, 0)
            bar = "■" * sev + "□" * (6 - sev)
            lines.append(f"- **{label}:** AIS{sev} {bar}")
        lines.append("")

    return "\n".join(lines)


def save_updated_patient(patient_path: Path, patient_data: dict):
    """JSON・MD を更新保存"""
    patient_path.write_text(json.dumps(patient_data, ensure_ascii=False, indent=2))
    md_path = patient_path.with_suffix(".md")
    md_path.write_text(regenerate_md(patient_data), encoding="utf-8")
    print(f"\n✓ 保存完了")
    print(f"  JSON: {patient_path}")
    print(f"  MD:   {md_path}")


# ─── ダミー患者データ（--dummy デバッグ用）────────────────────────────────────

def _make_dummy_patient() -> tuple[dict, Path]:
    """AISコーディングのデバッグ用ダミー患者データを生成する"""
    import uuid
    from datetime import datetime

    sid  = "dummy_" + str(uuid.uuid4())[:6]
    now  = datetime.now().isoformat()
    path = PATIENTS_DIR / f"{datetime.now().strftime('%Y%m%d')}_{sid}.json"

    data = {
        "meta": {
            "jtdb_schema_version": "1.0",
            "session_id": sid,
            "created_at": now,
            "completed_at": now,
            "ais_coding_status": "pending",
        },
        "patient": {
            "2":  {"label": "年齢",   "value": 55,  "display": "55", "raw": "55歳", "unknown": False},
            "38": {"label": "収縮期血圧", "value": 90,  "display": "90", "raw": "90",  "unknown": False},
            "40": {"label": "呼吸数", "value": 20,  "display": "20", "raw": "20",  "unknown": False},
            "45": {"label": "GCS-E",  "value": 3,   "display": "3（呼びかけで開眼）", "raw": "E3", "unknown": False},
            "46": {"label": "GCS-V",  "value": 4,   "display": "4（混乱した会話）",   "raw": "V4", "unknown": False},
            "47": {"label": "GCS-M",  "value": 5,   "display": "5（疼痛部位への反応）","raw": "M5", "unknown": False},
            "8":  {"label": "外傷分類","value": 1,   "display": "鈍的", "raw": "鈍的", "unknown": False},
        },
        "injuries": {
            "70": ["右硬膜外血腫", "くも膜下出血"],
            "71": [],
            "72": ["左肋骨骨折（第4・5・6肋骨）", "左血胸"],
            "73": ["脾臓損傷"],
            "74": ["左大腿骨骨幹部骨折"],
            "75": [],
        },
        "scores": {
            "gcs": 12, "rts": None, "iss": None,
            "triss_ps": None, "ais_coding_status": "pending",
        },
    }
    return data, path


# ─── エントリポイント ─────────────────────────────────────────────────────────

def main():
    import argparse
    from google import genai

    parser = argparse.ArgumentParser(
        prog="jtdb-ais-coder",
        description="JTDB AISコード付与・ISS/TRISS計算ツール（AIS 2005 Update 2008）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
使用例:
  jtdb-ais-coder                              # 最新の患者ファイルで実行
  jtdb-ais-coder output/patients/foo.json     # ファイル指定
  jtdb-ais-coder --session abc12345           # セッションID指定
  jtdb-ais-coder --dummy                      # ダミーデータでデバッグ
  jtdb-ais-coder --reset-ais --session abc12  # AISコーディングをリセットして再実行
        """,
    )
    source = parser.add_mutually_exclusive_group()
    source.add_argument(
        "patient_file", nargs="?", metavar="FILE",
        help="患者JSONファイルのパス（省略時は最新ファイルを自動選択）",
    )
    source.add_argument(
        "--session", metavar="SESSION_ID",
        help="セッションIDで患者ファイルを指定",
    )
    source.add_argument(
        "--dummy", action="store_true",
        help="ダミーデータでAISコーディングを実行（デバッグ用）",
    )
    parser.add_argument(
        "--reset-ais", action="store_true",
        help="AISコーディング済みファイルを強制的に再実行",
    )
    parsed = parser.parse_args()

    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        print("ERROR: GEMINI_API_KEY が .env に設定されていません")
        sys.exit(1)
    client = genai.Client(api_key=api_key)

    patient_path: Path | None = None
    patient_data: dict | None = None

    # ── --dummy: ダミーデータでAISコーディングを実行 ──
    if parsed.dummy:
        print("【デバッグモード】ダミー患者データでAISコーディングを実行します")
        patient_data, patient_path = _make_dummy_patient()
        PATIENTS_DIR.mkdir(parents=True, exist_ok=True)
        print("損傷記述:")
        for fid, descs in patient_data["injuries"].items():
            label = JTDB_FIELD_LABELS.get(fid, fid)
            if descs:
                print(f"  {label}: {' / '.join(descs)}")
        print()
        try:
            patient_data = run_ais_coding(patient_data, client)
            save_updated_patient(patient_path, patient_data)
        except KeyboardInterrupt:
            print("\n\n中断しました")
        return

    # ── ファイル特定 ──
    if parsed.session:
        matches = sorted(PATIENTS_DIR.glob(f"*_{parsed.session}.json"))
        if not matches:
            print(f"ERROR: セッション {parsed.session} の患者ファイルが見つかりません")
            sys.exit(1)
        patient_path = matches[-1]
    elif parsed.patient_file:
        patient_path = Path(parsed.patient_file)
    else:
        all_patients = sorted(PATIENTS_DIR.glob("*.json"))
        if not all_patients:
            print("ERROR: output/patients/ に患者ファイルがありません")
            print("先に: jtdb-collector でデータを収集してください")
            print("デバッグ用: jtdb-ais-coder --dummy")
            sys.exit(1)
        patient_path = all_patients[-1]
        print(f"最新の患者ファイルを使用: {patient_path.name}")

    if not patient_path.exists():
        print(f"ERROR: ファイルが見つかりません: {patient_path}")
        sys.exit(1)

    patient_data = json.loads(patient_path.read_text(encoding="utf-8"))

    if patient_data.get("meta", {}).get("ais_coding_status") == "complete":
        if parsed.reset_ais:
            print("AISコーディングをリセットして再実行します")
            patient_data["meta"]["ais_coding_status"] = "pending"
        else:
            print(f"⚠ このファイルはすでにAISコーディング済みです")
            print(f"  再実行: jtdb-ais-coder --reset-ais {patient_path}")
            try:
                ans = input("このまま再実行しますか？ (y/N) > ").strip().lower()
            except (KeyboardInterrupt, EOFError):
                ans = "n"
            if ans != "y":
                sys.exit(0)

    try:
        patient_data = run_ais_coding(patient_data, client)
        save_updated_patient(patient_path, patient_data)
    except KeyboardInterrupt:
        print("\n\n中断しました")
        sys.exit(1)


if __name__ == "__main__":
    main()
