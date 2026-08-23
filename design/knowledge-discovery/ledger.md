# ledger: knowledge-discovery

## 反証round-11（B段実装）の帰結 — 2026-08-23

原文: reviews/round-11.md（critic: claude design-critic = claude-opus-5[1m]、3レンズを1人で順に）。B-1〜B-5、全件受理。中核（LLM非介入の決定的sweep・ツール1本の読み取り専用対話・失敗の非無音化・secret最小権限）は実機とコードの双方で成立を確認済み。

- **B-1（high）**: sweepクライアントtimeout 30s < 再シード直後の初回sweep 30.8s → 既定120s（`KD_API_TIMEOUT` で上書き可。Scheduler deadline 180s内）
- **B-2（mid）**: `min_instances=1`＋cpu4/8Giの常駐コスト（約$8〜10/日）が design「呼び出し時のみ課金」と矛盾 → 既定 `min_instances=0`（スケールtoゼロ）に変更、designのコスト記述を訂正、READMEに後始末（Scheduler削除＋Runtime削除）手順を追加。コールドスタートでの Scheduler 成功を単独テストで確認（結果は末尾）
- **B-3（mid）**: user_id は自己申告で「本人スコープ」は過大／`kd-scheduler-sa` の `roles/aiplatform.user` が広すぎる → design/READMEの文言を「呼び出し側が申告するセッションID。デモ割り切り（S-10同型）、本番はエージェント・アイデンティティ／IAMとのマッピング」に訂正。SAはカスタムロール `kdReasoningEngineInvoker`（`aiplatform.reasoningEngines.query/get` のみ）に縮小し `aiplatform.user` を除去
- **B-4（low）**: `google-genai` 未ピン留め → `google-genai==2.19.0` を requirements-agent.txt にピン留め
- **B-5（low・受理／対応見送り）**: ToolContext・timeout・env をモック置換しており外部契約をテストで検証していない → ライブ検証（ゴール19〜21の実機確認）を ledger に記録済みで代替。凍結前の追加テストは見送り（write-upの既知制限に含める）

**最終確認（2026-08-23 02:02 UTC）**: timeout120s・`min_instances=0`・カスタムロールSAで再デプロイ後、Scheduler単独発火→1試行でHTTP 200（約20秒）、Cloud Run sweep 1件、再試行なし。**B段クローズ**。本番値: reasoningEngines/4310793666370207744（asia-northeast1）、cpu4/8Gi、min0/max2、concurrency4、Scheduler `kd-secretary-sweep-runtime` 07:55 JST（A段 `kd-secretary-sweep` 08:00 JST と並走）。


## B段実装・検証の記録 — 2026-08-23

design v11 承認後の実装（secretary_agent パッケージ＝sonnet委譲、インフラ＝メインループ）。検証ゴール19〜22の結果:
- **19（Scheduler→Runtime→Cloud Run）**: 正系OK（単独発火で1試行200・sweep 1件・再試行なし）。負系OK（Cloud Run 404 時に `:query` が400で "sweep returned HTTP 404" を伝播し、Schedulerが失敗として記録・再試行）。A段ジョブは並走のまま
- **20（対話）**: `get_my_digest` のみ呼ばれ最終要約が返る（global推論先で実モデル呼び出し成功）。別user_idのセッションから他人のダイジェストは取れない。LLMツールに書き込み系なし
- **21（Registry）**: 手動登録5件（4体＋A段秘書）＋Runtime秘書の手動登録（計6件、asia-northeast1、説明・能力情報つき）。Runtime秘書の**自動登録は観測されず**（SDKデプロイ直後も `agents list` に現れなかったため手動登録で補完。READMEに明記）
- **22**: オフライン（skip 13）／`.venv-agent`（13件 skip 0）両方OK

**発生した問題と対処（記録価値あり）**: (1) `extra_packages` が相対パスを保持するため `src/secretary_agent` のままでは import 失敗 → cwd=src で同梱。(2) 既定リソースではリクエストごとにワーカー再起動ループ→Vertex が 503/`400 Service Unavailable` を返し Scheduler が全試行失敗（バックエンドは完走）→ `run_daily_sweep` を async＋スレッド実行化し、`resource_limits cpu4/8Gi・min1/max2・container_concurrency 4` を明示して解消。(3) 並行呼び出しに弱い（テストは逐次で行う。本番は1日1回）。(4) Reasoning Engine サービスエージェントは初回デプロイ試行で生成されるため secret IAM は初回失敗後に付与。


## 批評round-10（design v10 B段追補）の帰結 — 2026-08-23

原文: reviews/round-10.md（critic: claude design-critic = claude-opus-5[1m]）＋ reviews/round-10-codex-raw.txt（codex/gpt-5.6-sol xhigh）。指摘10件（重複3組・実質7論点）、**全件受理**し design v11 で解決。前提ルーティング1件（spec文言）は承認CPでユーザーに提示。

- **C-31 / Y-1 / Y-4（high・両ベンダー一致）**: トリガーがLLMのツール呼び出し依存＋SSEは常時2xx＋A段pauseで、sweep不発が無音化 → 定期起動を**LLM非介入の決定的オペレーション `run_daily_sweep`（register_operations、`:query` 非ストリーム、Cloud Run非2xxを非2xxに伝播）**に変更。SchedulerはContent-Type: application/json・OAuth・retry設定を明記。**A段ジョブはpauseせず並走**（冪等なので無害。不発時の保険）
- **Y-2（high）**: Runtime配置 asia-northeast1 と Gemini 3.7 Flash のエンドポイント（global/us/eu）の不整合 → モデルロケーションを `global` に明示、ゴール20に実モデル呼び出し成功を含める。通らなければ us-central1 配置にフォールバック
- **Y-3 / C-32（high/mid）**: Registry「実採用」の根拠が自動登録の一点・フォールバック時に登録ゼロ・spec文言（MCPツール）との不整合 → 手動登録のスパイクを実装初日に行い、Cloud Run 4体の手動登録を**8/27ゲートより前に完了**（B段撤退と独立に実採用を維持）。ゴール21を「説明・能力情報つきで検索可能」に強化。spec「MCPツールを登録」は該当物がないため文言修正を**承認CPでユーザーに提案**（前提ルーティング）
- **C-33（mid）**: `run_daily_sweep` が対話から呼べ収録中に状態変異 → 対話LLMのツールは `get_my_digest` のみ。`run_daily_sweep` はLLMツールにしない
- **C-34（mid）**: Runtime経由で任意employee_idのダイジェスト本文が読めトレースに残る → `get_my_digest` は引数なし・セッションuser_id固定。tracingは無効のまま。Sessionsの内容は本人スコープであることをREADMEに明記
- **C-35 / Y-5（mid）**: extra_packages・ベースURL・attempt-deadline欠落、ゴール22がB段未検証でもgreen → 独立パッケージ `src/secretary_agent/`＋`extra_packages`、`KD_API_BASE_URL`/`KD_API_KEY` env、ゴール22を(a)オフラインskip可 (b)B段専用環境でskip 0件 に分離
- **用語（codex）**: 「読み取り専用」→「配送権限を持たない」に訂正


## 反証round-9（round-8修正のクローズ検証）の帰結 — 2026-08-19

原文: reviews/round-9.md（critic: claude design-critic = claude-opus-5[1m]、fresh）。**round-8の10論点は10/10クローズ確認**（high 3件は原文の破綻シナリオ再実行で再現消滅を実測）。新規2件はメインループ（Claude Fable 5）が直接修正し、テスト84件全パス:

- **R-1（設計/high・受理）**: V-10修正の副作用で差分提案カードが不可視（digestがJordan固定、mail所有者はMarcus） → requester.htmlに**candidate.htmlと同一のデモ用1人切替ドロップダウン**を追加（round-6 S-3で意図的仕様として受理済みの機構の踏襲。design §14.8はUIの人物固定を規定しないため実装レベルで解決、design改訂・再承認なし）
- **R-2（実装/mid・受理）**: LLM失敗（例外・空応答）を「差分なし」と同一視しmailを無音消費 → `EXTRACTION_FAILED` sentinelで三値化（差分あり/明示null/失敗）。失敗時はprocessed=Falseのまま次回sweepで再試行。失敗時のヒューリスティック（メール本文コピー）も廃止（S-6残滓の完全閉塞。ヒューリスティックはllm_client未設定の環境のみ）。回帰テスト追加（失敗→未消費→復旧の一連）

M3（秘書プロアクティブ層・A段）の反証工程はこれで収束。ループ外残タスク: デプロイ＋シード再投入、Cloud Schedulerジョブ設置（ゴール18）、B段載せ替え判断（8/27期限）、デモ台本。

## 反証round-8（M3実装・3レンズ）のルーティング — 2026-08-19

原文: reviews/round-8-correctness.md（V-6〜V-10）/ round-8-safety.md（S-6〜S-10）/ round-8-excess-codex-raw.txt（E-6〜E-10。critic: claude design-critic ×2 = claude-opus-5[1m]、過剰実装レンズ = codex/gpt-5.6-sol xhigh）。15件・重複統合で実質10論点、**全件「実装」種別として受理**（設計差し戻しなし）。修正はCC内sonnetサブエージェントへ委譲、修正後に再検証・再反証。

- **V-6/S-8（high・一致）**: confirm CASが非トランザクション（並行二重POSTで二重配送を両criticが実測） → Storeに原子的遷移操作を追加（InMemory=Lock、Firestore=transaction）
- **V-7/S-7（high・一致）**: 差分反映が同一キー上書き＋visibility反転可（NDA項目のpublic化経路） → 厳密に「追加」のみへ。既存項目の変更を構造的に排除
- **V-8/E-7/S-6（high・3レンズ一致）**: llm_client未配線でヒューリスティック（メール全文コピー→public公開）が既定経路化 → serverで配線。LLM経路を既定、null（差分なし）対応
- **V-9/E-6（mid・一致）**: 帯変化なし再sweepでプレビュー再実行・preview_search監査行重複・0件時降格 → §14.2どおりscore/evidence更新のみに
- **V-10（mid）**: mail_seed所有者にprofile/agentが無くゴール16検証不能＋ダミープロフィール生成 → シード修正・実在前提化
- **E-8（mid）**: 汎用dismissが状態機械外の遷移を許す → type=stagnation & status=open に制限
- **E-9（low）**: DEMO_TODAYのAPI上書き・item_key付け替えの露出 → 削除（環境変数のみ）
- **E-10（low）**: 呼び出しゼロのCRUDヘルパー → 削除
- **S-9（low）**: `${tagText}` 未エスケープ（round-6 S-4の局所回帰） → esc()適用
- **S-10（low）**: /api/secretary/* の本人性突合なし → デモ割り切りとしてREADME明記（round-6 S-3と同じ整理）

**反証の肯定的所見（記録価値あり）**: M3の中核主張——プレビュー無痕跡（ゴール12）・public限定・fail-closedマスク——は正しさ・安全性の両レンズが実測で成立を確認。破綻は中核の外側（差分反映の書き込み側・並行処理）に限定されていた。

## スコープ追加の記録（2026-08-19 / spec v7 / M3）

ユーザーとの対話で**秘書（プロアクティブ）層**が追加され、spec.md が v7 へ改訂された（詳細は spec.md「v7改訂の経緯」）。要点: ①タスク停滞のルールベース検知（重み付きスコア＋2段閾値）→プレビュー検索（配送なし・候補者に痕跡なし）→本人の依頼確定で既存つながりレーンに合流、②モーニングダイジェスト（期日リマインドのみ、回答はしない）、③プロフィール継続更新（模擬メールシードからの差分提案→本人レビュー）、④苦手先回りはstretch、⑤GEAP採用範囲確定（Agent Registry実採用＋秘書のAgent Runtime 2段構え載せ替え、起動はCloud Scheduler）。既存の配送・同意・監査フロー（M1/M2実装済み・反証round-6クローズ済み）は変更しない——秘書は新しい配送経路を持たず、既存フローの入口に段を足すのみ。次工程: design.md 追補（M3設計）→批評1巡→実装。批評エージェントへ: 「秘書が自律配送する」方向の提案、および停滞検知のLLM判定化は、v7で明示的に却下された前提に反するため提案しないこと。

## 批評round-7（design v8 M3追補）の帰結 — 2026-08-19

原文: reviews/round-7.md（critic: claude design-critic = claude-opus-5[1m]）＋ reviews/round-7-codex-raw.txt（codex/gpt-5.6-sol xhigh、クロスベンダー）。指摘10件（重複3組、実質7論点）は全件「設計」種別のため design.md v9 改訂で全受理・解決。前提ルーティング（ユーザー質問）は発生なし。

- **C-26 / X-1（high・両ベンダー一致）**: プレビュー1段目が全項目embeddingを再利用しprivateが選定に影響（FR19違反） → `profiles.embedding_public` 新設、プレビューは両段public限定。VECTOR_FLOORはプレビュー非適用（落選判定を主張しない）。プレビューと正式実行の候補差は仕様として明示しUI固定文言化
- **C-27 / X-5（high/mid）**: Scheduler OIDCが実デプロイの `--allow-unauthenticated`＋DEMO_API_KEYと両立しない／A段起動の検証ゴール欠落 → APIキーヘッダ方式（`X-API-Key`）に変更、本番OIDC化はwrite-up将来項目。ゴール18（ジョブ手動発火＋キーなし401）を追加
- **C-28（high）**: 「今日」の基準とシード日付が未定義で収録日ずれに脆い → env `DEMO_TODAY` 導入、シードは `--today` からの相対日付生成。ゴール12〜17は DEMO_TODAY 固定で実行
- **C-29（mid）**: `deliver=False` フラグがfail-open（既存 `run_matching` は送信を持たない純粋関数） → フラグ案廃止、プレビューは純粋関数の直接呼び出し（送信層コードに到達し得ない構造的無痕跡）
- **C-30 / X-2 / X-3（mid/high/high）**: cards状態遷移の未定義（T1→T2昇格・回復時終了・dismissed/confirmed後の再発火・プレビュー0件・confirm多重実行） → §14.2に状態機械表を明文化（tier新設・resolved自動終了・dismissedは同一タスク非再生成・confirmはFirestoreトランザクションのCAS＋失敗時open戻し）
- **X-4（mid）**: `source: "mail_seed"` がprofiles schemaの許容値外 → enumに追加
- **枠外（claude総評）**: mail_seed由来項目のvisibility既定未記載 → 「既定public（業務由来デフォルト公開の原則）」を§14.5に明記

## 前提変更の記録（重要）

2026-08-18、design.md v4 の承認CPでユーザーから前提の誤りが指摘され、spec.md が v2〜v4 へ全面改訂された（詳細は spec.md の改訂経緯セクション）。design.md v1〜v4 と批評ラウンド1〜3の指摘（C-1〜C-15）は**旧2レーン構造を前提としたもの**であり、多くは機構ごと廃止された。以下の索引は「解決済み」ではなく「前提変更により消滅 or 縮小継承」として読むこと。批評エージェントは、廃止済み機構（来歴検証・reject_visibility・定時応答・ロール別射影・セッションロール認証・辞退不可視）の復活を提案しないこと。

## 未解決の指摘

（なし。反証工程round-6の15件も全て解決/意図的却下済み）

## 反証工程 round-6（実装検証・3レンズ並列）の帰結

原文: reviews/round-6-correctness.md / round-6-safety.md / round-6-scope.md（critic: claude-opus-5[1m]×3。codexは未インストールのため省略）。修正コミット 76732e2。

- **V-1/S-1（high・両レンズ一致）**: マスクがLLM自己申告のcited_item_keysに無検証依存＋捏造キーfallback → 送信層でキー実在検証＋reason_textへのprivate本文断片スキャン、fail-closed化。回帰テスト3件追加
- **V-2（high）**: 依頼者⇔打診リンクがプロセス内メモリのみ → payload永続化(requester_id/query_id/ask_audit_id)でステートレス化
- **V-3/S-2（high/mid）**: UIのAPIキー焼き込み・/docs無認証 → URLクエリ取得化・docs/openapi閉鎖
- **V-4（mid）**: 射影の質問混在・辞退誤突合 → 質問単位スコープ＋ask_audit_id突合
- **V-5（mid）**: match_proposalが双方に届いていない → 依頼者・同意者へ各1通実送信＋宛先assert
- **S-3（mid）**: 候補受信箱のagent_id切替 → デモ用1人4役ドロップダウンの意図的仕様としてREADMEに明記（採用せず）
- **S-4（mid）**: 未エスケープinnerHTML → esc()全面適用
- **S-5（low）**: funnel類似度の桁数オラクル → 社内デモ文脈で許容（採用せず）
- **E-1（high）**: VECTOR_FLOOR=0.20がGemini埋め込み空間で無意味 → 実測較正（無関係0.59/関連0.68-0.84）し本番env VECTOR_FLOOR=0.62
- **E-2（high）**: 拒否経路のライブ実演手段なし → /api/probe/unregistered-intent 追加
- **E-3（high）**: 合成データの重なり分岐が到達不能＋5クローン → 部門ローカル連番化＋役職/focus句織り込み、再シード済み
- **E-4（high）**: 監査ポーリング毎の全プロフィール取得(~10MB/3s) → 初回のみの静的カウントに
- **E-5（mid）**: README/rules不在 → README新設（再現手順・3責務対応表・rules方針・デモ割り切り）。firestore.rulesはclient SDK既定拒否をREADMEに文書化する方式
- E付録の削除候補(~200行)は凍結優先で見送り（陳腐化リスクよりも手戻りリスクを重視）。「実シード＋実デモ質問のゴールデンテスト」は台本確定後に追加検討

## 新前提での解決済み（一行索引）

- **C-16**: 候補選定の両端破綻（20件プールに4体が入る保証なし・接点なし出力路なし） → 配送用ランキング（レジストリ事前フィルタ）と画面用ファネル（スケール表示と明示）の2トラック分離＋`connection: null`と足切り＋デモで落選1件表示。design.md v6 §2
- **C-17**: 2段目推論の実行主体未定義（中央集約でStarmind同型化） → 候補エージェントごとの独立推論呼び出し（自分のプロフィールのみ参照、データ境界=プロセス境界）を明記。v6 §2
- **C-18**: public/private振り分けをLLM出力に依存 → `cited_item_keys`×`visibility`から送信層で機械的に確定。payloadとaudit_payloadを分離保存。v6 §3
- **C-19**: 依頼者側の状態・画面が未設計 → match_proposalのpayload定義（reason_text含めない）、状態語彙3種、監査画面の主張範囲（事実は記録・内容は非表示、存在秘匿は依頼者UIに限定）。v6 §3/§4/§6
- **C-20**: 工数配分（デモに映らない機能への投資・agent discoveryの実体なし） → ユーザー判断: レビューUI最小化（デモ非登場）・添付はdocも含む3種・agent discoveryは自前Firestore`agents`レジストリに実体化（GEAP実採用はせず将来構成言及に留める）。v6 §3/§8/§9
- **C-21**: `no_connection`経由のprivate漏出（C-16の直しがC-18の穴を裏口から復活） → マスク規則をメッセージ横断の1ルールに変更、2段目出力スキーマ統一（null時もcited_item_keys返却）、監査表示fail-closed（ホワイトリスト）。v7 §2/§3
- **C-22**: spec.md v4の3箇所未追随（FR12悪性クエリ赤表示・完了条件1の400件選定・完了条件4/FR8の監査画面文言） → spec.md v5で文言修正（方針変更なし）
- **C-23**: 落選判定がLLM自己申告scoreに単独依存 → ベクトル類似度下限`VECTOR_FLOOR`（決定的）とのOR判定＋台本用「確実に落ちる1体」のシード意図設計＋較正不収束時のフォールバック明記。v7 §2/§13
- **C-24**: doc添付のGCS署名URLがCloud RunデフォルトSAで失敗する罠 → Cloud Run自身の静的配信（/attachments/<id>）に変更し権限問題ごと回避。v7 §3/§8
- **C-25**: `supported_intents`がデッドフィールド → 送信層の`reject_unsupported_intent`拒否経路に接続して実機能化（レジストリが実際に流量を止める統制になる）。v7 §3。GEAP対応も「責務の1対1対応表」方式に強化（round-5総評）。v7 §8

## 旧ラウンドの指摘の帰結（一行索引）

- **D-0（レーン1回答方式の矛盾）**: 前提消滅。レーン1自体が廃止され、AI生成制限も「本人名義で流通させない」の一点に緩和された（spec v4）
- **D-0-note（辞退と無応答の区別）**: 前提消滅。辞退は理由付きで可視化される方針に反転（spec v2）
- **C-1（396体の扱い）**: **縮小継承**。「検索候補には含むが配送はimplemented=trueのみ」はv5 §2でも維持
- **C-2（private絞り込みの実施層）**: **部分継承**。privateを「他エージェント・依頼者に開示しない」原則は残るが、本人のエージェント自身はprivateを参照してよい（意味の反転、spec v4）。来歴検証は廃止
- **C-3（監査ログのアクセス制御）**: **縮小継承**。Firestore直接アクセス禁止＋サーバーAPI経由は維持。ロール別射影は廃止し、マスクは非公開項目打診の1ルールのみ（v5 §4）
- **C-4（タイミングチャネル・複数同意）**: 前提消滅。定時応答は廃止。複数同意は全員とマッチ成立してよい方針に反転（v5 §6）
- **C-5（禁止語「承認」）**: 継承。用語統一は維持
- **C-6（ロール判定・本人識別）**: 前提消滅。セッションロール認証体系は廃止し、デモ用の簡易な全体保護のみ（v5 §7）
- **C-7（T_lane1/T_lane2分離）**: 前提消滅。レーン1廃止によりタイムアウト機構自体が不要化（v5 §6: 応答待ちに時限を設けない）
- **C-8（embeddingのpublic限定）**: **意図的に反転**。embeddingは全項目から生成する。private由来のシグナルで候補に浮上することが非公開項目打診の入口になる（v5 §2）。旧C-8は過剰安全と判断
- **C-9（実装優先度分類）**: 考え方は継承（v5は機構が減ったため表は不要と判断）
- **C-10（来歴検証の強化）**: 前提消滅。来歴検証ごと廃止
- **C-11（悪性クエリの検知）**: 前提消滅。reject_visibilityは作らない。NDA情報を狙う質問には「会っても本人が断るだけ」という人間の判断に委ねる方針（ユーザー決定）
- **C-12（射影のフィールド漏れ）**: 前提消滅。隠すものがなくなった
- **C-13（監査者キーの配置矛盾）**: 前提消滅。ロール分離廃止
- **C-14（MIN_QUOTE_LENGTHの誤爆）**: 前提消滅。来歴検証ごと廃止
- **C-15（レーン2の実行主体等）**: 部分継承。match_proposalの宛先（双方）は v5 §6 に明記済み

全文は archive/ledger-resolved.md（旧前提時の記録として保存。読み返す場合は前提変更を念頭に）
