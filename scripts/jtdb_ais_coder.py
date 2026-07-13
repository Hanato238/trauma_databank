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

DATA_JSON_DIR = Path(__file__).parent.parent / "output" / "codebook_v2" / "json"
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

# 臓器名キーワード → サブツリー識別用（クエリ中の臓器名を検出して subtree を優先）
# エントリの _organ_context タグを設定するキーワードマップ（一意キー）
_ORGAN_SUBTREE_KEYWORDS: dict[str, list[str]] = {
    # 血管サブツリーは実質より先に判定する（"肺動脈" は "肺" を含むため順序が重要）。
    # これにより血管は実質(肺)と別コンテキストになり、実質クエリで臓器ボーナスを受けず
    # 実質裂傷が血管に埋もれない。ただし候補からは除外しない（併発しうるため）。
    "肺動脈": ["肺動脈", "pulmonary artery"],
    "肺静脈": ["肺静脈", "pulmonary vein"],
    "肺":    ["肺", "lung", "pulmon"],
    "肝":    ["肝", "liver", "hepat"],
    "脾":    ["脾", "spleen"],
    "腎":    ["腎", "kidney", "renal"],
    "大脳":  ["大脳", "cerebrum"],
    "小脳":  ["小脳", "cerebellum"],
    "脳幹":  ["脳幹", "brain stem", "brainstem", "midbrain", "medull", "pons"],
    "心臓":  ["心臓", "心筋", "cardiac", "heart", "myocard"],
    "大動脈":["大動脈", "aorta"],
    "気管":  ["気管", "trachea"],
    "横隔膜":["横隔膜", "diaphragm"],
    "食道":  ["食道", "esophag"],
    "膵臓":  ["膵", "pancrea"],
    "小腸":  ["小腸", "small intestin", "duoden", "jejun", "ileum"],
    "大腸":  ["大腸", "colon", "rectum"],
}
# クエリ中の広域臓器名 → 対応する _organ_context タグセット（サブツリーを包括）
_ORGAN_QUERY_EXPANSION: dict[str, list[str]] = {
    "脳":   ["大脳", "小脳", "脳幹"],
    "脊髄": ["大脳", "脳幹"],  # 脊髄損傷で脳幹も含む場合に対応
}
_ORGAN_BOOST = 4.0  # 臓器名一致サブツリーへの加算


def _detect_organ_keywords(description: str) -> set[str]:
    """クエリ記述から臓器名タグを検出する。広域キー（「脳」など）は展開する。"""
    desc_lower = description.lower()
    detected: set[str] = set()
    # 通常の臓器キーワード
    for organ, pats in _ORGAN_SUBTREE_KEYWORDS.items():
        if any(p in description or p in desc_lower for p in pats):
            detected.add(organ)
    # 広域キーワードの展開（「脳」→「大脳」「小脳」「脳幹」）
    for broad, expansions in _ORGAN_QUERY_EXPANSION.items():
        if broad in description:
            detected.update(expansions)
    return detected


def _propagate_organ_context(entries: list[dict], inherited: str = "") -> None:
    """親エントリの臓器名を子孫に _organ_context として伝播する（in-place）"""
    for e in entries:
        title_ja = e.get("title_ja") or ""
        title_en = (e.get("title_en") or "").lower()
        matched = ""
        for organ, pats in _ORGAN_SUBTREE_KEYWORDS.items():
            if any(p in title_ja or p in title_en for p in pats):
                matched = organ
                break
        ctx = matched or inherited
        if ctx:
            e["_organ_context"] = ctx
        _propagate_organ_context(e.get("children", []), ctx)


def load_codebook() -> dict[str, list[dict]]:
    """output/data/json/ から全AISコードブックを読み込み、臓器コンテキストを伝播する"""
    codebook: dict[str, list[dict]] = {}
    for f in DATA_JSON_DIR.glob("*.json"):
        data = json.loads(f.read_text(encoding="utf-8"))
        bp = data.get("body_part", f.stem)
        entries = data.get("entries", [])
        _propagate_organ_context(entries)
        codebook[bp] = entries
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


# ─── 正規化・重症度ヘルパー ─────────────────────────────────────────────────────

def _normalize_kana(s: str) -> str:
    """
    カタカナをひらがなへ正規化し、英字を小文字化して表記ゆれを吸収する。
    例: 「クモ膜下出血」「くも膜下出血」を同一視できるようにする。
    コードブックの照合・スコアリングは全てこの正規化を通す。
    """
    if not s:
        return ""
    out: list[str] = []
    for ch in s:
        o = ord(ch)
        if 0x30A1 <= o <= 0x30F6:        # カタカナ → ひらがな
            out.append(chr(o - 0x60))
        elif ch in ("ヽ", "ヾ"):          # カタカナ繰り返し記号
            out.append(chr(ord(ch) - 0x60))
        else:
            out.append(ch)
    return "".join(out).lower()


def _severity_from_code(code: str, fallback: int = 0) -> int:
    """
    AIS重症度はコード末尾「.」の右1桁で確定する（コードが唯一の情報源）。
    codebookの ais_severity 欄は抽出誤りがありうるため信用しない。
    「.9」は重症度不明（AIS 9）を意味し、そのまま返す（ISS計算側で除外する）。
    """
    if code and "." in code:
        tail = code.rsplit(".", 1)[1].strip()
        if tail[:1].isdigit():
            return int(tail[0])
    return fallback


def _entry_to_candidate(e: dict, confidence: str = "low") -> dict:
    """codebookエントリを候補フォーマット（run_ais_coding が扱う形）へ変換する"""
    code = e.get("code") or ""
    return {
        "code":           code,
        "japanese":       e.get("title_ja", ""),
        "english":        e.get("title_en", ""),
        "description_ja": "",
        "severity":       _severity_from_code(code, e.get("ais_severity", 0)),
        "confidence":     confidence,
    }


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
    "penetrating":  ["穿通", "穿刺", "吸い込み", "刺傷", "刺創", "刺し傷", "切創"],
    "vascular":     ["血管損傷", "血管裂傷"],
    "nerve":        ["神経損傷", "神経麻痺"],
}

# Stage 2: フィールド重み
_FIELD_WEIGHTS: dict[str, float] = {
    "title_ja":       3.0,
    "title_en":       2.0,
    "section":        1.0,
    "description_ja": 0.5,
    "description_en": 0.5,
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
    """エントリからIDF計算用term集合を抽出（カナ正規化済み）"""
    terms: set[str] = set()
    ja = _normalize_kana(e.get("title_ja") or "")
    en = (e.get("title_en") or "").lower()
    terms.update(ja[i:i+2] for i in range(len(ja) - 1))
    terms.update(w for w in en.split() if len(w) >= 2)
    terms.update(en[i:i+2] for i in range(len(en) - 1))
    return terms


def _query_terms(description: str) -> list[str]:
    """クエリ記述からスコアリング用termリストを抽出（カナ正規化済み）"""
    norm    = _normalize_kana(description.replace("　", " "))
    words   = [w for w in norm.split() if len(w) >= 2]
    bigrams = [norm[i:i+2] for i in range(len(norm) - 1)]
    return words + bigrams


def _detect_injury_types(description: str) -> set[str]:
    """クエリ記述から損傷形態タグを検出する（Stage 1）"""
    return {tag for tag, pats in _INJURY_TYPE_QUERY.items()
            if any(p in description for p in pats)}


def _score_entry(
    entry: dict,
    terms: list[str],
    idf: dict[str, float],
    detected_types: set[str],
    detected_organs: set[str],
) -> float:
    """1エントリのIDF重み付き多フィールドスコアを計算する（Stage 2）"""
    score = 0.0
    field_texts: dict[str, str] = {
        "title_ja":       _normalize_kana(entry.get("title_ja") or ""),
        "title_en":       (entry.get("title_en") or "").lower(),
        "section":        (entry.get("section") or "").lower(),
        "description_ja": _normalize_kana(entry.get("description_ja") or ""),
        "description_en": (entry.get("description_en") or "").lower(),
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

    # 臓器コンテキスト一致ボーナス（Stage 4: 伝播された _organ_context を参照）
    if detected_organs and entry.get("_organ_context") in detected_organs:
        score += _ORGAN_BOOST

    return score


def _score_tree(
    entries: list[dict],
    terms: list[str],
    idf: dict[str, float],
    detected_types: set[str],
    detected_organs: set[str],
    parent_boost: float = 0.0,
) -> list[tuple[float, dict]]:
    """階層構造を考慮したスコアリング（親スコアの一部を子に継承: 案3）"""
    results: list[tuple[float, dict]] = []
    for e in entries:
        own   = _score_entry(e, terms, idf, detected_types, detected_organs)
        total = own + parent_boost
        results.append((total, e))
        children = e.get("children") or []
        if children:
            results.extend(
                _score_tree(children, terms, idf, detected_types, detected_organs,
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
    多段階スコアリングでエントリを絞り込む。

    Stage 1: 損傷形態タグ検出 → 一致エントリにボーナス付与
    Stage 2: IDF重み付き多フィールドスコアリング
    Stage 3: 親スコアを子に継承（_PARENT_BOOST_RATIO）
    Stage 4: 臓器名検出 → 対応サブツリーにボーナス付与

    戻り値: (絞り込み済みエントリリスト, 検出された損傷形態タグ)
    """
    terms           = _query_terms(description)
    detected_types  = _detect_injury_types(description)
    detected_organs = _detect_organ_keywords(description)

    scored = _score_tree(entries, terms, idf, detected_types, detected_organs)
    scored.sort(key=lambda x: -x[0])

    matched   = [e for s, e in scored if s > 0]
    unmatched = [e for s, e in scored if s == 0]
    filtered  = matched[:top_n] + unmatched[:max(0, top_n - len(matched))]
    return filtered, detected_types


def structural_candidates(
    codebook: dict,
    body_parts: list[str],
    description: str,
    idf: dict[str, float],
    top_n: int = 9,
) -> list[dict]:
    """
    Geminiが候補を返さなかった場合の決定論的フォールバック。
    structural_filter の上位ヒットを候補フォーマット（run_ais_coding が扱う形）に変換する。
    LLM非依存のため、コードブックに該当があれば必ず候補を提示できる。
    """
    scored: list[tuple[float, dict]] = []
    terms           = _query_terms(description)
    detected_types  = _detect_injury_types(description)
    detected_organs = _detect_organ_keywords(description)
    for bp in body_parts:
        scored.extend(
            _score_tree(codebook.get(bp, []), terms, idf, detected_types, detected_organs)
        )
    scored.sort(key=lambda x: -x[0])

    out: list[dict] = []
    seen: set[str] = set()
    for s, e in scored:
        if s <= 0:
            break
        code = e.get("code") or ""
        if not code or code in seen:
            continue
        seen.add(code)
        out.append(_entry_to_candidate(e, confidence="low"))  # 構造検索のため低確信度
        if len(out) >= top_n:
            break
    return out


def _clean_title_for_match(title_ja: str) -> str:
    """
    完全名一致用に codebook タイトルを正規化する。
    脚注・[OIS]注記・「詳細不明」等の付帯語を除去し、カナ正規化して返す。
    """
    t = _abbreviate_child_title(title_ja or "", max_len=100)
    for drop in ("詳細不明", "NFS", "nfs"):
        t = t.replace(drop, "")
    return _normalize_kana(t.strip())


def exact_name_candidates(
    codebook: dict,
    body_parts: list[str],
    description: str,
    limit: int = 6,
) -> list[dict]:
    """
    記述中に病名がそのまま現れる codebook エントリを決定論的に抽出する。
    LLMが明示された病名（例: くも膜下出血）を見落としても候補から欠落させない。

    正規化タイトル（カナ・脚注・「詳細不明」除去後）が正規化記述の部分文字列で
    あれば一致とみなす。より具体的（＝長い）な名称を優先し、確信度 high で返す。
    """
    d = _normalize_kana(description)
    if not d:
        return []
    hits: list[tuple[int, bool, dict]] = []
    seen: set[str] = set()
    for bp in body_parts:
        for e in _flatten_entries(codebook.get(bp, [])):
            code = e.get("code") or ""
            if not code or code in seen:
                continue
            name = _clean_title_for_match(e.get("title_ja") or "")
            if len(name) < 3 or name not in d:
                continue
            seen.add(code)
            is_nfs = "詳細不明" in (e.get("title_ja") or "")
            hits.append((len(name), is_nfs, e))
    # 名称が長い＝具体的なものを優先。同名なら「詳細不明」でない方を優先。
    hits.sort(key=lambda h: (-h[0], h[1]))
    return [_entry_to_candidate(e, confidence="high") for _l, _n, e in hits[:limit]]


def merge_candidates(*groups: list[dict], limit: int = 9) -> list[dict]:
    """
    複数の候補リストをコード重複を除いて優先順に統合する。
    severity は必ずコード末尾から再導出し、ISS計算の一貫性を担保する。
    先頭グループ（＝完全名一致）を最優先に配置する。
    """
    merged: list[dict] = []
    seen: set[str] = set()
    for group in groups:
        for c in group:
            code = c.get("code") or ""
            if not code or code in seen:
                continue
            seen.add(code)
            c = dict(c)
            c["severity"] = _severity_from_code(code, c.get("severity", 0))
            merged.append(c)
            if len(merged) >= limit:
                return merged
    return merged


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
        lines.append("コード | 親分類 | 日本語 | English | AIS重症度")
        lines.append("-------|--------|--------|---------|----------")
        for e in filtered:
            organ_ctx = e.get("_organ_context", "")
            lines.append(
                f"{e.get('code','')} | {organ_ctx} | {e.get('title_ja','')} | "
                f"{e.get('title_en','')} | {e.get('ais_severity','')}"
            )
    return "\n".join(lines)


# ─── 対話確認ヘルパー ──────────────────────────────────────────────────────────

def _note_severity(entry: dict) -> None:
    """
    重症度不明（AIS 0 / .9）の場合に注意喚起のみ行う。
    AIS重症度はコードから一意に決まるため手入力での変更は行わない
    （確信度が低くてもコードが確定していれば重症度は確定している）。
    """
    sev  = entry.get("severity", 0)
    code = (entry.get("code") or "")
    ja   = entry.get("japanese", "")
    if sev == 0:
        print(f"\n  ⚠  重症度が導出できません（AIS 0）: [{code}] {ja}")
        print(f"     → より具体的なコードを選び直すと重症度が確定します")
    elif sev == 9 or code.endswith(".9"):
        print(f"\n  ⚠  重症度不明コード（AIS 9 / ISS計算からは除外）: [{code}] {ja}")
        print(f"     → 詳細が判明していれば具体的なコードを選び直してください")


# ISS身体部位の選択肢: (JTDB field ID相当, 表示ラベル)
_SPINE_ISS_OPTIONS: list[tuple[str, str]] = [
    ("70", "頭部・頸部（頸椎: C1-7）"),
    ("72", "胸部    （胸椎: T1-12）"),
    ("73", "腹部・骨盤内臓器（腰椎: L1-5）"),
    ("74", "四肢・骨盤（レベル不明 / その他）"),
]

# コードブックの section 値 → ISS field ID
_SPINE_SECTION_TO_ISS: dict[str, tuple[str, str]] = {
    "CERVICAL SPINE": ("70", "頭部・頸部（頸椎: C1-7）"),
    "THORACIC SPINE": ("72", "胸部（胸椎: T1-12）"),
    "LUMBAR SPINE":   ("73", "腹部・骨盤内臓器（腰椎: L1-5）"),
}


def _auto_spine_iss_region(code: str, codebook: dict) -> tuple[str, str] | None:
    """
    spine コードブックのエントリ section から ISS 部位を自動判定する。
    戻り値: (field_id, label) または None（判定不能）
    """
    for e in _flatten_entries(codebook.get("spine", [])):
        if e.get("code") == code:
            sec = (e.get("section") or "").upper().strip()
            return _SPINE_SECTION_TO_ISS.get(sec)
    return None


def _lookup_codebook_entry(code: str, body_parts: list[str], codebook: dict) -> dict | None:
    """コードブックからコードに一致するエントリ（children含む）を返す"""
    for bp in body_parts:
        for e in _flatten_entries(codebook.get(bp, [])):
            if e.get("code") == code:
                return e
    return None


def _child_to_entry(entry: dict, child: dict) -> dict:
    """親エントリ entry を選択された子 child の属性で更新した新エントリを返す"""
    code = child.get("code", "")
    return {**entry,
            "code":       code,
            "japanese":   child.get("title_ja", ""),
            "english":    child.get("title_en", ""),
            "severity":   _severity_from_code(code, child.get("ais_severity", entry.get("severity", 0))),
            "confidence": "confirmed"}


def _resolve_open_fracture(entry: dict, open_child: dict, attrs: dict) -> dict:
    """
    骨折が確定した部位/形態で開放/閉鎖を確定する。
    記述に「開放」があれば自動で開放コードを採用、無ければ y/N で確認する。
    「なし（Enter）」＝閉鎖として現エントリ（詳細不明含む）のまま確定。
    """
    open_ja = _abbreviate_child_title(open_child.get("title_ja") or "")[:40]
    if attrs.get("open_fracture"):
        print(f"     ↳ 開放骨折の有無: 「開放」と記述より自動判定 → "
              f"[{open_child.get('code','')}] AIS{open_child.get('ais_severity', entry.get('severity',0))}")
        return _child_to_entry(entry, open_child)

    print(f"\n  🔍 {_AXIS_QUESTION['open_fracture']}")
    print(f"     現在（閉鎖として確定）: [{entry.get('code','')}] AIS{entry.get('severity',0)}  {entry.get('japanese','')}")
    print(f"     開放の場合: [{open_child.get('code','')}] AIS{open_child.get('ais_severity','')}  {open_ja}")
    try:
        ans = input("     (y=開放骨折 / Enter=閉鎖で確定) > ").strip().lower()
    except (KeyboardInterrupt, EOFError):
        ans = ""
    if ans == "y":
        print(f"     → [{open_child.get('code','')}] 開放骨折で確定")
        return _child_to_entry(entry, open_child)
    print(f"     → [{entry.get('code','')}] 閉鎖で確定")
    return entry


def _drill_down(entry: dict, cb_entry: dict | None, attrs: dict | None = None,
                in_fracture: bool = False) -> dict:
    """
    選択されたエントリに子セクションがある場合、詳細を確定する。

    子集合を「軸」（サイズ・片側/両側・昏睡時間 等）に分類し、
      1. attrs（記述から解析済みの既知属性）で自動解決できれば黙って選び再帰
      2. できなければ軸に応じた質問文でユーザーに確認し、回答を attrs に記録
    子が選択されればその子に対して再帰的に実行する。

    骨折サブツリーでは各階層に混在する「開放 詳細不明」を部位/形態選択から
    切り離し、部位/形態が確定した後に必ず開放/閉鎖を一度だけ確認する
    （従来は形態確認に埋もれ開放の有無が問われず詳細不明で確定していた）。
    """
    if not cb_entry:
        return entry
    attrs = dict(attrs or {})

    children = [c for c in cb_entry.get("children", []) if c.get("code")]
    if not children:
        return entry  # リーフ = 確定

    in_fracture = in_fracture or _is_fracture_node(cb_entry)

    open_child: dict | None = None
    rest = children
    if in_fracture:
        open_child, rest = _split_open_child(children)

    chosen: dict | None = None
    if rest:
        axis = _classify_axis(rest)
        if axis:
            chosen = _auto_pick_child(rest, axis, attrs)
            if chosen is not None:
                ja = _abbreviate_child_title(chosen.get("title_ja") or "")[:45]
                print(f"     ↳ {_AXIS_QUESTION.get(axis, '詳細分類')}: "
                      f"「{attrs.get(axis)}」と記述より自動判定 → [{chosen.get('code','')}] "
                      f"AIS{chosen.get('ais_severity', entry.get('severity', 0))}  {ja}")
        if chosen is None:
            chosen = _ask_child_selection(entry, rest, axis)
            if chosen is not None and axis:
                attrs[axis] = _child_axis_value(axis, chosen)

    if chosen is not None:
        return _drill_down(_child_to_entry(entry, chosen), chosen, attrs, in_fracture)

    # 部位/形態はこれ以上確定しない（詳細不明で確定）。骨折なら開放/閉鎖を確認。
    if in_fracture and open_child is not None:
        return _resolve_open_fracture(entry, open_child, attrs)
    return entry


def _ask_child_selection(entry: dict, children: list[dict], axis: str | None) -> dict | None:
    """軸に応じた質問文で子分類をユーザーに確認する。選択された子を返す（不明ならNone）。"""
    cur_code = entry.get("code", "")
    cur_sev  = entry.get("severity", 0)
    cur_ja   = entry.get("japanese", "")
    header   = _AXIS_QUESTION.get(axis) if axis else None

    if len(children) == 1:
        child     = children[0]
        child_sev = child.get("ais_severity", cur_sev)
        child_ja  = _abbreviate_child_title(child.get("title_ja") or "")[:50]
        print(f"\n  🔍 {header or '詳細分類があります'}")
        print(f"     現在: [{cur_code}] AIS{cur_sev}  {cur_ja}")
        print(f"     該当しますか?: [{child.get('code','')}] AIS{child_sev}  {child_ja}")
        try:
            ans = input("     (y=該当する / Enter=詳細不明で確定) > ").strip().lower()
        except (KeyboardInterrupt, EOFError):
            ans = ""
        if ans == "y":
            print(f"     → [{child.get('code','')}] AIS{child_sev}  {child_ja}")
            return child
        print(f"     → [{cur_code}] で確定")
        return None

    print(f"\n  🔍 {header or f'詳細分類があります（{len(children)}件）'}")
    print(f"     現在: [{cur_code}] AIS{cur_sev}  {cur_ja}")
    print(f"     判明している場合は選択してください（不明なら Enter で確定）:")
    for i, child in enumerate(children, 1):
        num       = str(i % 10)
        child_sev = child.get("ais_severity", cur_sev)
        child_ja  = _abbreviate_child_title(child.get("title_ja") or "")[:45]
        print(f"       {num}. [{child.get('code','')}] AIS{child_sev}  {child_ja}")
    try:
        ans = input(f"     選択（1-{min(len(children), 9)} / Enter=詳細不明で確定）> ").strip()
    except (KeyboardInterrupt, EOFError):
        ans = ""

    if ans.isdigit():
        n   = int(ans)
        idx = (n - 1) if n > 0 else 9
        if 0 <= idx < len(children):
            child     = children[idx]
            child_sev = child.get("ais_severity", cur_sev)
            child_ja  = _abbreviate_child_title(child.get("title_ja") or "")[:45]
            print(f"     → [{child.get('code','')}] AIS{child_sev}  {child_ja}")
            return child

    print(f"     → [{cur_code}] で確定")
    return None


# ─── 詳細分類の軸判定ヘルパー ────────────────────────────────────────────────────
# 量・大きさの閾値で段階分けされていることを示すパターン（血胸の量・気胸の虚脱率 等）
_SIZE_THRESHOLD_PATTERNS = ["ml", "cc", "cm", "mm", "%", "厚さ", "表面積", "直径",
                            "を超え", "を超", "以上", "以下", "未満", "一葉",
                            "大量", "緊張", "軽症", "中等", "重症"]


def _child_text(c: dict) -> str:
    return ((c.get("title_ja") or "") + " " + (c.get("title_en") or "")).lower()


def _is_size_partition(children: list[dict]) -> bool:
    """子が量・大きさの閾値で段階分けされているか（単一の閾値子も許容）"""
    coded = [c for c in children if c.get("code")]
    if not coded:
        return False
    n_thr = sum(1 for c in coded if any(p in _child_text(c) for p in _SIZE_THRESHOLD_PATTERNS))
    if len(coded) == 1:
        # 例: 血胸「大：1000mlを超える」— 閾値を満たすか否かの確認
        return n_thr == 1
    # 複数子: 過半が閾値表現で、重症度に差がある（サイズ分類の意味がある）
    sev = {c.get("ais_severity") for c in coded}
    return n_thr >= max(2, len(coded) // 2) and len(sev) >= 2


def _is_laterality_partition(children: list[dict]) -> bool:
    """
    子が片側/両側で分かれているか。
    AISの左右軸は必ず「両側」選択肢を持つ。閾値記述中に偶発的に「片側」が
    現れるだけ（例: 血胸「少なくとも片側で1000ml超」）を左右軸と誤判定しない。
    """
    has_bi  = any("両側" in (c.get("title_ja") or "") or "bilateral" in _child_text(c)
                  for c in children)
    has_uni = any("片側" in (c.get("title_ja") or "") or "unilateral" in _child_text(c)
                  for c in children)
    return has_bi and (has_uni or len(children) <= 2)


def _is_fracture_node(cb_entry: dict) -> bool:
    """エントリが骨折コードか（開放/閉鎖の確認対象）"""
    return "骨折" in (cb_entry.get("title_ja") or "") \
        or "fracture" in (cb_entry.get("title_en") or "").lower()


def _split_open_child(children: list[dict]) -> tuple[dict | None, list[dict]]:
    """
    骨折ノードの子から「開放」を表す子を1つ切り出す（残りは形態・部位の細分化）。
    AISの骨折ツリーは各階層で『開放 詳細不明』を兄弟として持つため、
    形態選択とは独立に開放/閉鎖を確認できるよう分離する。
    """
    open_child: dict | None = None
    rest: list[dict] = []
    for c in children:
        t = (c.get("title_ja") or "").strip()
        if open_child is None and (t.startswith("開放") or "open fracture" in _child_text(c)):
            open_child = c
        else:
            rest.append(c)
    return open_child, rest


def _abbreviate_child_title(title_ja: str, max_len: int = 60) -> str:
    """子エントリのタイトルを表示用に整形する"""
    import re
    title = re.sub(r"[ᵃ-ᶻ°ᵉᵍʰ]", "", title_ja)   # 上付き脚注文字
    title = re.sub(r"\s*\[OIS.*?\]", "", title)      # [OIS ...] 注釈
    title = re.sub(r"(?<=[ぁ-鿿])[a-z]$", "", title.strip())  # 末尾の脚注アルファベット
    return title.strip()[:max_len]


# ─── 損傷属性スロット（決定論的解析）────────────────────────────────────────────
# 詳細分類の「軸」定義: (axis名, 子タイトルに現れるキーワード, 質問文)
_AXIS_PATTERNS: list[tuple[str, list[str], str]] = [
    ("coma",          ["6時間を超える昏睡", "coma >6 hours", "coma > 6"],
                      "昏睡（意識障害）は6時間を超えましたか？"),
    ("laterality",    ["片側", "両側", "unilateral", "bilateral"],
                      "損傷は片側ですか、両側ですか？"),
    ("open_fracture", ["開放骨折", "open fracture"],
                      "骨折は開放骨折（皮膚を貫通）ですか？"),
    ("comminuted",    ["粉砕", "comminut"],
                      "骨折は粉砕骨折ですか？"),
    ("paralysis",     ["完全麻痺", "不完全麻痺", "complete paralysis", "incomplete paralysis"],
                      "脊髄損傷は完全麻痺ですか、不完全麻痺ですか？"),
]
_AXIS_QUESTION: dict[str, str] = {axis: q for axis, _kw, q in _AXIS_PATTERNS}
_AXIS_QUESTION["size"] = "損傷の大きさ・程度はどれくらいですか？"


def _classify_axis(children: list[dict]) -> str | None:
    """
    子集合がどの詳細分類軸（サイズ・片側/両側・開放骨折 等）に属するかを判定する。

    サイズ（量・大きさの閾値）を左右より先に判定する。血胸の子は
    「大：少なくとも片側で1000ml超」のように閾値記述内に偶発的な「片側」を
    含むため、キーワード一致だと左右軸と誤判定され量が確認されない。
    """
    # 1) 量・大きさの閾値分類を最優先（血胸の量・気胸の虚脱率 等）
    if _is_size_partition(children):
        return "size"
    # 2) 真の左右軸（「両側」選択肢を持つもの）
    if _is_laterality_partition(children):
        return "laterality"
    # 3) その他のキーワード軸（昏睡・開放・粉砕・麻痺）
    text = " ".join(_child_text(c) for c in children)
    for axis, kws, _q in _AXIS_PATTERNS:
        if axis == "laterality":
            continue  # 上で厳密判定済み
        if any(k.lower() in text for k in kws):
            return axis
    return None


def _auto_pick_child(children: list[dict], axis: str, attrs: dict) -> dict | None:
    """既知属性 attrs に基づき、質問せずに該当する子を選ぶ（該当なしは None）"""
    val = attrs.get(axis)
    if not val:
        return None

    def find(pred) -> dict | None:
        for c in children:
            t = ((c.get("title_ja") or "") + " " + (c.get("title_en") or "")).lower()
            if pred(t):
                return c
        return None

    if axis == "laterality":
        if val == "両側":
            return find(lambda t: "両側" in t or "bilateral" in t)
        return find(lambda t: "片側" in t or "unilateral" in t)
    if axis == "open_fracture":
        return find(lambda t: "開放" in t or "open" in t)
    if axis == "comminuted":
        return find(lambda t: "粉砕" in t or "comminut" in t)
    if axis == "paralysis":
        if val == "不完全麻痺":
            return find(lambda t: "不完全" in t or "incomplete" in t)
        return find(lambda t: ("完全" in t and "不完全" not in t)
                              or ("complete" in t and "incomplete" not in t))
    if axis == "coma":
        return find(lambda t: "6時間を超える" in t or "coma >6" in t or "coma > 6" in t)
    return None


def _child_axis_value(axis: str, child: dict) -> str:
    """ユーザーが選んだ子から、その軸の確定値を記録用に取り出す（深い階層での再質問防止）"""
    t = (child.get("title_ja") or "")
    if axis == "laterality":
        return "両側" if ("両側" in t or "bilateral" in t.lower()) else "片側"
    return t[:20]


def parse_injury_attrs(description: str) -> dict:
    """
    損傷記述から決定論的に既知属性スロットを充填する（I/O・LLM非依存）。
    ここで埋まった軸は drill_down で質問せず自動確定する。

    左右のルール: 右または左があれば「片側」、両側語があれば「両側」。
    AIS重症度は片側/両側でのみ変化し左右では不変のため、右/左は片側として扱う。
    """
    d  = description
    dl = d.lower()
    attrs: dict[str, str] = {}

    if "両側" in d or "bilateral" in dl:
        attrs["laterality"] = "両側"
    elif "右" in d or "左" in d:
        attrs["laterality"] = "片側"

    if "開放" in d:
        attrs["open_fracture"] = "開放骨折"
    if "粉砕" in d:
        attrs["comminuted"] = "粉砕骨折"
    if "不完全麻痺" in d:
        attrs["paralysis"] = "不完全麻痺"
    elif "完全麻痺" in d:
        attrs["paralysis"] = "完全麻痺"
    if "6時間を超える" in d or "6時間超" in d:
        attrs["coma"] = "6時間を超える昏睡あり"

    return attrs


def _enrich_query(description: str, attrs: dict) -> str:
    """解析済み属性のうち記述に未出現の語を付加してGeminiクエリを補強する"""
    adds = [v for v in attrs.values() if v and v not in description]
    if adds:
        return description + "、" + "、".join(adds)
    return description


# ─── 血管損傷の併発確認（実質損傷とは別コード）──────────────────────────────────

def _vascular_subtree_roots(description: str, body_parts: list[str],
                            codebook: dict) -> list[dict]:
    """
    記述中の臓器に対応する血管サブツリーの起点エントリを返す。
    実質損傷があっても血管は併発しうるため、除外せず別候補として提示する。
    """
    organs = _detect_organ_keywords(description)
    if not organs:
        return []
    roots: list[dict] = []
    seen: set[str] = set()
    for bp in body_parts:
        for e in _flatten_entries(codebook.get(bp, [])):
            code = e.get("code") or ""
            ja   = e.get("title_ja") or ""
            if not code or not e.get("children"):
                continue  # サブツリー起点（子を持つノード）のみ
            if not ("動脈" in ja or "静脈" in ja or "血管" in ja):
                continue
            if not any(o in ja for o in organs):  # 検出臓器に関連する血管に限定
                continue
            if code in seen:
                continue
            seen.add(code)
            roots.append(e)
    return roots


def _code_vascular_coinjury(injury_desc: str, attrs: dict,
                            body_parts: list[str], codebook: dict) -> list[dict]:
    """
    血管損傷の併発をユーザーに確認し、併発ありなら血管コードを別途確定する。
    ゲート: 検出臓器の血管サブツリーが実在し、記述に血管語が無いときのみ確認する。
    戻り値: 追加で確定した血管損傷エントリのリスト（0〜1件）。
    """
    if any(w in injury_desc for w in ("血管", "動脈", "静脈")):
        return []  # 記述で血管が明示済み → 主経路が扱う
    roots = _vascular_subtree_roots(injury_desc, body_parts, codebook)
    if not roots:
        return []

    print("\n  🩸 血管損傷の併発確認")
    try:
        ans = input("     血管損傷の併発はありますか？ (y=あり / Enter=なし) > ").strip().lower()
    except (KeyboardInterrupt, EOFError):
        return []
    if ans != "y":
        return []

    print("     併発している血管損傷を選択してください:")
    for i, r in enumerate(roots, 1):
        print(f"       {i % 10}. [{r.get('code','')}] AIS{r.get('ais_severity','')}  "
              f"{_abbreviate_child_title(r.get('title_ja') or '')[:40]}")
    try:
        sel = input(f"     選択（1-{min(len(roots), 9)} / Enter=なし）> ").strip()
    except (KeyboardInterrupt, EOFError):
        return []
    if not sel.isdigit():
        return []
    n   = int(sel)
    idx = (n - 1) if n > 0 else 9
    if not (0 <= idx < len(roots)):
        return []

    root  = roots[idx]
    root_code = root.get("code") or ""
    entry = {
        "description":    injury_desc,
        "code":           root_code,
        "japanese":       root.get("title_ja", ""),
        "description_ja": "",
        "english":        root.get("title_en", ""),
        "severity":       _severity_from_code(root_code, root.get("ais_severity", 0)),
        "confidence":     "confirmed",
    }
    # 血管サブツリー内の詳細分類（サイズ・両側 等）を確定
    entry = _drill_down(entry, root, attrs)
    _note_severity(entry)
    return [entry]


def _confirm_spine_iss_region(code: str, ja: str, codebook: dict) -> str:
    """
    脊椎損傷コードのISS身体部位を決定する。
    コードブックの section 情報から自動判定し、判定できない場合のみ手動確認する。
      頸椎 (CERVICAL SPINE)  → ISS field 70「頭部・頸部」
      胸椎 (THORACIC SPINE)  → ISS field 72「胸部」
      腰椎 (LUMBAR SPINE)    → ISS field 73「腹部・骨盤内臓器」
    戻り値: JTDB field ID相当の文字列
    """
    auto = _auto_spine_iss_region(code, codebook)
    if auto:
        fid, label = auto
        print(f"\n  📍 脊椎ISS部位を自動判定: [{code}] {ja}")
        print(f"     → {label}")
        return fid

    # 自動判定不能（section 不明）→ 手動確認
    print(f"\n  📍 脊椎損傷のISS部位を確認してください（椎体レベル不明）")
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
※ 「親分類」列はエントリが属する臓器・構造の親カテゴリ（例: 肺, 大脳, 小脳, 脳幹, 心臓）を示します。
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

重要な指示:
- 記述にある病名がコードブックの損傷名と（表記ゆれを含め）一致するものは必ず候補に含めること。
  例: 記述「くも膜下出血」→ コードブックの「くも膜下出血」を必ず選ぶ。カタカナ／ひらがなの違いは無視する。
- コードは必ずコードブックに実在するものだけを使い、勝手に作らないこと。
- AIS重症度はコード末尾「.」の右1桁である。コードブックのコードをそのまま写すこと（数値を推測・改変しない）。
- 記述に複数の損傷（例: 実質損傷と血管損傷）が含まれる場合は、それぞれ別コードとして列挙すること。

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
    # AIS 1-6 のみ有効。9（重症度不明）等は除外する。
    scores = [v for v in region_max.values() if 1 <= v <= 6]
    if not scores:
        return None
    if 6 in scores:
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

            # 記述から既知属性を決定論的に解析し、Geminiクエリを補強する
            # （対話的な詳細確認は選択後の _drill_down に一本化）
            attrs = parse_injury_attrs(injury_desc)
            enriched_desc = _enrich_query(injury_desc, attrs)

            print("  AISコードを検索中...", end="", flush=True)

            try:
                llm_candidates = suggest_ais_codes(enriched_desc, body_parts, codebook, client, idf)
                print(f" AI {len(llm_candidates)}件")
            except Exception as e:
                print(f" AIエラー: {e}")
                llm_candidates = []

            # 決定論的なコードブック照合を必ず併用する。
            #   1. 記述に病名がそのまま現れるエントリ（完全名一致）を最優先で保証
            #   2. LLM候補
            #   3. 構造スコア検索で不足分を補完
            # LLMが明示された病名（例: くも膜下出血）を見落としても欠落させない。
            exact = exact_name_candidates(codebook, body_parts, injury_desc)
            structural = structural_candidates(codebook, body_parts, enriched_desc, idf)
            candidates = merge_candidates(exact, llm_candidates, structural)
            if exact:
                names = "、".join(c.get("japanese", "") for c in exact)
                print(f"  （コードブック名称一致を優先候補に追加: {names}）")
            elif not llm_candidates and candidates:
                print(f"  （AI候補なし → コードブック構造検索で {len(candidates)}件を提示）")

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
                        "severity": _severity_from_code(ans, 0),
                        "confidence": "manual",
                    }
                    _note_severity(manual_entry)
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
                                "japanese": e.get("title_ja", ""),
                                "english": e.get("title_en", ""),
                                "severity": _severity_from_code(ans, e.get("ais_severity", 0)),
                                "confidence": "manual",
                            }
                            break
                    if found_entry:
                        break
                if found_entry:
                    selected = found_entry
                else:
                    # コードブックに無くても、AIS重症度はコード末尾から確定できる
                    print(f"  ⚠ コード {ans} がコードブックにありません（手動登録）")
                    selected = {
                        "description": injury_desc,
                        "code": ans,
                        "japanese": "",
                        "english": "",
                        "severity": _severity_from_code(ans, 0),
                        "confidence": "manual",
                    }

            if selected:
                sel_code = selected.get("code") or ""
                entry = {
                    "description": injury_desc,
                    "code": sel_code,
                    "japanese": selected.get("japanese", ""),
                    "description_ja": (selected.get("description_ja") or "").strip(),
                    "english": selected.get("english", ""),
                    "severity": _severity_from_code(sel_code, selected.get("severity", 0)),
                    "confidence": selected.get("confidence", ""),
                }

                # 子セクションがある場合は詳細確認（詳細不明 → 詳細あり へのドリルダウン）
                cb_entry = _lookup_codebook_entry(
                    entry.get("code", ""), body_parts, codebook
                )
                entry = _drill_down(entry, cb_entry, attrs)

                # 重症度はコードから確定済み。不明（0/9）のときのみ注意喚起する。
                _note_severity(entry)

                # 脊椎コードはISS計算用の椎体レベルを確認する
                code = entry.get("code") or ""
                if code and _is_spine_code(code, codebook):
                    iss_fid = _confirm_spine_iss_region(code, entry.get("japanese", ""), codebook)
                    entry["iss_region"] = iss_fid
                    # spine_iss に記録（ISS計算時に正しい部位で加算）。
                    # AIS 9（重症度不明）はISSに使えないため除外する。
                    if 1 <= entry["severity"] <= 6:
                        spine_iss.setdefault(iss_fid, []).append(entry["severity"])

                coded_entries.append(entry)
                print(f"  ✓ 確定: [{entry['code']}] AIS{entry['severity']}  {entry['japanese']}")
                if entry["description_ja"]:
                    print(f"         {entry['description_ja']}")

                # 血管損傷の併発を確認（実質＋血管で複数コードになりうる。個別に確定）
                for ve in _code_vascular_coinjury(injury_desc, attrs, body_parts, codebook):
                    coded_entries.append(ve)
                    print(f"  ✓ 併発（血管）確定: [{ve['code']}] AIS{ve['severity']}  {ve['japanese']}")

        ais_results[fid] = coded_entries
        # spine_iss に記録済みの脊椎損傷はmax_aisから除外（二重計上防止）。
        # AIS 9（重症度不明）はISS計算に使えないため除外する。
        severities = [
            e["severity"] for e in coded_entries
            if 1 <= e.get("severity", 0) <= 6 and "iss_region" not in e
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

# 各テンプレート: (説明, patient dict, injuries dict, GCS合計)
_DUMMY_TEMPLATES: list[tuple[str, dict, dict, int]] = [
    (
        "55歳男性・鈍的（交通事故）: 頭部・胸部・腹部・四肢",
        {
            "2":  {"label": "年齢",      "value": 55, "display": "55", "raw": "55歳", "unknown": False},
            "38": {"label": "収縮期血圧","value": 90, "display": "90", "raw": "90",   "unknown": False},
            "40": {"label": "呼吸数",    "value": 20, "display": "20", "raw": "20",   "unknown": False},
            "45": {"label": "GCS-E",     "value": 3,  "display": "3（呼びかけで開眼）","raw": "E3","unknown": False},
            "46": {"label": "GCS-V",     "value": 4,  "display": "4（混乱した会話）",  "raw": "V4","unknown": False},
            "47": {"label": "GCS-M",     "value": 5,  "display": "5（疼痛部位への反応）","raw": "M5","unknown": False},
            "8":  {"label": "外傷分類",  "value": 1,  "display": "鈍的", "raw": "鈍的","unknown": False},
        },
        {
            "70": ["右硬膜外血腫", "くも膜下出血"],
            "71": [],
            "72": ["左肋骨骨折（第4・5・6肋骨）", "左血胸"],
            "73": ["脾臓損傷"],
            "74": ["左大腿骨骨幹部骨折"],
            "75": [],
        },
        12,
    ),
    (
        "28歳男性・鈍的（歩行者 vs 自動車）: 頭部・胸部・四肢・骨盤",
        {
            "2":  {"label": "年齢",      "value": 28, "display": "28", "raw": "28歳", "unknown": False},
            "38": {"label": "収縮期血圧","value": 80, "display": "80", "raw": "80",   "unknown": False},
            "40": {"label": "呼吸数",    "value": 24, "display": "24", "raw": "24",   "unknown": False},
            "45": {"label": "GCS-E",     "value": 2,  "display": "2（疼痛で開眼）",   "raw": "E2","unknown": False},
            "46": {"label": "GCS-V",     "value": 3,  "display": "3（不適切な言葉）", "raw": "V3","unknown": False},
            "47": {"label": "GCS-M",     "value": 5,  "display": "5（疼痛部位への反応）","raw": "M5","unknown": False},
            "8":  {"label": "外傷分類",  "value": 1,  "display": "鈍的", "raw": "鈍的","unknown": False},
        },
        {
            "70": ["脳挫傷", "外傷性くも膜下出血"],
            "71": [],
            "72": ["右気胸", "右肋骨骨折（第2・3・4肋骨）"],
            "73": [],
            "74": ["骨盤骨折（恥骨結合離開）", "右脛骨骨折"],
            "75": [],
        },
        10,
    ),
    (
        "42歳女性・鈍的（高所転落）: 脊椎・胸部・腹部・四肢",
        {
            "2":  {"label": "年齢",      "value": 42, "display": "42", "raw": "42歳", "unknown": False},
            "38": {"label": "収縮期血圧","value": 100,"display": "100","raw": "100",  "unknown": False},
            "40": {"label": "呼吸数",    "value": 22, "display": "22", "raw": "22",   "unknown": False},
            "45": {"label": "GCS-E",     "value": 4,  "display": "4（自発的に開眼）", "raw": "E4","unknown": False},
            "46": {"label": "GCS-V",     "value": 5,  "display": "5（見当識あり）",   "raw": "V5","unknown": False},
            "47": {"label": "GCS-M",     "value": 6,  "display": "6（命令に従う）",   "raw": "M6","unknown": False},
            "8":  {"label": "外傷分類",  "value": 1,  "display": "鈍的", "raw": "鈍的","unknown": False},
        },
        {
            "70": [],
            "71": [],
            "72": ["多発肋骨骨折（右第5〜9肋骨）", "肺挫傷"],
            "73": ["肝損傷（Ⅱ度）"],
            "74": ["胸椎骨折（T8圧迫骨折）", "右踵骨骨折"],
            "75": [],
        },
        15,
    ),
    (
        "35歳男性・穿通性（刺創）: 胸部・腹部",
        {
            "2":  {"label": "年齢",      "value": 35, "display": "35", "raw": "35歳", "unknown": False},
            "38": {"label": "収縮期血圧","value": 70, "display": "70", "raw": "70",   "unknown": False},
            "40": {"label": "呼吸数",    "value": 28, "display": "28", "raw": "28",   "unknown": False},
            "45": {"label": "GCS-E",     "value": 4,  "display": "4（自発的に開眼）", "raw": "E4","unknown": False},
            "46": {"label": "GCS-V",     "value": 4,  "display": "4（混乱した会話）", "raw": "V4","unknown": False},
            "47": {"label": "GCS-M",     "value": 6,  "display": "6（命令に従う）",   "raw": "M6","unknown": False},
            "8":  {"label": "外傷分類",  "value": 2,  "display": "穿通性", "raw": "穿通性","unknown": False},
        },
        {
            "70": [],
            "71": [],
            "72": ["左血気胸", "左肺裂傷"],
            "73": ["小腸穿孔", "腸間膜損傷"],
            "74": [],
            "75": ["左胸部刺創"],
        },
        14,
    ),
    (
        "78歳女性・鈍的（自宅転倒）: 頭部・顔面・四肢",
        {
            "2":  {"label": "年齢",      "value": 78, "display": "78", "raw": "78歳", "unknown": False},
            "38": {"label": "収縮期血圧","value": 130,"display": "130","raw": "130",  "unknown": False},
            "40": {"label": "呼吸数",    "value": 18, "display": "18", "raw": "18",   "unknown": False},
            "45": {"label": "GCS-E",     "value": 3,  "display": "3（呼びかけで開眼）","raw": "E3","unknown": False},
            "46": {"label": "GCS-V",     "value": 4,  "display": "4（混乱した会話）",  "raw": "V4","unknown": False},
            "47": {"label": "GCS-M",     "value": 6,  "display": "6（命令に従う）",    "raw": "M6","unknown": False},
            "8":  {"label": "外傷分類",  "value": 1,  "display": "鈍的", "raw": "鈍的","unknown": False},
        },
        {
            "70": ["急性硬膜下血腫"],
            "71": ["眼窩骨折", "鼻骨骨折"],
            "72": [],
            "73": [],
            "74": ["右大腿骨頸部骨折"],
            "75": [],
        },
        13,
    ),
]


def _make_dummy_patient() -> tuple[dict, Path]:
    """ダミーテンプレートからランダムに1件選択してデバッグ用患者データを生成する"""
    import uuid
    import random
    from datetime import datetime

    label, patient_tmpl, injuries, gcs = random.choice(_DUMMY_TEMPLATES)

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
            "dummy_label": label,
        },
        "patient": patient_tmpl,
        "injuries": injuries,
        "scores": {
            "gcs": gcs, "rts": None, "iss": None,
            "triss_ps": None, "ais_coding_status": "pending",
        },
    }
    return data, path, label


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
        patient_data, patient_path, dummy_label = _make_dummy_patient()
        print(f"【デバッグモード】ダミー患者: {dummy_label}")
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
