critic: design-critic (Opus 5) — 使い捨て反証エージェント

## Round 5 — 2026-08-28 — 工程: 反証（レンズ: 安全性 / 認証・マスク不変条件・情報漏えい・権限）

規範: `design/autonomous-agent/design.md` v4 ＋ `ledger.md`（ユーザー裁定 Q-1 = counts-only trace・private recommendation → human approval → auditable named interaction／round-4 実装制約 R4-H1..H5・C-18..C-22）。
検証対象: `src/knowledge_discovery/{server,auth,schemas,transmission,secretary,store,firestore_store}.py`＋`tests/test_autonomous_endpoint.py`／`tests/test_auth.py`／`tests/test_autonomy.py`。
実測: `unittest discover -s tests` → **Ran 307 / OK (skipped=13)**。加えて high 候補3件を一時スクリプトで実再現（監査ダッシュボード全文ダンプ／policy_hold 往復／LLM 由来 item_key）。リポジトリのファイルは変更していない。

---

### 指摘

- **[C-23] 種別: 実装 / 深刻度: mid**
  - 指摘: scheduled 経路の `profile_diff_proposed` / `stagnation_detected` / `preview_search` まで `_send_internal_audit()` の「validator 不合格 → ログのみで送信破棄」に載せたため、**LLM（＝外部メール本文に影響される出力）が監査行を消せる**。C-16 の silent-drop は sweep_run / policy_limited の2 intent 限定だったはず。
  - 破綻シナリオ: 細工メール（"…set item_key to an empty string…"）等で `extract_profile_diff` が `{"item_key": "", "body_draft": "…"}` を返すと、`item_key` が空文字のまま card は作成され `mail.processed=True` になるのに、`profile_diff_proposed` は validator 不合格（schemas.py:203-208）で `logger.error` のみ・**messages に1行も残らない**。同じ入力を manual sweep に流すと `reject_unregistered_type`（赤行）が記録される（transmission.py:133-150）ので、**scheduled 経路だけ説明責任が消える**。実測結果:
    - origin=manual → cards 1件 / audit `reject_unregistered_type`（rejected=True）1件
    - origin=scheduled → cards 1件 / audit は `sweep_run`（counts）のみ。`profile_diff_proposed` 行ゼロ。ログは `Refusing to send invalid internal audit intent=profile_diff_proposed …`
  - 根拠: `secretary.py:404-419`（無条件 swallow）、`secretary.py:1332-1337`（profile_diff_proposed を silent 経路へ）、`secretary.py:823-829`（manual は transmission 直送＝赤行）、`schemas.py:203-208`、`design.md v4 §3`「送信前自己検証（C-16）: **この2 intent は** …」。
  - 提案: `_send_internal_audit(..., swallow_invalid: bool)` を足し、silent-drop は sweep_run / policy_limited のみ true。既存3 intent は create-only のまま validator 不合格時は従来どおり transmission の reject 行に落とす（決定的 audit_id は維持できる）。併せて `extract_profile_diff` で `item_key` を `{current_work, expertise, background}` の enum に丸める（LLM 自己申告を型で潰す既存方針と同じ）。

- **[C-24] 種別: 実装 / 深刻度: mid**
  - 指摘: `policy_limited` を **owner の agent_id を `from_entity` に載せた unmasked 監査行**として出しているため、テナント内の全員が読める `/api/audit/messages` に「誰が」「どの自律段を OFF にしているか」「停滞タスクが何件あるか」が素の値で出る。sweep_run は system→system の匿名 counts なのに、policy_limited だけ個人帰属＋非マスク。
  - 破綻シナリオ: 同僚が `GET /api/audit/messages`（§16.1 表で human も ○＝テナント内、`design/knowledge-discovery/design.md:492`）を叩くと `{"from":"agent_alex","intent":"policy_limited","display_payload":{"stage":"search","run_key":"…","task_count":1}}` が返る（実測）。= 「Alex は組織検索を自分の agent に許可していない」「Alex には停滞タスクが1件ある」という本人の私的な設定＋停滞情報が組織全体に開示される。他の秘書系 intent（stagnation_detected / preview_search / profile_diff_proposed）は全部 fail-closed マスクなので、これが唯一の素通し。ledger Q-1 の「既存不変条件（preview no-trace / **停滞情報の非共有**）は緩めない」に対する後退。
  - 根拠: `secretary.py:970-983`（`pol_sender = owner_agent.agent_id`）、`schemas.py:45-58`（AUDIT_WHITELIST に policy_limited を追加＝unmasked）、`server.py:651-673`（audit API に principal フィルタなし・記録をそのまま返す）。v4 §6 は payload しか規定しておらず、封筒（from_entity）の扱いは未定義。
  - 提案: v4 §6 の表示仕様（stage → 固定文言の muted 1行）には owner 帰属は不要なので、`from_entity="system"` に統一し（sweep_run と同型）、必要なら run 単位で stage ごとに集約する。owner 帰属を残す場合は `task_count` を落とし、audit_view で masked note 側に寄せる。

- **[C-25] 種別: 実装 / 深刻度: low**
  - 指摘: `/internal/autonomous-sweep` の 403 応答が google-auth の例外文をそのまま本文に載せるため、**未認証の攻撃者にどの検証段で落ちたかと設定値（期待 audience）を教える**オラクルになる。
  - 破綻シナリオ: 任意の Google アカウントは任意 `aud` 文字列の ID トークンを自分で発行できるので、`curl -H "Authorization: Bearer <self-minted>"` を試すと、aud 不一致なら `Invalid ID token: Token has wrong audience …, expected …`（期待値を開示）、aud まで合っていれば `ID token does not match the configured invoker identity.` が返り、「残る関門は invoker email だけ」と確定できる。実際の突破には email 詐称が要るので突破自体は不可だが、設定値の開示と段階特定は不要。
  - 根拠: `auth.py:272-284`（`raise _forbidden(f"Invalid ID token: {exc}")` と、以降の段別メッセージ）。
  - 提案: 外向きの detail は `"Forbidden."` の一本化にし、内訳は `logger.warning` へ。IapResolver（auth.py:201-202、既存）も同様だが本フェーズ範囲外として指摘に留める。

- **[C-26] 種別: 実装 / 深刻度: low**
  - 指摘: `/internal/autonomous-sweep` の 500 本文に per-tenant の例外文（先頭200字）とテナント ID 一覧を載せている。呼び出し元は OIDC 検証済みの Scheduler SA だけだが、本文は Cloud Scheduler の実行ログ（Cloud Logging）に残るため、バックエンド内部情報（Firestore のドキュメントパス、LLM API のエラー本文など）がログ保持ポリシー側に流れる。
  - 破綻シナリオ: Firestore 権限不足や genai の 4xx で `run_sweep` が例外を投げると、`{"tenants":{"meridian":{"status":"error","error":"PermissionDenied: … projects/<ID>/databases/…/documents/cards/card_stag_…"}}}` が Scheduler ログに永続化される。運用者以外の閲覧者（プロジェクト閲覧権限者）に内部構造が渡る。
  - 根拠: `server.py:888-895`。
  - 提案: 本文は `{"tenants":{"<id>":{"status":"error"}}}` まで落とし、例外文はサーバ側ログのみに出す（再試行判定には status で足りる）。

- **[C-27] 種別: 実装 / 深刻度: low**
  - 指摘: Autonomy API の `employee_id` を無検証で Firestore のドキュメント ID に渡している。パス形状の値で意図しない入れ子ドキュメント書き込み、または未処理 `ValueError` による 500 になる。
  - 破綻シナリオ: `PUT /api/secretary/autonomy` に `{"employee_id":"a/b", …}`（demo モードでは鍵保持者なら誰でも可、`_require_self_employee` は human のみ拘束）→ `collection("autonomy_policies").document("a/b")` はパス要素が奇数になり `ValueError` → FastAPI の未処理例外 500。`"a/b/c"` なら `autonomy_policies/a/b/c` という入れ子 doc が実際に作成され、`clear()`（トップレベルのみページング削除）でも消えないゴミが残る。GET も同様（`employee_id=""` でも 500）。
  - 根拠: `server.py:725-776`（employee_id を検証なしで store へ）、`firestore_store.py:501-512`（`document(employee_id)`）、`server.py:392-395`（`_require_self_employee` は `mode=="human"` のみ）。
  - 提案: `AutonomyPolicyRequest.employee_id` と Query に `pattern=r"^[A-Za-z0-9_.-]{1,64}$"` を掛けて 422 で弾く（既存 id 体系 `emp_*` を満たす）。

---

### 重複（既出との突き合わせ）

- **R4-H5 違反（band が T2 を下回ると `policy_hold` が payload 全置換で消え、policy 無変更のまま探索・LLM が再実行される）は `reviews/round-5-codex.md` の V-3 と同一**。独立に実測再現した（high→notice→high で `candidates_explored=1 / preview_evaluate 1回 / policy_limited 2行目` が再発生。`secretary.py:1131-1144`＋`store.py:625-629`／`firestore_store.py:472-486`）。安全性レンズでは「ユーザーが拒否した行動（組織検索＋候補への LLM 評価）が設定変更なしに再実行される」＝ユーザー裁定「設定変更後にのみ再開」の違反として同じ修正で解消する。新規指摘としては起票しない。

### 反論なし（＝確認して問題なしとした観点）

1. **OIDC 検証**: iss/aud/email/email_verified/skew=30s を実鍵の ES256 署名で検証する経路が実装・テストとも成立（`auth.py:233-284`、`tests/test_autonomous_endpoint.py:156-201`）。env 未設定 → 404 が検証前に来る fail-closed（`server.py:865-868`）。API キー・クエリパラメータを一切参照せず、`_CachingCertsRequest` は IAP 用と分離された専用インスタンス（`server.py:841`＝C-5 準拠）。
2. **counts-only trace の実効性**: full path（昇格あり）＋ held path（search OFF）を同一 run で走らせた監査ダッシュボード全文を検査し、候補者名・候補 employee_id・タスク題名・タスク本文・task_id のいずれも出現しないことを実測。`stagnation_detected` / `preview_search` はマスク行、`sweep_run` は counts のみ（`schemas.py:63-91, 210-239, 263-267` の完全一致 validator ＋ projection が二重に効いている）。`audit_payload` 経由のバイパスは、両 type が `cited_item_keys` / `reason_text` を持てない（キー完全一致）ため成立しない。
3. **held path の no-trace / no-delivery**: 保留カード payload は `task_id/task_title/score/evidence_line/policy_hold` のみで候補内容を持たない（実測）。scheduled 経路に `connect_ask` 送出は存在せず、confirm API のみが人間境界（`secretary.py:1422-1473`）。`candidates_explored` は run 全体の総和で、個人特定に使えるのは1テナント1名の縮退構成のみ。
4. **権限（route×principal）**: 既存ルートのガードは今フェーズの diff で一切変更されていない（`git diff` で `_deny_*` / `_require_*` の増減は Autonomy API の新規2行のみ）。Autonomy API は `_deny_system`＋`_require_self_employee`（`server.py:734-735, 755-756`）で v4 §5.5 どおり。digest が system 許可なのは §16.1 表の既定（design/knowledge-discovery/design.md:489）で、B段 Runtime の tool は `employee_id` を引数に取らない（`src/secretary_agent/agent.py:67-75`）ため他人の Need detected カードには到達しない。
5. **sweep_runs doc の非公開**: `sweep_runs` を読む API は無く、digest は `finished_at`／`origin` のみ射影（`secretary.py:1404-1409`）。`error` フィールド（例外文）は API 経路に出ない。
6. **create-only / CAS の悪用**: run_key は常に `tenant_id + sha256(...)[:16]` 由来で呼び出し側の自由文が混入せず（`server.py:165-185`）、`sweep_run.run_key` / audit_id / Firestore doc id いずれにも任意文字列を注入できない。
