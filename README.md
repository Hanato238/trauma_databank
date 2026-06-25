# AIS 2005 Update 2008 — コードブック解析・JTDB入力支援システム

## 前提条件

`.env` ファイルに Gemini API キーを設定してください。

```
GEMINI_API_KEY=your_api_key_here
```

依存パッケージのインストール:

```bash
uv sync
```

---

## スクリプト一覧

### 1. `convert.py` — コードブック画像 → JSON 変換

AIS 2005 コードブックの撮影画像を Gemini 2.5 Flash で解析し、部位別 JSON・Markdown を生成する。

```bash
uv run convert.py
```

**処理フロー:**

| Phase | 内容 | 出力先 |
|-------|------|--------|
| Phase 1 | 各画像を Gemini で解析 → 1 画像 1 JSON | `output/results/IMG_*.json` |
| Phase 2 | 結果 JSON から部位別 Markdown と画像リネーム | `output/markdown/*.md`, `output/renamed/` |

- 処理済み画像はスキップされるため、途中中断後に再実行可能
- 入力画像: `images/IMG_*.jpg`

---

### 2. `fix_hierarchy.py` — AIS コード階層・親説明文 修正

コード規則（suffix `-00` / `-99`）に基づき、親子関係を自動判定して `hierarchy_level` と `parent_japanese` / `parent_english` を設定する。

```bash
# 全部位を処理
uv run fix_hierarchy.py

# 特定の部位のみ処理
uv run fix_hierarchy.py abdomen

# 変更内容を確認のみ（ファイル非更新）
uv run fix_hierarchy.py --dry-run
uv run fix_hierarchy.py abdomen --dry-run
```

- 入力・出力: `output/data/json/*.json`
- `merge_unknown_to_abdomen.py` 等から `fix_entries()` としてインポートされる内部モジュールでもある

---

### 3. `ais_query.py` — AIS コード照会 CLI

損傷の記述文（日本語・英語可）を入力すると、Gemini が AIS コードブックを参照して最適なコードを返す対話型 CLI。

```bash
uv run ais_query.py
```

**実行例:**

```
損傷を入力してください > 右大腿骨骨幹部骨折
  → 推定部位: extremity_lower, extremity
  → Gemini照会中...

AISコード: `853151.3`
損傷名: 大腿骨骨幹部骨折 / Femur, shaft fracture
AIS重症度: 3
...
```

- `quit` / `exit` / `Ctrl+C` で終了
- コードブックデータは `output/results/*.json` から自動読み込み

---

### 4. `jtdb_collector.py` — JTDB 患者情報収集

日本外傷データバンク（JTDB）登録票に沿って患者情報を収集する対話型 CLI。Gemini による自然言語入力補助付き。

```bash
# 新規セッション（対話モード）
uv run jtdb_collector.py

# セッション再開
uv run jtdb_collector.py --resume <session_id>

# NanoClaw スキル用 JSON stdin/stdout モード
uv run jtdb_collector.py --json
```

- セッションデータ保存先: `output/sessions/<session_id>.json`
- 患者データ保存先: `output/patients/<date>_<session_id>.json`
- AIS 部位フィールド（70〜75）は収集のみ行い、コーディングは `jtdb_ais_coder.py` に委ねる

---

### 5. `jtdb_ais_coder.py` — AIS コード付与・ISS/TRISS 計算

`jtdb_collector.py` で収集した患者データに AIS コードを付与し、ISS および TRISS を計算する。

```bash
# 患者 JSON を直接指定
uv run jtdb_ais_coder.py output/patients/20260621_abc12345.json

# セッション ID で指定
uv run jtdb_ais_coder.py --session <session_id>
```

- JTDB 部位フィールド（70〜75）と AIS コードブック部位のマッピングを内部で管理
- ISS = 各 AIS 大区分の上位 3 件の二乗和
- TRISS = ISS・年齢・受傷機序をもとに生存確率を推定

---

## ディレクトリ構成

```
workspace/
├── .env                      # GEMINI_API_KEY
├── images/                   # 入力画像（IMG_*.jpg）
├── output/
│   ├── data/json/            # 部位別 AIS コードブック JSON（入れ子構造）
│   ├── results/              # convert.py の中間出力（1画像1JSON）
│   ├── renamed/              # 部位別にリネームされた画像
│   ├── sessions/             # jtdb_collector.py セッションデータ
│   ├── patients/             # 患者データ JSON
│   ├── jtdb_schema.json      # JTDB 登録票スキーマ
│   └── jtdb_schema.md        # 同上（Markdown版）
├── convert.py
├── fix_hierarchy.py
├── ais_query.py
├── jtdb_collector.py
└── jtdb_ais_coder.py
```

## 典型的なワークフロー

```
# 1. コードブック画像を解析して JSON 化
uv run convert.py

# 2. 階層・親子関係を修正
uv run fix_hierarchy.py

# 3. 必要に応じて AIS コード照会
uv run ais_query.py

# 4. 患者情報を収集
uv run jtdb_collector.py

# 5. AIS コード・ISS/TRISS を付与
uv run jtdb_ais_coder.py --session <session_id>
```
