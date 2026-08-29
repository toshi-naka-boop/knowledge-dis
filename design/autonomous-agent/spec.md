# spec — Autonomous Agent Phase（Auto Sweep / Stalled→Need Detected / Autonomy Policy）

出典: ユーザー要求仕様 2026-08-28（26節）。本ファイルはその要求を設計入力として固定したもの。解釈の追加は design.md 側で行い、ここには要求のみを置く。

## 中心思想（変更禁止）
- Humans are large. Agents are small.
- AI activity is evidence, not the protagonist.
- AI shouldn't replace human connections. It should create them.
- 「何でも勝手に処理するAI」にしない。自律性の目的は人間同士の接点の準備まで。

## 追加する3機能
1. **Scheduled Autonomous Sweep** — Run sweep 相当をバックグラウンド定期実行。work observation → stalled detection → candidate search → candidate agent evaluation → Need detected まで。人間への contact は自動化しない。
2. **Stalled → Need detected 自動遷移** — `No updates for 2 days · Monitoring` を UI 表現でなく実際の Agent state として扱う。
3. **Autonomy Policy / Standing Permission** — Agent にどこまで自律動作を許すかの事前設定。

## §0 事前調査（実装前必須）
requester UI / Bridge Trace / Connection Requests / 現行 sweep / task・need・match・request の state / Firestore schema / store / API routes / Cloud Run 構成 / DEMO_TODAY 等 env / reseed・clear / tests / 既存 scheduler 相当 / 既存 user 設定保存箇所。
**manual sweep と autonomous sweep は同一 domain/service layer を使う（二重実装禁止）。** baseline: 208 tests passing。

## §1 Design Loop 使用（本ファイルがその取り込み）
Design Loop 対象: (A) My Agent 上の autonomous state 表現 (B) Autonomy Policy UI (C) Bridge Trace 上の autonomous run 表示。
既存 UI 情報階層を維持: NEED DETECTED 最強 / Watching secondary / TODAY 背景 / Human-first wording。「設定画面」「監視ダッシュボード」「AI管理コンソール」に見せない。

## §2 Scheduled Autonomous Sweep
- 推奨: Cloud Scheduler → authenticated internal endpoint → 既存 sweep service → Firestore。既存構成により自然な方法があればそちらでよい。巨大 worker 基盤は作らない。
- endpoint 例: `POST /internal/autonomous-sweep`（命名は既存 route 設計に合わせる）。
- **Security**: public 無認証にしない。Scheduler→Cloud Run は OIDC 等 GCP 構成に適した認証。一般ユーザーがブラウザから実行できる構造にしない。
- **manual `Run sweep` は削除しない**（demo/debug/override 用）。manual/scheduled は同じ domain logic を呼ぶ。

## §3 Idempotency
Scheduler 再送・timeout・retry で同一処理が複数回走っても: Need detected 重複生成なし / 同一人物への candidate request 重複なし / Bridge Trace event の無意味な重複なし / connection request 重複なし。run_id / task_id / state version / last_sweep_at / last_transition_at 等を必要なら利用。既存 schema を確認し最小限で。

## §4 Stalled → Need detected 自動遷移
- State 概念（最低限表現できること）: observing → stalled → searching → candidate_found → need_detected → awaiting_human_approval。**既存 state model があれば名前を無理に変えず統合。**
- Threshold: hard-code 禁止。`STALL_THRESHOLD_HOURS/DAYS` 等 config 化。本番相当 default とデモ用短縮を分離。DEMO_MODE で時間を捏造せず既存 demo data / clock abstraction（DEMO_TODAY）を使う。
- Meaningful update: updated_at だけでなく既存データ構造から「本当に進展があったか」を判断（status change / note / owner action / completion / relevant event 等）。MVP は deterministic でよい。LLM 停滞判定の新規構築は不要。

## §5 Autonomous behavior boundary
自動で行ってよいのは Observe → Detect → Explore → Evaluate → Prepare まで。**Contact a person は Human Boundary（Marcus への自動送信は実装しない）。**

## §6 Autonomy Policy（MVP）
- Monitor stalled work automatically / Search the organization automatically / Ask candidate agents for fit automatically / Prepare introduction drafts automatically（各 ON/OFF）。
- Contacting a person: **Always ask me first（今回固定）**。将来 `Allow within my policy` へ拡張可能な schema にはしてよいが自動 contact は実装しない。

## §7 Permission dependency
Monitor → Search → Ask candidate agents → Prepare introduction の階層。上位 OFF なら下位の自律動作は実行しない。UI disabled か domain layer で保証（server-side enforcement は §24 で必須）。

## §8 Default Policy
既存ユーザーに突然自律動作を開始しない安全な default。候補: Monitor ON / Search OFF / Ask OFF / Prepare OFF / Contact ALWAYS ASK。既存 demo UX との整合を確認し、必要ならより自然な default を提案（existing user behavior changes unexpectedly を避ける）。

## §9 Policy persistence
user / agent 単位で Firestore 永続化（localStorage のみ禁止）。最低限: user_id / monitor_stalled_work / search_organization / ask_candidate_agents / prepare_introduction / contact_mode / updated_at。既存 user settings schema があれば統合。schema を無駄に増やさない。

## §10 My Agent UI
Autonomy Policy を新しい大きな navigation item にしない。My Agent 内から自然にアクセス（例: Your agent [Autonomy] / Agent settings）。配置は Design Loop で検討。設定画面を主役にしない。通常状態の体験（Good morning → Need detected → Watching → Today）は変えない。Policy は secondary control。

## §11 Autonomous state UI
自動監視は説明文でなく小さな状態表示で伝える（例: `Monitoring automatically` / `Last sweep · 8 min ago` / `Your agent is watching for stalled work.`）。`AI IS RUNNING` / `AUTONOMOUS MODE` / `AGENT ACTIVE` 等の AI 主役表現は禁止。

## §12 Need detected UI
既存 NEED DETECTED カードを維持。自動生成の Need でも同一の Human-first UX（NEED DETECTED / task / Marcus / WHY MARCUS? / 15 min / [Ask for 15 min]）。CTA を自動実行しない。

## §13 Bridge Trace
run が manual / automatic かを監査可能に残す。ただし主役にしない（`Automatic sweep` 程度の小さな metadata）。Timeline は Human-first wording 維持（Need detected / No strong match found with… / Request prepared for Marcus Delgado 等）。**十字型 Agent Network の visual redesign は scope 外・変更禁止。**

## §14 Bridge Trace event data
後から確認できること: run origin (manual/scheduled) / run timestamp / task・need / candidates explored / candidate selected / policy decisions / state transition / human approval boundary。PII・private 文書本文を trace へ過剰保存しない。

## §15 Policy decision trace
Policy で処理を止めた場合、なぜ止まったか追跡可能に（例: `Stalled task detected` → `Search requires approval under current policy`）。内部ログだけでなく必要に応じ Bridge Trace でも簡潔に確認可能。通常 UI でエラーのように見せない。

## §16 Failure handling
途中失敗で state を中途半端に壊さない。Firestore timeout / candidate search failure / agent response failure / partial writes / Scheduler duplicate delivery を考慮。retry-safe / idempotent / recoverable。巨大 distributed transaction は不要。

## §17 Firestore clear/reseed
clear() のページング処理を壊さない。reseed/clear は demo/test 用で production user から呼び出せないことを再確認。reseed と scheduler が競合しないように。

## §18 Tests
既存 208 tests 維持＋追加テスト必須（16項目）:
1. manual sweep still works / 2. scheduled sweep uses same core logic / 3. scheduled sweep idempotent / 4. before threshold → observing / 5. after threshold → stalled / 6. Monitor OFF → detection 進まない / 7. Monitor ON+Search OFF → detection のみ、search 開始しない / 8. Search ON → search 開始可 / 9. Ask OFF → agent-to-agent fit request 自動送信しない / 10. Prepare OFF → draft 自動生成しない / 11. policy persists per user / 12. duplicate scheduler 実行で need 重複しない / 13. duplicate 実行で candidate request 重複しない / 14. human contact 前に manual user approval 必須 / 15. Bridge Trace が manual/scheduled origin を正しく記録 / 16. scheduler endpoint が unauthorized access を拒否。
既存 fake/mock を実挙動に近づける必要があれば修正。

## §19-20 Demo scenario
Jordan 通常業務中（Allied Health… No updates for 2 days · Monitoring）→ **クリックなしで** Agent が自動実行 → NEED DETECTED（Riverside / Marcus / WHY MARCUS? / 15 min / Ask for 15 min）→ Bridge Trace に Automatic sweep → Need detected → 400 profiles explored → candidates evaluated → Marcus Delgado selected → Awaiting Jordan's approval → Jordan が Ask → Marcus 側 accept → Jordan ↔ Marcus / tagline。
収録は実時間2日を待てないため demo seed / controlled timestamps / 既存 demo clock（DEMO_TODAY）で正しい state transition を再現。**本番ロジック自体を fake にしない。**

## §21-22 Cloud Scheduler deployment
ローカルで終わらせずデプロイ後に Scheduler からの実起動まで確認。ただし本番設定前に schedule frequency / target URL / authentication / target service account / environment / affected users を一覧報告（意図せず全ユーザーへ有効化しない）。頻度は 15min〜hourly 等の合理値、秒単位 polling 禁止。

## §23 Scope 外（実装禁止）
自動で人間へ contact / 自動 Calendar scheduling / email 送信 / Slack・Teams / push notification 基盤 / 新 knowledge ingestion pipeline / Bridge Trace network 再設計 / LLM による複雑な stall 判定 / org-wide admin console / RBAC 大規模再設計。

## §24 Definition of Done
- Functional: autonomous sweep バックグラウンド実行 / stalled→need detected 自動遷移 / policy 保存 / policy による制御 / human contact 承認必須 / manual Run sweep 動作。
- Reliability: duplicate run で重複なし / failure から再実行可 / 既存208＋新規テスト passing。
- Security: scheduler endpoint authenticated / demo・reseed 保護 / policy boundary server-side enforcement。
- UX: NEED DETECTED primary / Watching secondary / TODAY background / Human-first wording / Agent activity は evidence。
- Demo: no-click Need detection 再現 / Bridge Trace に automatic run / Jordan→Marcus→Accept→Connection 成立。

## §25 実装順序
Phase 1 現状調査・Architecture・State transition・Firestore schema impact・Autonomy policy model・Scheduler design → **報告**。Phase 2 domain/state＋tests。Phase 3 scheduler endpoint・auth・idempotency＋tests。Phase 4 policy persistence・server-side enforcement＋tests。Phase 5 Design Loop（My Agent UI / Policy UI / Trace metadata）。Phase 6 full flow test・screenshots・self-review。Phase 7 Cloud Run/Scheduler deployment plan。重大 architecture 変更が必要なら実装前に理由を報告。

## §26 最終報告項目
Architecture / State machine / Policy schema / Scheduler config / Idempotency / Security boundary / UI changes / New tests / Total tests / Demo procedure / Cloud deployment changes / Remaining risks / Intentionally not implemented。加えて no-click 成立 run を1回実流し、My Agent / Bridge Trace / Connection Requests / Connection Created を最終検品。**既存完成 UI を壊すくらいなら新機能 UI 表現を最小化する方を優先。**
