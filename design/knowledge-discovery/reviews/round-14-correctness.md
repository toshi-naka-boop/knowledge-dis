# round-14 反証（レンズ: 正しさ）

critic: claude design-critic（model: claude-opus-5[1m]）
対象: 9f54139..HEAD（部品A/B/C/D 実装）／基準: design.md v15 §16・§10 ゴール23〜30
検証環境: オフラインのみ（`.venv/bin/python`、src/ 変更なし）。`unittest discover -s tests` = 199 tests, OK (skipped=13)

## Round 14 — 2026-08-24 — 反証(正しさ)

### 指摘

- [V-11] 種別: 実装 / 深刻度: high
  - 指摘: `SecretaryService._sync_owners` の同期対象は「登録エージェント ∪ プロフィール所有者」だけで、`GWS_SELF_EMPLOYEE_ID` はその集合の**フィルタにしかなっていない**。所有者が Store に居ない場合、コネクタの `fetch()` が一度も呼ばれない。
  - 破綻シナリオ: ゴール28 は「**シード無しの空 InMemory** で `SOURCE_CONNECTOR=google_workspace GWS_SELF_EMPLOYEE_ID=… の sweep→digest に実データ由来のリマインド／停滞カードが現れる**」を要求する（偽陽性防止のため意図的に空ストアを指定）。空ストアでは `list_agents()==[]` かつ `list_profiles()==[]` なので `target_owners` が空集合になり、ループ本体が一度も実行されない。実測（`src/knowledge_discovery/secretary.py:352-366` を空ストアで実行）:
    `connector.fetch called for: []` / `sync_tasks=0` / `tasks in store: 0` / `digest reminders: 0 cards: 0`。
    つまり部品C/Dの手動実機ゲート（ゴール28）は**現状のコードでは構造的に達成できない**。作者アカウントで実行しても digest は必ず空になり、「コネクタが動いていない」のか「実データが無い」のか区別できないまま緑と誤認する危険もある。
  - 補足（なぜテストで落ちないか）: `tests/test_secretary.py::TestSyncThenDetect` は全ケースで `_register_owner()`（agent＋profile を先に作る）を呼んでおり、空ストア条件を一度も通していない。`scripts/gws_probe.py --apply-to-memory` も `run_sweep` を経由せず `apply_fetch_result` を直接呼ぶため、この欠陥を迂回してしまう（probe が緑でも sweep は空）。
  - 提案: `_sync_owners` で `self_only` が設定されているときは `target_owners = {self_only}` とする（フィルタではなく対象の指定にする）。併せて `TestSyncThenDetect` に「agent/profile 未登録の owner を self モードで同期できる」ケースを追加する。

- [V-12] 種別: 実装 / 深刻度: high
  - 指摘: 既定の `SeedConnector` は design §16.3 で「無操作」と定義されているが、`_sync_owners` は**コネクタ種別に関係なく全所有者に対して `apply_fetch_result` を実行する**。空の `FetchResult(complete=True)` は「完全に取得できたが 0 件だった」と解釈されるため、完全性バリアが破壊的 reconciliation を素通しする。
  - 破綻シナリオ（a・データ破壊）: 一度でも `SOURCE_CONNECTOR=google_workspace` で同期した Store に対し、`SOURCE_CONNECTOR` を `seed` に戻す（または env を渡し忘れる＝既定値）と、次の sweep で `source="gws"` のタスクが**全件 `done`**、`source="gws"` の schedule が**全件削除**される。実測:
    `task status after a 'seed' (no-op) sweep: done` / `gws schedules remaining: 0`。
    ゴール27の「消滅→done→resolved」が、消滅していないタスクに対して発火するため、収録中に env を切り替えた瞬間に停滞カードが `resolved_reason="task_done"` で静かに閉じる。
  - 破綻シナリオ（b・デモ経路のコスト）: seed 経路（=Cloud Run 本番デモ、`USE_FIRESTORE=1`）でも所有者ごとに `list_tasks(owner, source="gws")` と `list_schedules(owner, source="gws")` が走る。実測（シード投入済みストアで 1 sweep）: `list_tasks: 401, list_schedules: 400` = **801 クエリ**（変更前は `list_tasks` 1 回）。InMemory では無害だが Firestore では 800 往復の追加であり、ledger B-1 で実測済みの sweep 30.8s／クライアント timeout 120s／Scheduler deadline 180s に対して余裕を大きく削る。§16 冒頭の「demo/seed の機能結果は変えない」は満たすが、A段/B段 Scheduler ゲート（ゴール18・19）の安全余裕を実測なしに削っている。
  - 提案: `_sync_owners` の先頭で `isinstance(self.connector, SeedConnector)` なら即 return する（(a)(b) 両方が同時に消える）。あるいは `FetchResult` に「このコネクタは同期対象を持たない」フラグを持たせ、`apply_fetch_result` の破壊的 reconciliation をスキップする。

- [V-13] 種別: 実装 / 深刻度: mid
  - 指摘: `GoogleWorkspaceConnector.fetch(owner_employee_id, today)` は `owner_employee_id` を**一切使わない**（作者ADCの単一アカウントを見るだけ）。`SOURCE_CONNECTOR=google_workspace` かつ `GWS_SELF_EMPLOYEE_ID` 未設定という組み合わせに対するガードが存在せず、design が定義していない意味論のまま実行される。
  - 破綻シナリオ: この設定で sweep すると、作者1人分の Tasks/Gmail が全所有者ぶんループされ、`source_id`（`gws_task_<listId>_<taskId>` / `gws_mail_<msgId>`）に所有者が含まれないため同一ドキュメントを奪い合う。実測（3名登録）:
    `author task ended up owned by: emp_carol`（最後の所有者が奪う）/ `author mail ended up owned by: emp_alice`（`get_mail_seed` の重複判定で最初の所有者に固定）。
    結果、作者の実メール本文が**赤の他人のプロフィール差分カード**（`profile_diff` の `body_draft`／`subject`）として生成され、その他人がレビューすれば他人のプロフィールに書き込まれる。加えて所有者が毎 sweep で入れ替わるため `reschedule_count`・`created_at` が毎回リセットされ、停滞スコアが安定しない。デモ舞台では 400 プロフィールが対象になる。
  - 提案: `build_connector_from_env`（または `_sync_owners`）で「`google_workspace` かつ `GWS_SELF_EMPLOYEE_ID` 未設定」を起動時エラーにする（DWD 実装までは単独モード必須）。最低限、`source_id` に所有者を含める。

- [V-14] 種別: 実装 / 深刻度: low
  - 指摘: `IapResolver` は検証済み email を小文字化する（auth.py:211）が、`TenantRegistry` は台帳側の `email_domains` / `system_accounts` を正規化せず**受け取った文字列のまま**辞書キーにしている（tenancy.py:81-84）。design §16.1 は「email を小文字正規化 → 台帳で一意にテナント確定」としか書いておらず、台帳側の正規化が抜けている。
  - 破綻シナリオ: `TENANTS_JSON` に `"system_accounts": ["KD-Scheduler-SA@proj.iam.gserviceaccount.com"]` のように大文字混じりで登録すると、`resolve_by_system_account` が外れて human 経路に落ち、identities にも無いため 403。IAP モードで Scheduler の sweep が 08:00 JST に初めて静かに落ちる（起動時検査も通ってしまうため事前に気づけない）。`email_domains` 側では、その台帳の全ユーザーが 403 になる。
  - 提案: `TenantRegistry.__init__`（または `_config_from_entry`）で domain / system_account を `.strip().lower()` して格納する。

- [V-15] 種別: 実装 / 深刻度: low
  - 指摘: 観測値の意味が2種類混ざる／死にフィールドがある。(1) `_sync_owners` は `sync_skipped` に「単独モードでスキップした所有者数」と `SyncSummary.skipped`（＝既に取り込み済みの `mail_id` 重複数、base.py:278）を合算している。(2) `FetchResult.cancelled_ids` は `google_workspace.py` で組み立てられ docstring も「caller が reconcile するため」と書いているが、`apply_fetch_result` は一度も参照しない（「取得結果に無い＝削除」で結果的に処理されるため）。
  - 破綻シナリオ: ゴール26/28 の確認で `sync_skipped` を根拠に「他所有者が正しくスキップされた」と読むと、Gmail 有効時は重複メール数が上乗せされるため件数が一致せず、判定を誤る（あるいは誤って緑と読む）。`cancelled_ids` は将来「取消だけ別扱いしたい」と考えた実装者が参照して無効な前提を作る。
  - 提案: `sync_skipped_owners` / `sync_skipped_mails` に分ける。`cancelled_ids` は使わないなら削除するか、docstring を「参考情報。削除は不在判定で行う」に直す。

### 反論なしとした観点（確認して問題なしと判断したもの）

- **権限表 default-deny の全ルート突合**: server.py の全18ルート（`/`・`/requester`・`/candidate`・`/audit`・`/attachments/{id}`・`/api/me`・`/api/agents`・`/api/query`・`/api/requester/{id}/status`・`/api/candidate/{id}/asks`・`/api/candidate/{id}/consent`・`/api/audit/messages`・`/api/secretary/sweep|digest|confirm|cards/{id}/dismiss|profile-diff/{id}/review`・`/api/probe/unregistered-intent`）が §16.1 の表と1対1に対応し、表に無いルートは存在しない。`_deny_system` / `_deny_human` / `_require_self_*` の付与に漏れなし。
- **require_self の列挙**: query の `requester_id`、status の `requester_id`、asks/consent の agent→employee 照合（`_require_self_agent` は Store から agent をロードして `employee_id` を比較）、digest の `employee_id`、confirm/dismiss/review の card 所有者（`get_card` → `owner_employee_id`）まで、round-12 C-36/Z-1 の列挙が全て実装・テストされている。
- **consent の原子遷移**: `store.try_transition_ask_consent` が InMemory はロック下、Firestore は `@firestore.transactional` の read-modify-write で `to_entity` 一致・`connect_ask*` intent・`pending` を検証してから遷移し、`service.py` が forbidden→403 / conflict→409 / not_found→404 に写像。二重POST 409 のテストあり。
- **IAP 検証**: テストは実際に ES256 鍵ペアを生成し `google.auth.jwt.encode` で署名した本物のJWTを `id_token.verify_token` の実コードパスに通している（フェイク検証器ではない）。skew 30s の境界（±30s の正/負）、署名不正・aud/iss 不一致・期限切れ・未来iat・email欠落、鍵キャッシュのヒット／失敗時の旧鍵継続／猶予超過で例外→401 fail-closed まで負系が揃っている。`IAP_AUDIENCE` 形式検査は `build_principal_resolver` の `mode == "iap"` 分岐でのみ実行され、demo では未設定で起動できる。
- **テナント分離**: `ContextRouter.for_tenant` 以外に store を得る経路が無く、`X-KD-Tenant` 相当のヘッダも全テナント sweep も存在しない。テストは agent_id/employee_id を2テナントで**わざと衝突させて**全API（agents/query/status/asks/audit/digest/sweep）の分離を確認しており、system 主体も鍵でテナント束縛されている。`create_app` の `preset_store` は `registry is None`（単一テナント互換パス）でのみ設定され、既定テナント以外に漏れない。
- **既存の機能結果の不変**: `tests/` の差分は追加のみ（既存テストからの削除行は import 1 行の書き換えのみ）で、M1〜M3・B段の期待値は一切変更されていない。199件 OK（skip 13＝ADK未導入）。`matching.py` / `transmission.py` / `schemas.py` / `src/secretary_agent` は差分ゼロで、§16.4 の「触らない」が守られている。
- **apply_fetch_result のフィールド所有**: 初回同期で `reschedule_count` 非加算・`last_seen_due` 設定のみ・`status_changed_at = Tasks.updated`、再同期で due 差分のときだけ +1、`complete=False` で破壊的 reconciliation を行わない、mail は既存ID非再投入、Calendar は窓外/取消（＝取得結果に不在）を削除、という §16.3 の規則どおり。ただし発火条件そのものの欠陥は V-11 / V-12 を参照。
