auditor: red-team security audit (information disclosure & injection lens) — claude-opus-4-8, 2026-08-30

# Security audit — 情報漏洩 & injection（autonomous-agent フェーズ）

対象: `schemas.py` `transmission.py` `secretary.py` `matching.py` `models.py` `server.py` `service.py` `auth.py` `gemini_adapters.py` `connectors/google_workspace.py`。
レンズ: 4つの中核約束（非公開項目は本人以外に出ない／マスクは型システムで fail-closed・LLM 自己申告非依存／未検品 AI 文を本人名義で流通させない／人間承認前の監査は counts のみ）を破れるか。

結論を先に: **counts-only 監査の約束・attachment ルート・auth ログ/エラーは健全。** 実務上意味のある残存リスクは1件（D1: 既存クエリ経路の Gemini 推論が private 込みの profile を受け取り、public keys だけ cite しつつ reason_text に非公開内容を言い換えて載せると、public connect_ask として監査ダッシュボードに表示され得る）。他は理論上・軽微・デモ簡略化。

---

## D1. マスク回避: public connect_ask の reason_text に非公開内容が言い換えで載る（実クエリ経路）
- **severity: MEDIUM**（real-exploitable だが Gemini の挙動依存 / 影響先は監査ダッシュボードと本人）
- **区分: real（条件付き）**
- **precondition**: `POST /api/query`（既存のオンデマンド経路。autonomous sweep ではない）。候補者 profile に private item が存在し、Gemini が「非公開の内容に言及するが cited_item_keys には public key だけを挙げる」応答を返すこと。
- **経路**: `matching.py:413,429` `run_matching` は Gate2 で **full profile（private item 込み）** を `inferencer.infer_connection(question, profile)` に渡す。`gemini_adapters.py:184-186` `_format_profile_context` は private を含む全 item を本文ごとプロンプトに入れる。応答 reason_text は `service.py:117` で connect_ask payload に入る。`transmission.py:193-197` のマスク判定は `has_any_private(cited_list)`（型システム=実 visibility 参照）＋ unknown key ＋ `_reason_leaks_private` の3条件。Gemini が private key を cite すれば `has_any_private`→マスク（健全）。しかし **public key だけ cite しつつ非公開内容を言い換え**た場合、cited は全て public、fragment scanner も逐語一致でないと素通り → connect_ask のまま。`schemas.py:269` で connect_ask は AUDIT_WHITELIST に入るため `get_audit_view` が payload 全体（reason_text 含む）をそのまま返し、`server.py:668` `/api/audit/messages` で同一テナントの任意の human/demo に表示される。
- **なぜ核約束に触れるか**: 設計は「マスクは LLM 自己申告に依存しない」。マスク**判定**（connect_ask→connect_ask_private 昇格）は実 visibility 駆動で健全。だが reason_text という**自由文の中身**の監査可視性は、Gemini が非公開参照時に private key を cite することに実質依存しており、その部分だけ自己申告依存になっている。fragment scanner はバックストップだが下記 D2 の通り言い換え/非連続で回避可能。
- **fix**: (a) 実クエリ経路の Stage-2 も preview 経路（`matching.py:289-307`）と同様 public-only profile を推論に渡す。private を使いたい設計なら (b) connect_ask（非マスク）の監査表示から reason_text を落とし、audit_payload 相当の要約に限定する。最小修正は (b)。

## D2. `_reason_leaks_private` fragment scanner の回避可能性
- **severity: LOW**（D1 の二次防御としての限界。単体では新規漏洩を生まない）
- **区分: theoretical**
- **precondition**: D1 と同経路で、非公開本文を逐語ではなく変形して reason_text に載せる。
- **経路**: `transmission.py:44-59`。正規化は `lower()` + 空白除去のみ。回避例: (1) **言い換え/語順変更**で15文字連続一致を崩す、(2) **Unicode 等価文字**（全角/合字/類似字形）で正規化後も別バイト列、(3) 本文長 <12 の短い private item は `norm_body in norm_reason` の完全包含のみ判定で、間に1文字挟むと不一致、(4) 大文字小文字以外の正規化（NFKC 等）が無い。casing のみは吸収される（健全）。
- **fix**: これは本質的に不完全な防御なので、D1 の fix（推論に private を渡さない or reason_text を監査非表示）で根本を断つのが正。scanner 強化（NFKC 正規化・トークン集合の n-gram 一致）は緩和にすぎない。

## D2b. preview 経路（autonomous sweep）は public-only で健全
- **区分: 健全**
- **mechanism**: `matching.py:274-307` `preview_evaluate` は候補ごとに `visibility=="public"` の item だけで `public_prof` を再構築し、`embedding=embedding_public` を渡して isolated 推論。`compute_profile_embedding`（`matching.py:218-233`）が embedding_public を public item のみから生成。`preview_shortlist`（`matching.py:265-267`）は `embedding_public` のみでランク。cited_item_keys も `safe_cited = [k for k in ... if public_prof.get_item(k)]`（`matching.py:312`）で public に限定。**autonomous フェーズの候補探索は private item を一切見ない。**

---

## D3. 人間承認前の autonomous 監査は counts-only（probe 1）— 健全
- **区分: 健全（複数の独立防御）**
- Sweep が生成する監査メッセージは5種。`/api/audit/messages` の `display_payload`（`service.py:318` `get_audit_view`）で以下が保証される:
  - **sweep_run / policy_limited**: AUDIT_WHITELIST 収録だが `schemas.py:210-239` validator が **完全一致キー集合**を要求し、数値キーは `bool 拒否・int・>=0`、`schema_version==1`、`stage` は enum、文字列キー（origin/run_key/date）は**サーバ生成のみ**（`server.py:173-193` `_scheduled_run_key` は `tenant_id + "-" + sha256(...)[:16]`、manual は `"manual-"+uuid4`）。攻撃者自由文が値に載る経路が無い。加えて `get_audit_view`（`schemas.py:263-267`）が SWEEP_RUN_KEYS/POLICY_LIMITED_KEYS へ**再射影**（未知キー drop）する二重防御。`_send_internal_audit`（`secretary.py:429`）は送信前に再度 validate。
  - **stagnation_detected / preview_search / profile_diff_proposed**: `schemas.py:45-58` で **AUDIT_WHITELIST から意図的に除外** → `get_audit_view` の fail-closed fallback（`schemas.py:275-278`）が固定 MASKED_NOTES を返す。よって payload 内の task_id・candidate employee_id（`secretary.py:1074`）・item_key・mail_id は**表示されない**。
- 候補名・task_title・reason_text・question_draft は **Card payload**（`secretary.py:1007-1027`）にのみ入り、Card は messages コレクション外。Card を返すのは `get_morning_digest`（`secretary.py:1257-1265`）のみで、`server.py:736` `_require_self_employee` により**本人（Jordan）限定**。監査経路に Card 内容が到達するコードパスは無い。

## D4. 下層 message payload の残留（probe 1 の裏面）
- **severity: LOW（defense-in-depth 指摘）**
- **区分: deliberate-design（表示層マスクで保護、データは保持）**
- `store.save_message` は payload を verbatim 保存するため、preview_search（候補 employee_id + score, `secretary.py:1074`）・stagnation_detected（task_id）・profile_diff_proposed（mail_id, item_key）の**生 payload は Firestore に残る**。API 経由では whitelist 除外のマスクで守られるが、この3種は sweep_run と違い **key 再射影を持たず、防御が「whitelist 非収録」の一枚のみ**。将来 whitelist に誤追加すると即漏洩。Firestore 直接アクセス権を持つ者には見える。
- **fix**: 現状 API 表面は健全。堅牢化するなら sweep_run 同様に「保存時点で counts/ID 最小化」または表示層の allowlist projection をこの3種にも適用。

---

## D5. Prompt injection（probe 3）— 影響範囲は isolation + human-review で限定、intent 注入不可
- **区分: 概ね健全 / 残存は LOW**
- **(a) 他ユーザーの非公開データ持ち出し**: 候補評価は per-candidate isolation（`matching.py` C-17、preview は public-only D2b）。profile への注入は**その候補自身の reason_text**（本人が見る）にしか効かず、他候補の推論には別プロセスで到達しない。question 側（=依頼者自身の task）注入は全候補推論に流れるが依頼者自身の文。**cross-user 漏洩は構造上不可**。唯一の実リスクは D1（自分＝候補の private が監査に出る）。
- **(b) 未登録 intent / malformed payload**: intent と payload_type は全て**コード側でハードコード**された `transmission.send(...)` 呼び出し（LLM 出力から intent を作る経路が無い）。LLM 出力は自由文（question_draft, body_draft, reason_text）のみ。未登録 intent は送信層 `schemas.py:111` で `reject_unregistered_type`。**注入で intent を作れない。**
- **(c) why-you note の中傷/誤誘導**: reason_text は候補本人（consent 画面 `server.py:619`）と監査に出る。isolation により他人を貶める材料（他人の profile）は同一推論に入らない。question_draft（`secretary.py:242-269`）・body_draft（`extract_profile_diff`）は Card に入り、confirm / 4-way review で**本人が検品してから**クエリ化・profile 反映（`secretary.py:1267-1406`）。未検品 AI 文が本人名義で自動流通しない（核約束③健全）。
- **セキュリティ判断の LLM 依存**: マスク**判定**は `has_any_private`（実 visibility）＋ unknown-key fail-closed で LLM の cited_item_keys を信用しない（`transmission.py:191-197`）。`gemini_adapters.py:253-258` も cited 欠落時に profile key を捏造しない（V-1）。profile_diff の visibility は**本人の action**（`private_apply` 等 `secretary.py:1361`）が決め、LLM 非依存。item_key は Card payload 由来で呼び出し側が差し替え不可（`secretary.py:1355`）。**核約束②は判定レベルで健全**（自由文中身の残存が D1）。

## D6. 監査ダッシュボードは public connect_ask の全文と question を露出（設計上）
- **severity: LOW / INFO**
- **区分: deliberate-demo（透明性のための設計）**
- `/api/audit/messages` は `_deny_*` を持たず、同一テナントの demo/human/system 全員が閲覧。public connect_ask は `get_audit_view` が payload 全体（question_summary=依頼者の質問全文, reason_text, requester_id, score）を返す。private 起因は audit_payload でマスクされるが、**public 質問文と public 接続理由は誰でも見える**。エンタープライズ運用では「誰が何を尋ねたか」の可視性ポリシー確認を推奨。デモでは意図的。

---

## D7. 4xx/5xx 本文のエラー漏洩（probe 4）
- **severity: LOW**
- **区分: real だが self-scope（本人のみ）**
- `server.py:815,839` の `confirm_stagnation_card` / `review_profile_diff` は `except Exception -> HTTPException(500, detail=str(exc))`。`confirm` の内部は `RuntimeError(f"Failed to submit discovery query: {exc}")`（`secretary.py:1318`）で、下層例外文字列（Firestore パス等）が本文に載り得る。ただし両ルートとも `_require_card_owner` で**本人のカード限定**なので、漏洩先は本人。API key / OIDC token は載らない。
- **fix**: 500 の detail を汎用文言に固定し、詳細は `logger.exception` のみ（`server.py:913` の autonomous-sweep と同じ方式）に統一。

## D8. autonomous-sweep 500 集約 & auth ログ（probe 4）— 健全
- **区分: 健全**
- `server.py:908-925`: テナント例外は `logger.exception`（サーバのみ）、レスポンス本文は固定 `"Sweep failed. See server logs for details."`。**例外文字列・スタック・パス・run 詳細を本文に出さない**（E4）。
- `auth.py:236-296` `verify_autonomous_sweep_token`: 401/403 の detail は固定2文言（`"authentication required"` / `"invalid token"`）。失敗理由は `logger.warning` のみ。ログ内容は google-auth 例外文字列（生 token を含まない）・issuer claim（URL）・「email 不一致」の固定文言で、**token 本体・API key・email 値はログに出ない**（`auth.py:291` は値を出さず固定文）。`DemoKeyResolver` は key をログしない。
- `connectors/google_workspace.py:143,369` のログは **owner_id と件数のみ**（task/mail の title/body 非出力）。

## D9. `GET /attachments/{doc_id}` 無認証（probe 5）— 健全
- **区分: 健全**
- **precondition**: なし（無認証）。
- **経路**: `server.py:466-472`。`SAMPLE_ATTACHMENTS.get(doc_id)`（**dict ルックアップ**）で、無ければ 404。doc_id は**ファイルパスに一切使われない**（パストラバーサル不可）、テンプレート展開も無い。露出するのは3つのハードコード静的文書（clinic relocation guide 等、架空 Meridian の一般ガイド、PII 無し）のみ。列挙しても3件以上は取れない。C-24 の意図的な静的配信。

---

## サマリ（重大度順）
| ID | severity | 区分 | 一言 |
|---|---|---|---|
| D1 | MEDIUM | real(条件付き) | 実クエリ経路の Gemini 推論が private 込み profile を受け取り、public key cite + 言い換えで非公開内容が public connect_ask の reason_text として監査表示され得る |
| D2 | LOW | theoretical | fragment scanner は言い換え/Unicode/非連続で回避可（D1 の二次防御の限界） |
| D4 | LOW | design | preview_search/stagnation/profile_diff の生 payload は Firestore 残留、防御は whitelist 非収録の一枚のみ |
| D6 | LOW/INFO | demo | 監査ダッシュボードは public 質問全文・接続理由を同一テナント全員に露出（意図的） |
| D7 | LOW | real(self) | confirm/review の 500 detail=str(exc) が内部例外を本人に露出 |
| D2b/D3/D5/D8/D9 | — | 健全 | preview public-only / counts-only 監査 / injection 影響限定・intent 注入不可・マスク判定は型システム駆動 / sweep500・auth ログ / attachments dict ルックアップ |

**核約束の評価**: ①非公開項目非漏洩=判定は健全、reason_text 自由文に D1 の残存。②マスクは型システム=**判定レベルで健全**（`has_any_private` 実 visibility + unknown-key fail-closed）、自由文中身が D1/D2。③未検品 AI 文の本人名義流通=健全（confirm/4-way review の human gate）。④人間承認前 counts-only=**健全**（whitelist 除外 + sweep_run/policy_limited の完全一致 validator + キー再射影の二重防御）。
