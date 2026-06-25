"""
AIS コード階層・親説明文 修正スクリプト（保守的ルール版）

修正方針:
 - hierarchy_level が 1 のエントリのみ階層レベルを変更（lv1 → lv2）
 - 2 以上のエントリはレベルを変更せず、親説明文のみ追加
 - MD ファイルを再生成

親子関係の判定ルール:
 A) [suffix-00 / 厳格 qualifier-0]
      suffix2='00' のコード → NFS 親
      同じ prefix4 かつ d6∉{0,9} → 子
      ※ 別の suffix-00 コードは除外
 B) [suffix-99 / d5=0 限定]
      suffix2='99' のコード → NFS 親
      同じ prefix4 かつ d5=0 かつ d6∉{0,9} → 子
      ※ Rule A で既に親が割り当て済みの場合は上書きしない
"""

import json
from pathlib import Path
from collections import defaultdict

JSON_DIR = Path("/workspace/output/data/json")
MD_DIR   = Path("/workspace/output/data/md")


def get_digits(code: str) -> str:
    return code.split(".")[0]


def build_parent_map(entries: list[dict]) -> dict[str, dict]:
    """
    2 つのルールに従って child_digits → parent_entry マップを作成する。
    """
    code_to_entry: dict[str, dict] = {}
    for e in entries:
        d = get_digits(e["code"])
        if d not in code_to_entry:
            code_to_entry[d] = e

    child_to_parent: dict[str, dict] = {}

    by_prefix4: dict[str, list[str]] = defaultdict(list)
    by_prefix5: dict[str, list[str]] = defaultdict(list)
    for d in code_to_entry:
        if len(d) == 6:
            by_prefix4[d[:4]].append(d)
            by_prefix5[d[:5]].append(d)

    # ---- Rule A: suffix2='00' (d5=0, d6=0) → NFS 親 ----
    # prefix5 スコープ（prefix4 より狭い）で適用し誤検知を減らす
    # 2 件以上の子候補がある場合のみ適用（孤立した 1 件のみの誤マッチを防ぐ）
    # 親コードが既に lv2 以上の場合は適用しない（自身がより上位の親の子であるため）
    for prefix5, group in by_prefix5.items():
        parents_a = [d for d in group if d[4:6] == "00"]
        if not parents_a:
            continue
        parent_d = sorted(parents_a)[0]
        parent_e = code_to_entry[parent_d]
        # 親コード自身が lv2 以上なら NFS 親として使用しない
        if parent_e.get("hierarchy_level", 1) >= 2:
            continue
        children_a = [
            d for d in group
            if d != parent_d
            and d[4:6] != "00"  # 別の suffix-00 は除外
            and d[5] not in {"0", "9"}  # d6=0/9 は別親候補として除外
        ]
        if len(children_a) < 2:  # 子候補が 1 件だけなら偶然のマッチとみなしスキップ
            continue
        for d in children_a:
            child_to_parent[d] = parent_e

    # ---- Rule B: suffix2='99' → NFS 親 / d5=0 の子のみ ----
    for prefix4, group in by_prefix4.items():
        parents_b = [d for d in group if d[4:6] == "99"]
        if not parents_b:
            continue
        parent_d = parents_b[0]
        parent_e = code_to_entry[parent_d]
        for d in group:
            if d == parent_d:
                continue
            if d[4] != "0":        # d5 ≠ '0' は除外
                continue
            if d[5] in {"0", "9"}:
                continue
            if d not in child_to_parent:  # Rule A 割当済みなら上書きしない
                child_to_parent[d] = parent_e

    return child_to_parent


def build_chain(d: str, child_to_parent: dict, field: str) -> str:
    """祖先→親 の順で指定フィールドを ' > ' で連結した文字列を返す"""
    chain: list[str] = []
    current = d
    visited: set[str] = set()
    while current in child_to_parent and current not in visited:
        visited.add(current)
        parent_e = child_to_parent[current]
        val = (parent_e.get(field) or "").strip()
        if val:
            chain.append(val)
        current = get_digits(parent_e["code"])
    chain.reverse()
    return " > ".join(chain)


def fix_entries(entries: list[dict]) -> tuple[list[dict], int, int]:
    """
    エントリリストを受け取り、修正済みリスト・レベル変更件数・親追加件数を返す。

    変更内容:
    - lv1 のエントリのみ → parent_map に合致すれば lv2 に更新
    - 全エントリ → parent_japanese / parent_english を設定（子でなければ削除）
    """
    child_to_parent = build_parent_map(entries)

    updated: list[dict] = []
    level_changes = 0
    parent_adds   = 0

    for e in entries:
        d    = get_digits(e["code"])
        new_e = e.copy()

        if d in child_to_parent:
            chain_ja = build_chain(d, child_to_parent, "japanese")
            chain_en = build_chain(d, child_to_parent, "english")

            if chain_ja:
                new_e["parent_japanese"] = chain_ja
                parent_adds += 1
            elif "parent_japanese" in new_e:
                del new_e["parent_japanese"]

            if chain_en:
                new_e["parent_english"] = chain_en
            elif "parent_english" in new_e:
                del new_e["parent_english"]

            # lv1 のみ lv2 に引き上げ
            if e.get("hierarchy_level", 1) == 1:
                new_e["hierarchy_level"] = 2
                level_changes += 1
        else:
            # 子でない → parent フィールド削除
            new_e.pop("parent_japanese", None)
            new_e.pop("parent_english", None)

        updated.append(new_e)

    return updated, level_changes, parent_adds


def regenerate_md(body_part: str, entries: list[dict]) -> str:
    """JSON エントリから Markdown を再生成する"""
    title_map = {
        "head":      "Head / 頭部",
        "face":      "Face / 顔面",
        "neck":      "Neck / 頸部",
        "thorax":    "Thorax / 胸部",
        "abdomen":   "Abdomen / 腹部",
        "spine":     "Spine / 脊椎",
        "extremity": "Extremity / 四肢",
        "surface":   "Surface / 体表",
        "other":     "Other / その他",
        "unknown":   "Unknown / 不明",
    }
    title  = title_map.get(body_part, body_part.title())
    lines  = [
        f"# {title} — AISコード一覧",
        "",
        "> AIS 2005 Update 2008 日本語対訳版",
        "",
        "---",
        "",
    ]

    current_section = None
    for e in entries:
        section = e.get("section", "")
        if section != current_section:
            current_section = section
            if section:
                lines.append(f"\n### {section}\n")
            lines.append("| コード | 日本語（親 > 自身） | English (parent > self) | AIS重症度 | 階層 |")
            lines.append("|--------|---------------------|------------------------|-----------|------|")

        lv      = e.get("hierarchy_level", 1)
        code    = e.get("code", "")
        sev     = e.get("ais_severity", "")
        lvmark  = "▶" * lv
        indent  = "　" * (lv - 1)

        ja_self   = (e.get("japanese") or "").strip()
        ja_parent = (e.get("parent_japanese") or "").strip()
        ja_full   = f"{ja_parent} > {ja_self}" if (ja_parent and ja_self) else (ja_parent or ja_self)

        en_self   = (e.get("english") or "").strip()
        en_parent = (e.get("parent_english") or "").strip()
        en_full   = f"{en_parent} > {en_self}" if (en_parent and en_self) else (en_parent or en_self)

        lines.append(
            f"| `{code}` | {indent}{ja_full} | {indent}{en_full} | {sev} | {lvmark} |"
        )

    return "\n".join(lines) + "\n"


def process_body_part(json_path: Path, dry_run: bool = False, verbose: bool = True) -> None:
    data    = json.loads(json_path.read_text(encoding="utf-8"))
    bp      = data.get("body_part", json_path.stem)
    entries = data.get("entries", [])

    fixed, level_changes, parent_adds = fix_entries(entries)

    if verbose:
        print(f"\n[{bp}] {len(entries)} entries")
        print(f"  → lv1→lv2 変更: {level_changes} 件")
        print(f"  → 親説明文 追加: {parent_adds} 件")

        # 変更詳細（lv変更のみ）
        for orig, new in zip(entries, fixed):
            if orig.get("hierarchy_level") != new.get("hierarchy_level"):
                ja = (new.get("japanese") or new.get("english") or "")[:45]
                pja = new.get("parent_japanese", "")[:30]
                print(f"     lv1→lv2  {new['code']}  [{pja}] > {ja}")

    if not dry_run:
        data["entries"]       = fixed
        data["total_entries"] = len(fixed)
        json_path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        md_path = MD_DIR / f"{json_path.stem}.md"
        md_path.write_text(regenerate_md(bp, fixed), encoding="utf-8")
        if verbose:
            print(f"  → 保存: {json_path.name}  {md_path.name}")


def main(dry_run: bool = False, target: str | None = None) -> None:
    print("=== AIS 階層修正スクリプト（保守的ルール版）===")
    if dry_run:
        print("（ドライラン: ファイルは変更しません）\n")

    json_files = sorted(JSON_DIR.glob("*.json"))
    if target:
        json_files = [f for f in json_files if f.stem == target]
    if not json_files:
        print("ERROR: JSON ファイルが見つかりません")
        return

    for f in json_files:
        process_body_part(f, dry_run=dry_run)

    print("\n=== 完了 ===")


if __name__ == "__main__":
    import sys
    args  = sys.argv[1:]
    dry   = "--dry-run" in args
    tgt   = next((a for a in args if not a.startswith("-")), None)
    main(dry_run=dry, target=tgt)
