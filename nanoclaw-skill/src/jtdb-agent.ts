/**
 * JTDB エージェントループ
 *
 * NanoClaw の channel（Telegram/LINE 等）から呼ばれる想定。
 * Claude がツールを使って患者情報を収集・登録する完結したエージェントループ。
 *
 * 使い方例（NanoClaw の telegram.ts から）:
 *
 *   import { JtdbSession, runJtdbAgentTurn } from "./jtdb-agent";
 *   const sessions = new Map<string, JtdbSession>();
 *
 *   bot.on("message:text", async (ctx) => {
 *     const key = String(ctx.chat.id);
 *     const session = sessions.get(key) ?? createJtdbSession();
 *     const { reply, session: updated } = await runJtdbAgentTurn(session, ctx.message.text);
 *     sessions.set(key, updated);
 *     await ctx.reply(reply);
 *   });
 */

import Anthropic from "@anthropic-ai/sdk";
import { jtdbTool, handleJtdbToolUse } from "./jtdb-tool";
import type { MessageParam } from "@anthropic-ai/sdk/resources/messages";

const anthropic = new Anthropic({
  apiKey: process.env.ANTHROPIC_API_KEY,
});

// NanoClaw が使うモデル（最新の claude-sonnet-4-6 を使用）
const MODEL = "claude-sonnet-4-6";
const MAX_TOKENS = 2048;

// ─── セッション型 ─────────────────────────────────────────────────────────────

export interface JtdbSession {
  chatHistory: MessageParam[];
  jtdbSessionId: string | null;   // Python スクリプトのセッションID
  phase: "idle" | "collecting" | "phase2_done" | "complete";
}

export function createJtdbSession(): JtdbSession {
  return { chatHistory: [], jtdbSessionId: null, phase: "idle" };
}

// ─── システムプロンプト ───────────────────────────────────────────────────────

const SYSTEM_PROMPT = `あなたは日本外傷データバンク（JTDB）患者情報登録アシスタントです。
外傷患者の情報収集から、AISコード付与・ISS/TRISS計算・JTDB形式出力まで一貫して支援します。

## 使用ツール
jtdb_register ツールを使って患者情報を収集します。

## 対話フロー
1. ユーザーが患者登録を要求したら jtdb_register(action="start") を呼ぶ
2. ツールの message をそのままユーザーに伝える
3. ユーザーの返答を jtdb_register(action="input", session_id=..., text=...) に渡す
4. status に応じて対応する:
   - "asking"      → message をユーザーに伝えて入力を促す
   - "confirming"  → 確認メッセージを伝え、jtdb_register(action="confirm") を呼ぶ
   - "phase2_done" → 「基本情報の収集が完了しました。AISコーディングを開始します」と伝え、
                     jtdb_register(action="ais_auto") を呼ぶ（ユーザー確認不要）
   - "done"        → 完了メッセージとスコア（GCS/RTS/ISS/TRISS Ps）を日本語で伝える
   - "error"       → エラー内容を日本語で伝える

## 質問フォーマット
質問には番号付き選択肢が含まれる場合がある（例: 1.男 / 2.女）。
ユーザーは番号（「1」「2 3」など）または自由記述で回答できる。
ユーザーの入力をそのまま text に渡せばよい（番号でもテキストでも AI が正しく解釈する）。

## 注意事項
- session_id は必ず保持して次の呼び出しに使う
- 収集中はユーザーの入力を忠実に jtdb_register に渡す（勝手に補完・編集しない）
- スコア表示時は「生存確率 XX%」のように分かりやすく伝える
- Markdownは使わず、シンプルなテキストで返答する`;

// ─── エージェントターン ───────────────────────────────────────────────────────

export interface TurnResult {
  reply: string;
  session: JtdbSession;
}

export async function runJtdbAgentTurn(
  session: JtdbSession,
  userText: string
): Promise<TurnResult> {
  // ユーザーメッセージを履歴に追加
  const history: MessageParam[] = [
    ...session.chatHistory,
    { role: "user", content: userText },
  ];

  let reply = "";
  let updatedSession = { ...session };

  // エージェントループ（tool_use が終わるまで継続）
  // eslint-disable-next-line no-constant-condition
  while (true) {
    const response = await anthropic.messages.create({
      model: MODEL,
      max_tokens: MAX_TOKENS,
      system: SYSTEM_PROMPT,
      tools: [jtdbTool],
      messages: history,
    });

    // レスポンスを履歴に追加
    history.push({ role: "assistant", content: response.content });

    if (response.stop_reason === "tool_use") {
      // ツール呼び出しブロックを処理
      const toolResults: Anthropic.Messages.ToolResultBlockParam[] = [];

      for (const block of response.content) {
        if (block.type !== "tool_use") continue;

        const rawResult = await handleJtdbToolUse(
          block.input as Record<string, unknown>
        );

        // session_id を追跡
        try {
          const parsed = JSON.parse(rawResult);
          if (parsed.session_id) {
            updatedSession.jtdbSessionId = parsed.session_id;
          }
          if (parsed.status === "phase2_done" || parsed.status === "done") {
            updatedSession.phase = parsed.status === "done" ? "complete" : "phase2_done";
          }
        } catch { /* ignore */ }

        toolResults.push({
          type: "tool_result",
          tool_use_id: block.id,
          content: rawResult,
        });
      }

      // ツール結果を履歴に追加してループ継続
      history.push({ role: "user", content: toolResults });
      continue;
    }

    // stop_reason が "end_turn" など → テキスト応答を取り出す
    const textBlock = response.content.find((b) => b.type === "text");
    reply = textBlock?.type === "text" ? textBlock.text : "処理が完了しました。";
    break;
  }

  // 履歴が長くなりすぎた場合は古いものを切り捨て（直近20ターン分）
  const trimmed = history.slice(-40);

  return {
    reply,
    session: { ...updatedSession, chatHistory: trimmed },
  };
}
