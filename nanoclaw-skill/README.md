# NanoClaw スキル: JTDB 患者情報登録

日本外傷データバンク（JTDB）への患者情報登録を LINE/Telegram から行うための NanoClaw スキル。

## アーキテクチャ

```
Telegram/LINE
     │ メッセージ
     ▼
NanoClaw (TypeScript)
  Claude Agent SDK
     │ tool_use: jtdb_register
     ▼
jtdb_collector.py --json   ← Phase 2: 患者情報収集（Gemini）
     │
     ▼
jtdb_ais_coder.py          ← Phase 3: AISコード付与・ISS/TRISS計算（Gemini）
     │
     ▼
output/patients/YYYYMMDD_<id>.{json,md}
```

## ファイル構成

```
nanoclaw-skill/
├── src/
│   ├── jtdb-tool.ts          # ツール定義 + Python subprocess 呼び出し
│   ├── jtdb-agent.ts         # Claude エージェントループ
│   └── telegram-adapter.ts   # Telegram ボット（スタンドアロン）
├── skill/
│   └── SKILL.md              # NanoClaw スキル定義（.claude/skills/jtdb/ に配置）
├── package.json
└── tsconfig.json
```

## インストール方法

### A. スタンドアロン Telegram ボットとして起動

```bash
cd /workspace/nanoclaw-skill
npm install
cp .env.example .env   # 環境変数を設定

# 開発モード
npm run dev

# 本番
npm run build && npm start
```

### B. 既存 NanoClaw に組み込む（スキルとして適用）

1. `src/jtdb-tool.ts` を NanoClaw の `src/tools/jtdb.ts` にコピー
2. `src/jtdb-agent.ts` の `jtdbTool` と `handleJtdbToolUse` を NanoClaw のツールリストに追加
3. `skill/SKILL.md` を `.claude/skills/jtdb/SKILL.md` にコピー

```typescript
// NanoClaw の src/index.ts または src/tools/index.ts に追加
import { jtdbTool, handleJtdbToolUse } from "./tools/jtdb";

// tools 配列に追加
const tools = [
  // ... 既存のツール ...
  jtdbTool,
];

// tool_use ハンドラーに追加
if (toolUse.name === "jtdb_register") {
  result = await handleJtdbToolUse(toolUse.input);
}
```

## 環境変数（.env）

```env
# Claude API（エージェント推論用）
ANTHROPIC_API_KEY=sk-ant-...

# Telegram（Telegram ボット使用時）
TELEGRAM_BOT_TOKEN=123456789:ABC...

# JTDB Python スクリプトの場所
JTDB_WORKSPACE=/workspace

# uv コマンドのパス（フルパスが必要な場合）
JTDB_UV_CMD=uv
```

## 必要なもの

| 依存 | 用途 |
|------|------|
| Node.js ≥ 20 | TypeScript ランタイム |
| uv + Python 3.11+ | JTDB Python スクリプト実行 |
| GEMINI_API_KEY | AIS照会・患者情報抽出（`/workspace/.env` に設定） |
| ANTHROPIC_API_KEY | Claude エージェント推論 |

## 使い方（Telegram）

ボットに以下のいずれかを送信して開始：
- 「患者登録」
- 「JTDB登録」
- 「外傷登録」

その後は AI の質問に答えるだけで JTDB 提出データが自動生成されます。

## コマンド

| コマンド | 説明 |
|---------|------|
| `/reset` | 現在のセッションをリセット |
| `/status` | 現在のセッション状態を確認 |

## 出力

登録完了時、以下のファイルが `/workspace/output/patients/` に生成されます：

- `YYYYMMDD_<session_id>.json` — JTDB提出用データ（機械可読）
- `YYYYMMDD_<session_id>.md` — 患者記録（人間可読）

スコア例：
```
GCS: 12 / RTS: 6.904
ISS: 27 / TRISS Ps: 79.6%（生存確率）
```
