/**
 * JTDB（日本外傷データバンク）患者情報登録ツール
 *
 * NanoClaw スキルとして src/tools/jtdb.ts に配置する。
 * Python CLI (jtdb_collector.py --json) を stdin/stdout JSON で呼び出す。
 *
 * 環境変数:
 *   JTDB_WORKSPACE  - jtdb_collector.py が存在するディレクトリ（デフォルト: /workspace）
 *   JTDB_UV_CMD     - uv コマンドのパス（デフォルト: uv）
 */

import { spawn } from "child_process";
import * as path from "path";
import type { Tool, ToolResultBlockParam } from "@anthropic-ai/sdk/resources/messages";

const WORKSPACE = process.env.JTDB_WORKSPACE ?? "/workspace";
const UV_CMD    = process.env.JTDB_UV_CMD    ?? "uv";

// ─── ツール定義（Anthropic Tool Use 形式）────────────────────────────────────

export const jtdbTool: Tool = {
  name: "jtdb_register",
  description: `日本外傷データバンク（JTDB）患者情報登録システム。
外傷患者の情報を対話収集し、AISコード・ISS・TRISS Psを自動計算してJTDB提出形式（JSON/MD）を出力します。

アクション一覧:
- start      : 新規セッション開始。最初に必ず呼ぶ。
- input      : ユーザーの入力テキストを処理（session_id + text 必須）。
- confirm    : Phase 2（基本情報収集）を確定・保存（session_id 必須）。
- ais_auto   : Phase 3 AISコード自動付与・ISS/TRISS計算（session_id 必須）。

status の意味:
- asking     : message をユーザーに伝え、次の入力を待つ。
- confirming : 全項目収集完了。confirm アクションを呼ぶ。
- phase2_done: Phase 2完了。ais_auto アクションを呼ぶ。
- done       : 登録完了。scores と output_path が含まれる。
- error      : エラー。message に詳細。`,
  input_schema: {
    type: "object" as const,
    properties: {
      action: {
        type: "string",
        enum: ["start", "input", "confirm", "ais_auto"],
        description: "実行するアクション",
      },
      session_id: {
        type: "string",
        description: "セッションID（start 以外は必須）",
      },
      text: {
        type: "string",
        description: "ユーザーのテキスト入力（input アクション時に必須）",
      },
    },
    required: ["action"],
  },
};

// ─── サブプロセス呼び出し ─────────────────────────────────────────────────────

export interface JtdbInput {
  action: "start" | "input" | "confirm" | "ais_auto";
  session_id?: string;
  text?: string;
}

export interface JtdbOutput {
  status: "asking" | "confirming" | "phase2_done" | "done" | "error";
  session_id?: string;
  message: string;
  output_path?: string;
  scores?: {
    gcs: number | null;
    rts: number | null;
    iss: number | null;
    triss_ps: number | null;
    ais_coding_status: string;
  };
  progress?: {
    filled: number;
    total: number;
  };
}

export function callJtdb(input: JtdbInput, timeoutMs = 120_000): Promise<JtdbOutput> {
  return new Promise((resolve, reject) => {
    const proc = spawn(
      UV_CMD,
      ["run", "python", path.join(WORKSPACE, "jtdb_collector.py"), "--json"],
      {
        cwd: WORKSPACE,
        stdio: ["pipe", "pipe", "pipe"],
        env: { ...process.env },
      }
    );

    // stdin に JSON を送る
    proc.stdin.write(JSON.stringify(input) + "\n");
    proc.stdin.end();

    let stdout = "";
    let stderr = "";

    proc.stdout.on("data", (chunk: Buffer) => { stdout += chunk.toString(); });
    proc.stderr.on("data", (chunk: Buffer) => { stderr += chunk.toString(); });

    const timer = setTimeout(() => {
      proc.kill("SIGTERM");
      reject(new Error(`JTDB プロセスがタイムアウトしました（${timeoutMs / 1000}秒）`));
    }, timeoutMs);

    proc.on("close", (code) => {
      clearTimeout(timer);
      const line = stdout.trim();
      if (!line) {
        reject(new Error(`JTDB プロセスが空の出力を返しました (exit ${code})\n${stderr}`));
        return;
      }
      try {
        resolve(JSON.parse(line) as JtdbOutput);
      } catch {
        reject(new Error(`JTDB 出力のJSONパースに失敗: ${line}`));
      }
    });

    proc.on("error", (err) => {
      clearTimeout(timer);
      reject(new Error(`サブプロセス起動エラー: ${err.message}`));
    });
  });
}

// ─── ツールハンドラー（NanoClaw の tool_use ループから呼ぶ）─────────────────

export async function handleJtdbToolUse(
  toolInput: Record<string, unknown>
): Promise<string> {
  const input = toolInput as unknown as JtdbInput;
  try {
    const result = await callJtdb(input);
    return JSON.stringify(result, null, 2);
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    return JSON.stringify({ status: "error", message: msg } satisfies Partial<JtdbOutput>);
  }
}
