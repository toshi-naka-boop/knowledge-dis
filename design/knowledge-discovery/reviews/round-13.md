critic: claude design-critic (model: claude-opus-5[1m])

## Round 13 — 2026-08-23 — 工程: 批評（design v13 §16 接続部品／ゴール23〜30／§12 対応表、2巡目）

読んだもの: design.md v13（§16 全体・§14.2・§10 ゴール23〜30・§12）、ledger.md「批評round-12の帰結」、spec.md v8（機能要件25〜30・完了条件14〜17）、実装 `src/knowledge_discovery/server.py`（全ルート）・`store.py`・`firestore_store.py`・`secretary.py`（`run_sweep` / `calculate_stagnation_score`）・`models.py`（Task）・`web/requester.html`・`src/secretary_agent/client.py`。round-12 の C-36〜C-40 / Z-1〜Z-6 は「修正が十分か」の観点でのみ再検討した（同一論点の蒸し返しはしていない）。

### 指摘

- [C-41] 種別: 設計 / 深刻度: high
  - 指摘: §16.3 のフィールド所有規則（C-39 の修正）が `last_updated_at` を落としており、コネクタ導入後は停滞シグナル(b)無更新日数・(d)相対停滞が実データを反映しない。
  - 破綻シナリオ: `calculate_stagnation_score`（secretary.py:126・139）は `task.last_updated_at`（無ければ `created_at`）で無更新日数と相対停滞を出す。§16.3 が定めるコネクタ書き込みフィールドは title/description/due_date/status/source/last_seen_due だけで `last_updated_at` が無い。したがって GWS から新規取り込みしたタスクは `Task` の `default_factory=utc_now_iso`（models.py:342）で「同期時刻＝いま」になり、初回 sweep では全タスクが無更新日数 0 ＝ 停滞スコアが立たない（ゴール28の停滞カードが出ない＝偽陰性）。逆に 2 回目以降、コネクタが供給元由来フィールドしか書かないなら `last_updated_at` は初回同期時刻に凍結され、Google 側で毎日更新しているタスクでも日数が単調増加して 3〜4 日で全タスクが一斉に停滞判定になる（偽陽性、朝のダイジェストが停滞カードで埋まる）。どちらに転んでも「実データで停滞が検知できた」というゴール28の主張が成立しない。
  - 提案: 所有規則に `last_updated_at` を追加し、**Tasks API の `updated` を写す（コネクタ所有）**と明記する。`status_changed_at`（初回=`updated`）と同値になるため、着手なし判定 `status_changed_at == created_at` が 0 のままである性質は崩れない。Calendar/Gmail 由来には該当フィールドが無いことも一行で書く。

- [C-42] 種別: 設計 / 深刻度: high
  - 指摘: §16.2 の不変条件「テナント横断到達が許される主体は system の sweep のみ」と、§16.1 権限表の「digest は system（`X-KD-Tenant`＋employee_id 明示）でも取れる」が矛盾しており、spec FR27「他テナントのデータに到達する経路を持たない」を満たさない。しかも system の識別子は全テナント共通の API キー 1 本である。
  - 破綻シナリオ: `DEMO_API_KEY` を持つ任意の主体（Cloud Run ログの流出、Scheduler ジョブ定義の閲覧権限、Runtime の env、README の手順を見た第三者）が `GET /api/secretary/digest?employee_id=<任意>` に `X-KD-Tenant: <任意テナント>` を付ければ、テナントBの任意社員の停滞カード（タスク名・evidence_line・プレビュー候補の氏名と理由）を読める。sweep は件数しか返さないので越境の実害は小さいが、digest は本文であり、ゴール25「テナントAのカード・監査行がテナントBの全APIから見えない」を満たすのは human 主体だけになる。分離の主張を write-up に書けば、審査軸「認証情報のセキュリティ」で自己矛盾を突かれる。
  - 提案: (a) 台帳エントリに system キー（またはそのハッシュ）を持たせ、**キー→テナントを一意に決めて `X-KD-Tenant` を廃止**する（Runtime はテナントごとに 1 デプロイなのでキーも 1 本ずつでよい。全テナント sweep 用の運用キーだけを別に定義する）か、(b) system の digest を「`KD_TENANT_ID` で固定されたテナント内に限る」と明記し、越境は sweep の件数のみ、と不変条件を書き直す。いずれにせよ §16.2 の一文と権限表の digest 行を同じ文言に揃えること。

- [C-43] 種別: 設計 / 深刻度: high
  - 指摘: `iap` モードの system 主体（X-API-Key）は、§16.1 が README に書くと宣言している本番構成（Cloud Run IAP 統合＋`--no-allow-unauthenticated`＝IAP を経由しない経路を残さない）と両立しない。C-40/Z-4 で「機械主体を足した」ことが、IAP がエッジで前段に立つ事実と突合されていない。
  - 破綻シナリオ: IAP を掛けたサービスでは、Google 発行トークンを持たないリクエストは IAP が 401/302 で落とすため、アプリの `PrincipalResolver` まで届かない。A段 Scheduler は現状 API キーヘッダのみ（CLAUDE.md・ledger の運用記録）、B段 Runtime も `client.py:_headers` が `X-API-Key` 1 本だけ（`src/secretary_agent/client.py:48-49`）なので、iap 構成に載せた瞬間に両方が sweep に到達できず、朝のダイジェストが無音で消える。逆に機械側が IAP を通るよう OIDC トークンを付ければ、IAP は SA メール（`kd-scheduler-sa@<project>.iam.gserviceaccount.com`）入りのアサーションを注入するので、解決順 (a)→(b) により **human 判定**になり、ドメインが台帳に無く 403 になる。つまり「system 主体」は、IAP を掛けない構成でしか成立しない。
  - 提案: どちらかを選んで明記する。(1) iap は human 向けサービス（別 Cloud Run サービス）にのみ掛け、機械経路は現行のキー保護サービスに残す＝「IAP を経由しない経路を残さない」という文言を撤回し「人間向け経路は IAP のみ」に直す。(2) 機械も IAP を通す前提にし、解決順を「アサーションの `email` が SA 許可リストに一致 → system（テナントは (1) のキー or `X-KD-Tenant`）、それ以外 → human」に変更する。あわせてゴール26の「Scheduler／Runtime の起動経路は無変更で動く」が (2) では偽になる点も直すこと。

- [C-44] 種別: 設計 / 深刻度: mid
  - 指摘: `reschedule_count` の更新規則（C-39 の修正）に `last_seen_due` の**初期値**が定義されておらず、素直に実装すると初回同期で全タスクが +1 される。ゴール27のテスト項目（再同期で不変・due 変更で +1）では検出できない。
  - 破綻シナリオ: 新規取り込みタスクは `last_seen_due` 不在（None）で、取得した due（例 2026-08-25）と「異なる」ため規則どおり +1 され、`reschedule_count=1` から始まる。デモ台本の「期日を 2 回延ばし」は実データ上 1 回延ばした時点で成立してしまい、evidence_line が事実と食い違う。加えてリスケ 1 回分のスコア底上げが 400 件全体に一律にかかるため、T1/T2 のシード較正値がそのまま使えず、ゴール28で「実データ由来の停滞カード」が出ても、それが本物の停滞かオフセットの産物か区別できない（偽陽性の検証不能）。
  - 提案: 「`last_seen_due` が未設定（初回同期）のときは `reschedule_count` を増やさず `last_seen_due` の記録のみ行う」と一行足し、ゴール27のテスト項目に「初回同期で `reschedule_count=0`」を追加する。

- [C-45] 種別: 設計 / 深刻度: mid
  - 指摘: §16.1 の「漏れを作らない」権限表が server.py の実ルート一覧と突合されていない（`POST /api/probe/unregistered-intent` が未列挙、`/attachments` の説明が実体と不一致）。また `IAP_AUDIENCE` の起動時形式検査に適用条件が書かれていない。
  - 破綻シナリオ: (1) `POST /api/probe/unregistered-intent`（server.py:501）は送信層を通して `messages` に拒否行を書き込む書き込み系だが、権限表の「human-facing の書き込み（query/confirm/dismiss/review/consent）」にも「system のみ」にも入っていない。iap で認証された一般社員が任意回数叩けば、テナントの監査ログに `from_entity="demo_probe"` の赤行を無制限に注入できる。本プロダクトの中心的主張は監査ログなので、そこに任意主体が書ける経路が表の外にあるのは主張の穴になる。(2) `/attachments/{doc_id}` は現状 API キー検証すら付いておらず（server.py:255）、中身はモジュール定数 `SAMPLE_ATTACHMENTS` でテナントデータではない。表の「テナント内の可視」という記述はどちらの意味でも実体と合っておらず、実装者が「テナント別に添付を引く」と誤読すると存在しない分岐を作る。(3) 「起動時に正規表現で形式検査」を条件なしで実装すると、`AUTH_MODE=demo_key` で動いている本番デモサービス（`IAP_AUDIENCE` 未設定）が次回デプロイで起動失敗し、収録直前に本体が落ちる。ゴール26の文言も無条件検査に読める。
  - 提案: 権限表に probe 行（system/demo のみ、または human 可を明示）と `/attachments` 行（認証要否を明示。テナント非依存の静的資料である旨）を追加し、`IAP_AUDIENCE` 検査は「`AUTH_MODE=iap` のときのみ」と限定してゴール26も同じ条件に直す。

### 指摘に至らなかった確認結果（観点3・4への回答）

- **round-12 修正の十分性**: require_self の列挙は server.py の 11 ルートと突合し、C-45 の probe/attachments 以外に漏れなし（query の `requester_id`、card_id 受け 3 本の所有者ロード照合はいずれも実体と整合）。done→resolved の遷移は §14.2 の状態機械（`task.status == "done"` → open カードを `resolved`／secretary.py:362-370）で成立し、`confirmed`/`dismissed` は先に弾かれるため「削除されたタスクを done にする」reconciliation で古いカードが不正に閉じることはない。Calendar 窓外削除・`showCompleted/showHidden`・Gmail 既定 OFF（`GWS_GMAIL_ENABLED=false`・ラベル opt-in・保持期限・ログ非出力）は記述として閉じている。テナント解決（小文字正規化・ドメイン一意の起動時検証・未登録 403・identities 不在 403）、human が `X-KD-Tenant` を付けても無視される点、`static_counts` のテナント文脈化（現状も初回 audit 要求時の遅延キャッシュなので挙動差なし）は問題を見つけられなかった。
- **実装規模と 8/27 撤退線**: §16.4 の変更範囲（auth.py / tenancy.py / connectors 3 本 / server.py 全ルートの主体化 / store 両実装への identities・source・last_seen_due・削除系追加 / seeds / UI / client.py / README）に加え、テストが実鍵 ES256 の JWT・権限表・2 テナント横断ゼロ・コネクタ写像と reconciliation・冪等と広い。平日夜のみの 4 日で全部は入らない見込みが高い。§16.5 の撤退は「全部 or 部品のみ」の粒度しか無いので、**部品単位の優先順位と中間ゲートを先に決める**ことを勧める（推奨順: 認証差し替え点＋権限表＋require_self → コネクタ（Tasks のみ・self モード）→ テナント（InMemory 2 テナントまで。実 Firestore 第2DB とゴール25後半は任意）→ Calendar → Gmail は着手しない）。ゴール28の「シードを入れない空の InMemory ストア」は、現状 `create_app` が store 未指定なら必ず `populate_store` を呼ぶ（server.py:181-186）ため、シード無しで起動する env スイッチが要る。§16.4 の変更範囲に一行加えるか、ゴール28を「専用スクリプトで空ストアを組む」と書き直すのが安い。
- **既存テスト不変**: `create_app(store=, service=, llm_client=)` の注入を ContextRouter の既定テナント文脈に写像できれば 97 件は不変に保てる（注入された store を無視して router が自前生成する実装にすると全滅するので、そこだけは実装時の明示的な約束事にすること）。B段 13 件は `client.py` にヘッダ 1 つ増えるだけ、sweep 応答は `resp.json()` の素通しなので、テナント別件数への戻り値変更でも壊れない。なお `requester.html` は `api_key` をクエリから取り、無ければ赤帯「Missing api_key」を出す（requester.html:169-172）ので、iap モードでは常時この帯が出る。`/api/me` 対応の際に帯の条件を mode 依存にすること（挙動そのものは害なし）。
