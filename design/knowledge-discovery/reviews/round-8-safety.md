critic: claude design-critic（モデル: claude-opus-5[1m]）

## Round 8 — 2026-08-19 — 反証(安全性)

対象: コミット範囲 360f261..HEAD（M3実装）。読んだもの: design.md v9 §3/§4/§7/§14、ledger.md、secretary.py / matching.py / models.py / schemas.py / server.py / store.py / firestore_store.py / transmission.py / service.py / web/requester.html / scripts/generate_seeds.py。検証は `.venv/bin/python3` の再現スクリプト（scratchpad、src/ は無変更）。

### 指摘

- [S-6] 種別: 実装 / 深刻度: high
  - 指摘: `extract_profile_diff` のヒューリスティック fallback が「メール本文まるごと」を profile item 本文にしており、しかも `SecretaryService` はどの起動経路でも `llm_client` が渡されないため、この fallback が**唯一の実挙動**になっている。既定ボタン「Apply (Public)」で生の受信メール全文が `visibility=public` / `reviewed=true` の項目として profiles に書かれ、`embedding` と `embedding_public` の両方が再生成される。
  - 破綻シナリオ: `server.py:435` の `SecretaryService(store=..., kd_service=..., matching_engine=...)` に `llm_client` 引数がなく、`create_app_from_env` も Gemini を matching_engine にしか配線していない → `secretary.py:268-273` の `return "current_work", f"{subj}: {body}"` が常に走る。実測（DEMO_TODAY=2026-08-19、シード投入済み InMemoryStore）:
    - 差分カード payload の `body_draft` = `"Update on ambulatory surgery center partnership discussions: Hi Jordan, regarding our discussions with St. Jude ASC, we have finalized the preliminary staffing protocol for outpatient surgical teams. We are also tracking surgical suite utilization patterns across our regional network. Please update your records."`（メール全文）
    - `review_profile_diff(action="apply")` 後の profiles: `[current_work] vis=public src=mail_seed body='Update on ambulatory surgery center partnership...'`
    - つまり取引先名（St. Jude ASC）・未確定の人員配置プロトコル・自社ネットワークの稼働統計という、まさにこのデモが「守る」と主張している類の内容が、要約も抽出もされずに公開索引（`embedding_public`＝プレビュー1段目の対象）と公開2段目コンテキストに入る。デモ4体の誰かに mail_seed を1件足せば、その本文が他人の質問に対する `reason_text` として引用され得る（`connect_ask` は AUDIT_WHITELIST 入りなので監査画面にも平文表示される）。
    - 加えて `item_key` が常に `"current_work"` で、シード4体全員がそのキーの public 項目を持つため、**手で作り込んだ公開項目本文が上書き消去**される（design §14.5-3 は「`profiles.items` に追加」と規定）。
    - 検証ゴール16は「項目が追加され embedding が再生成され `cited_item_keys` に現れ得る」までしか見ないので、この状態でも**ゴールは通ってしまう**。
  - 提案: (a) `create_app_from_env` で genai クライアントを `SecretaryService(llm_client=...)` に配線する。(b) fallback は「メール全文を本文にする」のをやめ、`None`（差分なし＝カードを作らない）に倒す。design §14.5-1 は「差分なしなら null を許す」と書いてある。(c) 公開反映の直前に `TransmissionLayer._reason_leaks_private` 相当で本人の private 項目断片を走査し、ヒットしたら public 反映を拒否する。

- [S-7] 種別: 実装 / 深刻度: mid
  - 指摘: `review_profile_diff` が同一キーの既存項目を見つけると `body` / `source` / `visibility` を上書きする（`secretary.py:740-745`）。`visibility` は action から決まるため、**既存の private 項目が `apply` 一発で public に反転**する。さらに対象キーはリクエストボディの `item_key`（`server.py` `ProfileDiffReviewRequest`）で呼び出し側が自由に指定でき、カード payload の値と突合されない（`secretary.py:721` `key = item_key or card.payload.get(...)`）。
  - 破綻シナリオ: 実測。Elena の profile_diff カード（mail_seed を1件足せば通常の sweep で生成される）に対し `POST /api/secretary/profile-diff/<card_id>/review {"action":"apply","item_key":"transition_pipeline"}` を投げると、
    - before: `[transition_pipeline] vis=private body='Currently advising two unannounced clinic succession deals... Details under NDA.'`
    - after: `[transition_pipeline] vis=public body='attacker text'`、`embedding_public` 再生成
    つまり (1) NDA 項目の visibility が本人の明示操作なしに public 化され、以後その項目はプレビュー1段目・公開2段目コンテキスト・`cited_item_keys` の対象になる（非公開項目打診 §4 の入口が丸ごと消える）、(2) private 本文が破壊される。UI は現状 `item_key` を送らないが、エンドポイントは共有APIキーの内側で誰でも叩ける。悪意がなくても、LLM 配線後に `item_key` が既存の private キーと衝突すれば同じことが起きる。
  - 提案: `item_key` はリクエストから受け取らず、カード payload の値に固定する（編集させるなら `edited_body` のみ）。既存項目がある場合は visibility を触らない。既存キーが private のときは反映を拒否し、別キーでの追加か明示的な公開転換フローに落とす（design の「追加」規定に合わせる）。

- [S-8] 種別: 実装 / 深刻度: mid
  - 指摘: `confirm_stagnation_card` の docstring は "Atomic CAS" を主張しているが、実体は `get_card` → 判定 → `save_card` の read-modify-write で、Firestore トランザクションを一切使っていない（`store.py` / `firestore_store.py` の `save_card` は素の set）。design §14.4（X-3対応）が明文で要求した排他が実装されていない。
  - 破綻シナリオ: 「Request an intro」ボタンは押下時に disable されない（`requester.html` `confirmStagnationCard`）。ダブルクリック＝2本の同時POST。FastAPI の同期エンドポイントはスレッドプールで並行実行され、Firestore 版では get/save が各1RTT なので窓が広い。ストアに20msの遅延を入れた再現（`LatencyStore(InMemoryStore)`、src/ は無変更）で:
    - `results: [{'status':'confirmed','query_audit_id':'msg_43851158a481'}, {'status':'confirmed','query_audit_id':'msg_4d2e26d0237f'}]`
    - `query messages: 2 | connect_ask messages: 2`
    本人の1回の確定意思に対し候補者へ**同じ打診が2通**届き、候補側の受信箱と監査に二重の痕跡が残る。カードの `linked_query_audit_id` は後勝ちの片方しか指さないので、監査上のカード↔質問の対応も1本壊れる。検証ゴール13の「二重POSTで質問投入が1回」は逐次POSTでしか成立しない。
  - 提案: `Store` に `try_confirm_card(card_id) -> bool` を足し、Firestore 側は `@firestore.transactional` で `status=="open"` を条件に `confirmed` へ遷移（in-memory 側は `threading.Lock`）。UI 側もボタンを即 disable。

- [S-9] 種別: 実装 / 深刻度: low
  - 指摘: `requester.html:244` のリマインド行で `${tagText}` が未エスケープ。`due_category` が `overdue`/`today`/`tomorrow` 以外（＝`upcoming`、または `due_date` がパース不能で `upcoming` に落ちた場合）に `tagText = r.due_date` が生値のまま innerHTML に入る。round-6 S-4「esc() 全面適用」の局所回帰。
  - 破綻シナリオ: `schedules/{item_id}.due_date` に `<img src=x onerror=...>` のような文字列が入ると（パース失敗→`upcoming`→生挿入）、requester ページで実行される。このページは URL クエリに `api_key` を保持しているので、注入コードは `location.search` からキーを読んで `/api/audit/messages` を丸ごと外部に投げられる。書き込み経路がサーバー側シードのみなので実害到達性は低いが、esc() 適用の網は塞いでおくべき箇所。
  - 提案: `${esc(tagText)}` に直す（1文字修正）。

- [S-10] 種別: 実装 / 深刻度: low
  - 指摘: `/api/secretary/*` は呼び出し元と対象の本人性を一切突合しない。`GET /api/secretary/digest?employee_id=` は任意の社員の停滞カードを返し、`confirm` / `dismiss` / `profile-diff/{card_id}/review` は `card_id` だけで他人のカードを操作できる。design §14.4 は「停滞検知・カード・ダイジェストは本人UI以外に露出しない」と明記しており、README には秘書関連の記述が皆無（`grep -n "secretary\|digest\|sweep\|DEMO_TODAY" README.md` → 0件）。
  - 破綻シナリオ: 同じデモURLと単一APIキーを持つ人（`/candidate?api_key=...` を開いている観客含む）が `employee_id` を差し替えるだけで、他人のタスク名・停滞根拠一行・AI質問下書き・プレビュー候補の氏名と理由文を読める。実測: `get_morning_digest('emp_jordan_lee')` は `task_title: "Riverside Clinic Relocation Assessment"`、`evidence_line: "Rescheduled 2 times, overdue by 3 days..."`、`question_draft`、候補名＋`reason_text` をそのまま返す。round-6 S-3（候補受信箱のエージェント切替）は「デモ用の意図的仕様としてREADMEに明記」で決着したが、M3 で増えたこの読み取り面は未記載。なお `/api/query` が既に任意の `requester_id` を受ける既存の割り切りがあるため、操作系（confirm/dismiss）は新しいクラスの穴ではない。
  - 提案: README に M3 の割り切りを1行追記（round-6 S-3 と同じ扱い）。安価なので、`digest`/`confirm`/`dismiss`/`review` に `employee_id` を必須引数として受け、`card.owner_employee_id` との一致だけ検査する程度の突合は入れてよい（デモの1人4役ドロップダウンとは両立する）。

### 確認して問題なしと判断した観点（賛成側の根拠）

1. **プレビュー無痕跡は成立している**: sweep 実行後の `messages` は `stagnation_detected` 2件 / `preview_search` 1件 / `profile_diff_proposed` 1件のみで、`connect_ask`・`connect_ask_private` は0件（実測）。`preview_search` は `MatchingEngine.preview_search`（送信を持たない純粋関数）を直接呼び、`transmission.send` は `to="system"` の監査記録にしか使われない。C-29 の「フラグを作らない」判断がコードレベルで守られている。
2. **fail-closed は回帰していない**: `AUDIT_WHITELIST` に新intent3種は入っておらず、`SchemaRegistry.get_audit_view` が3種すべてに `{"masked": true, "note": "表示不可（マスク既定）"}` を返すことを実測で確認。`preview_search` payload に候補 employee_id とスコアが保存され表示だけがマスクされる、という §14.6 の設計どおり。タスク名・質問下書き・候補名は監査 payload に入っていない。
3. **プレビューの public 限定は両段で効いている**: 1段目は `embedding_public`、2段目は public 項目だけで再構築した `Profile` を渡し、戻ってきた `cited_item_keys` を public キーで再フィルタしている（`matching.py preview_search`）。VECTOR_FLOOR 非適用も設計どおり。カード payload・digest レスポンス・`preview_search` 監査 payload のいずれにも private 本文が乗る経路は見つからなかった（S-7 で public 化された後は別問題）。
4. **既存の private マスク1ルールは M3 が壊していない**: `transmission.py` の `cited_item_keys` 実在検証＋`reason_text` 断片スキャン＋fail-closed 昇格は無変更で、confirm 経由の正式実行は既存の `submit_query` をそのまま通る（private 込みの再検索と §4 の非公開打診はここで初めて発動する、という §14.4 の想定どおり）。
