# AIS 2005 Update 2008 — JTDB AISコーディング支援システム

日本外傷データバンク（JTDB）患者データへの AIS コード付与・ISS/TRISS 計算を対話形式で支援するシステム。

---

## 必要条件

```bash
# .env に Gemini API キーを設定（プロジェクトルートまたは scripts/ 直下）
GEMINI_API_KEY=your_api_key_here

# 依存パッケージのインストール
uv sync
```

---

## クイックスタート

```bash
# 患者情報収集（Phase 4）
uv run scripts/jtdb_collector.py

# AIS コード付与・ISS/TRISS 計算（Phase 5）
uv run jtdb-ais-coder
```

---

## スクリプト一覧

```
scripts/
  convert_renamed.py    [1] コードブック画像 OCR（Gemini 2.5 Flash）
  merge_results.py      [2] OCR 結果統合
  build_codebook_v2.py  [3] コードブック v2 生成
  jtdb_collector.py     [4] JTDB 患者情報収集（対話）
  jtdb_ais_coder.py     [5] AIS コード付与・ISS/TRISS 計算（対話）
```

---

## jtdb_collector.py — JTDB 患者情報収集

JTDB 登録票（全 7 セクション・約 90 フィールド）に沿って患者情報を対話形式で収集する。

```bash
uv run scripts/jtdb_collector.py                       # 新規セッション
uv run scripts/jtdb_collector.py --resume <session_id> # セッション再開
uv run scripts/jtdb_collector.py --json                # NanoClaw スキル用（JSON stdin/stdout）
```

| 出力 | パス |
|------|------|
| セッション中間データ | `output/sessions/<session_id>.json` |
| 患者データ（完了時） | `output/patients/<日付>_<session_id>.json` |

AIS 損傷部位フィールド（70〜75）は損傷記述のみ収集し、コーディングは `jtdb_ais_coder.py` に委ねる。

---

## jtdb_ais_coder.py — AIS コード付与・ISS/TRISS 計算

`jtdb_collector.py` が出力した患者 JSON を受け取り、損傷記述から AIS コードを特定・確定して ISS・TRISS を計算する。

```bash
uv run jtdb-ais-coder                                    # 最新の患者ファイルを自動選択
uv run jtdb-ais-coder output/patients/20260621_abc.json  # ファイル直接指定
uv run jtdb-ais-coder --session <session_id>             # セッション ID 指定
uv run jtdb-ais-coder --dummy                            # ダミー患者でデバッグ
uv run jtdb-ais-coder --reset-ais --session <id>         # AIS コーディングをリセットして再実行
```

### JTDB 損傷フィールドと AIS 参照部位

| JTDB フィールド | 損傷部位 | AIS 参照チャプター |
|----------------|---------|------------------|
| 70 | 頭頸部 | head / neck |
| 71 | 顔面 | face |
| 72 | 胸部 | thorax |
| 73 | 腹部 | abdomen |
| 74 | 四肢・骨盤 | extremity / spine |
| 75 | 体表 | surface |

### コーディングフロー（1 損傷記述あたり）

```
1. 詳細分類の事前確認（_ask_clarifying）
   ├─ 子エントリのパターンから臨床質問をローカル生成（LLM 不使用）
   │    昏睡時間 / 片側・両側 / 骨折タイプ / サイズ・量（ml/cm/% 閾値）
   └─ ユーザー回答を損傷記述に追記して Gemini への入力を補完

2. AIS コード候補の生成（Gemini 2.5 Flash）
   └─ 構造化フィルタで絞り込んだコードブック文脈を Gemini に渡す

3. ユーザーによるコード選択
   └─ 候補一覧表示 → 選択 / コード直入力 / スキップ

4. 詳細ドリルダウン（_drill_down）
   └─ 選択コードに子分類がある場合、詳細を対話確認

5. 重症度確認（_confirm_severity）
   └─ AIS 0 / .9 コード / 低確信度の場合にユーザー確認
```

### 構造化フィルタ（LLM 非依存）

Gemini に渡す前に候補エントリを以下の多段階スコアリングで絞り込む。

| ステージ | 処理 |
|---------|------|
| Stage 1 | 損傷形態タグ検出（骨折 / 裂傷 / 血腫 / 穿通など 20 種）→ 一致エントリにスコア 2 倍ボーナス |
| Stage 2 | IDF 重み付きバイグラム・単語マッチ（`title_ja` ×3.0 / `title_en` ×2.0 / `section` ×1.0 / description ×0.5） |
| Stage 3 | 親スコアの 30% を子エントリに継承（階層ブースト） |
| Stage 4 | 臓器名検出（肺 / 大脳 / 小脳 / 脳幹 / 心臓 / 肝 など）→ 対応サブツリー全体に +4.0 ボーナス |

コンテキストには「親分類」列を含め、Gemini が「肺の裂傷」「大脳の挫傷」を正確に識別できるようにする。

### 詳細分類の事前確認（`_ask_clarifying`）

Gemini を使わず、コードブックの子エントリタイトルのパターンマッチで臨床質問を生成する。

| 質問タイプ | トリガー | 選択肢 |
|-----------|---------|--------|
| 昏睡時間 | 子エントリに「6時間を超える昏睡」 | あり（6時間超）/ なし |
| 側性 | 子エントリに「片側」「両側」 | **1. 片側 / 2. 両側**（順序固定） |
| 骨折タイプ | 子エントリに「開放骨折」「粉砕」 | 開放骨折 / 閉鎖骨折 など |
| 麻痺分類 | 子エントリに「完全麻痺」「不完全麻痺」 | 完全麻痺 / 不完全麻痺 |
| 血胸・気胸合併 | 子エントリに「血気胸」「血胸」 | 血気胸 / 血胸のみ / 気胸のみ |
| サイズ・量（動的） | 子エントリに ml / cm / % などの閾値 | 子エントリタイトルをそのまま選択肢に表示 |

サイズ質問の選択肢例（硬膜外血腫）:

```
❓ 損傷の大きさ・程度はどれくらいですか？
   1. AIS2: 微小；厚さ0.6cm未満
   2. AIS4: 小；中程度；50ml以下；厚さ0.6〜1cm
   3. AIS5: 大；50ml超；厚さ1cm超
```

### 脊椎損傷の ISS 部位判定

脊椎コードは AIS 上「spine」チャプターで統一されているが、ISS 計算では椎体レベルによって部位が異なる。コードブックの `section` フィールドを参照して自動判定する。

| コードブック section | ISS 部位 | JTDB field |
|--------------------|---------|------------|
| `CERVICAL SPINE` | 頭部・頸部 | 70 |
| `THORACIC SPINE` | 胸部 | 72 |
| `LUMBAR SPINE` | 腹部・骨盤内臓器 | 73 |

section が不明な場合のみ手動選択を求める。

### スコア計算

| スコア | 算出方法 |
|--------|---------|
| GCS | GCS-E + GCS-V + GCS-M（患者データから取得） |
| RTS | 0.9368×GCS符号 + 0.7326×SBP符号 + 0.2908×RR符号 |
| ISS | 上位 3 ISS 部位の最大 AIS² の和（AIS6 → 75 固定） |
| TRISS Ps | ISS・RTS・年齢・外傷種別から生存確率推定（MTOS 1990 係数） |

TRISS 係数（AIS 90 / MTOS 1990）:

| 外傷種別 | b0 | b1 | b2 | b3 |
|---------|-----|-----|-----|-----|
| 鈍的（Blunt） | −1.2470 | 0.9544 | −0.0768 | −1.9052 |
| 穿通性（Penetrating） | −0.6029 | 1.1430 | −0.1516 | −2.6676 |

b = b0 + b1×RTS + b2×ISS + b3×age_group（55歳以上=1）
Ps = 1 / (1 + e^−b)

### 出力ファイル

AIS コーディング完了時、患者 JSON に以下が追記される。

```json
{
  "ais_codes": {
    "70": [
      {
        "description": "右硬膜外血腫",
        "code": "140630.3",
        "japanese": "硬膜外 詳細不明",
        "description_ja": "コードの解説（1〜2文）",
        "english": "epidural NFS",
        "severity": 3,
        "confidence": "high"
      }
    ]
  },
  "scores": {
    "gcs": 12,
    "rts": 6.8932,
    "iss": 29,
    "triss_ps": 0.8421,
    "max_ais_by_region": { "70": 3, "72": 4, "73": 3, "74": 0, "71": 0, "75": 0 }
  }
}
```

Markdown レポートも同名の `.md` ファイルに出力される。

---

## コードブック v2

**ソース:** `output/codebook_v2/json/`（8 部位、計約 2,000 エントリ）

| 部位 | ファイル |
|------|---------|
| 頭部 | `head.json` |
| 顔面 | `face.json` |
| 頸部 | `neck.json` |
| 胸部 | `thorax.json` |
| 腹部 | `abdomen.json` |
| 脊椎 | `spine.json` |
| 四肢 | `extremity.json` |
| 体表・その他 | `other.json` |

### エントリ構造

```json
{
  "code":            "140602.3",
  "title_ja":        "挫傷 詳細不明",
  "title_en":        "contusion NFS",
  "ais_severity":    3,
  "section":         "頭部（頭蓋と脳）",
  "injury_types":    ["contusion", "edema", "nfs"],
  "description_ja":  "大脳 詳細不明 > 挫傷 詳細不明",
  "description_en":  "Cerebrum NFS > contusion NFS",
  "children": [ ... ]
}
```

`_organ_context` フィールド（実行時に `load_codebook()` が自動付与）:

```json
{ "_organ_context": "大脳" }
```

親エントリの臓器名（肺・大脳・小脳・脳幹・心臓など）を子孫エントリに伝播することで、
「裂傷・裂創 詳細不明」のような汎用タイトルの子エントリがどの臓器に属するかを検索時に参照できる。

### コードブック再構築手順

```bash
# [1] OCR（コードブック撮影画像 → JSON）
uv run scripts/convert_renamed.py

# [2] 統合（ページ別 JSON → 部位別 JSON）
uv run scripts/merge_results.py

# [3] コードブック v2 生成（フィールド整備 → ツリー構築 → パス結合）
uv run scripts/build_codebook_v2.py --all
```

---

## ディレクトリ構成

```
workspace/
├── .env                          # GEMINI_API_KEY
├── pyproject.toml                # エントリポイント定義（jtdb-ais-coder）
├── GEMINI.md                     # Gemini へのシステムプロンプト（AIS コーダー役割定義）
├── scripts/
│   ├── convert_renamed.py        # [1] OCR パイプライン
│   ├── merge_results.py          # [2] OCR 結果統合
│   ├── build_codebook_v2.py      # [3] コードブック v2 生成
│   ├── jtdb_collector.py         # [4] 患者情報収集
│   └── jtdb_ais_coder.py         # [5] AIS コード付与・ISS/TRISS
├── output/
│   ├── results_renamed/          # [1] OCR 生データ（部位/ページ単位）
│   ├── merged/                   # [2] 統合済みフラット JSON（8 部位）
│   ├── codebook_v2/
│   │   ├── stage1/               # [3-S1] フィールド整備済み中間データ
│   │   └── json/                 # 権威コードブック（ツリー構造）← メインソース
│   ├── sessions/                 # [4] 患者収集セッション
│   └── patients/                 # [5] 患者データ（AIS コード・ISS 付き）
├── docs/
│   ├── AIS_ISS_coding_guide.md   # AIS/ISS コーディングガイド
│   ├── jtdb_schema.json          # JTDB フィールド定義 JSON
│   └── jtdb_schema.md            # JTDB フィールド定義 Markdown
└── archive/                      # 旧スクリプト・旧コードブック（参照用）
```

---

## ダミーデータでの動作確認

`--dummy` フラグを使うと患者ファイルなしでコーディングフローを確認できる。

```bash
uv run jtdb-ais-coder --dummy
```

5 種類のテンプレートからランダムに 1 件選択される。

| テンプレート | 主な損傷 |
|-------------|---------|
| 55歳男性・鈍的（交通事故） | 硬膜外血腫 / くも膜下出血 / 肋骨骨折 / 血胸 / 脾損傷 / 大腿骨骨折 |
| 28歳男性・鈍的（歩行者 vs 自動車） | 脳挫傷 / 外傷性くも膜下出血 / 気胸 / 肋骨骨折 / 骨盤骨折 |
| 42歳女性・鈍的（高所転落） | 多発肋骨骨折 / 肺挫傷 / 肝損傷 / 胸椎骨折 / 踵骨骨折 |
| 35歳男性・穿通性（刺創） | 血気胸 / 肺裂傷 / 小腸穿孔 / 腸間膜損傷 / 胸部刺創 |
| 78歳女性・鈍的（自宅転倒） | 急性硬膜下血腫 / 眼窩骨折 / 鼻骨骨折 / 大腿骨頸部骨折 |
