# AISコーディングの仕組み（`scripts/jtdb_ais_coder.py`）

`run_ais_coding()` を中核に、JTDB損傷フィールド（70〜75）ごとに損傷記述をループし、
各記述に対して **決定論的コードブック照合 ＋ LLM(Gemini)候補** を統合 → ユーザー確認 →
詳細ドリルダウンでAISコードを確定し、最後に ISS/RTS/TRISS を計算する。

**設計の要点**: LLMは候補提示のみで、確定は必ず決定論的照合＋ユーザー確認を経由する。
重症度はコード末尾1桁が唯一の真実（`_severity_from_code`）で、コードブックの
`ais_severity` 欄は信用しない多重防御構造。

---

## 全体フロー

```mermaid
flowchart TD
    Start([run_ais_coding 開始]) --> Init[load_codebook + build_idf_index<br/>臓器コンテキスト伝播 / IDF計算]
    Init --> LoopField{フィールド70-75<br/>ループ}
    LoopField -->|損傷なし| Skip[max_ais=0 / スキップ]
    LoopField -->|損傷あり| LoopDesc{各損傷記述<br/>ループ}

    LoopDesc --> Parse[① parse_injury_attrs<br/>両側/開放/粉砕/麻痺/昏睡 抽出]
    Parse --> Enrich[_enrich_query<br/>未出現語をクエリ補強]
    Enrich --> Cand[② 候補生成 3系統マージ]
    Cand --> Select[③ ユーザー選択<br/>番号/Enter/コード直入力/s]
    Select --> Drill[④ _drill_down<br/>詳細分類の確定]
    Drill --> Spine{脊椎コード?}
    Spine -->|Yes| SpineISS[⑤ ISS部位判定<br/>section→70/72/73<br/>spine_iss に別管理]
    Spine -->|No| Vasc
    SpineISS --> Vasc[⑥ _code_vascular_coinjury<br/>血管併発を別コードで確認]
    Vasc --> LoopDesc

    LoopDesc -->|完了| MaxAis[max_ais 更新<br/>AIS9除外/spine二重計上防止]
    MaxAis --> LoopField
    LoopField -->|全部位完了| Score[スコア計算]
    Score --> Save[regenerate_md → JSON/MD保存]
    Save --> End([終了])
```

---

## ② 候補生成（3系統マージ）

```mermaid
flowchart LR
    Desc[損傷記述] --> Exact["exact_name_candidates<br/>完全名一致 (◎high)<br/>最優先"]
    Desc --> LLM["suggest_ais_codes<br/>Gemini 2.5 flash<br/>(○/△)"]
    Desc --> Struct["structural_candidates<br/>IDF構造スコア検索<br/>(△low・決定論的)"]

    Exact --> Merge[merge_candidates<br/>コード重複除去<br/>severityはコード末尾から再導出]
    LLM --> Merge
    Struct --> Merge
    Merge --> Out[統合候補リスト<br/>優先順: 完全名一致 → LLM → 構造検索]

    subgraph LLMコンテキスト
      Filter["build_ais_context<br/>structural_filter で絞込"]
    end
    Filter -.プロンプトに投入.-> LLM
```

### 構造スコア（`structural_filter` / `_score_tree`）の4段階

```mermaid
flowchart TD
    S1["Stage1: 損傷形態タグ検出<br/>骨折/裂傷/破裂… → 一致ボーナス ×2"]
    S2["Stage2: IDF重み付き多フィールドスコア<br/>title_ja×3 / title_en×2 / section×1 / desc×0.5"]
    S3["Stage3: 親スコアを子へ継承<br/>_PARENT_BOOST_RATIO=0.3"]
    S4["Stage4: 臓器コンテキスト一致<br/>肺/肝/脳幹… → +4.0"]
    S1 --> S2 --> S3 --> S4 --> Rank[スコア降順ソート → 上位N]
```

---

## ④ ドリルダウン（軸判定と自動/対話確定）

```mermaid
flowchart TD
    In[選択エントリ + cb_entry] --> Leaf{子分類あり?}
    Leaf -->|なし=リーフ| Done[確定]
    Leaf -->|あり| FracSplit{骨折ノード?}
    FracSplit -->|Yes| Split[_split_open_child<br/>「開放」子を分離]
    FracSplit -->|No| Axis
    Split --> Axis[_classify_axis<br/>優先: サイズ > 左右 > 昏睡/開放/粉砕/麻痺]

    Axis --> Auto{attrs で<br/>自動解決可?}
    Auto -->|Yes| Pick[_auto_pick_child<br/>質問せず選択]
    Auto -->|No| Ask[_ask_child_selection<br/>軸ごとの質問文で確認]
    Ask --> Record[attrs に確定値を記録<br/>深い階層での再質問防止]

    Pick --> Recurse[選択子で再帰]
    Record --> Recurse
    Recurse --> Leaf

    Axis -.部位/形態はこれ以上不明.-> OpenChk{骨折 & 開放子あり?}
    OpenChk -->|Yes| ResolveOpen[_resolve_open_fracture<br/>開放/閉鎖を一度だけ確認]
    OpenChk -->|No| Done
```

---

## スコア計算の依存関係

```mermaid
flowchart LR
    subgraph 生体情報
      GCS[GCS-E/V/M] --> RTS
      SBP[収縮期血圧] --> RTS
      RR[呼吸数] --> RTS[calculate_rts]
    end
    subgraph 損傷
      MaxAis[max_ais 部位別] --> ISS[calculate_iss<br/>上位3部位のAIS²和]
      SpineISS[spine_iss] --> ISS
    end
    RTS --> TRISS[calculate_triss<br/>生存確率 Ps]
    ISS --> TRISS
    Age[年齢] --> TRISS
    Type[鈍的/穿通] --> TRISS
```

- **ISS** = 上位3部位のAIS²の和（AIS6→75）。AIS9(不明)は除外、`spine_iss` を部位別に合算。
- **RTS** = GCS/SBP/RR のコード化値の重み付き和。
- **TRISS** = RTS・ISS・年齢・鈍的/穿通の係数から生存確率 Ps。
