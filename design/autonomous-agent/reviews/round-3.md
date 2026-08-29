critic: design-critic (Opus 5) / 対象: design/autonomous-agent/design.md v3 / 実装ベース: src/knowledge_discovery @ main dd3e765

## Round 3 — 2026-08-28 — 工程: 批評（v3 新規変更の収束確認）

前提: ledger の解決済み索引（C-1..C-12 / X-1..X-5 / Y-1..Y-5）は蒸し返さない。Q-1 は指摘対象外。
以下はすべて **v3 で新しく入った機構が生む新規欠陥**（round-1/2 で誰も指摘していない）。

### 指摘

- [C-13] 種別: 設計 / 深刻度: high
  - 指摘: `upsert_card_if_open` の terminal 集合に **`resolved` を入れた**ことで、「一度解消したタスクが再び停滞したときの再検知」が構造的に不可能になる（決定的 card_id と組み合わさって永久ブロック）。
  - 破綻シナリオ: 実測で現行挙動を確認済み（InMemoryStore、T1=3.0/T2=7.0）。
    1. `task_cycle` が停滞 → sweep1 で `card_stag_fee1e5526d`(open/notice) 生成。
    2. Jordan が実際に進捗を出す（due 更新・last_updated_at=today・reschedule=0）→ score < T1 → sweep2 で同カードが `resolved`。
    3. 12日後また放置される → **現行は sweep3 で新カード `card_stag_f15a6c8fcf`(open/notice) を生成し、NEED/Watching に復帰する**（`find_cards_for_task` の terminal ガードは confirmed/dismissed のみを見ており、resolved は再生成を妨げない）。
    v3 では card_id が `sha1(owner:task_id)` で 1. と同一 doc になり、その doc は `resolved`＝terminal なので **書かれずに既存 resolved が返る**。結果、そのタスクは以後どれだけ停滞しても二度とカードが出ない（30分ごとの scheduled sweep が永久に空振り）。しかも `cards_created` はインクリメントされるため、`sweep_run` audit は「作った」と嘘の counts を報告する。
    デモ影響も直接的: §10 の「`--today` を進めて2回目 run」の2段演出や、reseed で同一 task_id を書き戻す運用（clear なし）で、前回収録時の resolved doc が残っていると需要が一切出ない。
  - 提案: terminal = `{confirmed, dismissed, applied}` とし、`resolved` は **再オープン可**（resolved→open は connect_ask 未送出・profile 未適用なので C-9 が守りたかった不変条件を一切壊さない）。upsert 時に `resolved_reason` をクリアし tier を再計算する。加えて「open でも terminal でもない状態（rollback 中など）」の扱いを明文化する（現行 status 語彙は open/confirmed/dismissed/resolved/applied の5つで、confirm 失敗時に confirmed→open へ戻す経路が secretary.py:841 に存在する）。

- [C-14] 種別: 設計 / 深刻度: mid
  - 指摘: run 失敗時の `sweep_runs` doc の後始末が未定義（`failed` 状態がない）。その結果、**§2 の「1件でも失敗 → 500、リトライで失敗テナントだけ再実行」という Y-4 の解決が §3 の「running かつ非 stale は実行しない」と衝突して機能しない**。
  - 破綻シナリオ: 3テナント中 B の `run_sweep` が例外（Firestore 一時エラー等）。A/C は done、B の doc は `{status:"running", claim_token, started_at=now}` のまま残る。endpoint は 500 を返す → Cloud Scheduler が数秒〜数十秒後にリトライ → 同一 run_key（schedule_time 同一）で A/C は `done`→dedup、**B は `running` かつ非 stale（TTL 300s 未経過）→ `deduplicated:true, in_progress:true`** → 全テナント「成功扱い」で **200 を返す**。すなわち (a) 失敗テナントは1周期まるごとスキップ、(b) 障害シグナルが「500 の直後に 200」で自己修復したように見え、運用ログから消える。TTL 300s > Scheduler の実質リトライ窓のため、リトライは構造的に必ず無駄撃ちになる。
  - 提案: 例外時に `claim_token` 一致を条件に `{status:"failed", error, finished_at}` へ CAS（`finally`/`except` で必ず通す）。claim 判定に `failed` → 再クレーム可を追加。さらに endpoint の応答判定で `in_progress`/`deduplicated` を「実行した」と同一視せず、per-tenant 結果に `ran|deduplicated|in_progress|failed` を区別して返す。

- [C-15] 種別: 設計 / 深刻度: mid
  - 指摘: policy のゲート範囲が**停滞検知系のみ**に定義され、同じ `run_sweep` 内で回る **connector sync（`_sync_owners`）と mail seed → profile_diff パイプライン（LLM 抽出・カード生成・`processed=True`・retention による本文消去/削除）が policy の外に置かれたまま**。v3 で「HTTP 既定 scheduled」＋30分周期になったことで、この未定義領域が初めて「毎日1回」から「毎日48回・全テナント・policy 無関係」に格上げされる。
  - 破綻シナリオ: (a) ユーザーが Autonomy Policy を4つとも OFF にする（UI の意味は「自動では何もしない」）。それでも scheduled sweep は 30 分ごとに本人の GWS タスク/メールを取り込み、**メール本文を Gemini に送って** profile_diff カードを作り、処理済みメールの本文を消す。spec §24「policy boundary server-side enforcement」の期待から見て、最も侵襲的な自律動作（本人のメール読解）だけがゲート外にある。(b) 逆に実装者が「Monitor OFF なら §5.3 の『新規 card 作成をしない』に profile_diff も含まれる」と解釈してカード生成だけを止めると、mail ループは `mail.processed = True` を先に立てて `_apply_mail_retention` が本文を空にする（secretary.py の処理順）ため、**提案が復元不能に失われる**。どちらの解釈でも事故になり、design.md はどちらとも読める。
  - 提案: §5.3 に profile_diff パイプラインと `_sync_owners` の扱いを明記する。最小案は「sync とメール取り込みは policy 非依存（データ取得はゲート対象外）／profile_diff カード生成は Monitor 配下でゲートし、ゲート時は `processed` を立てない（次回持ち越し）」。仕様の意図（Monitor OFF で本人メールの LLM 読解を続けてよいか）はユーザー確認事項の候補。

- [C-16] 種別: 設計 / 深刻度: mid
  - 指摘: Y-3 の「許可キー完全一致 validator で fail-closed」は、`TransmissionLayer.send` が **例外を投げず `reject_unregistered_type` を同一 audit_id で保存して正常復帰する**実装（transmission.py:123-140）と噛み合っておらず、失敗時に「静かにブロック」ではなく **Bridge Trace の赤い拒否行**として表面化する。
  - 破綻シナリオ: 将来 counts に1キー（例 `mails_processed`）を足して validator を更新し忘れる／`schema_version` を 2 に上げる、といった通常の保守で `sweep_run` の payload が完全一致に落ちる。すると (1) `msg_sweep_<run_key>` doc には `reject_unregistered_type` レコードが入り、(2) それは `AUDIT_WHITELIST` に含まれるうえ `recordToEvent` の**先頭分岐**（audit.html:584）で拾われるため、C-11 で除外したはずの新 intent が **`Transmission rejected — unregistered payload type` ＋ `policy-blocked`（danger 色）行**としてタイムラインに描画される。(3) 一方 run 自体は成功として done-CAS まで進み、`Last sweep` 行と compact 行は消える。§12-4 の「生行が出ないこと」の検品が、よりによってデモ画面で赤く落ちる。
  - 提案: 新2 intent の `send()` は戻り値の `rejected` / `intent` を検査し、拒否ならログ＋run を失敗扱い（C-14 の failed 経路）にする。併せて `recordToEvent` の除外を `intent`ベースだけでなく `display_payload.raw_payload_type ∈ {sweep_run, policy_limited}` の拒否行にも広げる。

- [C-17] 種別: 設計 / 深刻度: low
  - 指摘: C-10 の「Monitor OFF でも evidence_line 更新は継続」と §8-A の `· Monitoring paused` 表示が、同一画面で矛盾する文言を並べる。
  - 破綻シナリオ: ユーザーが Monitor を OFF にする → ヘッダは `● Your agent · Monitoring paused`。しかし既存 notice カードの行は requester.html:693 の固定文言で `No updates for 9 days · Monitoring` を出し続け、しかも C-10 により evidence が毎 run 更新されて日数が増えていく。「止めたのに監視され続けている」ように見え、spec §11「AI activity is evidence」より悪い印象（設定が効いていない）を与える。
  - 提案: 実効 Monitor OFF のとき、既存 notice 行の接尾辞を `· Monitoring` から `· Paused`（または接尾辞なし）へ切り替える。digest 側に実効 policy を載せれば UI 側は1行の分岐で済む。

### 反論なし（＝v3 の他の新規変更は妥当と判断）の根拠

- **origin の HTTP 既定 scheduled / domain 既定 manual**: `/api/secretary/sweep` は `_deny_human` のみで demo/system 両方が到達するが、body 無し（A段 Scheduler・`{}` の B段 client.py）は自動的に scheduled になり、`manual` を名乗るのは UI の明示送信のみ。既存 test_auth（`json={}`）・test_tenancy（body なし）も既定側に落ちるため、シードが全 ON doc を書く前提が満たされる限り無改修で通る。
- **whitelist の二重防御**: `get_audit_view` は audit_payload 無し＋非 whitelist を masked に倒す既存 fail-closed（schemas.py:189-201）を維持しつつ、新2 type だけ projection を足す形なので既存 8 type の挙動に影響しない。
- **Agent Network SVG への非干渉**: `deriveAgentState`（audit.html:464）は intent 名で分岐し、`splitIntoSessions` は `query` 境界のみを見るため、`sweep_run`/`policy_limited` の混入で十字型 SVG・Match Found カードの導出は変化しない（C-16 の拒否行を除く）。
- **claim の TTL 整合**: TTL 300s > attempt-deadline 180s > 実行時間、かつ 30分周期 ≫ TTL なので、タイムアウトでリクエストが打ち切られても本体は完走して done-CAS に到達し、リトライは dedup される（この経路は健全。壊れるのは C-14 の例外経路のみ）。
- **full path の意味的同一性（Y-5）**: 全 ON 経路が現行の `q_draft → preview_search(question=q_draft)` を素通りする限り、カードの `question_draft` と評価に使ったクエリの一致は保たれる（secretary.py:558-575 の構造を変えない前提）。
