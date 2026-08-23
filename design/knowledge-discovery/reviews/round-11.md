critic: claude design-critic = claude-opus-5[1m]

## Round 11 — 2026-08-23 — 工程: 反証（3レンズ: 正しさ / 安全性 / 過剰実装）

対象: design v11 §14.7 B段（Agent Runtime 載せ替え）の実装。
読んだもの: design.md §10 ゴール19〜22 / §14.7、ledger.md 冒頭「B段実装・検証の記録」＋round-10帰結、
src/secretary_agent/{__init__,client,agent,app}.py、scripts/deploy_secretary_agent.py、
scripts/requirements-agent.txt、tests/test_secretary_agent.py、README「B-stage」節。
実機で読んだもの（設定変更なし）: Scheduler 2ジョブ、reasoningEngines/4310793666370207744 の spec、
プロジェクトIAM、demo-api-key のIAM、Agent Registry 一覧、Cloud Run の /api/secretary/sweep アクセスログ（7日）、
`.venv-agent` の ADK 2.7.1 実体（ToolContext / Gemini.api_client）。
実測1本: `run_daily_sweep`（冪等・逐次1本のみ）→ 12.4s / 200 / `{"status":"ok","tasks_evaluated":3,"cards_updated":2,...}`。

### 指摘

- [B-1] 種別: 実装 / 深刻度: high
  - 指摘: `SecretaryApiClient` の既定 `timeout=30.0` が、**実測されたリセット直後 sweep の所要 30.77 秒**より短い。収録日の手順（`--clear` 再シード → sweep）で B段トリガーが偽の失敗を出す。
  - 破綻シナリオ: Cloud Run アクセスログ（7日）の sweep 実測は、定常状態 11.7〜12.0s／08:00 JST の A段定期実行 16.6s／**本番デプロイ＋再シード直後の初回 sweep 30.77s（2026-08-22T15:56:06Z, status 200, LLM 呼び出しあり）**。初回 sweep が重いのは未処理 mail_seed の差分抽出と T2 カードのプレビュー検索が同時に走るためで、README「Demo reset & recording-day procedure」は収録前にこの状態を毎回作り直す。したがって 8/29 の朝、Scheduler `kd-secretary-sweep-runtime`（07:55）の第1試行は 30 秒でクライアント側が ReadTimeout → `SecretaryApiError` → `:query` 非2xx → Scheduler は失敗を記録して再試行、という経路にほぼ五分五分で入る。Cloud Run 側の sweep は FastAPI の同期 `def` エンドポイントでスレッドプール実行のため**クライアント切断では中断せず完走する**ので、「Cloud Run は成功しているのに Scheduler は失敗」という、round-10 C-31 が作ろうとした失敗シグナルの逆（偽陽性のアラーム）が出る。ゴール19の合格文言「1試行200・再試行なし」は、検証が行われた定常状態でしか成立しない。さらに sweep が 35 秒を超えるケース（mail_seed が増える／プレビュー対象カードが増える）では、5秒バックオフの再試行が**前の sweep の実行中**に2本目を開始する。`run_sweep` のカード生成は `find_open_card_for_task` → `save_card`（新規UUID）の read-then-write で、mail_seed も末尾で `processed=True` を書くだけの非トランザクションなので、重複した停滞カード／`stagnation_detected`・`preview_search` 監査行／重複した差分提案カードが本番デモのダイジェストに出る。
  - 提案: `SecretaryApiClient` の sweep 用タイムアウトを Scheduler の `--attempt-deadline 180s` の内側（例 150s）に上げる（`run_daily_sweep` 側で `SecretaryApiClient(timeout=150)` を渡すだけで足りる。digest 用は 30s のままでよい）。あわせて収録前チェックとして「再シード直後の sweep を1回 Runtime 経由で実行し、Scheduler 履歴が1試行成功であること」を確認手順に入れる。

- [B-2] 種別: 実装 / 深刻度: mid
  - 指摘: デプロイ既定が `min_instances=1` かつ `resource_limits cpu=4 / memory=8Gi`（実機 `deploymentSpec` で確認）で、Runtime は常時1レプリカ常駐になっている。design §14.7 の「コスト: Agent Compute は呼び出し時のみ課金」という前提と矛盾し、8/29 提出後も削除するまで課金が続く。
  - 破綻シナリオ: 1日1回 12秒の呼び出ししかない用途に対し、4 vCPU / 8GiB が 24時間×日数ぶん確保される。Agent Engine の公表単価（vCPU時間 概ね $0.1、GiB時間 概ね $0.01）で概算すると常駐分だけで 1日 $10 前後・1か月 $300 前後（**概算。請求コンソールでの確認が必要**）。ledger が記録した 503 の根本原因は「既定リソースでリクエストごとにワーカー再起動ループ」で、これに効いたのは async 化＋cpu/memory 引き上げであり、`min_instances=1` はコールドスタート短縮にしか効かない——つまり恒常課金だけが根拠なく残っている。撤退条件（§14.7）は「8/27までにゴール19・20が通らなければ削除」だけで、**通った場合に提出後どうするか（削除／min_instances=0 に戻す）が design にも README にも無い**ため、放置されて課金が続く経路が既定。
  - 提案: (a) `min_instances` の既定を 0 に戻し、再デプロイ後に Scheduler 手動発火 1本で 503 が再発しないことだけ確認する（再発するなら 1 に戻し、理由を README に1行残す）。(b) README の収録後手順に「提出・審査終了後に reasoningEngine を削除、または min_instances=0 に戻す」を追加。(c) `--cpu/--memory/--min-instances/--max-instances/--container-concurrency` の5フラグは経験的に決めた1組の値でしか使われていない——引数化をやめて定数化するか、少なくとも既定値の根拠コメントを「503対策で必要だったのは cpu/memory と async 化」に正す（現状のヘルプ文言は5つ全部が503対策だったように読める）。

- [B-3] 種別: 実装 / 深刻度: mid
  - 指摘: Runtime の `user_id` は呼び出し側の自己申告で、認証境界ではない。にもかかわらず design §14.7（C-34）と README は「本人 user_id スコープ」「owner-scoped」と書いており、Runtime 側に本人性の境界があるように読める。加えてトリガー用 SA `kd-scheduler-sa` に**プロジェクト全体の** `roles/aiplatform.user` が付いており（実機IAMで確認）、これは `aiplatform.reasoningEngines.query` だけでなく `reasoningEngines.delete/update`・`sessions.*` を含む。
  - 破綻シナリオ: `roles/aiplatform.user` を持つ主体（kd-scheduler-sa、kd-run-sa、および `roles/editor` を持つ既定 compute SA）は、Cloud Run の DEMO_API_KEY を知らなくても `:query` に `{"class_method":"async_stream_query","input":{"user_id":"emp_marcus_delgado","message":"..."}}` を投げるだけで、他人のダイジェスト本文（タスク名・質問下書き・プレビュー候補の実名と理由）を LLM 経由で読み出せ、その内容は Vertex Sessions に残る。「別 user_id のセッションから他人のダイジェストは取れない」（ゴール20）はセッション**内**のツール引数についての性質であって、セッション**の名乗り**は誰でも自由に選べる。README の「Demo-mode simplifications」は `/api/secretary/*` の本人性突合なし（round-8 S-10）しか書いていないため、S-10 の割り切りが Runtime という第2の入口（別のID体系・別の認可）に広がったことが文書化されていない。さらに、sweep を叩くだけのジョブ用IDが、B段成果物そのものを削除できる権限を持っている。
  - 提案: (a) README の割り切り節に「Runtime の user_id は呼び出し側の申告であり本人性を検証しない。Runtime を query できる IAM 主体は任意の従業員のダイジェストを読める」を1行追加し、design §14.7 の「本人スコープ」表現を「本人 user_id で分離して保存される（呼び出し側の名乗りは検証しない）」に訂正する。(b) `kd-scheduler-sa` のプロジェクトレベル `roles/aiplatform.user` を外し、`aiplatform.reasoningEngines.query` のみのカスタムロールを当該 reasoningEngine リソースに付与（設定変更は本レビューの範囲外なので未実施）。

- [B-4] 種別: 実装 / 深刻度: low
  - 指摘: `scripts/requirements-agent.txt` のピン留めが3行（google-adk / google-cloud-aiplatform / requests）だけで、`_GlobalGemini` が直接依存する `google-genai` が固定されていない（google-adk 2.7.1 の制約は `>=2.12.1,<3`）。GCS にアップされた実デプロイ用 requirements.txt にも cloudpickle/pydantic は SDK が自動追加する一方、google-genai は入っていない。
  - 破綻シナリオ: `_GlobalGemini.api_client` は `google.genai.Client(vertexai=True, project=..., location="global")` を直接組み立てる（`vertexai=` は 2.19.0 では `enterprise=` と併存する旧引数）。8/29 前に何らかの理由で再デプロイすると（例: Cloud Run URL 変更、パッケージ修正）、Runtime のイメージビルド時に解決される google-genai はローカル `.venv-agent`（2.19.0）と別バージョンになり得る。ゴール22(b)（`.venv-agent` で skip 0）はローカルの genai しか検証していないので、この差はデプロイ後の対話1本目——ゴール20の唯一の実モデル経路——で初めて表面化する。`run_daily_sweep` は LLM 非経由なので気づかないまま収録に入り得る。
  - 提案: `requirements-agent.txt` に `google-genai==2.19.0` を1行足す（ローカルとRuntimeで同一版が保証され、ゴール22(b)の意味が実機に届く）。

- [B-5] 種別: 実装 / 深刻度: low
  - 指摘: テスト13件は構造（ツール一覧・引数・登録モード・例外伝播）を押さえている一方、外部契約に触れる3点——ToolContext の `user_id` 契約、クライアントのタイムアウト値、環境変数の読み取り——はモックで置換されており、実挙動を検証していない。
  - 破綻シナリオ: `GetMyDigestToolTest` は `mock.Mock()` を ToolContext として渡すため、`.user_id` は常に存在する。ADK 側で属性名や取得元が変わっても（2.7.1 実体では `ReadonlyContext.user_id` → `_invocation_context.user_id` を継承していることを確認済み）テストは green のままで、壊れるのは再デプロイ後の対話1本目。同様に、B-1 の 30 秒タイムアウトはどのテストからも参照されていないため、値を変えても・変え忘れても回帰では気づけない。
  - 提案: (a) `SecretaryApiClient` のタイムアウトを定数化し、「sweep 用タイムアウト > Cloud Run の最悪 sweep 時間」を主張する1アサーションを置く（B-1 の修正とセットで）。(b) ToolContext は `mock.Mock()` ではなく `mock.Mock(spec=ToolContext)` にして、属性契約が消えたらテストが落ちるようにする。

### 反論しなかった点（確認して問題なしとした観点）

- **正しさ**: `run_daily_sweep` は `build_secretary_llm_agent()` にも LLM にも触れず `SecretaryApiClient.run_sweep()` を返すだけで、例外は捕捉されていない（LLM 非介入・失敗伝播は構造的に成立）。デプロイ済み spec でも `run_daily_sweep` は `api_mode: "async"`・parameters 空で、`stream_query` 系とは別枠。実測1本で 12.4s / 正しい JSON を確認。
- **正しさ（モデル location）**: ADK 2.7.1 の `Gemini` クラス docstring は「location などを固定したい場合は `api_client` を `cached_property` で override する」ことを明示しており、`_GlobalGemini` はその公式の拡張点に沿っている。`GOOGLE_CLOUD_LOCATION` を読む経路は無く、genai Client に `location="global"` を直接渡している。Python 3.14 の `cached_property` はロックを持たないため、docstring が警告するマルチスレッド競合も該当しない。落としているのは `_tracking_headers` と `retry_options`（既定 None）のみ。
- **安全性**: `demo-api-key` の `secretmanager.secretAccessor` は**シークレット単位**で kd-run-sa と Reasoning Engine サービスエージェントの2主体だけに付与されており最小。API キーは `secretEnv` 参照でデプロイコマンドにも spec にも平文で現れない。エラーメッセージは Cloud Run のレスポンスボディを含むが、sweep エンドポイントは try/except を持たず FastAPI 既定の "Internal Server Error" を返すため、カード内容・タスク名が Scheduler ログに漏れる経路は見当たらない。API キー自体はエラー文字列に入らない。
- **安全性（LLM 側の権限）**: デプロイ済み classMethods と `agent.tools` の両方で、LLM が触れるのは `get_my_digest` のみ。書き込み系（confirm/dismiss/review）も `run_daily_sweep` も LLM ツールとして存在しない＝Runtime 経由で人を巻き込む操作は構造的に不可能（FR20 はエージェント境界でも成立）。ダイジェスト本文にプロンプトインジェクションが混ざっても、到達できる副作用が無い。
- **過剰実装**: `src/secretary_agent` は3モジュール・約190行で、`knowledge_discovery` に依存せず、未使用の抽象・将来用レイヤ・起こり得ない分岐は見つからなかった（`_build_client()` の差し替え口だけがテスト用の1関数）。Agent Registry も実機で6件（4体＋A段秘書＋Runtime秘書）が能力説明つきで登録済みで、employee_id もシードと一致（ゴール21の主張は実体に裏付けあり）。過剰と言えるのは B-2 のリソース既定値と5フラグだけ。
