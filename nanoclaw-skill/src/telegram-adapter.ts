/**
 * Telegram アダプター（NanoClaw スタンドアロン版）
 *
 * NanoClaw を使わずに直接 Telegram ボットとして動かす場合はこのファイルを使う。
 * NanoClaw に組み込む場合は jtdb-agent.ts のみを src/tools/ に追加すれば良い。
 *
 * 起動:
 *   TELEGRAM_BOT_TOKEN=xxx ANTHROPIC_API_KEY=xxx JTDB_WORKSPACE=/workspace \
 *     npx ts-node src/telegram-adapter.ts
 */

import { Bot, Context } from "grammy";
import { JtdbSession, createJtdbSession, runJtdbAgentTurn } from "./jtdb-agent";

const BOT_TOKEN = process.env.TELEGRAM_BOT_TOKEN;
if (!BOT_TOKEN) {
  console.error("ERROR: TELEGRAM_BOT_TOKEN が設定されていません");
  process.exit(1);
}

const bot = new Bot(BOT_TOKEN);

// chatId → セッション（メモリ内 Map）
const sessions = new Map<number, JtdbSession>();

// トリガーキーワード（いずれかを含む場合にJTDBモード起動）
const TRIGGERS = ["患者登録", "jtdb", "外傷登録", "trauma", "患者情報"];

function shouldActivate(text: string): boolean {
  const lower = text.toLowerCase();
  return TRIGGERS.some((t) => lower.includes(t.toLowerCase()));
}

// ─── メッセージハンドラー ─────────────────────────────────────────────────────

bot.on("message:text", async (ctx: Context) => {
  if (!ctx.message?.text || !ctx.chat) return;

  const chatId  = ctx.chat.id;
  const text    = ctx.message.text;
  let   session = sessions.get(chatId);

  // セッションがなく、かつトリガーキーワードもない場合はスルー
  if (!session && !shouldActivate(text)) {
    await ctx.reply(
      "JTDB患者登録を開始するには「患者登録」または「外傷登録」と入力してください。"
    );
    return;
  }

  // 新規セッション作成
  if (!session) {
    session = createJtdbSession();
    sessions.set(chatId, session);
  }

  // タイピングインジケーター
  await ctx.replyWithChatAction("typing").catch(() => {});

  try {
    const { reply, session: updated } = await runJtdbAgentTurn(session, text);
    sessions.set(chatId, updated);

    // 長いメッセージは分割送信（Telegram は 4096文字制限）
    const chunks = splitMessage(reply);
    for (const chunk of chunks) {
      await ctx.reply(chunk);
    }

    // 完了したらセッションをクリア
    if (updated.phase === "complete") {
      sessions.delete(chatId);
      console.log(`[${chatId}] 患者登録完了 session=${updated.jtdbSessionId}`);
    }
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    console.error(`[${chatId}] エラー:`, msg);
    await ctx.reply(`エラーが発生しました: ${msg.slice(0, 200)}`);
  }
});

// /reset コマンド
bot.command("reset", async (ctx) => {
  if (!ctx.chat) return;
  sessions.delete(ctx.chat.id);
  await ctx.reply("セッションをリセットしました。「患者登録」と入力して再開できます。");
});

// /status コマンド
bot.command("status", async (ctx) => {
  if (!ctx.chat) return;
  const session = sessions.get(ctx.chat.id);
  if (!session || session.phase === "idle") {
    await ctx.reply("現在アクティブなJTDB登録セッションはありません。");
  } else {
    await ctx.reply(
      `フェーズ: ${session.phase}\nセッションID: ${session.jtdbSessionId ?? "未開始"}`
    );
  }
});

// ─── ユーティリティ ───────────────────────────────────────────────────────────

function splitMessage(text: string, maxLen = 4000): string[] {
  if (text.length <= maxLen) return [text];
  const chunks: string[] = [];
  let i = 0;
  while (i < text.length) {
    chunks.push(text.slice(i, i + maxLen));
    i += maxLen;
  }
  return chunks;
}

// ─── 起動 ─────────────────────────────────────────────────────────────────────

console.log("JTDB Telegram Bot 起動中...");
console.log(`JTDB_WORKSPACE: ${process.env.JTDB_WORKSPACE ?? "/workspace"}`);

bot.catch((err) => {
  console.error("Bot エラー:", err.message);
});

bot.start({
  onStart: (info) => {
    console.log(`Bot 起動完了: @${info.username}`);
    console.log("トリガー:", TRIGGERS.join(", "));
  },
});
