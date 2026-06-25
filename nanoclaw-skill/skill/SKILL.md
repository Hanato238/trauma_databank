---
name: jtdb-registration
description: 日本外傷データバンク（JTDB）患者情報登録スキル
version: 2.0.0
triggers:
  - 患者登録
  - JTDB
  - 外傷登録
  - trauma registry
  - 患者情報登録
---

# JTDBスキル — 日本外傷データバンク患者情報登録

## 概要

外傷患者の情報を対話形式で収集し、以下を自動処理する：
- JTDB必須項目の収集（自由記述→AI補完＋番号選択入力）
- AISコード自動照会（2623件コードブック）
- ISS・RTS・TRISS Ps 自動計算
- JTDB提出形式 JSON/MD 出力

## ツール

`jtdb_register` ツールを使って全処理を行う。

## 対話手順

### Phase 2: 患者情報収集

1. ユーザーが「患者登録」等を要求 → `jtdb_register(action="start")` を呼ぶ
2. 返却された `message` をそのままユーザーに送る
3. ユーザーの入力を `jtdb_register(action="input", session_id=..., text=...)` に渡す
4. `status` に応じて：
   - `"asking"` → `message` をそのまま送り次の入力を待つ
   - `"confirming"` → `message` を送り `jtdb_register(action="confirm", session_id=...)` を呼ぶ

### 質問フォーマットについて

`message` には番号付き選択肢が含まれる場合がある。ユーザーは番号で回答できる。

例（性別の質問）:
```
【性別】（u=不明）
    1. 男
    2. 女
```
→ ユーザーが「1」と返答 → 「男」として記録

例（合併症・複数選択）:
```
【合併症】（複数可: スペース区切り）
   1. ARDS（急性呼吸窮迫症候群）
   2. 敗血症
   3. DIC（播種性血管内凝固）
   ...
   ※ 番号 or 自由記述
```
→ ユーザーが「1 2」と返答 → 「ARDS / 敗血症」として記録

### Phase 3: AISコーディング

5. `status` が `"phase2_done"` になったら：
   - 「基本情報の収集が完了しました。AISコーディングと重症度計算を行います（しばらくお待ちください）」と送る
   - `jtdb_register(action="ais_auto", session_id=...)` を呼ぶ（ユーザー確認不要）

### 完了

6. `status` が `"done"` になったら：
   - `scores` の値を日本語で伝える：
     ```
     登録完了しました。
     GCS: {gcs} / RTS: {rts}
     ISS: {iss} / TRISS Ps: {triss_ps × 100}%（生存確率）
     ファイル: {output_path}
     ```

## 注意事項

- `session_id` は必ず保持して次の呼び出しに引き継ぐ
- ユーザーの入力（番号を含む）をそのまま `text` に渡す（AI が変換・補完しない）
- Markdown は使わずシンプルなテキストで返す
- AISコーディング（Phase 3）は Gemini API を使用するため 30〜60 秒かかる場合がある
- エラー時は日本語でわかりやすく伝える

## リセット

ユーザーが「リセット」「やり直し」と入力した場合：
- 現在のセッションを破棄して新しいセッションを開始する

## 環境変数

| 変数 | 説明 | デフォルト |
|------|------|----------|
| `JTDB_WORKSPACE` | JTDB Pythonスクリプトのディレクトリ | `/workspace` |
| `JTDB_UV_CMD` | `uv` コマンドのパス | `uv` |
| `ANTHROPIC_API_KEY` | Claude API キー | （必須） |
| `TELEGRAM_BOT_TOKEN` | Telegram Bot Token | （Telegram使用時） |
| `GEMINI_API_KEY` | Gemini API キー（AIS照会用） | （必須・JTDB側.envに設定） |
