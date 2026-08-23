critic: claude design-critic（＋ codex/gpt-5.6-sol xhigh クロスベンダー並走。原文: round-10-codex-raw.txt、指摘Y-1〜Y-5）
model: claude-opus-5[1m]

## Round 10 — 2026-08-23 — 工程: 批評（design v10 §14.7 B段追補）

対象: design.md v10 の §14.7 B段ブロック、§8対応表のトリガー行、§10 ゴール19〜22、§15 のB段項目。
前提として読んだが蒸し返さない: v9承認済み部分（A段本番稼働、反証round-8/9クローズ）、ledger の却下済み前提（秘書の自律配送化・停滞検知のLLM判定化）、S-10（/api/secretary/* の本人性突合なしはデモ割り切りとしてREADME明記済み）。
参照した実装: `src/knowledge_discovery/server.py`（`/api/secretary/*` は全て `verify_api_key` 依存、digest は `employee_id` クエリ引数）、`src/knowledge_discovery/secretary.py`（`run_sweep` は cards/messages/mail_seeds を書き換える。`get_morning_digest` は card payload をそのまま返す＝`task_title`/`question_draft`/`preview.candidates[].employee_id, reason_text` を含む）、`README.md`（A段ジョブは `--attempt-deadline=180s`、`X-API-Key` ヘッダ）、venv に `google-cloud-aiplatform` / `google-adk` は未導入（`google-genai` のみ）。

### 指摘

- [C-31] 種別: 設計 / 深刻度: high
  - 指摘: B段のトリガー経路は「LLMが `run_daily_sweep` を呼ぶか」という非決定要素に依存する一方、Schedulerの成功判定は HTTP 200 しか見ないため sweep 不実行を検知できず、さらに A段ジョブを pause して決定的な担保まで外している。三重に失敗が無音化する。
  - 破綻シナリオ: 収録前夜の定期実行で、Gemini がツールを呼ばずに "I'll run the daily routine now." とだけ返す（またはCloud Run側 sweep が 500 を返し、§14.7の方針どおり Runtime が「そのままテキストで報告」する）。どちらの場合も `:streamQuery` の HTTP ステータスは 200 のままで、Scheduler の実行履歴は成功と表示される。A段ジョブは paused なのでバックアップの sweep も走らない。収録当日の朝、Jordan のダイジェストに停滞カードが1枚も無く、デモ冒頭シーン（§11-1）が成立しない。原因調査は「Schedulerは成功している」から始まるため時間を溶かす。8/27の撤退判断も、ゴール19の「Schedulerの試行が成功」という条件だけを見ると偽陽性で通ってしまう。
  - 提案: (a) A段ジョブ `kd-secretary-sweep` は pause せず、B段ジョブより前の時刻（例 A段 03:00 / B段 03:30）で稼働継続する。sweep は §14.2 の状態機械により冪等なので二重実行は無害で、「Runtimeが実際に叩いた」因果は Cloud Run ログの時刻突合で示せる（ゴール19の証拠能力は落ちない）。(b) ゴール19の合格条件から「Schedulerの試行が成功」を単独条件として外し、**Cloud Run ログに B段ジョブ時刻の `/api/secretary/sweep` 200 が出ること**を必須条件にする。(c) 「A段ジョブが paused であること」という確認項目は削除するか、`kd-secretary-sweep-runtime` の発火時刻に対応するログ行の存在に置き換える。

- [C-32] 種別: 前提 / 深刻度: mid
  - 指摘: spec v7 の制約「Agent Registry を実採用」の充足根拠が、B段では「SDKデプロイで自動登録される」の一点に依存しているが、その Registry の実体（`reasoningEngines.list` に出ることなのか、§14.7が別途「手動登録手順は実装時に確認」と書いている Agent/Service リソースのレジストリなのか）が同一文書内で確定していない。同じ文書が「手動登録APIの具体手順は未確認」と認めている以上、自動登録先が同じレジストリだという前提も未検証である。
  - 破綻シナリオ: 実装当日に確認したところ、自動登録の実体は「Agent Engine にデプロイ済みリソースが一覧に出る」だけで、GEAP Agent Registry（Agent/Service リソース）には何も登録されていない。ゴール21は「Console または API 一覧で確認できる」と緩いのでチェックは通ってしまい、write-up には「Agent Registry 実採用」と書かれたまま提出される。GEAPを知る審査員が一覧の実体を突けば、B段の主要な価値主張(c)が「Agent Engine にデプロイしただけ」に瓦解し、round-1で懸念された theater 判定を、よりによって統制の主張で受ける。
  - 提案: 実装初日（デプロイ前でよい）に Registry 側の一覧API を1回叩き、(i) Runtime秘書がそこに現れるか (ii) 現れないなら手動登録で `Agent` リソースを1件作れるか、を確認して結果を design か README に事実として記録する。どちらも通らない場合は「Registry実採用」を主張せず「Agent Engine デプロイ（Registryは将来項目）」と書き換える判断を 8/27 の撤退条件と同じゲートに載せる。ゴール21の判定文も「Registryの一覧APIのレスポンスに当該エージェントのリソース名が含まれる」と機械判定可能な形に締める。

- [C-33] 種別: 設計 / 深刻度: mid
  - 指摘: 「書き込み系ツールを持たせない」と整理しているが、同居させる `run_daily_sweep` 自体が状態変異操作（cards の生成/昇格/resolve、mail_seeds の `processed=true`、messages への監査行追加、プレビュー用Gemini呼び出し）であり、対話セッションからも呼べる。境界は「配送しない」であって「読み取り専用」ではない。
  - 破綻シナリオ: 収録中、ゴール20のデモとして `async_stream_query(user_id="emp_jordan_lee", message="What's on my plate today?")` を投げる。モデルが instruction を厳密に守らず「最新化してから答える」つもりで `run_daily_sweep` も呼ぶ。その sweep で (i) 直前に手で done にしたタスクのカードが `resolved` になって画面から消える、(ii) Marcus の `mail_seeds` が `processed=true` で消費され、後で撮る予定だった差分提案カードのシーン（§14.5 / ゴール16）の素材が無くなる、(iii) 監査画面に予定外の `preview_search` 行が増える。いずれも原状復帰にはシード再投入（`--clear`）が要り、収録が巻き戻る。
  - 提案: `run_daily_sweep` の関数先頭で呼び出し元を1行ガードする（例: `user_id != "scheduler"` なら実行せず「定期実行専用」と返す。ADKのツールコンテキストから user_id を取れない場合は、Schedulerが送る message に含める合言葉ではなく、デプロイを2本（scheduler用エージェント／対話用エージェント）に分ける）。設計側は「Runtime秘書のツール境界は『配送権限なし』であって読み取り専用ではない」と明記し、対話経路から sweep を起動できないことを §14.7 の責務分割に加える。

- [C-34] 種別: 設計 / 深刻度: mid
  - 指摘: Runtime秘書はダイジェスト本文を「DEMO_API_KEY を知る者」の境界の外に出す。(a) `get_digest(employee_id)` は任意の employee_id を受けるため、APIキーを知らなくても `roles/aiplatform.user` を持つだけで他人のダイジェスト内容を取得できる（Runtimeが自分のsecretでAPIキーを注入する＝confused deputy）。(b) B段の価値主張(d)「Cloud Observability の自動収集」とSessionsにより、§14.6が監査画面で意図的にマスクしている `task_title` / `question_draft` / 候補名が、マスクされていない第2のコピーとしてトレース・セッション履歴に残る。A段のS-10（本人性突合なし）はAPIキー境界の内側の割り切りだったが、B段はその境界自体を1段緩める点で同値ではない。
  - 破綻シナリオ: プロジェクトに `roles/aiplatform.user` を持つチームメンバー（またはデモ用に招いた審査担当）が `:streamQuery` に "Show me the digest for emp_marcus_..." と投げ、Marcus の停滞タスク名・AI生成の質問下書き・打診候補の実名と理由を読む。監査ダッシュボードでは同じ内容が「内容非表示」でマスクされているため、write-up の「事実は記録され、内容は本人にしか見えない」という中核主張と、実際に読める経路が矛盾する。反証レンズで最初に突かれる。
  - 提案: 最小対応は文書化（README のデモ割り切り節に「Runtime経由のdigestは本人性突合なし・トレースに内容が残る」を S-10 と並べて明記し、§14.6 のマスク主張の適用範囲を『Cloud Run の監査画面について』と限定する）。設計対応をするなら `get_digest` の引数を廃してデモ固定の1名（Jordan）に束ねるか、Runtime秘書のツールを `run_daily_sweep` のみにして対話デモは SDK からの直接呼び出しで見せる。

- [C-35] 種別: 実装 / 深刻度: mid
  - 指摘: デプロイ手順に、Agent Engine 特有の失敗要因が3つ欠けている。(i) ローカルモジュール（`src/knowledge_discovery/secretary_agent/`）はcloudpickleが参照で固めるため `extra_packages` 指定がないとRuntime側で import に失敗する、(ii) ツールが叩く Cloud Run ベースURL の env_vars（APIキーの注入だけが書かれている）、(iii) Schedulerジョブの `--attempt-deadline`（A段はREADMEで180s明示、B段は無指定＝既定180s）。加えて `google-cloud-aiplatform[agent_engines,adk]` は現在の venv に未導入の新規依存であり、初回導入で解決に時間を取られる可能性がある。
  - 破綻シナリオ: `agent_engines.create` は成功して resource ID を README/state.json に記録するが、最初の `:streamQuery` が `ModuleNotFoundError: knowledge_discovery` で落ちる。Agent Engine のデプロイは1回あたり数分〜十数分かかるため、原因切り分け→再デプロイの往復で 8/27 のゲートを使い切り、B段が「時間切れで撤退」になる。ベースURL未設定の場合は同様にデプロイ成功・実行時404/接続エラー。attempt-deadline については、sweep が4名分のプレビュー検索＋Gemini呼び出し＋差分抽出を直列に回すため、Runtime側のLLMターンを足すと既定180sに接近し、超えるとSchedulerが失敗としてリトライ（sweep自体は冪等なので害はないが、ゴール19の「試行が成功」が落ちる）。
  - 提案: `scripts/deploy_secretary_agent.py` に (a) `extra_packages` の指定、(b) `env_vars` にCloud RunベースURLを追加、(c) デプロイ直後に `remote_agent.stream_query(...)` で1往復のスモークを実行し、成功したときだけ resource ID を state.json / README に書く、という3点を手順として §14.7 に明記する。Schedulerジョブには A段と同様に `--attempt-deadline` を明示（sweep実測時間を測ってから決める。A段の実測値が使える）。

### 肯定的所見（賛成の根拠と、その最も危うい前提）

- 「検知・状態機械・プレビュー・confirm はCloud Runに残す」という責務分割は、theater批判に対する正しい防御になっている。Runtimeに載せた途端に停滞判定をLLMに移していたら ledger の却下済み前提（停滞検知のLLM判定化）を裏口から復活させることになるが、v10はそれを明示的に避けている。ただしこの整理が誠実さを保つのは「Runtime秘書に固有の責務がある」と言える限りであり、現状の固有責務は実質「対話でのダイジェスト要約」1点である。write-up でB段の価値を語るときは、今回のデモで実証できたのは (c) Registry登録と (d) 可観測性であって (a) Memory Bank・(b) アイデンティティ・(e) A2A は未実装の将来項目である、と分けて書くこと（分けずに列挙すると、最も危うい前提＝C-32のRegistry実体と合わさって過大主張になる）。
- ツールから confirm / dismiss / profile-diff review を外した判断は妥当。FR20（配送権限なし）はCloud Run側のAPI境界で既に担保されているが、エージェント境界でも二重化しておくと、プロンプトインジェクション（タスク説明文やメール本文がLLMコンテキストに入る経路）が成立しても「人を巻き込む操作」に到達できない。この二重化の主張はC-33のガードを入れて初めて完全になる。
- A段ジョブを削除せず残す方針（フォールバックの存在）と、8/27の撤退条件を日付付きで書いていることは、締切に対する設計として適切。B段の追加物（エージェント1本＋ツール2本＋デプロイスクリプト）は過剰実装には当たらないと判断する（ゴール19〜22は全て spec 制約「GEAP採用」にトレースでき、UI・既存フローへの変更は0）。
