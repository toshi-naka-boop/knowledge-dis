auditor: red-team security auditor (authn/authz lens) — Fable 5 / Claude Code, 2026-08-30

# knowledge-discovery 認証・認可 レッドチーム監査

対象: `src/knowledge_discovery/auth.py` / `server.py` / `tenancy.py`
観点: (a) 無資格での特権エンドポイント呼び出し / (b) 別プリンシパル・別テナントへのなりすまし / (c) demo→system 昇格・他従業員データ読み取り。

重要な前提: **実際にデプロイされる構成は `AUTH_MODE=demo_key`**（既定）。この構成では `PrincipalResolver` が返す `mode` は常に `"demo"` であり、`"human"` / `"system"` は生成されない。したがって `_deny_human` / `_deny_system` / `_require_self_employee` / `_require_self_agent` / `_require_card_owner` の human/system 分岐は**本番デモでは全て休眠**する。human/system を前提にした指摘は原則「理論的（IAP 本番構成のみ）」に分類する。

---

## サマリ（重大度順）

| ID | 重大度 | タイトル | 分類 |
|----|--------|----------|------|
| F1 | Medium | API キーが query param (`?api_key=`) 受理 → アクセスログ / Referer / 履歴に漏洩 | 実在（demo_key・UIが使用） |
| F2 | Medium | `/api/secretary/digest` に `_deny_system` 欠落 → IAP で system が任意従業員の digest を読める | 理論的（IAP本番のみ） |
| F3 | Low | demo モードはテナント内 god-mode（`_require_self_*` が demo を拘束しない） | 実在だが設計どおり（demo簡略化・許容） |
| F4 | Low | `/attachments/{doc_id}` は完全に未認証（principal 依存なし） | 実在（内容は非機密の固定サンプル） |
| F5 | Low | OIDC トークンに nonce/jti なし → 有効期間内リプレイの余地 | 理論的（TLSで緩和） |
| F6 | Low | API キー照合が `dict.get`（定数時間比較でない） | 理論的（実害ほぼなし） |

---

## 個別 probe への回答

### 1. OIDC エンドポイント `POST /internal/autonomous-sweep`

**トークン無しで到達できる AUTH_MODE があるか → 健全。**
このルートは `get_principal`（＝AUTH_MODE 依存の resolver）に**依存していない**（server.py:863-864 は `request: Request` のみ）。したがって demo_key / iap のどちらでも挙動は同一で、AUTH_MODE によるバイパス経路は存在しない。認証は `verify_autonomous_sweep_token` のみ。さらに `AUTONOMOUS_SWEEP_AUDIENCE` か `AUTONOMOUS_SWEEP_INVOKER` のどちらかが未設定なら **404 で不活性**（server.py:885-888、fail-closed）。トークン欠落は 401（auth.py:260-265）。**健全。**

**`aud` は本当に検証されるか → 検証される（が aud 単独は防御にならない）。**
`google_id_token.verify_token(token, audience=audience, …)`（auth.py:275-280）に env の `AUTONOMOUS_SWEEP_AUDIENCE` を渡しており、google-auth が署名検証と同時に aud 一致を強制する。ただし OIDC ID トークンの `aud` は**トークン発行を要求する側が任意に指定できる**値であり、自分の GCP SA を持つ攻撃者は `aud=<設定値>` のトークンを合法的に鋳造できる。よって **aud は攻撃者にとって障壁にならない（defense-in-depth に留まる）**。設定値自体は秘密ではない前提で評価すべき。

**別プロジェクトの Google 署名 OIDC トークンで通るか → 通らない。真の gate は `email==invoker`。**
検証条件は署名（＝任意の Google 発行トークンで通過）＋ aud（攻撃者が偽装可能）＋ iss ∈ {accounts.google.com}（auth.py:285）＋ `email==invoker_email`（auth.py:289-292）＋ `email_verified is True`（auth.py:294-296）。このうち攻撃者が偽装**できない**のは `email` クレームだけ。`email` は「トークンを発行した SA 自身のアドレス」で Google が固定するため、`invoker_email` の SA を actAs / tokenCreator できない限り一致させられない。**したがって全セキュリティは「攻撃者が invoker SA のなりすましトークンを入手できないこと」に集約される**。invoker SA のメールアドレス自体は秘密ではない（推測可）が、そのメールを持つトークンを鋳造する権限がなければ無害。これは Google の標準 SA→SA 認証パターンで、**健全**。ただし「email のみが実質 gate」なので、invoker SA への `roles/iam.serviceAccountTokenCreator` 付与範囲の最小化が唯一かつ最重要のコントロールになる（コードの責任外・運用事項）。

**`email_verified` は検証されるか → される（auth.py:294）。健全。**

**リプレイ / nonce → F5（Low）。** nonce/jti 検証はない。realのinvokerの有効トークン（寿命〜1h）を経路上で捕捉すれば有効期限内はリプレイ可能。ただしサーバ間 TLS で保護され、トークンがログ等に落ちない限り捕捉困難。実害 Low。

### 2. API キー

- **ログ/エラーへの露出 → 健全。** リクエストログ用ミドルウェアは存在しない（grep 済み）。401 本文は `"Invalid or missing API key…"` で鍵値をエコーしない（auth.py:98-100）。
- **OIDC ルートで API キーが受理されるか → されない。** `run_autonomous_sweep` は `get_principal` を呼ばず query/header の api_key を一切見ない（server.py:863-895 のコメント「never by API key or query param」通り）。逆に Bearer トークンは API キー系ルートで受理されない。**健全。**
- **定数時間比較か → F6（Low）。** `TenantRegistry.resolve_by_api_key` は `dict.get`（tenancy.py:156-157）。線形バイト比較ではなくハッシュ照合のため、高エントロピー鍵に対するリモートタイミングオラクルは事実上成立しない。実害ほぼなし。
- **query param 受理による漏洩 → F1（Medium・実在）。** auth.py:95 は `request.query_params.get("api_key")` を受理。UI 各画面は実際に `?api_key=` で叩く設計。query string は Cloud Run のリクエストログ（URL 全体が残る）、外部リソース読込時の Referer、ブラウザ履歴、中間プロキシに残留する。**デプロイ済み demo_key 構成で実在する漏洩ベクタ**。ヘッダ `X-API-Key` のみ受理に絞れば解消するが、デモ UI 都合の簡略化でもある。

### 3. プリンシパル混同

- **demo が任意 victim の requester_id/employee_id を指定して読めるか → 読める（テナント内・設計どおり）。** `_require_self_employee` / `_require_self_agent` / `_require_card_owner` はいずれも `mode=="human"` のみ拘束し demo/system は素通り（server.py:400-431）。したがって demo プリンシパルは同一テナント内で任意従業員の status/digest/cards/agent を読み書きできる。**これはデモのペルソナ切替を前提にした god-mode で、demo キーがテナント共有シークレットであることを根拠に許容**（F3）。テナント越えは不可（下記4）。
- **`/api/secretary/digest` の system 読み取り → F2（Medium・理論的）。** 同ルートは `_require_valid_employee_id_format` → `_require_self_employee` のみで **`_deny_system` を欠く**（server.py:728-737）。対照的に `/api/secretary/autonomy` GET/PUT・`/api/query` 等は `_deny_system` を持つ。IAP 構成では sweep 用 system プリンシパルが**任意従業員の朝ダイジェスト（当人の私的項目由来の停滞カード等を含みうる）を読める**。これが §16.1 権限表に対する唯一の実質的なガード非対称。demo_key 本番では system が生成されないため休眠（＝理論的）。修正は 1 行 `_deny_system(principal)` 追加。

### 4. テナント分離

**健全。** データベースは常に `router.for_tenant(principal.tenant_id)` で解決され（server.py:378-385）、tenant_id は resolver が鍵/JWT から導出する。**body/query/header からテナントを指定できるルートは存在しない**（`X-KD-Tenant` 相当なし）。`/internal/autonomous-sweep` は全テナントを反復するが、反復対象は `registry.tenants`（サーバ主導）で**呼び出し側はテナントを選べない**（server.py:899-903）。鍵↔テナントは 1:1 束縛で、起動時に tenant_id/database/domain/api_key の重複を全て弾く（tenancy.py:66-93）。テナント A の鍵で B の Firestore database には到達不能。**分離主張は正確。**

### 5. ルート/ガード整合マトリクス

全 19 ルートを個別照合した結果:

| ルート | ガード | 評価 |
|--------|--------|------|
| `GET /` `/requester` `/candidate` `/audit` | 認証なし（HTML シェル） | 許容（S-2: 唯一の未認証面、データ無し） |
| `GET /attachments/{doc_id}` | **principal 依存なし** | F4（Low）: 未認証で固定3文書配信。dict.get のためパストラバーサル無し。内容は非機密の架空サンプル・全テナント共通 |
| `GET /api/me` | get_principal | 自分の identity のみ返す。健全 |
| `GET /api/agents` | principal+context（_deny 無し） | テナント内エージェント一覧。ディレクトリ情報、許容 |
| `POST /api/query` | _deny_system + _require_self_employee | 健全（demo は設計どおり任意 requester 可） |
| `GET /api/requester/{id}/status` | _deny_system + _require_self_employee | 健全 |
| `GET /api/candidate/{agent_id}/asks` | _deny_system + _require_self_agent | 健全 |
| `POST /api/candidate/{agent_id}/consent` | _deny_system + _require_self_agent | 健全 |
| `GET /api/audit/messages` | principal+context（_deny 無し・_require_self 無し） | human はテナント全体の監査（マスク済）を閲覧可。監査ビューの性質上許容。Low |
| `POST /api/secretary/sweep` | _deny_human + origin ゲート | 健全（scheduled は autonomy policy 下、manual は human override） |
| `GET /api/secretary/digest` | 形式 + _require_self_employee（**_deny_system 欠落**） | **F2** |
| `GET/PUT /api/secretary/autonomy` | _deny_system + 形式 + _require_self_employee | 健全 |
| `POST /api/secretary/confirm` | _deny_system + _require_card_owner | 健全 |
| `POST /api/secretary/profile-diff/{card_id}/review` | _deny_system + _require_card_owner | 健全 |
| `POST /api/secretary/cards/{card_id}/dismiss` | _deny_system + _require_card_owner | 健全 |
| `POST /internal/autonomous-sweep` | OIDC（env ゲート + verify_autonomous_sweep_token） | 健全（probe 1 参照） |
| `POST /api/probe/unregistered-intent` | _deny_human | 健全（demo/system のみ、意図的な赤行デモ） |

新規の autonomy/sweep 系ルートで**ガード表から漏れているのは F2（digest の _deny_system）のみ**。他の新規ルート（autonomy GET/PUT・sweep・autonomous-sweep）はガードが揃っている。未認証 HTML ルートは静的シェルで漏洩なし。

**demo→system 昇格 → 不可能（健全）。** mode は resolver が鍵/JWT から決定的に固定し、demo キーは必ず `mode="demo"` を返す。system になるには IAP 署名 JWT が必須で、IAP モードは API キーを受理しない。昇格経路なし。

---

## 所見の分類

**実在（デプロイ済み demo_key 構成で悪用可能）**
- F1: `?api_key=` のログ/Referer 漏洩。
- F3: demo はテナント内 god-mode（ただし設計どおりの簡略化）。
- F4: `/attachments` 未認証（内容は非機密）。

**理論的（IAP 本番構成でのみ顕在化）**
- F2: digest の `_deny_system` 欠落による system の他従業員 digest 読み取り。
- F5: OIDC リプレイ余地。

**意図的なデモ簡略化（許容）**
- F3（demo god-mode）: demo キーはテナント共有シークレットであり、テナント越え分離が保たれる限りペルソナ切替は仕様。
- 未認証 HTML シェル（S-2 で明文化）。

## 推奨修正（優先度順）
1. **F2（1行）**: `get_morning_digest` 冒頭に `_deny_system(principal)` を追加し、autonomy 系と整合させる（IAP 本番へ進むなら必須）。
2. **F1**: 提出後の本番運用に進むなら `api_key` query param 受理を廃し `X-API-Key` ヘッダのみに絞る。デモ継続中は「query キーはログに残る」ことを README/運用に明記。
3. **運用（コード外・最重要）**: OIDC の実質 gate は `email==invoker` のみ。invoker SA への `serviceAccountTokenCreator` 付与を Cloud Scheduler の 1 主体に限定する。
4. **F4**: 本番化時は `/attachments` に principal 依存を付けるか、公開静的コンテンツと割り切るなら明示。
5. **F5/F6**: 現状の脅威モデル（TLS・高エントロピー鍵）では対応不要。将来 nonce/`compare_digest` を検討。
