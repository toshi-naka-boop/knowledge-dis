critic: claude design-critic (claude-opus-5[1m])

## Round 12 — 2026-08-23 — 工程: 批評（design.md v12 §16「接続部品」／§10 ゴール23〜29／§12 FR25〜30）

対象外（前提として読み、蒸し返さない）: v11承認済みのA段/B段、ledger の解決済み索引・却下済み前提（秘書の自律配送化・停滞検知のLLM判定化・tenant_id列方式）。

### 指摘

- [C-36] 種別: 設計 / 深刻度: high
  - 指摘: `require_self` の対象を「employee_id を受ける全API」と定義したため、**識別子を employee_id 以外の名前で受ける書き込み系4本が iap モードでも無防備**に残る（`/api/query` の `requester_id`、`/api/secretary/confirm` の `card_id`、`/api/secretary/cards/{card_id}/dismiss`、`/api/secretary/profile-diff/{card_id}/review`）。spec FR26 は confirm / dismiss / 差分レビューを名指ししており、設計の列挙がそれを落としている。
  - 破綻シナリオ: `AUTH_MODE=iap` のテナント内で、社員B（正当にIAP認証済み）が他人のカードIDを推測または監査画面経由で知り `POST /api/secretary/confirm {card_id: <Aのカード>, edited_question: "..."}` を送る。`SecretaryService.confirm_stagnation_card`（secretary.py:701）は `requester_id=card.owner_employee_id` で既存の質問投入経路を走らせるため、**Aの名義で実在の候補者に `connect_ask` が実配送される**（人を巻き込む・取り消し不能。FR20「人を巻き込むのは本人確定のみ」が認証層で破れる）。同様に `profile-diff/{card_id}/review` で **Aのプロフィールに項目を追加**（public 既定）でき、`dismiss` でAの停滞カードを消せる。`/api/query` も `requester_id` を申告できるため他人名義の質問投入が可能で、`/api/requester/{id}/status` だけ 403 にしても投入側が開いている。
  - 提案: 列挙方式（fail-open）をやめ、**「principal と突合しないルートのホワイトリスト」（`/api/me`・`/api/agents`・`/api/audit/messages`・UI/静的）以外はすべて突合必須**に反転する。card_id 系は Store に `get_card_owner(card_id)` を足して所有者解決→突合。`/api/query` は iap モードでは `requester_id` を無視して principal.employee_id を使う（申告を受け付けない）。ゴール23に「アプリの全ルートを走査し、ホワイトリスト外に突合が掛かっていることをテストで機械的に確認する」を加える（人手の列挙は次の追加APIで再び漏れる）。

- [C-37] 種別: 設計 / 深刻度: high
  - 指摘: §16.3 の Tasks 写像は「**全タスクリストの未完了タスク**」を取ると規定しながら、同じ段落で「Tasks側で完了→`done`」と書いており両立しない。完了・削除されたタスクは API 応答から消えるだけなので、コネクタはそれを観測できない。
  - 破綻シナリオ: 実データ運用で Jordan が停滞タスクを Google Tasks 上で完了にする。次回 sync の応答に当該タスクは現れず、store のレコードは `status="todo"` のまま。§14.2 の状態機械が `resolved` にする条件は「task done」か「score < T1」のみで、`overdue_days` と `stale_days` は毎日増え続ける（secretary.py:120-128）ため score は下がらない。結果、**終わった仕事の停滞カードが digest に永久に残り、毎朝「まだ止まっています」と催促する**。「監視者ではなく秘書」というプロダクトの位置づけが実データで真っ先に壊れる。ゴール27（同一入力の再同期で重複しない／due 変更で +1）もゴール28（件数確認）もこの経路を踏まない。
  - 提案: `tasks.list` を `showCompleted=true&showHidden=true` で取り、`status: "completed"` を `done` に写像する。加えて「同期結果に現れなかった既存 `gws_task_*` は done 扱い（＝カードを resolve）」を §16.3 に明記する（削除・リスト移動の吸収）。ゴール27に「完了/消滅したタスクのカードが次の sweep で resolved になる」を追加。spec FR29 の「未完了タスクの」も同文で追随が必要。

- [C-38] 種別: 設計 / 深刻度: mid
  - 指摘: §16.2 は同一セクション内で矛盾している。「他テナントの store を参照するコードパスは存在しない（横断クエリを書く場所がない）」と「sweep は台帳の**全テナント**を順に処理し、テナント別件数を返す」。sweep は設計が自ら作る横断経路であり、しかも employee_id を取らないので `require_self` を掛けられない。テナント境界の主張が「リクエスト経路に限る」と限定されていない。
  - 破綻シナリオ: (a) `AUTH_MODE=iap`・2テナント構成で、テナントAの一般社員が `POST /api/secretary/sweep` を叩くと、テナントBのタスク検知・プレビュー検索（候補ごとに Gemini 呼び出し＝課金）まで走り、レスポンスの**テナント別件数でテナントBの稼働状況（社員数規模・停滞件数）が漏れる**。(b) demo_key モードでも鍵1本が全テナントの sweep 起動権になる。(c) 併走する既存のアプリレベル状態が置き去りになる: `server.py` の `static_counts`（audit エンドポイント内のクロージャ、初回リクエストで確定する profiles/agents 件数）はテナント別ではないため、**テナントBの監査ファネルにテナントAの母数（400件等）が表示される** — ゴール25「テナントBの audit から見えない」を素通りで突破する。§16.4 の「触る」一覧に server.py 内のこの種のキャッシュが含まれていない。
  - 提案: (1) 到達不能性の主張を「**ユーザーリクエスト経路に限る**」と明記し、sweep を唯一の意図的例外として宣言する。(2) sweep は既定で principal.tenant_id のみを処理し、全テナント処理はサービス主体（C-40 の第3 Resolver / Scheduler 専用資格）に限定する。(3) `static_counts` を含むアプリ内キャッシュを TenantContext 側へ移すことを §16.4 に列挙し、ゴール25の検査対象に funnel_stats を含める。

- [C-39] 種別: 設計 / 深刻度: mid
  - 指摘: コネクタが上書きするレコードのうち「Google 側に存在せず秘書が持つフィールド」（`reschedule_count` / `created_at` / `status_changed_at`）の維持規則と、due の比較正規化が未定義。「冪等」の定義がゴール27では「同じ入力の再同期で重複しない」（＝ドキュメント重複なし）にとどまり、**カウンタの冪等性を要求していない**。
  - 破綻シナリオ: (a) Google Tasks の `due` は RFC3339（`2026-08-25T00:00:00.000Z`）、store の `due_date` はシード由来の日付文字列。正規化せず「既存レコードの due と異なれば +1」を適用すると**毎回 sync で reschedule_count が +1**。しかも A段（08:00）とB段（07:55）のジョブが並走（ledger でクローズ済みの確定構成）なので**1日 +2**。数日で全タスクが T2 を超え、全タスクでプレビュー検索（候補ごとに Gemini 呼び出し）が走り、digest がリクエスト案で埋まる。(b) 再同期で既存ドキュメントを丸ごと上書きすると `reschedule_count` と `created_at` がリセットされ、FR29 の「秘書が前回見た期日を覚えて数える」が成立しない。(c) untouched シグナルの実装は `status=="todo" and status_changed_at == created_at`（secretary.py:147、文字列の完全一致）。§16.3 は「`created_at` は初回同期日時、`status_changed_at` は status 変化を検出した同期日時（着手なしシグナルは実質0）」と書くが、初回同期で両者に同じ同期時刻を入れれば**1 が立つ**（同じ変数を使い回すか `now()` を2回呼ぶかで結果が変わる非決定）。実データの全 todo タスクに `W_UNTOUCHED` が一律加算され、シードで較正した T1/T2 が意味を失う（ゴール28で「実データ由来の停滞カード」が出ても、それが正しい検知かノイズか判別できない）。
  - 提案: §16.3 に「sync が更新するのは title/description/due_date/last_updated_at/status のみ。`created_at`・`reschedule_count` は既存値を保持。初回同期の `status_changed_at` は `last_updated_at`（≠`created_at`）を入れ、untouched を確実に 0 にする」「due は日付へ正規化してから比較し、due 無→有・有→無は +1 に数えない」を明記。ゴール27に「due 不変の再同期で `reschedule_count` が増えない」「同期直後の untouched シグナルが 0」を追加する。

- [C-40] 種別: 設計 / 深刻度: mid
  - 指摘: `PrincipalResolver` が `demo_key` / `iap` の**排他的グローバル2択**であり、機械主体（Cloud Scheduler A段の `X-API-Key` ヘッダ、B段 Runtime の `KD_API_KEY` → `/api/secretary/sweep`・`/api/secretary/digest`）に対応する principal が設計に存在しない。§16.1 は「IAP を経由しないアクセス経路を残さないことは Cloud Run 側の設定で担保」と書くが、その設定下で動く機械経路の設計がない。
  - 破綻シナリオ: `AUTH_MODE=iap` に切り替えた（IAP を実際に前段に置いたかに関わらず、DemoKeyResolver が無効になる）瞬間、`src/secretary_agent/client.py` の `X-API-Key` ヘッダは JWT アサーションを持たないため 401 となり、**A段・B段の朝の sweep が両方とも停止する**（B段は ledger で本番稼働・クローズ済みの成果）。結果、spec v8 の改訂経緯が掲げる「IAPモードでは本人性が突合され S-10／B-3 が閉じる」は、**閉じた状態では秘書のトリガーが一切動かない構成**でしか成立しない。この矛盾を認識せずに write-up へ「本番構成では IAP で本人性が閉じる」と書くと、同じ write-up の Agent Runtime 節と自己矛盾する。
  - 提案: §16.1 に第3の Resolver（`ServiceResolver`: Scheduler/Runtime 専用の別シークレット、または将来の OIDC）を置き、`AUTH_MODE=iap` は**人間UI経路にのみ適用**すると明記する（機械経路のパスプレフィックス `/api/secretary/sweep` を分離するのが最小）。ゴール23の文言も「人間経路のAPIについて」と限定し、加えて「iap モードでも `run_daily_sweep` 相当の呼び出しが 200 で通る」を検査に加える。実装しない選択を採るなら、§16.5 の撤退線に「iap モードは秘書トリガーと排他（write-up で明記）」を既知の制限として書く。

### 反論なしの場合
（該当なし）

### 確認して問題なしとした点（参考・指摘には数えない）

- IAP JWT 検証の依存関係: 本リポジトリの `.venv` で `cryptography 50.0.0` が **google-auth 2.56.3 の必須依存**として入っており（`pip show cryptography` の Required-by: google-auth）、`google.auth.crypt.es256` が import 可能。`google.oauth2.id_token.verify_token(..., certs_url=..., audience=...)` の署名も `certs_url` を受ける。ES256・`https://www.gstatic.com/iap/verify/public_key`（kid→PEM）・audience env 化という §16.1 の構成は成立し、「新規パッケージなし」の主張は**正しい**（`AuthorizedSession` に必要な `requests` も requirements.txt に既存）。ただし `verify_token` は `iss` を検査しないので、`iss == "https://cloud.google.com/iap"` の明示チェックを実装時に足すこと（low、指摘には数えない）。
- テナント＝DB分離の骨格: `FirestoreStore.__init__` が既に `database` 引数を持ち（firestore_store.py:39）、テストのアプリ生成箇所は `tests/test_server.py:164` の1箇所のみ。ContextRouter 化で既存97件を書き換えずに済む見込みは高い（「回帰の定義」は守れる）。
- プレビュー無痕跡・fail-closed マスク・配送/同意/監査の不変性: §16 は matching / transmission / schemas に触れないと宣言しており、上記4指摘のいずれの修正案もその境界を越えない。
