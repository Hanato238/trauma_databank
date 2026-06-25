# AIS 2005 Update 2008 — コードブック解析・JTDB入力支援システム

## 概要

AIS 2005 Update 2008（Abbreviated Injury Scale）コードブックの画像から OCR・整備・ツリー構築を経て、
日本外傷データバンク（JTDB）患者データへの AIS コード付与・ISS/TRISS 計算を行うシステム。

---

## 達成されたこと

### コードブック v2 の構築

OCR 結果の精査・フィールド整備を経て、`output/codebook_v2/json/` を **権威あるコードブックソース** として新設した。

| 項目 | 内容 |
|------|------|
| 対象部位 | head / face / neck / thorax / abdomen / spine / extremity / other（8部位） |
| 総エントリ数 | 1,999件 |
| データソース | AIS 2005 Update 2008 コードブック撮影画像（英語・日本語ページ） |
| 旧ソース (`output/codebook/`) | 構造的欠陥があり廃止 → `archive/` へ移動済み |

### コードブック v2 のエントリ構造

```json
{
  "code":            "140602.3",
  "title_ja":        "硬膜外血腫",
  "title_en":        "Epidural hematoma",
  "ais_severity":    3,
  "hierarchy_level": 2,
  "section":         "HEAD (cranium and brain)",
  "iss_body_region": "head_neck",
  "injury_types":    ["hematoma"],
  "description_ja":  "頭蓋内損傷 > 硬膜外血腫",
  "description_en":  "Intracranial Injury NFS > Epidural hematoma",
  "children": [ ... ]
}
```

| フィールド | 説明 |
|------------|------|
| `title_ja` / `title_en` | コード単体の名称 |
| `description_ja` / `description_en` | 最上位祖先から自身までの `" > "` 結合パス |
| `ais_severity` | AIS 重症度スコア（1〜6、NFS=9） |
| `hierarchy_level` | 階層深度（1=最上位） |
| `iss_body_region` | ISS 身体区分（head_neck / face / thorax / abdomen / extremity / external） |
| `section` | コードブック章見出し |
| `injury_types` | 損傷形態タグ（fracture / hematoma / laceration など） |
| `children` | 下位エントリのネスト配列 |

---

## アクティブなスクリプト

```
scripts/
  convert_renamed.py    ← [1] OCR パイプライン（Gemini 2.5 Flash）
  merge_results.py      ← [2] OCR 結果統合
  build_codebook_v2.py  ← [3] コードブック v2 生成
  jtdb_collector.py     ← [4] 患者情報収集
  jtdb_ais_coder.py     ← [5] AIS コード付与・ISS/TRISS 計算
```

---

## 前提条件

`.env` ファイルに Gemini API キーを設定:

```
GEMINI_API_KEY=your_api_key_here
```

依存パッケージのインストール:

```bash
uv sync
```

---

## スクリプト詳細

### 1. `convert_renamed.py` — コードブック画像 → JSON（OCR）

AIS コードブック撮影画像を Gemini 2.5 Flash で解析し、部位別・ページ別 JSON を生成する。

```bash
uv run scripts/convert_renamed.py
```

- 入力: `raw_images/{部位}/{ページ}.jpg`
- 出力: `output/results_renamed/{部位}/{ページ}.json`
- 処理済みページはスキップされるため途中中断後に再実行可能

---

### 2. `merge_results.py` — OCR 結果統合

各部位ディレクトリ内の全ページ JSON を AIS コードをキーとして統合する。
同一コードで内容が衝突する場合は日本語ページを優先し `collision` フラグを付与する。

```bash
uv run scripts/merge_results.py
```

- 入力: `output/results_renamed/{部位}/*.json`
- 出力: `output/merged/{01_head.json … 08_other.json}`

---

### 3. `build_codebook_v2.py` — コードブック v2 生成（3ステージ）

`output/merged/` を入力にコードブック v2 を構築する。

```bash
# 全ステージ通し実行（推奨）
uv run scripts/build_codebook_v2.py --all

# 個別ステージ実行
uv run scripts/build_codebook_v2.py --stage 1
uv run scripts/build_codebook_v2.py --stage 2
uv run scripts/build_codebook_v2.py --stage 3
```

| ステージ | 処理 | 出力先 |
|----------|------|--------|
| Stage 1 | フィールド整備・メタデータ移植 | `output/codebook_v2/stage1/` |
| Stage 2 | `hierarchy_level` → `children` ツリー再構築 | `output/codebook_v2/json/` |
| Stage 3 | `description_ja/en` 祖先パス結合（上書き） | `output/codebook_v2/json/`（上書き） |

---

### 4. `jtdb_collector.py` — JTDB 患者情報収集

JTDB 登録票に沿って患者情報を対話形式で収集する。

```bash
# 新規セッション
uv run scripts/jtdb_collector.py

# セッション再開
uv run scripts/jtdb_collector.py --resume <session_id>

# NanoClaw スキル用 JSON stdin/stdout モード
uv run scripts/jtdb_collector.py --json
```

- セッションデータ: `output/sessions/<session_id>.json`
- 患者データ: `output/patients/<日付>_<session_id>.json`
- AIS 損傷部位フィールド（70〜75）は記述のみ収集し、コーディングは `jtdb_ais_coder` に委ねる

---

### 5. `jtdb_ais_coder.py` — AIS コード付与・ISS/TRISS 計算

患者データの損傷記述から AIS コードを特定し、ISS・TRISS を計算する。
**コードブックソースは `output/codebook_v2/json/` を使用。**

```bash
# エントリポイント経由（推奨）
uv run jtdb-ais-coder

# スクリプト直接実行
uv run scripts/jtdb_ais_coder.py

# 患者 JSON を直接指定
uv run scripts/jtdb_ais_coder.py output/patients/20260621_abc12345.json

# セッション ID で指定
uv run scripts/jtdb_ais_coder.py --session <session_id>
```

**コード検索の仕組み:**

| フェーズ | 処理 |
|----------|------|
| 損傷形態検出 | 入力テキストから fracture / hematoma など injury_type タグを抽出 |
| IDF 重み付きスコアリング | `title_ja`(3.0) / `title_en`(2.0) / `section`(1.0) / `description_ja/en`(0.5) |
| 損傷形態ボーナス | injury_type 一致時にスコア 2 倍 |
| 親スコア継承 | 親エントリのスコアの 30% を子に継承（階層ブースト） |
| Gemini 絞り込み | 上位候補を Gemini 2.5 Flash に渡して最終コードを選択 |

**ISS / TRISS 計算:**

- ISS = ISS 身体区分ごとの最大 AIS スコア上位 3 区分の二乗和
- TRISS = ISS・年齢・受傷機序をもとに生存確率を推定（MTOS 1990 係数）

---

## ディレクトリ構成

```
workspace/
├── .env                          # GEMINI_API_KEY
├── pyproject.toml                # エントリポイント定義（uv run jtdb-ais-coder）
├── raw_images/                   # コードブック原本撮影画像
├── scripts/
│   ├── convert_renamed.py        # [1] OCR パイプライン
│   ├── merge_results.py          # [2] OCR 結果統合
│   ├── build_codebook_v2.py      # [3] コードブック v2 生成
│   ├── jtdb_collector.py         # [4] 患者情報収集
│   └── jtdb_ais_coder.py         # [5] AIS コード付与・ISS/TRISS
├── output/
│   ├── results_renamed/          # [1] OCR 生データ（部位/ページ単位）
│   ├── merged/                   # [2] 統合済みフラット JSON（8部位）
│   ├── codebook_v2/
│   │   ├── stage1/               # [3-S1] フィールド整備済み中間データ
│   │   └── json/                 # [3-S2/3] 権威コードブック（ツリー構造）← メインソース
│   ├── sessions/                 # [4] 患者収集セッション
│   └── patients/                 # [5] 患者データ（AISコード・ISS付き）
├── docs/                         # AIS/ISS コーディングガイド・JTDBスキーマ
└── archive/                      # 旧スクリプト・旧コードブック（参照用）
```

---

## 典型的なワークフロー

### コードブック v2 を再構築する場合

```bash
# OCR（images → results_renamed）
uv run scripts/convert_renamed.py

# 統合（results_renamed → merged）
uv run scripts/merge_results.py

# コードブック v2 生成（merged → codebook_v2/json）
uv run scripts/build_codebook_v2.py --all
```

### 患者データに AIS コードを付与する場合

```bash
# 患者情報収集
uv run scripts/jtdb_collector.py

# AIS コード付与・ISS/TRISS 計算（対話モード）
uv run jtdb-ais-coder

# または患者 JSON を直接指定
uv run jtdb-ais-coder --session <session_id>
```
