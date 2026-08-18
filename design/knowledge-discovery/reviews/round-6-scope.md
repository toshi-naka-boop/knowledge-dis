critic: claude-opus-5[1m]

## Round 6 — 2026-08-18 — 反証(過剰実装・簡潔性)

対象: `src/knowledge_discovery/` 全体、`scripts/generate_seeds.py`、`tests/`（61件, venvで全パス確認）、`Dockerfile`
基準: design.md v7 / spec.md v6 / seed-spec.md / ledger.md（C-9・C-20の優先度判断、前提変更の記録を確認済み。廃止済み機構の復活提案はしていない）

前置き: 8/29凍結まで実質10日。以下は「削るもの」「直すもの」を残工期で仕分けた結果。**E-1〜E-3は"削る"より"直す"側だが、いずれも修正コストが5〜20行**で、デモ動画3幕の成立可否に直結するため優先度を上げた。純粋な削除候補は末尾の付録にまとめた（合計約200行）。

### 指摘

- [E-1] 種別: 実装 / 深刻度: high
  - 指摘: Gate 1（`VECTOR_FLOOR=0.20`）は本番構成では発火しない可能性が高く、C-23が用意した「LLM自己申告への単独依存を外す保険」が実質消えている。
  - 破綻シナリオ: 2つの経路のどちらかを踏む。
    (a) **次元不一致による全落選**: `scripts/generate_seeds.py` の `--embedder` 既定値は `deterministic`（128次元）。サーバは `create_app_from_env` で `GeminiEmbedder`（数千次元）を使う。`Embedder.similarity` は `len(vec_a) != len(vec_b)` のとき**例外を投げず 0.0 を返す**（matching.py:99-102, gemini_adapters.py:127-132）。0.0 < 0.20 なので4体全員が `vector_floor` で落選し、監査画面には「ベクトル類似度(0.000)が下限(0.200)を下回ったため落選」が4行並ぶ。配送0件。エラーログは一切出ず、見た目は「マッチング精度の問題」に化ける。デモ前夜に `--embedder gemini` を付け忘れて再投入すれば必ず踏む。
    (b) **Gemini埋め込みでの永久非発火**: 逆に次元が揃った場合、`VECTOR_FLOOR=0.20` は決定的Embedder向けに較正された値（matching.py:85-87 のコメントが「unrelated textsのcosineノイズをVECTOR_FLOOR未満に保つ」と明示している＝フェイク実装に対するチューニング）。実埋め込みモデルでは無関係な英文ペアでもcosineは0.4〜0.6程度に出るのが通例で、Tom Whitfield（経理）×クリニック移転質問も0.20を大きく上回る。するとGate 1は全ケースで素通りし、design §10 の**最重要ゴール（落選シーン）が Gemini の `connection: null` 出力1回に全依存**する。C-23の判断（「較正が収束しなくてもベクトル下限だけで落ちるから台本は守られる」）が成立しない。
  - 提案: 実シード400件＋デモ質問3種で実Embedderの類似度分布を1回だけ実測し（既存の `VECTOR_FLOOR` 環境変数にそのまま入る）、Tom が下限で落ちる値を確定する。合わせて `similarity()` の次元不一致を `0.0` 返しではなく例外にする（3行）。この2点だけで(a)(b)両方が閉じる。実測は10分、デモの最重要シーンの担保に直結する。

- [E-2] 種別: 実装 / 深刻度: high
  - 指摘: 拒否経路（`reject_unregistered_type` / `reject_unsupported_intent`）を**HTTP経由で発火させる手段が存在しない**。デプロイ済みシステム上ではデッドパスで、デモ第3幕が撮影できない。
  - 破綻シナリオ: `server.py` の全エンドポイントは `payload_type` を内部で固定して `TransmissionLayer.send` を呼ぶため、未登録型も未サポートintentも外から作れない（`grep reject src/knowledge_discovery/server.py` は0件）。一方 `audit.html:256-263` には赤い REJECTED 吹き出しのレンダラが実装済みで、`seed-spec.md` のデモ質問3「未登録 payload 型の送信テスト → 赤の system メッセージ」、design §11-3「尺が余れば未登録型の拒否（赤表示）を実演」、design §10 ゴール4b・7 が全て**ユニットテスト内でしか再現できない**。収録当日に「統制の3点セット」20秒を撮ろうとして、Cloud Run上で赤い行を出す方法がないことに気づく。C-25（`supported_intents` を実機能化した round-5 の成果）も、審査員が触れる面には露出していない。
  - 提案: `POST /api/probe`（仮）を12行程度で追加し、`from/to/intent/payload_type/payload` をそのまま `service.transmission.send()` に流す。「未登録エージェントが未知のスキーマを押し込もうとする」というナレーションになり、Fortifiedトラックの中核主張（何を流せるかの統制）がそのまま画になる。過剰実装ではなく、既存機構への唯一の露出口。採らない場合はデモ台本から第3幕を落とし、write-up側で「テストで実証」と書き切る判断が要る（その場合 audit.html の赤表示レンダラ約10行は削除候補）。

- [E-3] 種別: 実装 / 深刻度: high
  - 指摘: 合成396名は実質**5種類の本文のクローン**で、seed-spec が要求した「デモ質問と語彙が強く重なるプロフィールを各5名」の分岐は**到達不能**。ファネル上位20件が中身を持たない。
  - 破綻シナリオ: `generate_synthetic_profiles` の `idx` は全部門通しの連番で、部門割当は staffing 158件が先頭。よって `dept == "real_estate" and idx <= 5` / `dept == "transition" and idx <= 5` は永久に偽（real_estateのidxは159開始）。実測: 生成された396件の `current_work` は5種のみ（158/59/40/99/40件ずつ同一文）、"zoning" を含むプロフィール0件、"succession" を含むプロフィール0件。帰結は3つ。(1) design §10 ゴール9「異なる質問3種でファネル上位20件が変化する」は成立しない——同一文＝同一類似度でタイになり、上位クラスタが同じ質問群では上位20件が完全一致する。(2) 「全社展開ならこの400人が探索対象です」というデモ冒頭のスケール訴求を、名前入りで見せた瞬間に同一職務文のクローン20件が並ぶ（現状 `funnel_candidates` はどこにも描画されていないので今は露見しないが、それは訴求を捨てているのと同じ）。(3) 審査員が `generate_seeds.py` を30秒眺めれば、FR13「Gemini合成の模擬社員プロフィール約400名分」がテンプレ複製であることが分かる（Demo & Production Readiness 30%の「リポジトリが機能性を証明する」に直撃）。
  - 提案: 分岐条件を部門ローカルのカウンタに変える（3行）だけで意図した重なり10名が復活する。加えて各部門の本文を4〜5バリアントに割る（`idx % 4` で選ぶ、約30行）。そのうえで `funnel_candidates`（現在APIが返すだけで未描画）を監査画面のファネルバーに上位5名だけ名前表示すれば、スケール訴求が実データで裏づく。合計40行弱で、デモ冒頭の主張と goal 9 の両方が回収できる。

- [E-4] 種別: 実装 / 深刻度: high
  - 指摘: 監査画面の3秒ポーリングが、**整数2つを表示するために400プロフィール（埋め込みベクトル込み・約10MB）を毎回Firestoreから全件取得**している。さらにサービス状態がプロセス内メモリにあるため、その負荷がインスタンス増加を誘発すると依頼者画面が壊れる。
  - 破綻シナリオ: `server.py:390-402` の `/api/audit/messages` は `store.list_profiles()` と `store.list_messages()` を毎回呼ぶが、profiles の用途は `len(profiles)` だけ。`Profile.to_dict()` は `embedding` を含むため、Firestoreから400×数千次元のfloat配列を引く（3072次元なら1件約25KB＝毎回約10MB、3秒ごと）。これが3秒以内に返らなければポーリングが積み上がり、Cloud Runは同時実行数の増加でインスタンス#2を起動する。ここで `KnowledgeDiscoveryService._ask_to_requester` / `_query_to_asks` はプロセス内 dict（service.py:61-64）なので、ブラウザが#2に振られた瞬間に (i) 依頼者画面のステータスが恒久的に空、(ii) 同意処理の `requester_id` が既定値 `"requester"` にフォールバックし（service.py:155）、監査チャットに `system ➔ requester` という壊れた宛先と、participants に `"requester"` を含む match_proposal が残る。収録の途中で起き、しかも再現条件が「ポーリングが詰まったとき」なので追いにくい。加えて `get_requester_status` は declined 候補ごとに `store.list_messages()` を**ループ内で**呼ぶ（service.py:245）ので、requester画面の2秒ポーリングもメッセージ全件走査を候補数ぶん繰り返す。
  - 提案: 3点、いずれも小さい。(1) 監査エンドポイントから `list_profiles()` を外し、起動時に数えた定数か `count()` を返す（−3行、ポーリング負荷が10MB→数十KB）。(2) `list_messages()` をループ外に1回だけ出す（1行）。(3) 依頼者リンクを `connect_ask` payload の `requester_id` に持たせて messages から復元し、dict 2本を削除する（正味−10行、ステートレス化）。(3)を採らない場合の最低限の代替はデプロイ時 `--max-instances=1`（ただし再起動には耐えない）。ポーリング間隔も監査3秒→10秒で十分（デモの見た目は変わらない）。

- [E-5] 種別: 実装 / 深刻度: mid
  - 指摘: 提出物としてのリポジトリが薄い。**README不在（再現手順ゼロ）・`firestore.rules` 不在（design §10 ゴール11の根拠なし）・APIキーのHTMLハードコード**の3点は、いずれも審査基準の名指し項目に当たり、かつ合計1〜2時間で埋まる。
  - 破綻シナリオ:
    (1) **README不在**: リポジトリのルートに手順書が1つもない（実測: `README*` 該当なし）。審査員が clone しても、`PYTHONPATH=src:.` が要ること、`generate_seeds.py --use-firestore --embedder gemini` が要ること、`USE_FIRESTORE=1` / `GOOGLE_GENAI_USE_VERTEXAI` / `DEMO_API_KEY` の3環境変数が要ることを知る手段がない。Demo & Production Readiness 30%は「リポジトリが機能性をどれだけ説得力を持って証明しているか」なので、動く実装があるのに読み手が起動できない状態は直接の減点。同時にこのREADMEはアーキ図（Architectural Discipline 30%）の下書きそのものになる——design §8 のGEAP責務1対1対応表と §3 の3コレクション定義を貼るだけで骨格が埋まる。
    (2) **`firestore.rules` 不在**: design §3「Firestore Security Rulesでクライアント直接読み書き禁止」・ゴール11はリポジトリ上に痕跡がない。`allow read, write: if false;` の5行ファイルをコミットするだけで、write-upの「サーバAPI経由のみ」という主張が検証可能になる。無いままだと、Fortifiedトラックで最も見られる箇所に根拠が無い状態で主張だけが載る。
    (3) **APIキーのハードコード**: `requester.html:116` / `candidate.html:93` / `audit.html:201` に `const API_KEY = "demo-key-2026"` が直書きされ、`server.py:38` の既定値と一致している。**デプロイで `DEMO_API_KEY` を実値に設定した瞬間、3画面すべてが401で無音死する**（fetchのcatchはconsole.errorのみで、画面は「Loading...」のまま）。しかもHTMLは認証なしで配れるので、キーを設定しても保護は成立しない（キーがページに載る）＝現状のAPIキー層は認知コストだけ払って防御価値ゼロ。審査の「認証情報のセキュリティ」で、リポジトリに資格情報が直書きされている絵にもなる。
  - 提案: (3)は配信時にHTML内の `__API_KEY__` を `expected_api_key` で置換する2行で、ハードコード解消とデプロイ事故の両方が消える（`html_file.read_text().replace(...)`）。あるいはAPIキー層ごと削除してCloud Run ingress/IAMに委ねる（−30行）。どちらかを選び、READMEに「デモ用の全体保護であり本番認証ではない」と明記する。(1)(2)は残工期のうち最初の半日で終わる作業として、動画・図の制作より先に置くことを勧める。

### 付録A: 純粋な削除候補（デモにも審査にも寄与しない、合計約200行）

| 対象 | 場所 | 行数 | 削って軽くなるもの |
|---|---|---|---|
| `get_messages_for_entity` | store.py, firestore_store.py + テスト2件 | 約45 | 呼び出し元ゼロ。Store抽象の面が3割減り、将来Store実装を差し替える時の実装義務が減る |
| `get_agent_by_employee_id` | store.py, firestore_store.py + テスト2件 | 約40 | 同上。呼び出し元ゼロ |
| `Profile.get_full_text` | models.py + テスト1件 | 約20 | 死んだメソッド。docstringが「embedding用」と嘘をついており（実際は `item.body` のみ使用, matching.py:222）、読む人を誤誘導する |
| `TransmissionError` | transmission.py + `__init__` 再エクスポート | 約10 | 一度もraiseされない。「送信は例外を投げる」という誤った期待を生む |
| `doc_factory_automation_guide_2026` | server.py:73-78 | 7 | 旧・製造業シナリオの残骸。Meridian舞台と無関係な添付が本番に配信可能な状態で残っている（審査員が `/attachments/` を覗くと世界観が壊れる） |
| `__init__.py` の全部入り再エクスポート | \_\_init\_\_.py 全体 | 89 | 誰も `from knowledge_discovery import X` していない（67箇所すべてフルパス）。これがあるせいで leaf module の import が fastapi/firestore/genai を全部引き、`import knowledge_discovery.matching` に0.47秒（実測）かかる。docstring 3行だけ残せばよい |
| `DeterministicEmbedder` のCJK分岐 | matching.py:71-73 | 4 | 全データ・全質問が英語。到達しない |
| `screen_funnel`/`delivery_ranking` の遅延embed | matching.py:234-235, 267-268 | 4 | `if prof.embedding is None: compute()` は、埋め込み欠損時に**質問1回あたり最大400回のGemini呼び出し**（各レート制限で `time.sleep(62)`）を引き起こす地雷。起こり得ないケースのフォールバックとして削るか、明示的に例外にする |

補足: `funnel_candidates`（`/api/query` が返すが未描画）は削除ではなく E-3 の描画側に回すことを勧める。質問埋め込みは `screen_funnel` と `delivery_ranking` で二重に計算されている（1クエリあたりGemini呼び出し2回→1回にできる、−3行）。

### 付録B: テスト61件の仕分け（凍結前の回帰検出としての価値）

**残す（凍結の防波堤・約26件）**
- `test_schema_registry.py`（8）: fail-closedホワイトリスト、`match_proposal` の reason_text 禁止、未登録型拒否。write-upで主張する統制の本体そのもの。閾値やintentを足したときに真っ先に壊れる
- `test_transmission.py`（5）: privateマスクのメッセージ横断ルール（C-18/C-21）と2つの拒否経路。仕様の核心で、実装が最も分岐している箇所
- `test_goals.py`（8）: design §10 の検証可能ゴールに1対1で対応。凍結判定のチェックリストとしてそのまま使える
- `test_server.py` の射影系（api key / requester privacy / private badge / audit masking の4件程度）: 依頼者UIにprivate由来が漏れないという最重要主張のE2E位置での担保

**惰性（削っても凍結リスクが上がらない・約22件）**
- `test_models.py`（5）: dataclass の to_dict/from_dict 往復のみでロジックがない。うち `test_profile_full_text` は付録Aの死んだメソッドを固定している
- `test_store.py`（4）/ `test_firestore_store.py`（6）: 前者は dict の CRUD、後者は自作フェイククライアントの形状を検証しているだけで実Firestoreの挙動は保証しない（実環境E2E済みなら証拠として弱い）。うち3件は削除候補メソッドを守っている
- `test_gemini_adapters.py`（5）: 手書きモックのレスポンス形状に対するテスト。実SDKの形状が変わっても通ったまま壊れる。ただし `_parse_json_result` の null/パース失敗フォールバック2件は分岐が実在するので残す価値あり

**不足している唯一重要なテスト（1件追加を推奨）**
- 実シード（Meridian 4体）＋実デモ質問での**ゴールデンテスト**が存在しない。`test_goals.py` のfixtureは旧シナリオ（Alice/Bob/Charlie/David、質問「製造業の生産管理システム導入」、日本語）で、デモを実際に支える *Marcus 同意 / Rachel 辞退 / Tom 落選* の並びは一度も検証されていない。`VECTOR_FLOOR` / `CONNECTION_THRESHOLD` を較正（E-1）した瞬間に壊れるのはまさにここ。決定的Embedder＋固定した推論結果で「デモ質問1でMarcusとRachelが配送され、Tomが落選する」を1件書けば、凍結後の値いじりに対する唯一の安全網になる。

### 反論しなかった点（確認済み）

- 監査画面のfail-closed（`SchemaRegistry.get_audit_view`）は設計通り実装され、`audit_payload` 生成漏れ・ホワイトリスト外がマスクに倒れることをコードで確認した。C-21は実装まで通っている
- 2段目推論の候補分離（C-17）は `infer_connection(question, profile)` のシグネチャがプロフィール1体分しか受け取れない形で担保されており、データ境界＝プロセス境界の主張は誇張でない
- `match_proposal` の `reason_text` 禁止はスキーマ検証で機械的に拒否される（C-19）。実装が主張より弱い箇所ではない
