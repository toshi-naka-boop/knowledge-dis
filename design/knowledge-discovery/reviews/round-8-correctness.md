critic: claude design-critic = claude-opus-5[1m]

## Round 8 — 2026-08-19 — 反証(正しさ)

対象: コミット範囲 `360f261..HEAD`（M3秘書層）。基準: design.md v9 §14 と §10 ゴール12〜17。
検証環境: `.venv/bin/python3`、`tests/` 74件は全て OK（M1/M2の回帰なし）。以下の指摘はすべて実際にコードを読み、V-6/V-7/V-9 は再現スクリプトで実測した（src/ は変更していない）。

### 指摘

- [V-6] 種別: 実装 / 深刻度: high
  - 指摘: `confirm_stagnation_card` の「CAS」がトランザクションではなく単なる read-modify-write であり、並行 confirm で質問投入が二重に走る（design §14.4 の X-3 対応「`open → confirmed` の遷移と `linked_query_audit_id` の記録を1トランザクションで行う」が未実装。`Store` / `FirestoreStore` にトランザクションAPI自体が存在しない）。
  - 破綻シナリオ: `secretary.py:647-664` は `get_card()` → `status == "open"` 判定 → `card.status = "confirmed"` → `save_card()` の3ステップ。Firestore は get/set が各ネットワーク往復（数十ms）で、confirm ボタンには二重送信ガードも無い（`requester.html` の `confirmStagnationCard` はボタンを disable しない）。Firestore 相当のレイテンシ（get/save 各30ms）を注入した InMemoryStore で「依頼する」を同時2回POSTした再現では、両スレッドが `open` を読んで両方が通過し、**`query` メッセージ2件・`connect_ask` 6件（候補3名×2通）**が生成された（実測ログ: `RESULT: {'status':'confirmed', 'query_audit_id':'msg_4d97...'}` ×2、`query msgs: 2 / connect_ask msgs: 6`）。候補者の受信箱に同じ打診が2通並び、監査画面にも二重の配送が残る。ゴール13「confirmを二重POSTしても質問投入が1回しか起きない（CAS）」が満たされない。
  - なお `tests/test_secretary.py::TestConfirmCardCAS` は1回目の完了**後**に2回目を呼ぶ逐次テストで、docstring が謳う CAS 性質（並行時の排他）を検証していない。テストが通ることが本欠陥の不在を意味しない。
  - 提案: `Store` に `confirm_card_cas(card_id) -> Card | None`（Firestore は `@firestore.transactional` で「open のときのみ confirmed に遷移し、既に confirmed なら現値を返す」、InMemory は `threading.Lock` 下の compare-and-set）を追加し、`SecretaryService` はその戻り値でのみ分岐する。既存の逐次テストに加え、遅延注入 + 2スレッド同時実行で `query` メッセージが1件であることを assert する回帰テストを足す。

- [V-7] 種別: 実装 / 深刻度: high
  - 指摘: `review_profile_diff` が同一 `key` の既存プロフィール項目を**上書き**する（design §14.5-3 は「`profiles.items` に … を**追加**」）。`extract_profile_diff` の既定キーは `current_work` で、シードの4体全員が `current_work` を保持しているため、反映すると必ず既存本文が消える。
  - 破綻シナリオ: `secretary.py:740-745` の `existing_item.body = body` が該当。emp_rachel_kim 宛の mail_seed を1件足して sweep → 「Apply (Public)」を押した再現では、彼女の `current_work`（"Manages staffing accounts for 30+ hospital and clinic clients…" の4文。design §3 が「マッチングの主材料」と位置づける本文）が `"Client escalation follow-up: We closed the escalation with Metro General…"` に置換され、`source` も `seed_fixed → mail_seed` に書き換わった。上書き後に embedding / embedding_public を再生成するため、以後のマッチング品質がその1通のメール文面に支配される（プロフィールの主材料が復元不能に失われる。デモ中に押すと元に戻せない）。
  - 提案: 既存キーと衝突した場合は上書きせず、`f"{key}_from_mail"` 等の別キー、または `items` への純粋な追加とする（design の文言どおり「追加」に揃える）。回帰テストは「既存キーと同一キーの差分を反映しても元項目の body/source が保持される」を追加する（現行テスト (f) は既存キー `background` と提案キー `current_work` が食い違う条件のみを見ており、衝突ケースを踏んでいない）。

- [V-8] 種別: 実装 / 深刻度: high
  - 指摘: `server.py` の `SecretaryService(store=…, kd_service=…, matching_engine=…)` に `llm_client` が渡されておらず（`grep` 上、本番経路で `llm_client` を渡す箇所はゼロ）、本番でも `question_draft` は固定テンプレート、プロフィール差分はメールの生ペーストになる。design §14.4「質問下書きは title/description から Gemini が生成」・§14.5-1「差分候補を Gemini で抽出（差分なしなら null を許す）」が実機能していない。
  - 破綻シナリオ: シード投入 + sweep の実測で、ヒーローカードの下書きは `"Seeking expertise and advice regarding Riverside Clinic Relocation Assessment: Need to find suitable medical office site with required zoning (C-2/O-M) and parking ratios for Riverside Clinic relocation."`（`generate_question_draft` のフォールバック文＝タスク文の機械連結）、差分提案カードの `body_draft` は `"Update on ambulatory surgery center partnership discussions: Hi Jordan, regarding our discussions with St. Jude ASC, …Please update your records."`（件名＋本文まるごと）だった。デモ冒頭「秘書が質問を先に書いておく」シーンの下書きが定型文になり、差分提案は要約ではなくメール全文がプロフィール本文として提示される。さらに `extract_profile_diff` は LLM が明示的に `null`（差分なし）を返しても heuristic フォールバックに落ちる構造（`secretary.py:258-273`）のため、**LLM を接続しても「差分なし」を表現できず**、全 mail_seed が必ずカード化される。
  - 提案: `server.py` で `GEMINI_API_KEY` がある場合に `genai` クライアントを組み立てて `SecretaryService(llm_client=…)` に渡す（既存 `gemini_adapters._build_genai_client` を再利用）。併せて `extract_profile_diff` は「LLM 応答が null」と「LLM 呼び出しが例外」を区別し、前者では `None` を返す（heuristic は LLM 未接続時のみのフォールバックに限定する）。

- [V-9] 種別: 実装 / 深刻度: mid
  - 指摘: sweep が既に `open/request_draft` のカードに対しても毎回プレビュー検索と下書き生成を再実行して payload を上書きし、その回のプレビューが0件だと `tier` を `notice` へ**降格**させる（design §14.2「`request_draft` から score が T1帯へ下がっても降格しない（作成済みの下書きは無害）」に反する）。
  - 破綻シナリオ: `secretary.py:487-496` は `open_card.tier = tier` を無条件に代入する。再現では、1回目の sweep で `request_draft`＋候補3件のカードが生成された後、2回目の sweep で Stage-2 が誰にも接点なしと答えた（Gemini は非決定的で実際に起こり得る）だけで、同じカードが `tier=notice` / `preview={'candidates': [], 'note': 'No matching candidates found across public profiles.'}` に書き換わった。UI はこのとき「Request an intro」ボタンと候補一覧を描画しない分岐に入るため（`requester.html` の `isDraft` 分岐）、**A段の Cloud Scheduler 定期起動が収録直前に走っただけでデモのヒーローカードが「Notice」に落ちる**。副次的に、既存 `request_draft` カードは sweep のたびに Gemini 呼び出し（下書き1＋Stage-2×4）と `preview_search` 監査行を増やし続ける。
  - 提案: 既に `request_draft` のカードは score / evidence_line のみ更新し、`question_draft` と `preview` は再生成しない（初回昇格時のみ実行）。降格は行わない。これで冪等性・コスト・デモ安定性が同時に揃う。

- [V-10] 種別: 実装 / 深刻度: mid
  - 指摘: シードの秘書データ所有者 `emp_jordan_lee` に `profiles` も `agents` も存在せず（`generate_seeds.py` の固定4体は rachel/marcus/elena/tom）、mail_seed の所有者＝依頼者本人になっている。このためゴール16の後半「反映後、**直後の質問で当該項目が `cited_item_keys` に現れ得る**」がシードのまま検証不能で、反映操作はダミープロフィールを新規作成する。
  - 破綻シナリオ: 実測で `store.get_profile("emp_jordan_lee")` は `None`。`review_profile_diff` は `secretary.py:731-737` で `Profile(employee_id="emp_jordan_lee", name="emp_jordan_lee", role="Employee")` を新規作成して保存するため、(a) 反映後の profiles 件数が 400→401 になり監査画面のファネル「400件（スケール表示）」の主張とずれる、(b) 表示名が `emp_jordan_lee` という生ID・役職 `Employee` のプロフィールが検索対象に混入する、(c) 彼は `agents` 未登録なので配送用ランキングに乗らず、追加した項目は永久に `cited_item_keys` に現れない。ゴール16 をデモで満たすには、mail_seed の所有者が登録済み4体のいずれかである必要がある。
  - 提案: mail_seed を登録済みエージェント（例: emp_rachel_kim。ただし V-7 の上書き問題を先に直す）の所有に変更するか、`emp_jordan_lee` の profile + agent をシードに追加する。併せて `review_profile_diff` は対象プロフィール不在時にダミー生成せずエラーにする（存在しない社員のプロフィールを秘書が勝手に作らない）。

### 補足（指摘化しない軽微な観察）

- ダイジェストの `upcoming` カテゴリ: design §14.2 の日付ルールは「期日超過・当日・翌日」の3分類だが、実装は4分類目 `upcoming` を持ち、全ての未来スケジュールを表示する。ゴール15 が「ジャーナル」を含み、シードのジャーナルは +3日に置かれているため、実装側が正しい可能性が高い（design 側の文言不足）。件数が増えたときの表示過多だけ将来の論点。
- T1較正のノイズ: 現行の重み（`W_UNTOUCHED=2`, `W_STALE=1`）と `T1=3.0` では「作成2日前・未着手」の通常タスクも score 4.0 で notice カードになる。実測でデモシードの `task_jordan_credentialing_audit` にもカードが1枚生成され、ダイジェストに停滞カードが2枚並ぶ。設計違反ではないが、台本上ヒーローカードの隣に別の警告が出る点は収録前に確認しておくとよい。
- プレビューの public 限定（ゴール12の中核）は実装・テストとも妥当。`preview_search` は `embedding_public` で1段目を回し、2段目には public 項目のみで再構成した `Profile` を渡し、`cited_item_keys` も public 項目に実在するキーへフィルタしている。テスト (b) は「private のみが接点の候補が落ち、public 候補は残る」という区別可能な形で検証しており、見せかけのアサートではない。
