critic: design-critic (Claude Opus 5) — 反証工程 / レンズ=正しさ（設計との一致・ロジックの欠陥）

## Round 5 — 2026-08-28 — 工程: 反証(正しさ)

規範: `design/autonomous-agent/design.md` v4 ＋ `ledger.md`「ユーザー裁定」「round-4 verification の帰結（R4-H1..H5 / C-18..C-22）」。
検証対象: 作業ツリーの未コミット差分（secretary.py / store.py / firestore_store.py / models.py / matching.py / schemas.py / transmission.py / server.py / auth.py / generate_seeds.py / tests）。
実行環境: `PYTHONPATH=src:. .venv/bin/python -m unittest discover -s tests` → **Ran 307, OK (skipped=13)**。以下の指摘はすべて、既存スイートが緑のまま再現する。再現スクリプトは `/private/tmp/.../scratchpad/probe{1,2,3,4}.py`（リポジトリ非改変）。

### 指摘

- [C-23] 種別: 実装 / 深刻度: high
  - 指摘: `find_card_by_domain_key` が「最初に一致した card」を返すため、同一タスクに **resolved カードと open カードが併存**する状況で open を見落とし、resolved を再オープンして **1タスクに open カード2枚**を作る（C-18「二重 doc を作らない」・既存不変条件 single-open-card の破れ）。
  - 破綻シナリオ（実測・probe4.py）: ① デモ操作者が UI の Run sweep（manual）→ legacy 乱数 id の open カード L が出来る ② タスクが done になり 30 分周期の scheduled sweep が L を resolve ③ タスクが再開・再停滞し、操作者がもう一度 Run sweep（manual は現行のまま `find_open_card_for_task`＝None なので新しい乱数 id L2 を作る）④ 次の scheduled tick で `find_card_by_domain_key`（`store.py:578` の dict 走査／`firestore_store.py:388` の stream 走査、いずれも先頭一致）が **resolved の L** を返す → `open_card=None` と判定 → `upsert_card_gated` の legacy fallback（`store.py:596`）も同じ先頭一致で L を掴み **reopened** → L と L2 が同時に open。
    実測出力: `after scheduled tick: card_stag_15908d8b22[open/request_draft], card_stag_45a0f7c1cf[open/request_draft] | OPEN = 2` / `digest stagnation_cards = 2`。My Agent に **同一タスクの NEED DETECTED が2枚**並び、`needs_detected` も二重計上される。design §12-7「既存デモ台本（manual）1回通し regress なし」を、30 分 job を有効にした本番構成で満たさなくなる。
  - 提案: `find_card_by_domain_key` の選択規則を「open > resolved > terminal、同順位は updated_at 降順」に確定させる（InMemory / Firestore 両実装＋`upsert_card_gated` 内 fallback の3か所）。あわせて「open が1枚だけであること」を検査するテスト（manual→scheduled resolve→manual→scheduled）を §9 に追加。

- [C-24] 種別: 実装 / 深刻度: mid
  - 指摘: policy_hold の再開条件が **policy 変更のみ**になっていない。`secretary.py:1111-1116` の hold スキップ条件は `policy_updated_at 一致 かつ score >= t2` で、score が T2 を割ると `secretary.py:1131-1139` の notice 分岐が payload を作り直して **policy_hold を消す**。その後スコアが再び T2 を超えると、policy が一切変わっていないのに探索・LLM 評価が再実行され、`policy_limited` がもう1件出る。ledger「ユーザー裁定（実装時遵守）＝設定変更後にのみ再開」および R4-H5「band 変化は探索・LLM の再実行契機にしない」への違反（design v4 §5.3 の「band・policy 不変ならスキップ」という旧文言に従った形）。
  - 破綻シナリオ（実測・probe3.py、prepare OFF）: run1 で score 22 → hold(stage=prepare) 生成・inferencer 1 回・policy_limited 1 件。run2（不変）はスキップ＝再実行なし（ここは正しい）。**本人がタスクを触って score 6（T1≤score<T2）になった run3 で hold が消え**（`hold: None`）、再停滞した run4 で **inferencer が再び呼ばれ（calls 1→2）policy_limited が 2 件目**になる。GWS 連携時、担当者が触っては放置するタスクは再停滞のたびに Gemini 評価が走り、Bridge Trace に「Search/Prepare requires approval」行が積み増される。
  - 提案: hold を「band に依存しない」形にする（`score >= t2` 条件を外し、policy_updated_at 一致のみでブロック段をスキップ。band が notice まで落ちたら hold を消すのではなく `policy_hold` を保持したまま evidence だけ更新する）。R4-H5 と design v4 §5.3 の文言差は設計側で一本化する。

- [C-25] 種別: 実装 / 深刻度: mid
  - 指摘: R4-H3 の outcome matrix に `rejected_terminal` の扱いが無く、実装（`secretary.py:1341` の `outcome in ("created","updated")`）では **terminal（applied / dismissed）な profile_diff カードを持つメールが永久に `processed=False` のまま**になる。以後、毎 sweep で `extract_profile_diff` が同じメールに対して LLM を呼び続け、カードは毎回 `rejected_terminal` で捨てられる。`_apply_mail_retention`（`secretary.py:511-518`）は `processed=True` のときだけ body を消すので、**生の本文も保持され続ける**（14 日の delete までは残る）。
  - 破綻シナリオ（実測・probe1.py）: mail_1 の提案カードを本人が dismiss した状態で mail_1 が unprocessed に戻ると、以後 3 回の scheduled run で `processed=False` のまま **LLM 呼び出しが 3 回**（`extra LLM calls= 3`）。unprocessed に戻る現実経路は **`--clear` なしの reseed**（design §10 の収録前 reseed／`--today` 前進運用）: `generate_seeds.populate_store` は mail_seed を `processed=False` で無条件上書きする一方、カードは残るため、この状態が成立する。30 分周期 job のもとでは Gemini 呼び出しが恒常的に空回りする。
  - 提案: `rejected_terminal` を「恒久的に書けない＝メールを消費してよい」側に分類し `processed=True` を立てる（transient な `rejected_policy_changed` / EXTRACTION_FAILED とは区別）。テストを1件追加（terminal カード＋unprocessed メール → 次 run で LLM 未呼出・processed=True）。

- [C-26] 種別: 実装 / 深刻度: mid
  - 指摘: design §3 は manual の run_key（`"manual-"+uuid4`、dedup なし）を定義し、§8 は digest に `last_sweep:{at, origin}` を返して §6 で `Last sweep: Automatic · HH:MM` と origin 別に表示すると定める。しかし `_run_manual_sweep` は `sweep_runs` を一切書かず、`get_latest_sweep_run()` は `status=="done"` の doc のみ返すため、**`last_sweep.origin` は永久に "scheduled" にしかならない**。
  - 破綻シナリオ（実測・probe1.py P3）: scheduled run 後に digest は `{'at': ..., 'origin': 'scheduled'}`。続けて manual sweep を実行しても digest は **同じ値のまま**（`unchanged = True`、`sweep_runs docs: ['sX']`）。収録本番では「Run sweep をクリックしたのに Last sweep が数時間前の自動実行時刻のまま」、reseed 直後に manual だけ回した場合は `last_sweep=null`（＝Phase 5 UI で「まだ一度も動いていない」表示）になる。
  - 提案: `_run_manual_sweep` の終了時に claim を経ずに `sweep_runs/manual-<uuid4>` を `status=done, origin=manual, finished_at, summary` で1件 create するだけで足りる（legacy 経路の挙動・戻り値は不変のまま。dedup 不要なので claim も token も要らない）。あるいは design §3/§8 から manual run_key と origin 表示を落とす。

- [C-27] 種別: 実装 / 深刻度: low
  - 指摘: R4-H2「帯遷移は transaction 内で確定し outcome に含めて返す（prev_band→new_band）」が未実装。`prev_tier` は transaction の外（`secretary.py:1040-1088` の事前読み）で決まり、`upsert_card_gated` の outcome は created/updated/reopened/rejected_* の5値のみで帯遷移を含まない。
  - 破綻シナリオ: A段 job（08:00）と新 30 分 job が同一テナントで重なった場合、両 run が `prev_tier=notice` を読んでから片方が request_draft を書くと、**両方の summary が `needs_detected=1` / `cards_promoted=1` を報告**する（audit 自体は create-only の決定的 id で重複しないため、Bridge Trace の counts 行だけが二重に出る）。カード状態そのものは壊れないので low。
  - 提案: `upsert_card_gated` の戻り値に `prev_tier`（transaction 内で読んだ値）を加え、昇格系カウンタをその値で駆動する。

### 反論なしとした主要観点（確認済み・問題なし）

- **manual 経路の verbatim 温存**: `git show HEAD:secretary.py` の `run_sweep` 本体と現行 `_run_manual_sweep` を機械 diff → 差分は関数名と docstring 追記のみ、ロジック 306 行は完全一致。origin 分岐（`secretary.py:539-541`）も manual 側に副作用を足していない。
- **ゲート表 §5.3 の一致**: monitor OFF（新規 card なし・mail LLM なし・resolve/evidence は実行）／search OFF（探索ゼロで hold）／ask OFF（shortlist のみ counts）／prepare OFF（shortlist＋evaluate を counts、結果破棄）／full path（q_draft → shortlist(q_draft) → evaluate、`preview_search` と完全同値）を実測確認。held path の候補内容がカード payload に一切書かれないことも確認。
- **claim/finish/fail と R4-H4**: 不在/failed→claimed、done→deduplicated、running 非 stale→in_progress、running stale→再 claim、token 不一致 finish/fail は no-op。`finish_sweep_run` が True を返した後にのみ `sweep_run` audit を送出し、dedup 応答時は確定済み summary から create-only で補完（`secretary.py:881-917`）。
- **create-only / fail-closed whitelist**: `save_message_if_absent` は先着勝ち（timestamp 巻き戻しなし）、`_send_internal_audit` は送信前 validator で不合格を握り潰し `reject_unregistered_type` 行を trace に出さない（C-16）、`sweep_run`/`policy_limited` は許可キー完全一致＋stage enum＋note 不在、`get_audit_view` の projection も二重防御として機能。
- **run_key の既定差**: HTTP は body 無し/空/origin 無しをすべて "scheduled"、UI のみ `{"origin":"manual"}`（requester.html・ui.js の両方＝C-22）、domain API 既定は "manual"。scheduled run_key は Scheduler ヘッダ有り＝`tenant-sha256(job:scheduleTime)[:16]`、欠落時のみ 30 分丸め（B段クライアントはヘッダ無しなので後者に落ちる＝設計どおり）。`/internal` は全テナント done/deduplicated のときだけ 200、それ以外 500。
- **C-19 降格禁止**: request_draft は policy 制限でも score 低下でも notice に戻らない（`secretary.py:1099-1109`）。ただし score < T1 での resolve は従来どおり効く（design どおり）。
