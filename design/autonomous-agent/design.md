# design — Autonomous Agent Phase v4

対象: knowledge-discovery への3機能追加（Scheduled Autonomous Sweep / Stalled→Need detected 自動遷移の state 化 / Autonomy Policy）。要求は [spec.md](spec.md)。ベース実装の設計不変条件（候補ごと独立推論・fail-closed マスク・未レビューAI文の非流通・未登録 intent 拒否・preview は no-trace/no-delivery）は `design/knowledge-discovery/design.md` v15 を継承し、**一切緩めない**。

v3→v4: round-3 批評（design-critic C-13..C-17 / codex Z-1..Z-5、計10件・重複1組）を全件反映。主変更 = `resolved` は再オープン可能（再停滞の再検知を回復、C-13）／claim に `failed` 状態（C-14/Z-1）／card CAS が policy version を条件に含み outcome（created/updated/rejected_*）を返す（Z-2/Z-3）／sweep_run・policy_limited は **create-only 保存**（Z-4）／policy_limited から自由文 `note` を排し stage enum から表示時に固定文言導出（Z-5）／connector sync は非ゲート・mail→profile_diff は Monitor 配下と明定（C-15）／新 intent は送信前に自己検証し reject 経路に乗せない（C-16）／Monitor OFF 時は Watching 行も `· Paused` 表示（C-17）。
**注記: v4 は批評3巡の上限到達後の改訂であり、v4 自体への再批評は未実施**（承認時にユーザーが round-4 実施か現状承認かを判断する）。

## 0. 現状調査の結論（設計の前提）

1. sweep は単一 domain service `SecretaryService.run_sweep()`（secretary.py:424）。UI / A段 / B段すべて同一 API → 同一関数。
2. カードの再実行冪等ガードは既存（terminal guard は confirmed/dismissed のみ・resolved は再検知可 / single-open-card / 帯不変時の副作用抑制）。card id はランダム・save_card は無条件上書き → 同時実行対策が必要（§3）。
3. 認可は route×principal default-deny。system principal 機構・OIDC 検証部品は既存。`_CachingCertsRequest` は単一 URL キャッシュ。
4. 停滞判定は deterministic 5信号（W_*・T1/T2 env）。spec §4 は既存機構で充足。
5. state はカード（status×tier）。概念状態は写像（§4）。
6. origin/run メタデータ皆無。秘書系 intent は故意に whitelist 外 — 維持。
7. user 設定の保存箇所なし → 新規 collection。
8. clock seam は get_today/DEMO_TODAY のみ。
9. Cloud Scheduler 2本が既に sweep を自動実行（A段 API-key / B段 OAuth→Agent Engine）→ これらも自律実行として policy 対象（§1）。
10. 現行段順は draft→preview（評価に使った質問＝カードに載る質問）。この意味的同一性は維持（§5.3）。

## 1. Architecture（全体）

```
  UI [Run sweep] ─► POST /api/secretary/sweep  body {"origin":"manual"}   ← 人間クリックの override
  A段 Scheduler ──► POST /api/secretary/sweep  body なし → 既定 "scheduled" ┐
  B段 AgentEngine ► POST /api/secretary/sweep  body {}   → 既定 "scheduled" ├─ policy ゲート下
  新 kd-autonomous-sweep (30min, OIDC)                                      │
        ─► POST /internal/autonomous-sweep      常に "scheduled"            ┘
                                    ▼
              for tenant in registry: run_sweep(origin, run_key)（domain logic 一本）
                                    ▼
              Firestore 更新（決定的 card id・policy-version 条件付き CAS・claim token）
                                    ▼
              audit: sweep_run / policy_limited（create-only・counts/enum のみ）＋既存 masked intents
```

- **origin は呼び出し側宣言**: HTTP 層の既定 "scheduled"（無印の自動実行は必ずゲート下）。UI のみ `"manual"` を明示（route は demo/system 専用なので manual を名乗れるのはデモ操作者のみ）。**domain API `run_sweep()` の既定は "manual"**（既存テスト・直接呼び出しの無改修を保証）。A段・B段は無変更でゲート下に入る。
- 新規 worker 基盤なし。

## 2. 新規 endpoint と認証

`POST /internal/autonomous-sweep`。

- **Google OIDC ID トークン検証**: iss ∈ {accounts.google.com, https://accounts.google.com} / aud == env `AUTONOMOUS_SWEEP_AUDIENCE` / email == env `AUTONOMOUS_SWEEP_INVOKER` / email_verified、skew 30s。certs は OIDC 専用 `_CachingCertsRequest` インスタンス（IAP 用と分離）。env 未設定 → 404（fail-closed）。API キー不受理。失敗 401/403。
- **テナント反復**: caller 指定不可。registry 全テナントを順に `Principal(mode="system", tenant_id=<t>)` で `run_sweep(origin="scheduled", run_key=<t を含む>)`。
- **応答契約（Y-4/C-14/Z-1）**: この attempt で全テナントが `done`（または `deduplicated`）→ 200。**それ以外（failed / 例外 / in_progress を含む）→ 500** で Scheduler の再試行を誘発。成功済みテナントは done のため再試行時 dedup、失敗テナントだけ再実行される。
- 既存 `/api/secretary/sweep` の変更は origin body フィールド受理のみ。

## 3. Run/Card idempotency と同時実行（v4 で精緻化）

**カード層 — 決定的 ID ＋ 条件付き CAS**:
- `card_id`: stagnation = `"card_stag_" + sha1(owner + ":" + task_id)[:12]`、profile_diff = `"card_diff_" + sha1(mail_id)[:12]`。
- store 操作 `upsert_card_gated(card, expected_policy_updated_at=None) -> (Card, outcome)`（transaction）:
  - doc 不在 → 作成、outcome=`created`。
  - 既存 `open` → 更新、outcome=`updated`。
  - **既存 `resolved` → open に再オープンして上書き、outcome=`reopened`**（再停滞の再検知を回復 — C-13。解消履歴は audit の stagnation_detected 系列に残る。confirmed/dismissed/applied のみ terminal）。
  - 既存 terminal → 書かず outcome=`rejected_terminal`。
  - `expected_policy_updated_at` 指定時（scheduled の gated 書込み）: transaction 内で policy doc を再読し、`updated_at` が不一致なら書かず outcome=`rejected_policy_changed`（**LLM 実行中に policy が変わった古い run の昇格・hold 消去を防ぐ** — Z-2）。
- **副作用は outcome で駆動（Z-3）**: 件数カウント・`stagnation_detected` / `preview_search` / `profile_diff_proposed` の送出は outcome ∈ {created, updated(帯変化あり), reopened} のときだけ。rejected_* は無送出（audit/summary の二重化を排除）。
- 既存 `try_confirm_card`（open→confirmed CAS）は不変。

**audit 層 — create-only 決定的 ID（Z-4）**:
- `sweep_run` audit_id = `"msg_sweep_" + run_key`、`policy_limited` = `"msg_pol_" + sha1(run_key+owner+stage)[:12]`。
- 新 store 操作 `save_message_if_absent(message) -> bool`（create-only CAS）。この2 intent の送出は transmission 経由で **create-only**（既存 save_message の上書き経路を使わない）。→ 最初の書き手が勝ち、zombie の再送は no-op（timestamp 偽装・summary 巻き戻しなし）。クラッシュ後の retaker 再送は doc 不在なら作成＝欠落なし。
- **送信前自己検証（C-16）**: この2 intent は SecretaryService 側で validator を通してから送る。不合格（＝プログラミングバグ）は**送信せずログのみ**（transmission の reject_unregistered_type 経路に乗せず、Bridge Trace に赤い拒否行を出さない。バグはテストが検知）。

**run 層 — claim（C-14/Z-1 で failed 追加）**:
- `sweep_runs` doc id = run_key（scheduled: `tenant + "-" + sha256(job+":"+scheduleTime)[:16]`、ヘッダ欠落時のみ 30 分丸め。manual: `"manual-"+uuid4`、dedup なし）。
- claim transaction: 不在 or **`failed`** → 新 token で `{status:"running", claim_token, started_at, origin, date}`。`done` → 実行せず `{deduplicated:true}`。`running` 非 stale → 実行せず `{deduplicated:true, in_progress:true}`（応答契約上は 500 側に集約）。`running` stale（> env `SWEEP_CLAIM_TTL_SECONDS` 既定 300、attempt-deadline 180s と整合）→ 新 token で乗り直し。
- 実行中の例外 → token 一致時のみ `{status:"failed", error, finished_at}` に遷移（**failed が残るので次の再試行が即 claim できる**）。
- 完了: sweep_run を create-only 送出 → token 一致時のみ `{status:"done", finished_at, summary}`。
- 回復モデル: 決定的 id＋outcome 付き CAS＋create-only audit＋processed フラグ＋failed/token claim により、どの時点で落ちても再実行が同一終状態に収束。
- InMemoryStore に同型実装。`clear()` 対象に `sweep_runs` / `autonomy_policies` 追加（ページング既存踏襲）。

## 4. State machine（写像）

| 概念状態 | 既存表現 |
|---|---|
| observing | open card なし |
| stalled | open/notice |
| searching | run 内の一時遷移（永続化しない） |
| candidate_found / need_detected / awaiting_human_approval | open/request_draft（confirmed が承認後） |

`derive_autonomy_state(task, card, effective_policy)`。改名・migration なし。**Human Boundary**: `connect_ask` は confirm API 経由のみ。自動送出パスは存在せず追加しない。

## 5. Autonomy Policy

### 5.1 スキーマ（`autonomy_policies`、doc id = employee_id）

`{employee_id, monitor_stalled_work, search_organization, ask_candidate_agents, prepare_introduction, contact_mode:"always_ask", updated_at}`。`contact_mode` は enum（今回 always_ask 以外 400）。Store get/save（InMemory/Firestore）。

### 5.2 正規化

実効値: `search &= monitor; ask &= search; prepare &= ask`。保存時に正規化。UI は親 OFF で子 disabled。

### 5.3 ゲートと実行経路

**scheduled のみゲート。manual（UI クリック）は override で全段実行。**

**ゲート範囲の明定（C-15）**:
- **connector sync（task/schedule/mail の取得）は非ゲート**: これは自律「行動」ではなくユーザー自身のデータ更新（TODAY 表示の前提）。従来から daily で走っており、30 分周期化による差分は取得頻度のみ（GWS quota は README に注記）。
- **mail→profile_diff パイプライン（LLM 読解＋提案 card）は `monitor_stalled_work` 配下**: Monitor は「自分の作業物の観察」の root 権限。Monitor OFF の scheduled run では**メールを LLM に読ませない**。`processed` フラグは触らないため未処理のまま残り、**ON に戻した次の run で通常処理される**（提案は失われない・復元不能事故なし）。
- 停滞パイプラインの段別ゲート:

| 実効 policy | 挙動 |
|---|---|
| full path（全 ON） | **現行実装と完全同一**（q_draft → preview_search(q_draft) → 昇格。意味的同一性維持） |
| monitor OFF | 新規スコアリング・新規 card なし・mail LLM なし。**解消系は実行**: done→resolve、既存 open card の below-T1 resolve、evidence 更新 |
| search OFF | notice まで。探索なし。policy_hold |
| ask OFF | `preview_shortlist(task.title+description)` を counts のみで実行し破棄。notice 留め |
| prepare OFF | shortlist＋独立評価（task 由来クエリ）を counts のみで実行し破棄。q_draft なし・昇格なし |

- held path の探索結果は**どこにも保存せず** counts のみ（画面に出る候補は常に full path 由来）。
- **policy_hold**: card.payload `{stage, policy_updated_at, band}`。band・policy 不変ならブロック段（探索・LLM・関連 audit）をスキップ。evidence 更新等 monitor 段の出力はスキップしない。`policy_limited` は hold 新規/policy 変更時のみ。
- MatchingEngine は preview_shortlist / preview_evaluate に内部分離（preview_search は合成として不変）。preview 不変条件は各段に適用。

### 5.4 Default

- doc 不在時の実効値 = Monitor ON / Search OFF / Ask OFF / Prepare OFF / always_ask（コード定数）。
- シードは全 employee に全段 ON doc を書く（Firestore・in-memory 両方）→ 既存デモ UX は reseed 済み前提で不変（§10/§11 に明記）。

### 5.5 Policy API

`GET/PUT /api/secretary/autonomy` — `_deny_system`＋`_require_self_employee`。GET は doc なし時 既定値＋`persisted:false`。PUT は正規化→保存→実効値。contact_mode 固定。

## 6. Audit / Bridge Trace

既存マスク不変条件は不変更。追加 2 intent（いずれも create-only・送信前自己検証）:

1. `sweep_run`: payload = counts のみ `{origin, run_key, date, tasks_evaluated, cards_created, cards_promoted, cards_resolved, needs_detected, candidates_explored, policy_held, schema_version:1}`。固有名詞・題名・id・本文なし。per-run 詳細（card_ids 等）は API 非公開の sweep_runs doc summary へ。
   - **前提質問 Q-1（ユーザーへ）**: spec §19 の `Marcus Delgado selected` / `Awaiting Jordan's approval` 表示は confirm 前の候補名・owner 名公開となり既存不変条件と衝突。本設計は counts-only（`1 need prepared — awaiting the owner's review`）を推奨、名前入りは confirm 後の named timeline から。承認時に確認。
2. `policy_limited`: payload = `{stage ∈ {"search","ask","prepare"}（enum・validator で制限）, run_key, task_count}`。**自由文 note は保存しない**（Z-5）。表示文言は stage から UI 側の固定表で導出（例: search → `Search requires approval under current policy`）。

- **whitelist を閉じる**: 2 type の validator は許可キー完全一致＋enum 値制限（未知キー・不正値は送信前自己検証で弾く）。`get_audit_view` は表示時に許可キーへ projection（二重防御）。既存 8 type は不変。
- **タイムライン除外**: audit.html `recordToEvent` は sweep_run / policy_limited を明示 null。表示は2箇所のみ: ヘッダ直下 muted `Last sweep: Automatic · HH:MM` ＋ タイムライン枠上の secondary compact 行（counts から Human-first 文生成）。policy_limited は muted 1行（stage→固定文言）。
- セッション分割は現行（query 境界）。十字型 Agent Network SVG 不変更。

## 7. Firestore schema 影響

| collection | 新規/変更 |
|---|---|
| `autonomy_policies` / `sweep_runs` | 新規 |
| `cards` | 決定的 id＋`upsert_card_gated`（payload 内 policy_hold のみ追加） |
| `messages` | 無変更（create-only は store 操作の追加のみ） |
| `clear()` | 新2 collection を対象に追加（ページング既存踏襲） |

## 8. My Agent UI（Design Loop 対象 A/B）

情報階層不変。追加2点＋整合1点:

- **A. 自律状態表示**: `● Your agent · Monitoring automatically · Last sweep 12 min ago`（digest に `last_sweep:{at,origin}|null`）。実効 Monitor OFF → `· Monitoring paused`。
- **B. Autonomy Policy UI**: `Agent autonomy ›` disclosure（既存 Ask your agent と同型・最下部）。4 checkbox＋固定行 `Contacting a person: ● Always ask me first`。親 OFF→子 disabled。
- **C-17 整合**: 実効 Monitor OFF のとき Watching 1行カードの `· Monitoring` は `· Paused` に切り替え（digest が effective policy を返す）。
- NEED DETECTED カード・TODAY・Connection Created 不変更。CTA は human のみ。

## 9. Tests（spec §18 → 実装先）

新規 `tests/test_autonomy.py` 中心、全オフライン。spec 16項目は v3 §9 の表を維持しつつ v4 差分を追加:

- claim: 新規/done/running(非stale)/running(stale)/**failed→再claim**（C-14/Z-1）。
- upsert_card_gated: created/updated/**reopened（resolved 再停滞）**/rejected_terminal/**rejected_policy_changed**（Z-2/C-13）＋ outcome 駆動で audit 二重化しないこと（Z-3）。
- create-only audit: 同 id 再送で doc 1件・timestamp 不変（Z-4）。validator 不合格の内部 intent が送信されないこと（C-16、reject 行が trace に出ない）。
- policy_limited payload に note が無いこと・stage enum 制限（Z-5）。
- Monitor OFF: mail LLM 未呼出・processed 不変・ON 復帰後に処理されること（C-15）。connector sync は OFF でも実行。
- UI 表示: digest の effective policy で `· Paused` 切替（C-17。サーバ側 projection のテスト）。
- 部分失敗: 1 tenant 失敗 → 500・failed 記録・再試行で該当 tenant のみ再実行。

## 10. Demo（spec §19-20）

- 前提: 収録直前 reseed（policy 全 ON doc＋stale タスク）。
- 手順: ① job pause → reseed → resume ② `gcloud scheduler jobs run kd-autonomous-sweep`（無クリック）③ My Agent リロード → NEED DETECTED ④ Bridge Trace: `Last sweep: Automatic`＋compact 行 ⑤ Jordan Ask → 既存成立フロー。2段演出は `--today` 前進で。

## 11. Cloud deployment（Phase 7、設定前にユーザー報告）

v3 §11 と同一（job `kd-autonomous-sweep` `*/30 * * * *`・OIDC・env 2件・affected users=シード4名・pause→reseed→resume・teardown 追記）。既存 A/B job は定義不変のまま policy ゲート下に入る（シード全 ON のため現デモ挙動不変）。

## 12. 検証可能なゴール（完了条件 — 呼び出し側が実行して判定）

1. `PYTHONPATH=src:. .venv/bin/python -m unittest discover -s tests` → OK（既存 208＋新規全 green）。
2. unittest: 認証4ケース／claim 5態／outcome 5値／create-only／§9 全項目。
3. 本番: `gcloud scheduler jobs run kd-autonomous-sweep` → 200、audit に sweep_run(scheduled)、**UI 無操作で** NEED DETECTED（スクリーンショット）。
4. Bridge Trace: `Last sweep: Automatic`＋compact 行・生 intent 行や拒否行が出ない（スクリーンショット）。
5. Policy UI: Search OFF → scheduled run 後 notice 留め＋固定文言表示＋Watching 行 `· Paused`（Monitor OFF 時）→ 全 ON 復帰で既存成立フロー regress なし（スクリーンショット）。
6. `/internal/autonomous-sweep` 無トークン curl → 401/403（本番）。
7. 既存デモ台本（manual）1回通し regress なし。

## 13. 捨てたもの / やらないこと

- spec §23 全項目。Message への origin 列。中間 state 永続化。policy の env 制御。
- manual origin への policy 適用（override）。confirm 前の候補名・owner 名の audit 表示（Q-1 確認待ち）。
- 段順の全面再編（full path は現行維持）。分散ロック・outbox 基盤。A段 daily job の統合。
- resolved カードの履歴保全のための世代 id（再オープン方式を採用。履歴は audit 系列で追跡可能）。
