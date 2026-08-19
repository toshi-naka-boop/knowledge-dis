critic: claude design-critic（モデル: claude-opus-5[1m]）

## Round 9 — 2026-08-19 — 再反証（round-8修正の閉塞確認 + 回帰点検）

対象: コミット `dabd4bb`（差分は `git diff 13861fe..dabd4bb` に限定）。基準: design.md §14 / §10 ゴール12〜18、ledger.md 冒頭「反証round-8のルーティング」。
検証環境: `.venv/bin/python3`。`python3 -m unittest discover -s tests` → **Ran 83 tests / OK**（round-8時点74件 → +9件、既存の回帰なし）。
実測に使った再現スクリプト（`src/` は無変更）: scratchpad の `repro_round9.py`（並行confirm・キー衝突反映・LLM配線/null）と `repro_round9b.py`（実シード投入→sweep×2→digest→反映）。

### 1. round-8指摘の閉塞確認（10論点）

- **V-6 / S-8（high・CAS非トランザクション）— クローズ確認（実測）**
  `Store.try_confirm_card(card_id) -> (Card|None, bool)` が ABC に追加され、InMemory は `threading.Lock`、Firestore は `@firestore.transactional` 実装。round-8の再現（get/save に各30ms のレイテンシを注入した `LatencyStore`、2スレッド同時 confirm、`threading.Barrier` で同時発火）を再実行:
  `results: [{'status':'already_confirmed','query_audit_id':None}, {'status':'confirmed','query_audit_id':'msg_2dfc0ee1b797','dispatched_count':3}]` / **`query msgs: 1 | connect_ask msgs: 3`**（round-8は query 2 / connect_ask 6）。最終カードは `confirmed` + `linked_query_audit_id=msg_2dfc...` の1本。ゴール13のCAS要件を満たす。
  Firestore側は本物のFirestoreを持たないため実行検証不可だが、(a) `FIRESTORE_EMULATOR_HOST=localhost:9` に対する実行で SDK のトランザクション機構まで到達（`TypeError`/`AttributeError` ではなく begin失敗由来の `ValueError: The transaction has no transaction ID...`）＝呼び出し形が正しいこと、(b) SDK ソース（`_Transactional.__call__` → `_pre_commit` → `_commit` → `return result`）を読んで戻り値タプルが透過することを確認。読み→条件→`txn.set` の順序も規約どおり。
  テスト側も `TestConfirmCardConcurrentCAS`（20スレッド同時）が追加され、round-8で指摘した「逐次テストがCASを検証していない」も解消。

- **V-7 / S-7（high/mid・同一キー上書き＋visibility反転）— クローズ確認（実測）**
  `review_profile_diff` は完全に追加専用になった。既存 `current_work`(public/seed_fixed) と `transition_pipeline`(private/NDA) を持つプロフィールに対し `action="apply"` を実行:
  `item_key='current_work_mail_1'` が新規追加され、`[current_work] vis=public src=seed_fixed body='ORIGINAL...'` と `[transition_pipeline] vis=private body='NDA:...'` は**いずれも無変更**。visibility 反転経路は構造的に消滅（既存項目に触るコード自体が無い）。
  S-7後半（呼び出し側による `item_key` 差し替え）も閉塞: `ProfileDiffReviewRequest.model_fields == ['action','edited_body']`、`SecretaryService.review_profile_diff(self, card_id, action, edited_body=None)` で `item_key` 引数が消え、キーは `card.payload` 固定。

- **V-8 / E-7 / S-6（high・llm_client未配線）— クローズ確認（実測）**
  `create_app_from_env` が `_build_genai_client()` を組み立て `create_app(..., llm_client=)` → `SecretaryService(llm_client=)` へ配線。`server.SecretaryService` をスパイに差し替えて `create_app_from_env()` を実行し、`llm_client` に `google.genai.Client` インスタンスが渡ることを実測（`is None: False`）。Vertexモード（`GEMINI_API_KEY` 空）でも `_build_genai_client` が `genai.Client()` を返す分岐を持つため空キー起因の破綻はない。
  null対応も実測: `"null"` → `None`、```` ```json null``` ```` → `None`、`""` → `None`、例外 → heuristic、正常JSON → `('asc_partnership','Works on ASC partnerships.')`。「LLMを接続しても差分なしを表現できない」構造は解消。
  ※ S-6提案(b)（heuristic自体の廃止）と(c)（public反映前のprivate断片スキャン）はルーティングで採用外。heuristicは「LLM未接続 or 呼び出し例外」時のみ到達するが、到達した場合はメール全文がそのまま public 項目本文になる挙動は残る（下記 R-2 と観察1）。

- **V-9 / E-6（mid・帯変化なし再sweepの副作用）— クローズ確認（実測）**
  Rule 4 に「`open_card.tier == "request_draft"` なら score / evidence_line / task_title のみ更新して `continue`」が入った。実シードで sweep を2回連続実行:
  `preview_search` 監査行 1 → 1（重複なし）、`stagnation_detected` 2 → 2、`connect_ask` 0 → 0、tier変化なし、`question_draft`/`preview` payload 変化なし（差分検出ループで出力ゼロ）。0件時降格の経路（`open_card.tier = tier` の無条件代入）は `request_draft` カードに到達しない。Rule 3（T1帯）側も既存の非降格更新のままで、降格は発生しない。

- **V-10（mid・mail_seed所有者にprofile/agentが無い）— クローズ確認（実測）／ただし新規欠陥 R-1 を生成**
  `review_profile_diff` はプロフィール不在時に `LookupError`（server で 404）となり、ダミープロフィール生成は消滅（実測: `ghost apply raised: LookupError`）。シードの所有者は `emp_marcus_delgado`（profiles/agents 実在）に変更され、反映後も `profiles count 400 -> 400`、`embedding_public` 再生成（len=128）、追加項目 `current_work_mail_1` は public。ゴール16の「`cited_item_keys` に現れ得る」前提（所有者が登録エージェント）は満たされた。
  一方で「本人ダイジェストに出る」側が壊れた → **R-1**。

- **E-8（mid・汎用dismiss）— クローズ確認（読解＋実測）**
  `dismiss_card` は `type != "stagnation"` と `status != "open"` を拒否。実測で profile_diff カード・`confirmed` 済み停滞カードの双方が `ValueError`。UI 側の `dismissCard()` は停滞カード（request_draft/notice、いずれも open）にのみ結線され、profile_diff は `reviewDiff(..., 'dismiss')` 専用経路のままなので機能欠落はない。

- **E-9（low・DEMO_TODAYのAPI上書き／item_key露出）— クローズ確認（読解）**
  `SweepRequest` モデル削除、`/api/secretary/sweep` は引数なし、`/api/secretary/digest` は `employee_id` のみ。日付は `DEMO_TODAY` env 単独（実測で `run_sweep()` が `date: 2026-08-19` を返す）。`ProfileDiffReviewRequest.item_key` も削除。README/design に旧パラメータの記載は残っていない（grep 0件）。

- **E-10（low・呼び出しゼロのCRUD）— クローズ確認（読解）**
  `Profile.get_public_text` / `Store.get_task` / `Store.get_schedule`（ABC・InMemory・Firestore の3層）を削除。`grep -rn "get_public_text\|get_task(\|get_schedule(" src/ tests/ scripts/` → 0件。83テストOKで参照残りなし。

- **S-9（low・`${tagText}` 未エスケープ）— クローズ確認（読解）**
  `requester.html:244` が `${esc(tagText)} (${esc(r.due_date)})` に修正。同ファイル内の他の innerHTML 挿入も esc() 済みであることを再確認。

- **S-10（low・本人性突合なし）— クローズ確認（読解）**
  README に `/api/secretary/*` が呼び出し元の本人性を検証しないこと、`employee_id` 差し替えで他人のダイジェストが読めることを、round-6 S-3 と同じ「デモ割り切り」として6行で明記。ルーティング方針（README明記）どおり。

**閉塞: 10/10。未クローズ: 0。**

### 2. 新規欠陥（修正が持ち込んだもの）

- [R-1] 種別: 設計 / 深刻度: high
  - 指摘: V-10の修正（mail_seed 所有者を `emp_jordan_lee` → `emp_marcus_delgado`）により、`profile_diff` カードが**どの画面にも表示されなくなった**。ダイジェストUIは `requester.html` にしか存在せず、`REQUESTER_ID = "emp_jordan_lee"` がハードコードで、ペルソナ切替も無い（切替ドロップダウンは candidate.html 側のみ。`grep -l digest web/*.html` → requester.html のみ）。
  - 破綻シナリオ: 実シード（`DEMO_TODAY=2026-08-19`）投入 → `run_sweep()` → 実測で
    `JORDAN digest: stagnation cards=[('request_draft','Riverside Clinic Relocation Assessment'),('notice','Allied Health Clinician Credentialing Verification')] / profile_diff cards=0`、
    `MARCUS digest: stagnation cards=0 / profile_diff cards=[('current_work','Update on ambulatory surgery center partnership discussions')]`。
    したがって §14.8 が規定する本人ダイジェストの並び「期日リマインド → 停滞カード → **差分提案カード**」の3段目が空のまま、FR23（プロフィール差分提案）の可視面がデモから消える。ゴール16の「差分提案カード→『反映』」はUI経由で到達不能になり、`card_id` を手で拾って API を叩く以外に検証できない。加えてシードのメール本文は依然 `"Hi Jordan, ..."`・mail_id も `mail_jordan_clinic_mou` のまま所有者だけ Marcus なので、仮に表示できても宛名が別人の受信メールが Marcus のプロフィール提案として並ぶ（台本の説明が破綻する）。
    根因は design 側の三すくみ: ゴール8「agents 4件 / profiles 400件」× ゴール16「所有者は登録エージェントでないと `cited_item_keys` に現れ得ない」× §14.8/§11「カードは本人（＝タスク所有者 emp_jordan_lee）のダイジェストに出る」。実装だけではどれかを必ず割る。
  - 提案: design で1つ選んで明記する。(a) `emp_jordan_lee` を4体目相当の登録ペルソナに昇格させ（synthetic 1体と入れ替えて profiles=400 を維持、agents は5件に変更しゴール8の文言を更新）、mail_seed の所有者を Jordan に戻す＝§14.8・ゴール16の両立。(b) M3のタスク所有者ごと登録済みペルソナ（Marcus）に寄せ、`REQUESTER_ID` をそれに合わせる。(c) 差分提案シーンをデモ台本から外し（§14 の未決事項「§11に含めるか」を「含めない」で確定）、ゴール16は API 手順として write-up に記載する。どれを採っても、シードのメール本文・mail_id の宛名を所有者に合わせる修正が要る。

- [R-2] 種別: 実装 / 深刻度: mid
  - 指摘: `extract_profile_diff` が **LLMの空応答 `""` を「明示的な差分なし」と同一視**しており（`if not cleaned or cleaned == "null": return None`）、かつ sweep は結果に関わらず `mail.processed = True` を無条件で書く。V-8/S-6 の修正でLLM経路が既定になったため、この組み合わせが初めて実害を持つ。
  - 破綻シナリオ: Gemini がセーフティブロック・`MAX_TOKENS` 打ち切り・空 candidates を返すと `response.text` は空文字になり、例外ではないので heuristic にも落ちない → `None` → カードを作らないまま `processed=True` が確定する。§14.7 の Cloud Scheduler は sweep を定期起動するので、収録前に1回走っただけでデモ唯一の mail_seed が無音で消費され、以後どれだけ sweep しても差分提案カードは二度と生成されない（`list_mail_seeds(unprocessed_only=True)` に乗らない）。復旧はシード再投入のみで、当日その原因に気づける表示は無い（監査画面にも `profile_diff_proposed` が出ないだけ）。実測で「LLM が空文字を返す → `None`」を確認済み。既存テスト `test_llm_explicit_null_diff_creates_no_card_and_skips_heuristic_fallback` は `processed=True` を期待値として固定しているため、この経路は検知されない。
  - 提案: `cleaned == ""` は「LLM応答なし＝失敗」として扱い、heuristic フォールバック（または1回リトライ）に落とす。`processed=True` は「カードを作った」または「明示的に `null` と応答された」場合に限定する。少なくとも空応答時は sweep の戻り値に件数を出して sweep ログから気づけるようにする。

### 3. 指摘化しない観察

1. **heuristic の全文ペーストは残存**: LLM呼び出しが例外を投げた場合のみ到達するが、到達すると件名＋本文全文が公開項目本文になる（実測: `('current_work', 'Subj: Confidential body text ...')`）。S-6提案(b)(c) はルーティングで採用外と決まっているため再提起しない。ただし追加専用化により「既存の作り込み項目を破壊する」性質は消え、影響は「余計な公開項目が1つ増える（本人のApply操作が必要）」に縮小した。
2. **confirm 競合の敗者が `query_audit_id: None` を受け取る**: 勝者が `linked_query_audit_id` を書く前に敗者が `already_confirmed` を返すため。UI では勝者の応答が後着して `queryMeta` を正しい値で上書きするので実害は観測できなかった。将来 Firestore で敗者応答だけを使う経路を足すなら注意。
3. **`current_work_mail_1` というサフィックスキー**: 衝突回避として妥当だが、UI・監査に生キーとして出る。スキーマ側にキーの enum 制約はないので機能影響なし。

### 4. 反論なしと判断した観点（賛成側の根拠）

- round-8で「中核は正しい」と結論した3点（プレビュー無痕跡・public限定・fail-closedマスク）は今回の差分で触られていない。`matching.py preview_search` は無変更、`AUDIT_WHITELIST` も無変更、実シード2回sweepで `connect_ask` 系は 0件のまま。
- 修正の副作用が既存M1/M2に及んでいないことを 83テスト（+9件）と実シード経路の両方で確認した。削除された3ヘルパー（`get_public_text`/`get_task`/`get_schedule`）に呼び出し残りは無い。
- 新規テストは「通ることが欠陥の不在を意味しない」形になっていない: CASは20スレッド同時発火、追加専用化はキー衝突条件、V-9は監査行数とpayload同一性、V-10はシード所有者の登録済み判定を直接assertしている。
