critic: claude design-critic (claude-opus-5[1m])

## Round 14 — 2026-08-24 — 反証(安全性)

対象: 9f54139..HEAD（部品A 認証 / B テナント / C-2 コネクタ）。読んだもの: design.md v15 §16・§10 ゴール23〜30、ledger.md round-12/13 帰結、auth.py / tenancy.py / server.py / service.py / store.py / firestore_store.py / secretary.py / connectors/* / scripts/gws_probe.py / web/*.html / tests（199件 OK・skip 13）。オフライン再現のみ実施（`.venv`、src/ 無変更）。

### 指摘

- [S-11] 種別: 実装 / 深刻度: high
  - 指摘: `SOURCE_CONNECTOR=google_workspace` で `GWS_SELF_EMPLOYEE_ID` が未設定だと、`_sync_owners` が **全所有者（agents ∪ profiles、本番シードで400人）をループし、そのすべてに作者1アカウントのADCで取得した Tasks/Calendar/Gmail を書き込む**。`GoogleWorkspaceConnector.fetch()` は `owner_employee_id` を一切使わず常に `users/@me` `users/me` を叩くため、所有者ごとの資格情報が存在しない。単独モードは design §16.3 に書かれているが、**未設定時が fail-open** になっている。
  - 破綻シナリオ: 収録・審査でのデモ環境やユーザーの手元で `SOURCE_CONNECTOR=google_workspace` だけを設定して sweep すると、(a) 作者の Gmail（`kd-secretary` ラベル付き）の件名・本文が**無関係な従業員1人**の `mail_seeds` に入り、その人の差分提案カードとして Gemini に渡り、`apply` すれば**他人のプロフィール公開項目**として埋め込み・マッチングに載る。(b) 作者のカレンダー件名・タスク題名が別の従業員の資源になる（`apply_fetch_result` は `existing.owner_employee_id != owner` のとき所有者ごと上書きするため、同一 `source_id` を所有者数ぶん奪い合い、最後の1人だけが残る）。(c) 1回の sweep で 400×(タスクlist+ページ+カレンダー+Gmail 3種) の API 呼び出しが走りクォータを焼く。
    実測（`.venv`、フェイクGWSセッション・所有者3人・`GWS_SELF_EMPLOYEE_ID` 未設定）:
    ```
    sweep: {'sync_tasks': 3, 'sync_schedules': 3, 'sync_mails': 1, 'sync_skipped': 2, 'sync_errors': 0}
    emp_alice: tasks=[] schedules=[] mails=[('gws_mail_M1', 'Severance draft', ...)]
    emp_bob:   tasks=[] schedules=[] mails=[]
    emp_carol: tasks=[('gws_task_L1_T1', 'CEO comp review notes')] schedules=[('gws_cal_E1_meeting_prep', '1:1 with legal re: layoffs')] mails=[]
    ```
    メールは最初の所有者、タスク/予定は最後の所有者に付く（`sync_mails=1` / `skipped=2` は既存ID非再投入の副作用で、意図した所有者判定ではない）。
  - 提案: 所有者ごとの資格情報（DWD）が無い以上、**gws コネクタは単独モード専用として fail-closed にする**。`build_connector_from_env()` で `SOURCE_CONNECTOR=google_workspace` かつ `GWS_SELF_EMPLOYEE_ID` 未設定なら起動時に RuntimeError、または `_sync_owners` で全所有者を `skipped` にして0件同期にする（`GoogleWorkspaceConnector.fetch` 側で `owner != GWS_SELF_EMPLOYEE_ID` を弾く二重化が確実）。design §16.3 の1行（「単独モード必須。未設定は起動失敗」）と README（ゴール29 のコネクタ設定節。現状 README に `SOURCE_CONNECTOR` / `GWS_*` の記載が無い）を同時に更新する。多所有者同期を将来やるなら DWD の subject 指定が前提であることも明記。

- [S-12] 種別: 実装 / 深刻度: low
  - 指摘: `_CachingCertsRequest` は IAP 公開鍵の再取得に失敗したとき、**キャッシュ期限を過ぎてからさらに同じ長さの猶予（合計 2×TTL）まで旧鍵を返し続ける**（auth.py 152-158）。design §16.1 は「取得失敗時は期限内の旧鍵で継続、期限切れなら401で fail-closed」であり、期限の定義が実装側で倍に伸びている。
  - 破綻シナリオ: Google が IAP 署名鍵をローテート（=鍵の失効を含む）した直後に `www.gstatic.com` への到達が一時的に失敗すると、当プロセスは**失効済み鍵で署名されたアサーションを最大2時間受理し続ける**（既定TTL 3600s の場合）。逆にテスト `test_fetch_failure_beyond_grace_raises` はこの2倍窓を仕様として固定しており、design との差分が台帳に残らない。
  - 提案: 猶予を撤廃して `now - fetched_at >= ttl` で例外→401 にする（3行）。猶予を残すなら design §16.1 に「失敗時の猶予は1窓ぶん延長する」と明記して差分を消す。

- [S-13] 種別: 実装 / 深刻度: low
  - 指摘: 台帳の `email_domains` / `system_accounts` が**正規化されずに大文字小文字そのまま**辞書キーになる一方（tenancy.py 71-84）、照合側は JWT の email を小文字化してから引く（auth.py 211-218）。design §16.1 の「小文字正規化 → ドメイン一意（起動時に重複拒否）」という前提が、台帳側で成立していない。
  - 破綻シナリオ: (a) `TENANTS_JSON` を手書きしてテナントBに `MERIDIAN-CARE.EXAMPLE`、テナントAに `meridian-care.example` を登録すると、**重複ドメイン検査をすり抜けて起動が成功**し、B のユーザーは全員 A のテナントに解決される（A に同じ email の identity があれば A のデータへ、無ければ 403 でログイン不能）。設計が「ドメイン一意」を越境防止の前提に置いているのに、その起動時ガードが素通りする。(b) `system_accounts` に SA メールを大文字混じりで書くと `resolve_by_system_account` が永久に外れ、iap モードで Scheduler/Runtime が human 扱い→ドメイン解決→403 となり、**毎朝の sweep が静かに落ちる**（401/403 は Scheduler 側では失敗として出るが、原因が台帳の字面であることは追いにくい）。
  - 提案: `TenantConfig` 生成時に `email_domains` / `system_accounts` を `strip().lower()` して格納する（`_config_from_entry` と `single()` の2箇所、数行）。重複検査も正規化後の値で行う。

### 確認して問題なしとした観点（参考）

- **iap モードでAPIキー経路が死んでいること**を実測で確認（`AUTH_MODE=iap` + 正しい `X-API-Key` / `?api_key=` / `X-Goog-Authenticated-User-Email` 単独 → いずれも 401 "Missing X-Goog-IAP-JWT-Assertion header."）。`X-Goog-Authenticated-User-Email` はコード全体で未参照。JWT 検証は google-auth 2.56.3 の `jwt.decode`（`_ALGORITHM_TO_VERIFIER_CLASS` は RS256/ES256/ES384 のみ＝alg=none/HS256 の混同不可）＋ aud/exp/iat（skew30s）、`iss` は resolver 側で照合。鍵取得の初回失敗は 401（fail-closed）。`IAP_AUDIENCE` の形式検査は iap のみ。
- **権限表の全ルート実装**: server.py の `/api/*` 全12ルートが `Depends(get_principal)` を持ち、`_deny_system`（query/status/asks/consent/confirm/review/dismiss）・`_deny_human`（sweep/probe）・`_require_self_employee` / `_require_self_agent` / `_require_card_owner` が表どおりに置かれている。`/attachments/{id}` は辞書引きのみでパストラバーサル無し、静的3画面はデータを含まない。CORS 未設定＋JSONボディ必須のため、iap モードでの cross-site 書き込み（CSRF）は成立しない（ボディ無しの sweep/probe は human 403）。
- **テナント越境**: 鍵は完全一致の辞書引き（前方一致・空鍵・複数テナント同一鍵はいずれも起動時か照合時に拒否）、全ルートが `ContextRouter.for_tenant(principal.tenant_id)` の1点からしか store を得ない、`static_counts` は `TenantContext` 内、sweep は自テナントのみ、`preset_store` は単一テナント経路でのみ束縛。エラー文に他テナント情報は出ない（"Unknown tenant." のみ）。
- **コネクタの情報取扱い**: `logger.info` は件数・complete・error_count のみ、`errors` は「context: HTTP <status>」「request failed (<例外クラス名>)」で本文・資格情報を含まない、`SyncSummary`/sweep 応答も件数のみ、`gws_probe.py` は件数・種別のみ出力（`--apply-to-memory` も kind/status の集計のみ）。スコープは tasks/calendar/gmail の readonly 3本。Gmail は `GWS_GMAIL_ENABLED` 既定 false ＋ `kd-secretary` ラベル必須、件名80字・本文2000字で切り詰め。保持期限は実測で動作（処理済み本文は同一 sweep 内で空に、14日超は削除。`received_at` は空なら sync 時刻が入るので判定不能で残り続けることはない）。監査 payload は `{mail_id, item_key}` のみで件名・本文を含まない。
- **consent（W-1）の原子性**: InMemory は `_consent_lock` 下で存在/宛先/intent/pending を検査して遷移（`save_message` が `_messages` と `_messages_by_id` に同一オブジェクトを入れるため in-place 更新が両方に反映される）、Firestore は `@firestore.transactional` で同一検査。二重POST=409、他人宛=403、未知=404 をテストが押さえている（test_auth.py TestConsentCas）。
- **M3 の無痕跡・fail-closed マスクへの回帰なし**: 本コミット範囲で `schemas.py` / `transmission.py` / `matching.py` は無変更。既存199件（skip 13）green。`models.py` の追加は `Task.source` / `last_seen_due` / `Schedule.source` のみでマスク対象フィールドに触れていない。
