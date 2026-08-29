critic: claude/opus-5[1m]

## Round 2 — 2026-08-28 — 工程: 批評（design.md v2）

前提: ledger 解決済み索引（C-1..C-7 / X-1..X-5）は蒸し返さない。Q-1（Bridge Trace の名前表示）は指摘対象外。
以下 2 件（C-8 / C-9）は解決済み論点に隣接するが、**新しい根拠**（README の既存 Scheduler 定義、secretary.py の書込順序）を提示して再提起するもので、既決の結論そのものは争っていない。

### 指摘

- [C-8] 種別: 設計 / 深刻度: high
  - 指摘: 「policy がゲートするのは `origin=="scheduled"` のみ、manual は override」（§5.3）＋「A段 daily job は残す・既存2 job は不変更」（§11）を同時に採ると、**人間が押していない自動ジョブが 2 本とも manual 扱い**になり、Autonomy Policy を毎日完全にバイパスする。X-3 の「manual＝人間の override」という結論には同意するが、その manual の識別子が「エンドポイントの別」になっているのが欠陥。
  - 破綻シナリオ: 根拠は README:246-252（`kd-secretary-sweep` = `0 8 * * *` JST が `POST /api/secretary/sweep` を API キーで直叩き）と README:287-292 / 261-262（`kd-secretary-sweep-runtime` = `55 7 * * *` が Agent Engine `run_daily_sweep` 経由で同じ `/api/secretary/sweep` を叩く）。design §2 は `/api/secretary/sweep` を無変更・origin="manual" 固定と決めているので、Jordan が Search OFF にしていても 07:55 と 08:00 に全段が走り、notice が request_draft に昇格して NEED DETECTED が出る。`policy_hold` マーカーも上書きされ、次の 30 分 run は「band が変わった」と見なして再び LLM を叩く（C-2 の抑制が壊れる）。spec §24 Security「policy boundary server-side enforcement」が日次で破れ、§12 goal 6（Search OFF → notice 留めのスクショ）は収録時刻が 07:55 を跨ぐと再現しない。UI 側でも §8-A が「Monitoring paused」と表示しながら NEED DETECTED カードが最上部に出る矛盾表示になる。
  - 提案: origin を「どの route か」でなく「人間の明示操作か」で決める。(a) `/api/secretary/sweep` に `origin` を受け取らせ、A段/B段 job は `scheduled` を送る（UI の Run sweep だけ manual）、または (b) 提出前に A段/B段 daily job を pause/削除して autonomous job に一本化する（§11 の「並存」を撤回）。どちらを採るかは設計で確定させ、§13「捨てたもの」に書く。

- [C-9] 種別: 設計 / 深刻度: high
  - 指摘: §3 の「決定的 id により重複が構造的に不可能」「zombie が書いた card は同一 doc に収束するため無害」は、**doc の重複**にしか当てはまらない。card の書込が非原子（read → LLM → 無条件 `save_card`）のままなので、決定的 id は重複生成を*終端状態の上書き*に変換する。X-2 の「find→save 非原子」を決定的 id で閉じたとする結論に対する新根拠。
  - 破綻シナリオ: secretary.py:470-475 で terminal guard（confirmed/dismissed）と open card を読み、:558-566 で LLM 起草＋候補推論（実測で数秒〜数十秒）を挟み、:589-643 で無条件に `save_card` する。この窓の間に Jordan が `POST /api/secretary/confirm` を実行すると card は confirmed（linked_query_audit_id 付き）になるが、sweep 側は自分が読んだ状態のまま同じ id へ status="open" で set する → **confirmed が open に巻き戻り、linked_query_audit_id が消え、NEED DETECTED が再表示され、もう一度 Ask すると同一人物（Marcus）へ connect_ask が二重送出**される。spec §3 / §18-13 / §24 Reliability の明文違反。v2 で 30 分毎 48 回/日の自動 run と TTL 600s の zombie 容認が加わるため、露出は現行の「人が押した時だけ」から常時に変わる。profile_diff も同型で、`applied` 済みカードが zombie の書込で `open` に戻り、再 apply で `current_work_mail_1` として profile item が二重追加される（`review_profile_diff` は status=="open" しか見ない）。
  - 提案: `save_card` の sweep 経路をトランザクション CAS 化する（`try_confirm_card` と同じ `@firestore.transactional` で「doc 不在 or status=='open'」のときだけ set、terminal なら書込破棄）。InMemoryStore も同型。§3 の「無害」記述はこの CAS を前提とする形に書き換える。

- [C-10] 種別: 設計 / 深刻度: mid
  - 指摘: §5.3 のゲート表が monitor OFF で **below-T1 resolve（アラーム解除方向）** まで止めている。C-6 は `task.status=="done"` だけを救済したが、実務で多い「タスクを更新して停滞が解消したが done にはしていない」ケースが救済されない。
  - 破綻シナリオ: Jordan が NEED DETECTED を放置したまま Monitor を OFF にする → 翌日タスクを更新して score が T1 を割っても、owner ごとスキップされるので open card は resolve されず、My Agent の最上段に古い NEED DETECTED が永久に残る。同時にヘッダは「Monitoring paused」と出るため「監視は止まっているのに警告だけ残る」矛盾表示になる（spec §24 UX の NEED DETECTED primary を毀損）。同根で、`policy_hold` 中は T2 分岐に入らないため card.payload の score / evidence_line が凍結し、Watching 行の "No updates for 2 days" が日を跨いでも増えない（Search OFF のユーザーには監視が死んで見える）。
  - 提案: スコアリング自体は deterministic・LLM ゼロ・追加コストゼロなので monitor でゲートしない。ゲートするのは「card 新規作成／昇格」だけにし、resolve（done・below-T1）と既存 open card の score/evidence 更新は policy 非依存で回す。

- [C-11] 種別: 設計 / 深刻度: mid
  - 指摘: §6 は「セッション分割は現行のまま query 境界のみ」「追加はヘッダ 1 行とセッション外 compact 行の 2 点だけ」としているが、`sweep_run` / `policy_limited` は audit messages として保存される以上、**直近 query 以降に発生した分は latest session に含まれ、タイムライン内に生の intent 行として描画される**。除外規則が設計に無い。
  - 破綻シナリオ: audit.html:394-404 の `splitIntoSessions` は intent を問わず query 以降の全レコードを current session に push し、`buildSessionEvents`（:649-）は既知 intent 以外を `recordToEvent` の else 分岐（:637-642）に落として `action = r.intent` をそのまま表示する。よって Jordan が Ask した後（＝session 開始後）30 分以内に自動 sweep が走ると、Connection Created の Human-first タイムラインの中に `sweep_run` / `policy_limited` という生の英字 intent 行が挿入される。収録は 30 分より短いので混入確率は高い。spec §13「Automatic sweep は主役にしない」「Timeline は Human-first wording 維持」および §24 UX に違反する。
  - 提案: 設計に「`sweep_run` / `policy_limited` は timeline から除外（splitIntoSessions で session に積まない、または buildSessionEvents で skip）し、ヘッダ行と枠外 compact 行だけで表現する」を明記し、§9 の補助テストに「session に query 以外の秘書 intent が混ざっても timeline 行が増えない」を追加する。

- [C-12] 種別: 設計 / 深刻度: low
  - 指摘: §3 の claim ライフサイクルに未定義の分岐と設定不整合が残る。(a) `running` かつ **stale でない** doc に当たったときの応答が未定義。(b) Scheduler の attempt-deadline（既存 job は 180s、README:249/292）と `SWEEP_CLAIM_TTL_SECONDS=600` の関係が未規定。(c) `sweep_run` audit は「status=done に更新した後」に送出する順序なので、その間で落ちると当該 run の audit が永久に欠落する。
  - 破綻シナリオ: 全テナント直列＋400 profile 読込＋LLM 呼出で 1 run が 180s を超えると、Scheduler は失敗としてリトライするが、リトライは同一 run_key の `running`（開始 600s 未満＝非 stale）に当たり何もしない。結果、Scheduler 実行ログは「失敗」だけが残る — §10 で「押していない証拠 = Scheduler 実行ログ」としているので、証拠が失敗ログになる。また §10 手順②の `gcloud scheduler jobs run` は、ヘッダ欠落時フォールバック（30 分粒度丸め）に落ちると直前の自動 run と同じ run_key になり `deduplicated:true` の無操作で終わる（reseed 直後は sweep_runs が clear されるので救われるが、reseed なしの撮り直しでは沈黙する）。
  - 提案: §3 に「running かつ非 stale → 200 `{skipped:"in_progress"}`（Scheduler をリトライ地獄にしない）」を明記。`SWEEP_CLAIM_TTL_SECONDS` ≤ attempt-deadline とし、§11 の job 定義に `--attempt-deadline` と `--max-retry-attempts` を明記。audit 送出は status 更新と同一トランザクション後の即時実行＋次 run での欠落補完不要という判断理由を 1 行残す。

### 確認して問題なしと判断した点（根拠付き）

- **OIDC 認証（§2）**: `_CachingCertsRequest` は url を無視した単一キャッシュ（auth.py:141-157）なので専用インスタンス化は必須かつ十分。`verify_token` を certs_url 指定で呼ぶ既存パターンがそのまま流用でき、env 未設定 404 は fail-closed として一貫。
- **テナント反復（§2）**: `TenantRegistry.tenants` と `ContextRouter.for_tenant`（tenancy.py:95, 200-213）で caller 非指定の全テナント反復は実装可能。`Principal` は単なる dataclass（auth.py:52-59）なのでプロセス内合成に副作用がなく、cross-tenant 読取経路も増えない。
- **sweep_run counts-only の実装可能性（§6）**: `run_sweep` は既に counts の dict を返す（secretary.py:717-728）ので needs_detected / candidates_explored / policy_held の追加は加算のみ。`to_entity="system"` は `get_agent` が None を返すため supported_intents 検査を通過し（transmission.py:143）、cited_item_keys / reason_text が無い payload は private mask 判定にも入らない（:178）。whitelist 追加も `AUDIT_WHITELIST` への 2 要素追加で足りる。
- **claim CAS の実装可能性（§3）**: `try_confirm_card` / `try_update_consent_state` で `@firestore.transactional` の実装パターンが既にある（firestore_store.py:360-381, 408-427）ので、create-if-absent と token 一致 CAS は同型で書ける。
- **段順再編（§5.3）の不変条件**: `preview_search` は stage-2 に public のみ再構成した Profile を渡し、cited_item_keys を public に絞り、メッセージを一切送らない（matching.py:271-310）。①②に分離してもこの純粋性は関数内に閉じており、no-trace / no-delivery / embedding_public は保たれる。0 候補時に question_draft が payload に入らなくなる点も requester.html:741 が `p.question_draft || ""` で防御済み。
- **ベースライン**: `PYTHONPATH=src:. .venv/bin/python -m unittest discover -s tests` を実行し `Ran 208 tests ... OK (skipped=13)` を確認。§12-1 の「≥208」は正しい基準（プロジェクト CLAUDE.md の「97件」の方が古い記述）。
