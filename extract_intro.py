#!/usr/bin/env python3
"""
AIS 2005 コードブック intro ページを Gemini で再解析し、
AIS/ISS コーディング解説 MD を生成するスクリプト
"""

import os
import json
import time
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

IMAGES_DIR = Path("/workspace/images")
CACHE_DIR  = Path("/workspace/output/intro_cache")
OUT_MD     = Path("/workspace/output/data/AIS_ISS_coding_guide.md")

# intro ページの画像（時系列順 = コードブック掲載順）
INTRO_IMAGES = [
    "IMG_20250917_083238.jpg",  # 表紙・冒頭
    "IMG_20250917_083247.jpg",  # 訳者序文
    "IMG_20250917_083252.jpg",  # AIS90 訳者序文
    "IMG_20250917_083254.jpg",  # 目次
    "IMG_20250917_083304.jpg",  # 訳者一覧
    "IMG_20250917_083308.jpg",  # ACKNOWLEDGEMENT
    "IMG_20250917_083314.jpg",  # Interest Groups
    "IMG_20250917_083317.jpg",  # 序（続き）
    "IMG_20250917_083323.jpg",  # 序
    "IMG_20250917_083328.jpg",  # 3. 現在 AIS が抱えている課題
    "IMG_20250917_083331.jpg",
    "IMG_20250917_083338.jpg",
    "IMG_20250917_083343.jpg",
    "IMG_20250917_083350.jpg",  # 平均生存
    "IMG_20250917_083354.jpg",
    "IMG_20250917_083359.jpg",  # 参考文献
    "IMG_20250917_083401.jpg",  # 手引書の使い方
    "IMG_20250917_083419.jpg",
    "IMG_20250917_083421.jpg",
    "IMG_20250917_083426.jpg",
    "IMG_20250917_083428.jpg",  # 7. Functional Capacity Index
    "IMG_20250917_083433.jpg",
    "IMG_20250917_083436.jpg",  # AIS コード選択のルールと指針
    "IMG_20250917_083441.jpg",  # 穿通創
    "IMG_20250917_083443.jpg",
    "IMG_20250917_083448.jpg",  # ローカライザー
    "IMG_20250917_083453.jpg",
    "IMG_20250917_083455.jpg",
    "IMG_20250917_083502.jpg",  # 受傷原因の記載
    "IMG_20250917_083516.jpg",  # 複数損傷の評価
    "IMG_20250917_083523.jpg",
]

EXTRACT_PROMPT = """\
この画像はAIS 2005 Update 2008（Abbreviated Injury Scale）コードブックの序文・解説ページです。

ページに書かれているテキストをできるだけ忠実に日本語で抽出してください。
英語のみのページは日本語に翻訳してください。
日英対訳ページは両方を含めてください。

以下のJSON形式で返してください：

{
  "section_title": "このページの見出し（なければnull）",
  "content": "本文テキスト（改行は\\nで表現、見出しは## や ### で表現）",
  "has_table": true/false,
  "table_markdown": "表がある場合のMarkdown表（なければnull）"
}

注意：
- JSONのみを返すこと（```や説明文は不要）
- ページ番号・フッターは除外してよい
- 図・グラフの説明は [図: ...] として記述
"""


def call_gemini(client, image_path: Path, retries: int = 3) -> dict:
    from google import genai

    data = image_path.read_bytes()
    for attempt in range(retries):
        try:
            response = client.models.generate_content(
                model="gemini-2.5-flash",
                contents=[
                    genai.types.Part.from_bytes(data=data, mime_type="image/jpeg"),
                    EXTRACT_PROMPT,
                ],
            )
            raw = response.text.strip()
            if "```json" in raw:
                raw = raw.split("```json")[1].split("```")[0].strip()
            elif "```" in raw:
                raw = raw.split("```")[1].split("```")[0].strip()
            return json.loads(raw)
        except json.JSONDecodeError as e:
            print(f"  JSON解析エラー: {e}")
            return {"section_title": None, "content": response.text if 'response' in dir() else "", "has_table": False, "table_markdown": None}
        except Exception as e:
            err = str(e)
            if attempt < retries - 1:
                wait = 15 * (attempt + 1)
                print(f" [リトライ {attempt+1}/{retries}, {wait}秒待機]", end="", flush=True)
                time.sleep(wait)
            else:
                print(f" [ERROR: {err[:80]}]")
                return {"section_title": None, "content": f"[ERROR: {err}]", "has_table": False, "table_markdown": None}


def main():
    from google import genai

    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        print("ERROR: GEMINI_API_KEY が .env に設定されていません")
        return

    client = genai.Client(api_key=api_key)
    CACHE_DIR.mkdir(parents=True, exist_ok=True)

    pages = []

    for i, fname in enumerate(INTRO_IMAGES, 1):
        img_path = IMAGES_DIR / fname
        cache_path = CACHE_DIR / fname.replace(".jpg", ".json")

        if cache_path.exists():
            print(f"[{i:2d}/{len(INTRO_IMAGES)}] {fname} ... キャッシュ使用")
            result = json.loads(cache_path.read_text(encoding="utf-8"))
        else:
            if not img_path.exists():
                print(f"[{i:2d}/{len(INTRO_IMAGES)}] {fname} ... 画像なし → スキップ")
                continue
            print(f"[{i:2d}/{len(INTRO_IMAGES)}] {fname}", end=" ... ", flush=True)
            result = call_gemini(client, img_path)
            result["source"] = fname
            cache_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
            title = result.get("section_title") or "(無題)"
            content_len = len(result.get("content") or "")
            print(f"完了 [{title[:30]}] {content_len}文字")
            time.sleep(4)

        result["source"] = fname
        pages.append(result)

    print(f"\n{len(pages)}ページを取得しました。MD生成中...")
    build_md(pages)
    print(f"✓ 保存: {OUT_MD}")


def build_md(pages: list[dict]) -> None:
    lines = [
        "# AIS 2005 Update 2008 — コーディングガイド",
        "",
        "> AIS（Abbreviated Injury Scale）2005 Update 2008 日本語対訳版 序文・解説より抜粋",
        "",
        "---",
        "",
    ]

    current_section = None

    for page in pages:
        title = page.get("section_title")
        content = (page.get("content") or "").strip()
        table_md = page.get("table_markdown")

        if title and title != current_section:
            current_section = title
            lines.append(f"## {title}")
            lines.append("")

        if content:
            lines.append(content)
            lines.append("")

        if table_md:
            lines.append(table_md)
            lines.append("")

    OUT_MD.parent.mkdir(parents=True, exist_ok=True)
    OUT_MD.write_text("\n".join(lines), encoding="utf-8")


if __name__ == "__main__":
    main()
