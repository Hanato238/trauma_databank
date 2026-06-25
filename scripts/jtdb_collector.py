#!/usr/bin/env python3
"""
日本外傷データバンク（JTDB）患者情報収集システム

使い方:
  uv run jtdb_collector.py                       # 対話モード（新規）
  uv run jtdb_collector.py --resume <session_id> # セッション再開
  uv run jtdb_collector.py --json                # NanoClaw用 JSON stdin/stdout モード
"""

import os
import sys
import json
import uuid
import math
from datetime import datetime
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

SCHEMA_PATH  = Path(__file__).parent / "output" / "jtdb_schema.json"
SESSIONS_DIR = Path(__file__).parent / "output" / "sessions"
PATIENTS_DIR = Path(__file__).parent / "output" / "patients"

# AIS部位フィールド（Phase 3でコーディング）
AIS_FIELD_IDS = {70, 71, 72, 73, 74, 75}

# JCS フィールド（Ⅰ/Ⅱ/Ⅲ表記に変換）
JCS_FIELDS = {24, 44}

_JCS_LABEL: dict[int, str] = {
    0:   "0（清明）",
    1:   "Ⅰ-1（刺激なしで覚醒）", 2: "Ⅰ-2", 3: "Ⅰ-3",
    10:  "Ⅱ-10（刺激で覚醒）",   20: "Ⅱ-20", 30: "Ⅱ-30",
    100: "Ⅲ-100（刺激で覚醒せず）", 200: "Ⅲ-200", 300: "Ⅲ-300",
    99:  "不明",
}

# 条件付きスキップ: 特定フィールドが「施行せず」等なら後続をスキップ
def _surgery_not_done(s):
    v = s["fields"].get("63", {}).get("value")
    if isinstance(v, list):
        return 12 in v  # 12=施行せず
    return v == 12

def _not_dead(s):
    return s["fields"].get("88", {}).get("value") != 4

CONDITIONAL_SKIP = {
    "64": _surgery_not_done,
    "67": _surgery_not_done,
    "68": _surgery_not_done,
}


# ─── スキーマ ───────────────────────────────────────────────────────────────

def load_schema() -> dict:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def get_required_fields(schema: dict) -> list[dict]:
    """○必須フィールド（AIS・自動計算除く）"""
    return [f for f in schema["fields"] if f["required"] == "○" and f["id"] not in AIS_FIELD_IDS]


def get_ais_fields(schema: dict) -> list[dict]:
    return [f for f in schema["fields"] if f["id"] in AIS_FIELD_IDS]


# ─── セッション管理 ──────────────────────────────────────────────────────────

def new_session() -> dict:
    return {
        "session_id": str(uuid.uuid4())[:8],
        "created_at": datetime.now().isoformat(),
        "status": "in_progress",
        "fields": {},       # str(field_id) -> {"value": ..., "raw": ..., "unknown": bool}
        "injuries": {str(i): [] for i in AIS_FIELD_IDS},  # 自由記述（Phase 3でAIS化）
        "history": [],      # [{role: "user"|"assistant", text: str}]
    }


def load_session(session_id: str) -> dict | None:
    path = SESSIONS_DIR / f"{session_id}.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def save_session(session: dict):
    SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
    path = SESSIONS_DIR / f"{session['session_id']}.json"
    path.write_text(json.dumps(session, ensure_ascii=False, indent=2))


def find_missing(session: dict, required_fields: list[dict]) -> list[dict]:
    filled = set(session["fields"].keys())
    missing = []
    for f in required_fields:
        fid = str(f["id"])
        if fid in filled:
            continue
        skip_fn = CONDITIONAL_SKIP.get(fid)
        if skip_fn and skip_fn(session):
            continue
        missing.append(f)
    return missing


# ─── Gemini 呼び出し ─────────────────────────────────────────────────────────

EXTRACT_SYSTEM = """\
あなたは日本外傷データバンク（JTDB）の入力支援AIです。
患者に関するテキストからJTDB項目を抽出し、指定のJSON形式で返してください。

ルール:
- 提供されたフィールド定義からのみ値を抽出する
- 確信が低い場合は含めない（未抽出のまま）
- choices フィールド: value（数値）で返す。multi_select は数値配列
- choices_common フィールド: 該当テキストで返す。multi_select は文字列配列、select は文字列
  例: 合併症=[\"ARDS\", \"敗血症\"]、受傷機転=\"交通事故（自動車乗車中）\"
- ユーザーが番号で回答した場合は choices_common の対応テキストに変換する
- 「不明」と明示されている場合は unknown=true とする
- 日付は yyyy/mm/dd、時刻は hh:mm（24時間制）で正規化
- 損傷・傷病名は injuries に部位別自由記述でまとめる
- JSONのみ返すこと（説明文不要）
"""

def _field_desc(f: dict) -> str:
    desc = f"id={f['id']}, name=\"{f['name']}\", type={f['type']}"
    if f.get("choices"):
        opts = ", ".join(f"{c['value']}={c['label']}" for c in f["choices"])
        desc += f", choices=[{opts}]"
    elif f.get("choices_common"):
        numbered = ", ".join(f"{i+1}={v}" for i, v in enumerate(f["choices_common"]))
        ret = "文字列配列" if f.get("type") == "multi_select" else "文字列"
        desc += f", choices_common=[{numbered}]({ret}で返す)"
    if f.get("unit"):
        desc += f", unit={f['unit']}"
    if f.get("unknown_allowed"):
        desc += ", unknown_allowed=true"
    return desc


def extract_from_text(text: str, required_fields: list[dict], ais_fields: list[dict],
                      session: dict, client) -> dict:
    from google.genai import types as gtypes

    fields_lines = "\n".join(f"  {_field_desc(f)}" for f in required_fields)
    ais_lines = "\n".join(f"  id={f['id']}: {f['name']}" for f in ais_fields)
    existing_fields = json.dumps(session["fields"], ensure_ascii=False)
    existing_injuries = json.dumps(session["injuries"], ensure_ascii=False)

    prompt = f"""【すでに収集済みの情報】
fields: {existing_fields}
injuries: {existing_injuries}

【新しいテキスト入力】
{text}

【抽出対象フィールド定義】
{fields_lines}

【損傷部位フィールド（自由記述で収集）】
{ais_lines}

上記テキストから読み取れる情報を以下のJSON形式で返してください：
{{
  "fields": {{
    "<field_id文字列>": {{
      "value": <抽出値（選択肢なら数値、複数選択なら配列）>,
      "raw": "<元テキスト>",
      "unknown": false
    }}
  }},
  "injuries": {{
    "70": ["<頭頸部の損傷記述>"],
    "71": ["<顔面の損傷記述>"],
    "72": ["<胸部の損傷記述>"],
    "73": ["<腹部の損傷記述>"],
    "74": ["<四肢・骨盤の損傷記述>"],
    "75": ["<体表の損傷記述>"]
  }}
}}
確信が低い項目は含めないこと。injuries は既存分と重複しないこと。"""

    resp = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=prompt,
        config=gtypes.GenerateContentConfig(system_instruction=EXTRACT_SYSTEM),
    )
    raw = resp.text.strip()
    if "```json" in raw:
        raw = raw.split("```json")[1].split("```")[0].strip()
    elif "```" in raw:
        raw = raw.split("```")[1].split("```")[0].strip()
    return json.loads(raw)


def _pos(i: int) -> str:
    """1始まりの位置を1桁文字列に変換（10番目 → '0'）"""
    return str(i % 10)


def format_question(missing_fields: list[dict], batch_size: int = 1) -> tuple[str, list[str]]:
    """Gemini不要: フィールド定義から直接番号付き質問を生成する（JSONモード / NanoClaw用）"""
    batch = missing_fields[:batch_size]
    parts = []

    for f in batch:
        name   = f["name"]
        ftype  = f.get("type", "text")
        is_jcs = f.get("id") in JCS_FIELDS
        multi  = "（複数可: スペース区切り）" if ftype == "multi_select" else ""

        if f.get("choices"):
            main_c  = [c for c in f["choices"] if c["value"] != 99]
            has_unk = any(c["value"] == 99 for c in f["choices"]) or f.get("unknown_allowed")
            unk_line = "\n  u. 不明" if has_unk else ""
            use_pos = len(main_c) <= 10
            opts = "\n".join(
                f"  {_pos(i) if use_pos else str(c['value']):>3}. "
                f"{_JCS_LABEL.get(c['value'], c['label']) if is_jcs else c['label']}"
                for i, c in enumerate(main_c, 1)
            )
            parts.append(f"【{name}】{multi}\n{opts}{unk_line}")

        elif f.get("choices_common"):
            examples = f["choices_common"]
            main_ex  = [ex for ex in examples if ex != "不明"]
            has_unk  = "不明" in examples or f.get("unknown_allowed")
            unk_line = "\n  u. 不明" if has_unk else ""
            opts = "\n".join(f"  {_pos(i)}. {ex}" for i, ex in enumerate(main_ex[:10], 1))
            extra = ""
            if len(main_ex) > 10:
                extra = f"\n  ※ その他: {' / '.join(main_ex[10:])} は自由記述"
            free  = "\n  ※ 番号 or 自由記述" + ("（複数: スペース区切り）" if ftype == "multi_select" else "")
            parts.append(f"【{name}】\n{opts}{unk_line}{extra}{free}")

        elif ftype == "number":
            unit = f"（単位: {f['unit']}）" if f.get("unit") else ""
            unk  = "（u=不明）" if f.get("unknown_allowed") else ""
            parts.append(f"【{name}】{unit}{unk} を数値で入力してください")
        elif ftype in ("date", "datetime"):
            fmt = "yyyy/mm/dd hh:mm" if ftype == "datetime" else "yyyy/mm/dd"
            parts.append(f"【{name}】（形式: {fmt}）")
        elif ftype == "time":
            parts.append(f"【{name}】（形式: hh:mm）")
        else:
            parts.append(f"【{name}】")

    return "\n\n".join(parts), [str(f["id"]) for f in batch]


# CLI対話用: 1フィールドを直接入力させる
_DONE = object()  # sentinel: ユーザーが "done" を入力した


def prompt_field(f: dict):
    """
    フィールドをユーザーに直接入力させる。
    戻り値: _DONE | None（スキップ）| (value, raw, unknown_bool)
    選択肢は1桁ポジション番号（1-9、10番目=0）で入力。u=不明。
    """
    name   = f["name"]
    ftype  = f.get("type", "text")
    unk_ok = f.get("unknown_allowed", False)
    is_jcs = f.get("id") in JCS_FIELDS

    if f.get("choices"):
        choices  = f["choices"]
        is_multi = ftype == "multi_select"
        # 99=不明 を番号リストから除外
        main_c  = [c for c in choices if c["value"] != 99]
        has_99  = any(c["value"] == 99 for c in choices)
        has_unk = unk_ok or has_99
        unk_hint = "  u=不明" if has_unk else ""
        # 10件以下 → 1桁ポジション番号、11件以上 → 値番号（繰り返し防止）
        use_pos = len(main_c) <= 10

        print(f"\n【{name}】（空Enter=スキップ{unk_hint}  done=完了）")
        for i, c in enumerate(main_c, 1):
            label = _JCS_LABEL.get(c["value"], c["label"]) if is_jcs else c["label"]
            num   = _pos(i) if use_pos else str(c["value"])
            print(f"  {num:>3}. {label}")
        if is_multi:
            multi_ex = "1 3 5" if use_pos else f"{main_c[0]['value']} {main_c[2]['value']}"
            print(f"  ※ 複数可: スペース区切り（例: {multi_ex}）")

        while True:
            try:
                ans = input("  > ").strip()
            except (KeyboardInterrupt, EOFError):
                raise KeyboardInterrupt
            if not ans:
                return None
            if ans.lower() == "done":
                return _DONE
            if ans.lower() == "u":
                if has_99:
                    return (99, "不明", False)
                if unk_ok:
                    return (None, "不明", True)
            parts = ans.replace(",", " ").split()
            matched = []
            for p in parts:
                if use_pos:
                    if p.isdigit():
                        n   = int(p)
                        idx = (n - 1) if n > 0 else 9
                        if 0 <= idx < len(main_c):
                            matched.append(main_c[idx]["value"])
                else:
                    # 値で直接マッチ
                    for c in main_c:
                        if str(c["value"]) == p:
                            matched.append(c["value"])
                            break
            if not matched:
                hint = "1-9 / 0=10番目" if use_pos else "値番号"
                print(f"  ⚠ 有効な番号を入力してください（{hint} / u=不明）")
                continue
            value = matched if is_multi else matched[0]
            return (value, _label_for_field(f, value), False)

    elif f.get("choices_common"):
        examples = f["choices_common"]
        is_multi = ftype == "multi_select"
        main_ex  = [ex for ex in examples if ex != "不明"]
        has_unk  = "不明" in examples or unk_ok
        unk_hint = "  u=不明" if has_unk else ""

        print(f"\n【{name}】（空Enter=スキップ{unk_hint}  done=完了）")
        for i, ex in enumerate(main_ex[:10], 1):
            print(f"  {_pos(i)}. {ex}")
        if len(main_ex) > 10:
            print(f"  ※ その他: {' / '.join(main_ex[10:])} → 自由記述で入力")
        if is_multi:
            print("  ※ 複数可: スペース区切り（例: 1 3）または自由記述")
        else:
            print("  ※ 番号 or 自由記述")

        while True:
            try:
                ans = input("  > ").strip()
            except (KeyboardInterrupt, EOFError):
                raise KeyboardInterrupt
            if not ans:
                return None
            if ans.lower() == "done":
                return _DONE
            if ans.lower() == "u":
                if has_unk:
                    val = ["不明"] if is_multi else "不明"
                    return (val, "不明", False)
                if unk_ok:
                    return (None, "不明", True)
            parts = ans.replace("、", " ").replace(",", " ").split()
            items = []
            for p in parts:
                p = p.strip()
                if not p:
                    continue
                if p.isdigit():
                    n   = int(p)
                    idx = (n - 1) if n > 0 else 9
                    items.append(main_ex[idx] if 0 <= idx < len(main_ex) else p)
                else:
                    items.append(p)
            if not items:
                items = [ans]
            value = items if is_multi else items[0]
            return (value, ans, False)

    else:
        unit  = f.get("unit", "")
        fmt   = {"date": "yyyy/mm/dd", "time": "hh:mm",
                 "datetime": "yyyy/mm/dd hh:mm"}.get(ftype, "")
        extra = (f"（単位: {unit}）" if unit else "") + (f"（形式: {fmt}）" if fmt else "")
        unk_h = "  u=不明" if unk_ok else ""
        print(f"\n【{name}】{extra}（空Enter=スキップ{unk_h}  done=完了）")
        try:
            ans = input("  > ").strip()
        except (KeyboardInterrupt, EOFError):
            raise KeyboardInterrupt
        if not ans:
            return None
        if ans.lower() == "done":
            return _DONE
        if ans.lower() == "u" and unk_ok:
            return (None, "不明", True)
        if ftype == "number":
            try:
                v = float(ans) if "." in ans else int(ans)
                return (v, ans, False)
            except ValueError:
                print("  ⚠ 数値を入力してください")
                return None
        return (ans, ans, False)


INJURY_QUESTION = """\
損傷部位ごとに傷病名を教えてください。
該当しない部位は「なし」と入力してください。

  ①頭頸部（頭部・頸部）
  ②顔面
  ③胸部
  ④腹部
  ⑤四肢・骨盤
  ⑥体表（皮膚・熱傷等）

例: 「①右硬膜外血腫 ③左肋骨骨折3本・血胸 ⑤左大腿骨骨折」"""


def extract_injuries_from_text(text: str, client) -> dict:
    from google.genai import types as gtypes

    prompt = f"""以下のテキストから損傷記述を部位別に分類してください。

テキスト: {text}

JSON形式で返してください：
{{
  "70": ["<頭部・頸部の損傷記述>"],
  "71": ["<顔面の損傷記述>"],
  "72": ["<胸部の損傷記述>"],
  "73": ["<腹部の損傷記述>"],
  "74": ["<四肢・骨盤の損傷記述>"],
  "75": ["<体表の損傷記述>"]
}}
該当なし部位は空配列。JSONのみ返すこと。"""

    resp = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=prompt,
        config=gtypes.GenerateContentConfig(
            system_instruction="損傷記述を部位別に分類するAIです。JSONのみ返してください。"
        ),
    )
    raw = resp.text.strip()
    if "```json" in raw:
        raw = raw.split("```json")[1].split("```")[0].strip()
    elif "```" in raw:
        raw = raw.split("```")[1].split("```")[0].strip()
    return json.loads(raw)


# ─── スコア計算 ──────────────────────────────────────────────────────────────

def _rts_code_gcs(gcs):
    if gcs is None: return None
    if gcs >= 13: return 4
    if gcs >= 9:  return 3
    if gcs >= 6:  return 2
    if gcs >= 4:  return 1
    return 0

def _rts_code_sbp(sbp):
    if sbp is None: return None
    if sbp > 89:  return 4
    if sbp >= 76: return 3
    if sbp >= 50: return 2
    if sbp >= 1:  return 1
    return 0

def _rts_code_rr(rr):
    if rr is None: return None
    if 10 <= rr <= 29: return 4
    if rr > 29:        return 3
    if 6 <= rr <= 9:   return 2
    if 1 <= rr <= 5:   return 1
    return 0

def calculate_scores(session: dict) -> dict:
    fields = session["fields"]

    def val(fid):
        d = fields.get(str(fid), {})
        if d.get("unknown"):
            return None
        return d.get("value")

    # GCS
    e, v, m = val(45), val(46), val(47)
    gcs = None
    if isinstance(e, int) and isinstance(v, int) and isinstance(m, int):
        gcs = e + v + m

    # RTS
    rts = None
    sbp = val(38)
    rr  = val(40)
    gc  = _rts_code_gcs(gcs)
    sc  = _rts_code_sbp(sbp)
    rc  = _rts_code_rr(rr)
    if gc is not None and sc is not None and rc is not None:
        rts = round(0.9368 * gc + 0.7326 * sc + 0.2908 * rc, 4)

    # ISS / TRISS は Phase 3（AISコード確定後）
    return {
        "gcs": gcs,
        "rts": rts,
        "iss": None,
        "triss_ps": None,
        "ais_coding_status": "pending",
    }


# ─── 出力生成 ────────────────────────────────────────────────────────────────

def _label_for_field(f: dict, value) -> str:
    if value is None:
        return ""
    if f.get("id") in JCS_FIELDS:
        if isinstance(value, list):
            return " / ".join(_JCS_LABEL.get(v, str(v)) for v in value)
        return _JCS_LABEL.get(value, str(value))
    if f.get("choices"):
        if isinstance(value, list):
            return " / ".join(
                next((c["label"] for c in f["choices"] if c["value"] == v), str(v))
                for v in value
            )
        return next((c["label"] for c in f["choices"] if c["value"] == value), str(value))
    if isinstance(value, list):
        return " / ".join(str(v) for v in value)
    return str(value)


def _section_name(schema: dict, section_id: str) -> str:
    for s in schema["sections"]:
        if s["id"] == section_id:
            return f"{section_id}. {s['name']}"
    return section_id


def generate_output(session: dict, schema: dict) -> tuple[dict, str]:
    scores = calculate_scores(session)
    now = datetime.now().isoformat()
    sid = session["session_id"]

    # ── JSON ──
    fields_by_id = {str(f["id"]): f for f in schema["fields"]}
    patient_fields = {}
    for fid, fdata in session["fields"].items():
        f = fields_by_id.get(fid, {})
        patient_fields[fid] = {
            "label": f.get("name", fid),
            "section": f.get("section", ""),
            "value": fdata["value"],
            "display": _label_for_field(f, fdata["value"]),
            "raw": fdata.get("raw", ""),
            "unknown": fdata.get("unknown", False),
        }

    output_json = {
        "meta": {
            "jtdb_schema_version": "1.0",
            "session_id": sid,
            "created_at": session["created_at"],
            "completed_at": now,
            "ais_coding_status": "pending",
        },
        "patient": patient_fields,
        "injuries": session["injuries"],
        "scores": scores,
    }

    # ── Markdown ──
    sections_order = ["I", "II", "III", "IV", "V", "VI", "VII"]
    fields_by_section: dict[str, list] = {s: [] for s in sections_order}
    for fid, fdata in sorted(session["fields"].items(), key=lambda x: int(x[0])):
        f = fields_by_id.get(fid)
        if not f:
            continue
        sec = f.get("section", "")
        if sec in fields_by_section:
            display = _label_for_field(f, fdata["value"])
            if fdata.get("unknown"):
                display = "不明"
            fields_by_section[sec].append((f["name"], display))

    lines = [
        f"# JTDB患者記録",
        f"",
        f"**セッションID:** {sid}  ",
        f"**記録日時:** {now[:16].replace('T', ' ')}  ",
        f"**AISコーディング:** 未完了（Phase 3）",
        f"",
    ]

    for sec_id in sections_order:
        items = fields_by_section.get(sec_id, [])
        if not items:
            continue
        sec_name = _section_name(schema, sec_id)
        lines.append(f"## {sec_name}")
        lines.append("")
        for name, display in items:
            lines.append(f"- **{name}:** {display}")
        lines.append("")

    # 損傷セクション
    injury_labels = {
        "70": "頭頸部", "71": "顔面", "72": "胸部",
        "73": "腹部", "74": "四肢・骨盤", "75": "体表",
    }
    any_injury = any(v for v in session["injuries"].values())
    if any_injury:
        lines += ["## VI. 損傷記録（AISコーディング待ち）", ""]
        for fid, descs in session["injuries"].items():
            label = injury_labels.get(fid, fid)
            if descs:
                lines.append(f"- **{label}:** {' / '.join(descs)}")
            else:
                lines.append(f"- **{label}:** 該当なし")
        lines.append("")

    # スコアセクション
    lines += [
        "## スコア",
        "",
        f"- **GCS:** {scores['gcs'] if scores['gcs'] is not None else '計算不可'}",
        f"- **RTS:** {scores['rts'] if scores['rts'] is not None else '計算不可'}",
        f"- **ISS:** 未計算（AISコーディング後に算出）",
        f"- **TRISS Ps:** 未計算（AISコーディング後に算出）",
        "",
    ]

    output_md = "\n".join(lines)
    return output_json, output_md


def save_output(session: dict, schema: dict) -> Path:
    PATIENTS_DIR.mkdir(parents=True, exist_ok=True)
    date_str = datetime.now().strftime("%Y%m%d")
    sid = session["session_id"]
    base = PATIENTS_DIR / f"{date_str}_{sid}"

    output_json, output_md = generate_output(session, schema)
    (base.with_suffix(".json")).write_text(
        json.dumps(output_json, ensure_ascii=False, indent=2)
    )
    base.with_suffix(".md").write_text(output_md, encoding="utf-8")

    session["status"] = "completed"
    session["output_path"] = str(base)
    save_session(session)

    # セッションファイルを削除（完了済み）
    sess_path = SESSIONS_DIR / f"{sid}.json"
    if sess_path.exists():
        sess_path.unlink()

    return base


# ─── 対話モード ──────────────────────────────────────────────────────────────

def merge_extracted(session: dict, extracted: dict):
    for fid, fdata in extracted.get("fields", {}).items():
        if fid not in session["fields"]:
            session["fields"][fid] = fdata
    for fid, descs in extracted.get("injuries", {}).items():
        existing = session["injuries"].get(fid, [])
        for d in descs:
            if d and d not in existing:
                existing.append(d)
        session["injuries"][fid] = existing


def run_interactive(session: dict, schema: dict, client):
    required_fields = get_required_fields(schema)
    ais_fields = get_ais_fields(schema)
    injuries_collected = any(v for v in session["injuries"].values())

    print()
    print("=" * 60)
    print("JTDB 患者情報収集システム")
    print("（終了: Ctrl+C | スキップ: 空Enter | 確定: 'done'）")
    print("=" * 60)

    # ── ステップ1: 初期自由記述 ──
    if not session["fields"]:
        print("\n患者の状況を自由に入力してください。")
        print("例: 「65歳男性、車対車交通事故、2026/06/21 14:30受傷、")
        print("     救急車搬送、病院着15:00、GCS12、BP100、HR90、RR18、")
        print("     右硬膜外血腫、左肋骨骨折」")
        print()
        try:
            text = input("患者情報 > ").strip()
        except (KeyboardInterrupt, EOFError):
            print("\n中断しました")
            return

        if text:
            print("  → 情報を解析中...")
            try:
                extracted = extract_from_text(text, required_fields, ais_fields, session, client)
                merge_extracted(session, extracted)
                injuries_collected = any(v for v in session["injuries"].values())
                n = len(session["fields"])
                print(f"  → {n}項目を抽出しました")
            except Exception as e:
                print(f"  → 解析エラー: {e}")
            save_session(session)
        print()

    # ── ステップ2: 損傷収集（未収集の場合）──
    if not injuries_collected:
        print(INJURY_QUESTION)
        print()
        try:
            text = input("損傷記述 > ").strip()
        except (KeyboardInterrupt, EOFError):
            print("\n中断しました")
            return

        if text:
            try:
                inj = extract_injuries_from_text(text, client)
                for fid, descs in inj.items():
                    existing = session["injuries"].get(fid, [])
                    for d in descs:
                        if d and d not in existing:
                            existing.append(d)
                    session["injuries"][fid] = existing
            except Exception as e:
                print(f"  → 解析エラー: {e}")
            save_session(session)
        print()

    # ── ステップ3: 不足項目を順次補完（直接選択入力）──
    while True:
        missing = find_missing(session, required_fields)
        if not missing:
            break

        total  = len(required_fields)
        filled = total - len(missing)
        print(f"\n進捗: {filled}/{total} 項目収集済み（残り {len(missing)} 項目）")

        done_early        = False
        any_filled        = False
        skipped_non_unk   = 0

        for f in list(missing):
            fid = str(f["id"])
            # 前の回答で埋まった場合はスキップ
            if fid in session["fields"]:
                continue
            # 条件付きスキップ再チェック
            skip_fn = CONDITIONAL_SKIP.get(fid)
            if skip_fn and skip_fn(session):
                continue

            try:
                result = prompt_field(f)
            except KeyboardInterrupt:
                print("\n中断しました（セッション保存済み）")
                save_session(session)
                return

            if result is _DONE:
                done_early = True
                break
            if result is None:
                if f.get("unknown_allowed"):
                    session["fields"][fid] = {"value": None, "raw": "不明", "unknown": True}
                    save_session(session)
                    any_filled = True
                else:
                    skipped_non_unk += 1
                continue

            value, raw, unknown = result
            session["fields"][fid] = {"value": value, "raw": raw, "unknown": unknown}
            save_session(session)
            any_filled = True

        if done_early:
            break
        # 全フィールドがスキップされ続けると無限ループになるため脱出
        if not any_filled and skipped_non_unk > 0:
            print(f"\n⚠ {skipped_non_unk}項目がスキップされました（後で --resume で再開できます）")
            break

    # ── ステップ4: 確認・出力 ──
    missing = find_missing(session, required_fields)
    if missing:
        print(f"\n⚠ 未収集の必須項目が {len(missing)} 件あります:")
        for f in missing[:10]:
            print(f"  - {f['name']} (#{f['id']})")
        print()
        try:
            ans = input("このまま出力しますか？ (y/N) > ").strip().lower()
        except (KeyboardInterrupt, EOFError):
            ans = "n"
        if ans != "y":
            print("キャンセルしました。セッションは保存されています。")
            save_session(session)
            return

    print("\n出力を生成中...")
    base = save_output(session, schema)
    scores = calculate_scores(session)

    print()
    print("=" * 60)
    print("✓ 患者記録を出力しました（Phase 2完了）")
    print(f"  JSON: {base}.json")
    print(f"  MD:   {base}.md")
    print(f"  GCS:  {scores['gcs']}")
    print(f"  RTS:  {scores['rts']}")
    print("=" * 60)

    # ── Phase 3: AISコーディング ──
    print()
    try:
        ans = input("続けてAISコーディング（Phase 3）を行いますか？ (Y/n) > ").strip().lower()
    except (KeyboardInterrupt, EOFError):
        ans = "n"

    if ans != "n":
        from jtdb_ais_coder import run_ais_coding, save_updated_patient
        patient_path = base.with_suffix(".json")
        patient_data = json.loads(patient_path.read_text(encoding="utf-8"))
        try:
            patient_data = run_ais_coding(patient_data, client)
            save_updated_patient(patient_path, patient_data)
        except KeyboardInterrupt:
            print("\nAISコーディングを中断しました。")
            print(f"後で再実行: uv run jtdb_ais_coder.py {patient_path}")
    else:
        print(f"後でAISコーディングを実行: uv run jtdb_ais_coder.py {base}.json")


# ─── JSON モード（NanoClaw用）────────────────────────────────────────────────

def run_json_mode(schema: dict, client):
    """
    stdin から JSON を読み、stdout に JSON を返す。

    Input:
      {"action": "start"}
      {"action": "input", "session_id": "...", "text": "..."}
      {"action": "confirm", "session_id": "..."}

    Output:
      {"status": "asking", "session_id": "...", "message": "...", "progress": {...}}
      {"status": "done", "session_id": "...", "message": "...", "output_path": "..."}
      {"status": "error", "message": "..."}
    """
    def out(obj):
        print(json.dumps(obj, ensure_ascii=False), flush=True)

    try:
        req = json.loads(sys.stdin.readline())
    except Exception as e:
        out({"status": "error", "message": str(e)})
        return

    action = req.get("action")
    required_fields = get_required_fields(schema)
    ais_fields = get_ais_fields(schema)

    if action == "start":
        session = new_session()
        save_session(session)
        out({
            "status": "asking",
            "session_id": session["session_id"],
            "message": "患者情報を自由に入力してください。\n例: 「65歳男性、交通事故、BP100、GCS12、右硬膜外血腫」",
            "progress": {"filled": 0, "total": len(required_fields)},
        })
        return

    session_id = req.get("session_id")
    session = load_session(session_id) if session_id else None
    if not session:
        out({"status": "error", "message": f"セッション {session_id} が見つかりません"})
        return

    if action == "input":
        text = req.get("text", "").strip()
        if not text:
            out({"status": "error", "message": "テキストが空です"})
            return

        try:
            extracted = extract_from_text(text, required_fields, ais_fields, session, client)
            merge_extracted(session, extracted)
            save_session(session)
        except Exception as e:
            out({"status": "error", "message": str(e)})
            return

        missing = find_missing(session, required_fields)
        total = len(required_fields)

        # 損傷未収集なら損傷を聞く
        if not any(v for v in session["injuries"].values()):
            out({
                "status": "asking",
                "session_id": session_id,
                "message": INJURY_QUESTION,
                "progress": {"filled": total - len(missing), "total": total},
            })
            return

        if not missing:
            # 全項目収集完了 → 確認を促す
            out({
                "status": "confirming",
                "session_id": session_id,
                "message": f"全{total}項目の収集が完了しました。出力しますか？ (action: confirm を送信)",
                "progress": {"filled": total, "total": total},
            })
            return

        question, _ = format_question(missing, batch_size=1)

        out({
            "status": "asking",
            "session_id": session_id,
            "message": question,
            "progress": {"filled": total - len(missing), "total": total},
        })

    elif action == "confirm":
        base = save_output(session, schema)
        scores = calculate_scores(session)
        msg = (
            f"患者記録を保存しました（Phase 2完了）。\n"
            f"GCS: {scores['gcs']} / RTS: {scores['rts']}\n"
            f"AISコーディングを行うには action: ais_auto を送信してください。"
        )
        out({
            "status": "phase2_done",
            "session_id": session_id,
            "message": msg,
            "output_path": str(base),
            "scores": scores,
        })

    elif action == "ais_auto":
        # NanoClaw用: Gemini最上位候補を自動採用してISS/TRISSを計算
        matches = sorted(PATIENTS_DIR.glob(f"*_{session_id}.json"))
        if not matches:
            out({"status": "error", "message": f"患者ファイルが見つかりません: {session_id}"})
            return
        patient_path = matches[-1]
        patient_data = json.loads(patient_path.read_text(encoding="utf-8"))

        try:
            from jtdb_ais_coder import (
                load_codebook, suggest_ais_codes, calculate_iss,
                calculate_rts, calculate_triss, save_updated_patient,
                JTDB_TO_BODY_PARTS,
            )
            codebook = load_codebook()
            injuries = patient_data.get("injuries", {})
            ais_results: dict = {}
            max_ais: dict = {}

            for fid, body_parts in JTDB_TO_BODY_PARTS.items():
                descs = [d for d in injuries.get(fid, []) if d]
                if not descs:
                    max_ais[fid] = 0
                    ais_results[fid] = []
                    continue
                coded = []
                for desc in descs:
                    candidates = suggest_ais_codes(desc, body_parts, codebook, client)
                    if candidates:
                        top = candidates[0]
                        coded.append({
                            "description": desc,
                            "code": top.get("code"),
                            "japanese": top.get("japanese", ""),
                            "description_ja": (top.get("description_ja") or "").strip(),
                            "english": top.get("english", ""),
                            "severity": top.get("severity", 0),
                            "confidence": top.get("confidence", ""),
                        })
                ais_results[fid] = coded
                max_ais[fid] = max((e["severity"] for e in coded if e.get("severity")), default=0)

            fields = patient_data.get("patient", {})
            def fval(k):
                e = fields.get(str(k), {})
                return None if e.get("unknown") else e.get("value")

            gcs_e, gcs_v, gcs_m = fval(45), fval(46), fval(47)
            gcs = (gcs_e + gcs_v + gcs_m
                   if all(isinstance(x, int) for x in [gcs_e, gcs_v, gcs_m]) else None)
            rts = calculate_rts(gcs, fval(38), fval(40))
            iss = calculate_iss(max_ais)
            triss_ps = calculate_triss(rts, iss, fval(2), fval(8) or 1)

            patient_data["ais_codes"] = ais_results
            patient_data["scores"] = {
                "gcs": gcs, "rts": rts, "iss": iss, "triss_ps": triss_ps,
                "ais_coding_status": "complete",
                "max_ais_by_region": max_ais,
            }
            patient_data["meta"]["ais_coding_status"] = "complete"
            save_updated_patient(patient_path, patient_data)

            msg = (
                f"AISコーディング完了。\n"
                f"GCS:{gcs} / RTS:{rts} / ISS:{iss} / "
                f"TRISS Ps:{f'{round(triss_ps*100,1)}%' if triss_ps else 'N/A'}"
            )
            out({
                "status": "done",
                "session_id": session_id,
                "message": msg,
                "output_path": str(patient_path),
                "scores": patient_data["scores"],
            })
        except Exception as e:
            out({"status": "error", "message": str(e)})

    else:
        out({"status": "error", "message": f"未知のアクション: {action}"})


# ─── エントリポイント ─────────────────────────────────────────────────────────

def main():
    from google import genai

    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        print("ERROR: GEMINI_API_KEY が .env に設定されていません")
        sys.exit(1)

    client = genai.Client(api_key=api_key)
    schema = load_schema()

    args = sys.argv[1:]

    # JSON モード（NanoClaw用）
    if "--json" in args:
        run_json_mode(schema, client)
        return

    # 既存セッション再開
    session = None
    if "--resume" in args:
        idx = args.index("--resume")
        if idx + 1 < len(args):
            session_id = args[idx + 1]
            session = load_session(session_id)
            if not session:
                print(f"ERROR: セッション {session_id} が見つかりません")
                sys.exit(1)
            print(f"セッション {session_id} を再開します")

    if not session:
        session = new_session()
        print(f"新規セッション: {session['session_id']}")

    try:
        run_interactive(session, schema, client)
    except KeyboardInterrupt:
        print("\n\n中断しました。セッションは保存されています。")
        print(f"再開: uv run jtdb_collector.py --resume {session['session_id']}")


if __name__ == "__main__":
    main()
