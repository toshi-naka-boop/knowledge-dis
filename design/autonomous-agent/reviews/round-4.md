critic: design-critic (Opus 5, verification round)

## Round 4 — 2026-08-28 — 工程: 批評（verification round / design v4 fingerprint v4:32c0ea35c097）

検証範囲: v4 で新設・変更された機構のみ（resolved 再オープン / failed 状態と 500 契約 / upsert_card_gated outcome 5値 / create-only audit / policy_hold の再開条件 / mail LLM の Monitor 配下化）。新しい設計案は提示せず、回帰・矛盾・未解決 High の有無だけを見た。実在確認は `src/knowledge_discovery/{secretary,store,firestore_store,transmission,schemas,server,tenancy}.py` と `web/{requester,audit}.html`、`connectors/google_workspace.py`、`tests/test_secretary.py` の静的読解。実測（サーバ起動・Firestore 実行）は行っていない（high 候補の白黒はコード読解で確定できたため）。

台帳の解決済み 22 論点（C-1..C-17 / X-* / Y-* / Z-*）およびユーザー裁定 Q-1（counts-only）は再提起していない。

---

### 指摘

- [C-18] 種別: 設計 / 深刻度: **high**
  - 指摘: 決定的 card_id への切替に**移行手順が無く**、`find_open_card_for_task`（クエリ検索・`limit(1)`）と `upsert_card_gated`（決定的 id 直指定）が別の doc を指すため、既存の乱数 id カードが残る環境で open カードが二重化する。
  - 破綻シナリオ: 現在 Cloud Run 上の `cards` コレクションには `card_stag_<uuid4[:10]>` 形式の open カードが既に存在する。v4 をデプロイし `kd-autonomous-sweep`（`*/30`）が reseed 前に初回発火すると、各停滞タスクについて (a) `find_open_card_for_task` が legacy id のカードを返して `open_card is not None` 分岐に入り、(b) その結果を `card_stag_sha1(owner:task_id)[:12]` という**別 doc id** に書く。以後 legacy doc と決定的 doc の両方が `status=open` で残り、`limit(1)` は順序未指定なのでどちらが返るかも不定。My Agent の `stagnation_cards` は両方を返すため、同一タスクが NEED DETECTED と Watching 行に二重表示される（`renderNeedCard` は card_id ごとに描画）。design §0-2 が前提として挙げた single-open-card 不変条件が壊れ、収録直前 reseed（§10）より前に発火した分は自然回復しない。
  - 提案: §7/§11 に「決定的 id 移行は `cards` の事前 clear（または legacy id → 決定的 id の一括リネーム）が前提。job 作成は reseed 後」を明記するか、`upsert_card_gated` 呼び出し時に `find_open_card_for_task` が既存カードを返した場合はその `card_id` を優先して使う（新規作成時のみ決定的 id）。
  - **局所修正で解消可能: 可**（§7/§11 に前提1行＋デプロイ手順の順序変更、またはヘルパ内 3 行）。architecture 変更不要。

- [C-19] 種別: 設計 / 深刻度: **high**
  - 指摘: §5.3 のゲート表が「パイプラインをどこまで進めるか」だけを規定し、**既に `request_draft` へ昇格済みの open card を policy が後から巻き戻さない**という不変条件を明文化していない。§9 のテスト一覧にも該当ケースが無い。
  - 破綻シナリオ: 現行 `secretary.py:542` の「`open_card.tier == "request_draft"` なら score/evidence だけ更新して `continue`」という short-circuit が、v4 のゲート挿入位置次第で policy 判定の**後ろ**に回る実装が成立する。その実装では、全 ON で NEED DETECTED（Marcus 入り `payload.preview`）まで進んだカードがある状態でユーザーが Search を OFF にすると、`policy_updated_at` 変化により hold が無効化 → 次の scheduled run が「search OFF = notice まで」を適用 → `upsert_card_gated` の `既存 open → 更新` が notice + policy_hold のカードで上書き → **準備済みの NEED DETECTED が Watching 1行に降格し preview 候補が消える**。§12 goal 5 は「Search OFF → notice 留め」を確認する手順なので、この誤実装はゴール判定を通過してしまう。同じ経路で manual override（full path）の成果物を直後の scheduled run が消すことも起きる。
  - 提案: §5.3 に不変条件を1文追加（「policy ゲートは**新規に開始する作業**のみを止める。既存 open card の tier を降格せず、`payload.preview` を削除しない。tier=="request_draft" の short-circuit は policy 判定より前に評価する」）＋ §9 に「昇格済みカードがある状態で Search を OFF にしても tier/preview が保持される」テストを追加。
  - **局所修正で解消可能: 可**（§5.3 に1文、§9 にテスト1件）。

- [C-20] 種別: 設計 / 深刻度: mid（実装時注意）
  - 指摘: `policy_limited` の create-only audit_id が `sha1(run_key + owner + stage)` = (run, owner, stage) 粒度なのに、送出契機は §5.3 で per-card（「hold 新規/policy 変更時のみ」）、payload は `task_count` を持つ。粒度が三者三様で整合しない。
  - 破綻シナリオ: 同一 owner・同一 stage で 2 件のタスクが同じ run で hold された場合、1件目が `msg_pol_X` を `task_count:1` で作成 → 2件目は `save_message_if_absent` が doc 存在で no-op → **2件目の hold は Bridge Trace に一切残らず、`task_count` も 1 のまま**。spec §15（policy で止めた理由を追跡可能に）を部分的に満たさない。
  - 提案: hold 件数を run 内で (owner, stage) ごとに集計し、run 終了時に1回だけ送出する（送出契機を id 粒度に合わせる）。
  - **局所修正で解消可能: 可**（§6 の送出契機の記述を「run 末尾に集計送出」へ変更）。

- [C-21] 種別: 実装 / 深刻度: mid（実装時注意）
  - 指摘: §3 は card 層の CAS を「作成／昇格・更新」経路について定義しているが、`run_sweep` 内の**解消系書込み**（`secretary.py:483` task_done resolve、`:498` below-T1 resolve、`:536` notice の evidence 更新）が `upsert_card_gated` を通るのか無指定。§5.3 は「monitor OFF でも解消系は実行」と書くだけで書込み方法に触れない。
  - 破綻シナリオ: これらは `find_open_card_for_task` で読んだ stale なコピーを丸ごと `save_card` で書き戻すため、読み取りと書込みの間に `try_confirm_card` が open→confirmed を成立させると、**confirmed カードが resolved（または open）へ巻き戻り `linked_query_audit_id` が消える**。terminal guard が外れるので次の sweep が同一タスクを再検知し、ユーザーが再度 confirm すると同じ候補へ 2 回目の connect_ask が飛ぶ（spec §3「同一人物への candidate request 重複なし」違反）。窓はミリ秒だが、デモでは「Run sweep」クリックと Ask 操作が同時に起こりうる（`/api/secretary/sweep` は `_deny_human` でデモ操作者のみだが、まさにその操作者が両方を叩く）。
  - 提案: §3 に「`run_sweep` 内の**全**カード書込みは `upsert_card_gated` を通す（terminal は `rejected_terminal` で弾かれる）」と明記。resolve は policy 非依存なので `expected_policy_updated_at=None` で呼べばよい。
  - **局所修正で解消可能: 可**（§3 に1文、呼び出し置換のみ）。

- [C-22] 種別: 実装 / 深刻度: mid（実装時注意）
  - 指摘: §1 の「HTTP 層の既定は scheduled、UI のみ manual を明示」を満たすための UI 側変更が §8/§9/§12 のどこにも列挙されていない。現状 `triggerSweep()` は `body: JSON.stringify({})` を送っており、v4 の規則ではこれは **scheduled 扱い＝policy ゲート下**になる。しかも同じ関数が `web/requester.html:547` と `web/ui.js:160` の**2箇所に重複**して存在する（requester.html:386 に「Keep in sync with web/ui.js」の注記あり）。
  - 破綻シナリオ: requester.html だけ直して ui.js を放置すると、以後 ui.js を読み込む画面を作った時点で Run sweep が黙ってゲート下に落ちる。両方直し忘れると §12 goal 5（Search OFF → 全 ON 復帰 → 既存成立フロー regress なし）と goal 7（manual 1回通し）が、原因不明のまま候補ゼロで失敗する。
  - 提案: §8 の UI 変更リストに「D. `triggerSweep()` の body を `{"origin":"manual"}` に（requester.html 内インライン版と web/ui.js の2箇所）」を追加。ui.js 側は既存 dead code なので削除せず同期のみ。
  - **局所修正で解消可能: 可**（§8 に1項目、コードは2行）。

---

### low（記録のみ・修正必須ではない）

- **§5.3 の「提案は失われない」は無条件には成り立たない**: `_apply_mail_retention()`（`secretary.py:403`）は policy 非依存で毎 sweep 走り、`received_at` が 14 日以上前のメールを processed 有無に関わらず `delete_mail_seed` する。一方 Gmail の再取得は `newer_than:7d`（`GWS_GMAIL_DAYS` 既定 7）なので、Monitor OFF が 14 日続くと未処理メールは復元不能に消える。Gmail は `GWS_GMAIL_ENABLED=false` 既定でデモ範囲外のため low。文言を「14 日以内は失われない」に限定するか、未処理メールを retention 削除の対象外にするのが素直。副次的に、Monitor OFF 中は processed 化されないため生 body の保持期間も伸びる。
- **`AUDIT_WHITELIST` への追加を忘れると §12 goal 4 が空振りする**: `SchemaRegistry.get_audit_view()`（`schemas.py:180`）は whitelist 外の payload_type を `{masked:true, note:...}` に潰すため、`sweep_run` を whitelist / projection のどちらにも登録しないと `display_payload` から counts が消え、§6 の compact 行が描画できない。§6 に「許可キーへ projection」とは書かれているが、既存実装が frozenset 方式である点は未言及。
- **`tenancy.py` module docstring と `server.py:635-636` のコメントが陳腐化する**: いずれも「no cross-tenant sweep exists / 全テナント sweep は存在しない」と明言している。`/internal/autonomous-sweep` の registry 全テナント反復（C-4 で受理済み）はデータ境界自体は破らない（テナントごとに独立 context）が、この2箇所の記述は事実と食い違うので更新が要る。base design §16.2 の主張文言も同様。
- **profile_diff における outcome=updated の副作用が未定義**: §3 の「副作用は outcome ∈ {created, updated(帯変化あり), reopened}」の「帯変化」は stagnation の tier band を前提とした概念で、profile_diff カード（tier=None）に対する判定基準が無い。素直に読むと updated でも `profile_diff_proposed` を再送し得る。profile_diff は created/reopened のみ送出、と限定するのが安全。
- **`renderNetwork` の pulse 判定が sweep_run で誤発火し得る**: `audit.html:501` は `records.length > PREV_RECORD_COUNT` を条件に含むため、30 分ごとに増える sweep_run レコードでも中心ノードが pulse する。演出のみで機能影響なし。

---

### 判定

新規 **Critical: 0 / High: 2（C-18, C-19）**。いずれも **局所修正（設計書への追記 1〜2 文＋テスト1件、コードは数行）で解消可能**であり、architecture 変更・段順再編・スキーマ再設計は不要。ledger 記載のユーザー裁定（round-4 は「新規 High がゼロまたは局所修正で解消可なら Phase 2 へ」）に照らし、**C-18/C-19 を design.md v5 として反映したうえで Phase 2 に進んで差し支えない**（停止・大規模再設計の必要はない）。

verification 観点ごとの確認結果:
1. **resolved 再オープン**: 既存 resolve 系（`secretary.py:477-500`）とは status 遷移として矛盾しない。UI/サーバは resolved カードを一切読まない（`server.py`・`requester.html`・`audit.html` に参照なし）ため digest 表示との衝突もない。ただし決定的 id との組合せで C-18 が発生する。
2. **failed 状態と 500 契約**: `SWEEP_CLAIM_TTL_SECONDS=300 > attempt-deadline 180s` により「生きている run を stale と誤認して二重実行する」窓が構造的に閉じており、failed→即再 claim と「done 以外は 500」は整合。scheduleTime 由来の run_key により retry 時に成功テナントだけ dedup される点も確認。
3. **upsert_card_gated の outcome 5値**: stagnation は created/updated/reopened/rejected_terminal/rejected_policy_changed を網羅。profile_diff は resolved 状態を持たないので reopened が到達不能（無害）だが、updated 時の副作用のみ未定義（low）。Firestore の transaction パターン（`try_confirm_card` と同型、read→read→write 順）で実装可能なことも確認。
4. **create-only audit と既存 save_message の共存**: `save_message` は doc id = audit_id の無条件 set、`transmission.send` は既に `audit_id` 引数を受けるので create-only 経路の追加は既存経路を壊さない。粒度不整合が C-20。
5. **policy_hold の「設定変更後にのみ再開」**: hold 判定が band＋`policy_updated_at` の2条件なので、run を跨いだ LLM/探索の再実行は起きない（ユーザー裁定の追加制約と一致）。ただし hold の保存先が card.payload である以上、C-19 の降格問題が起きると hold 自体も一緒に飛ぶ。
6. **mail LLM の Monitor 配下化**: `extract_profile_diff` の EXTRACTION_FAILED 経路（`processed` を立てない）と Monitor OFF の「processed に触れない」は同じ「未処理のまま次 run で再試行」構造なので既存テスト（`test_secretary.py` の R-2 系）と矛盾しない。唯一の例外が retention 削除（low）。
