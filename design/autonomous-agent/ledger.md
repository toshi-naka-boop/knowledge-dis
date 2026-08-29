# ledger — Autonomous Agent Phase

## 未解決

（なし）

## ユーザー裁定（2026-08-28 承認時）

- **Q-1 解決**: counts-only 案を採用。承認前の Bridge Trace に候補者名を出さない（例: `Automatic sweep / 400 profiles explored / 1 need prepared / Awaiting owner's review`）。My Agent の Need detected カードは本人向け private UI なので Marcus 表示は現行どおり。Jordan の Ask 実行時点から Bridge Trace に名前を出してよい。境界 = **private recommendation → human approval → auditable named interaction**。既存不変条件（preview no-trace / 停滞情報の非共有）は緩めない。
- **追加制約（実装時遵守）**: policy による途中停止は failure ではなく**正常な停止状態**（observing → stalled → policy_hold）。毎回 LLM・探索を再実行しない。**設定変更後にのみ再開**できる設計を維持（v4 §5.3 policy_hold と一致 — 実装・テストで遵守を担保）。
- round-4 は verification round（回帰・矛盾・未解決 High/Critical の確認）。新規 High/Critical ゼロまたは局所修正で解消可なら Phase 2 へ。大規模 architecture 変更が必要な場合のみ停止・報告。

## round-4 verification の帰結（実装制約 — v4 本文への規範的補足。実装・反証はここを design.md と同格に扱う）

判定: Critical 0 / High 7（codex R4-H1..H5・critic C-18/C-19）/ Mid 3・Low 5 は実装時注意。全件局所修正 → ユーザー裁定によりPhase 2 続行。

- **R4-H1 + C-21**: sweep 内の**全**カード書込み（resolve・evidence 更新・reopen 含む）を `upsert_card_gated` 経由に統一。open→resolved も CAS（confirmed を resolved で上書きしない）。reopened 時は `resolved_reason` を明示クリア。
- **R4-H2**: 帯遷移は transaction 内で確定し outcome に含めて返す（prev_band→new_band）。カード副作用 audit（stagnation_detected / preview_search）は決定的 create-only id（`"msg_stag_"+sha1(card_id+":"+band)[:12]` 等）にし、クラッシュ後の再試行が重複なく補完できるようにする。
- **R4-H3**: mail の outcome matrix — `processed=True` は outcome ∈ {created, updated} のときのみ。`rejected_policy_changed`・LLM 失敗では processed=False を維持（メールを消費しない）。テスト必須。
- **R4-H4**: 順序反転 — token CAS で `done`+summary を**先に**確定し、`sweep_run` audit は**確定済み summary から**生成（create-only）。dedup 応答時に audit 欠落があれば summary から補完。
- **R4-H5**: blocked 段の再開条件は **policy_updated_at の変更のみ**（ユーザー裁定「設定変更後にのみ再開」に一致）。band 変化は resolve / evidence / reopen のみ許可し、探索・LLM の再実行契機にしない。
- **C-18**: 決定的 id への移行 — 既存 open カード（legacy 乱数 id）があれば**その id を再利用**して更新し、決定的 id は新規作成時のみ。二重 doc を作らない。デモは reseed 前提も維持。
- **C-19**: **降格禁止の不変条件** — 昇格済み request_draft は policy 変更で notice へ巻き戻さない（policy は前進遷移のみをゲート）。テスト1件追加。
- **C-20**: policy_limited は owner×stage につき run 末尾で1件（task_count を集約してから送出）。
- **C-22**: UI の Run sweep は `{"origin":"manual"}` を送る（**requester.html と web/ui.js の両方** — 関数が重複定義されている）。
- Low 5件（mail retention 非対称・AUDIT_WHITELIST 追加漏れ検知・「no cross-tenant sweep」コメント陳腐化・profile_diff updated 副作用・network pulse 誤発火）は reviews/round-4.md 記載。実装時に確認。

## round-5 反証（実装への3レンズ）指摘と修正方針（修正中 → round-6 で検証）

ID 規約: codex=V-1..6（round-5-codex.md）、正しさ=K-1..5（round-5-correctness.md の C-23..27）、安全性=S-1..5（round-5-safety.md の C-23..27。レポート内番号が正しさレンズと衝突しているため台帳では S- を正とする）。

- **A. 構造統一**（V-6 二重実装・V-1 R4-H1 未適用・V-5/K-4 manual 監査なし）→ manual/scheduled を単一パイプラインに統合。manual は「policy 全許可・hold/policy_limited なし・manual- run_key（dedup なし）・sweep_run audit(origin=manual) あり・全カード書込み CAS」。_run_manual_sweep は削除。
- **B. CAS/outcome 精緻化**（V-2/K-5 band 遷移が transaction 外・V-4 resolve×resolved 再オープン・K-1 find_card_by_domain_key が open を見落とす）→ upsert_card_gated が (card, outcome, prev_status, prev_tier) を返し、昇格判定・カウンタ・band audit は戻り値駆動。reopen は incoming が open のときだけ。domain key 検索は open 優先。
- **C. hold 保全**（V-3/K-2 R4-H5 違反）→ policy_hold は band 低下で消さない（payload merge）。除去条件は policy_updated_at 変更のみ。
- **D. mail matrix 補完**（K-3）→ rejected_terminal は processed=True（消費）。LLM 失敗・rejected_policy_changed のみ未処理維持。
- **E. 安全性**（S-1 silent-drop の適用範囲逸脱・S-2 policy_limited が owner 特定可能な unmasked 行・S-3 403 が検証段オラクル・S-4 500 に例外文/パス・S-5 employee_id 無検証）→ 送信前自己検証は sweep_run/policy_limited の2 intent に限定（profile_diff 等は通常経路で reject 行を残す）。policy_limited は from="secretary" の匿名集約（owner 識別子なし）に変更。403/500 本文は汎用文言のみ（詳細はサーバログ）。employee_id は形式検証（英数・アンダースコア・ハイフン）で 400。

## round-6 再反証の残指摘と修正方針（修正中 → round-7 最終確認）

round-5 修正 A〜E は両系統の実測で有効を確認（manual 同値・hold 往復・匿名 policy_limited・silent-drop 限定など）。残指摘は6件・全て局所修正:

- **W-1 (High) ＝ C-29 (mid)**: 降格禁止と payload 保全が transaction 外の事前 read 依存のまま。並行 run で request_draft→notice 降格・policy_hold 消失が再現 → **CAS transaction 内**に移設: 既存 open が request_draft で incoming が notice のとき tier と question/preview を txn 内で保持（evidence のみ更新）。policy_hold は txn 内で carry-forward し、除去は caller が policy 変更時に明示フラグを渡したときのみ。
- **W-2 (High)**: FirestoreStore の legacy/domain-key lookup（open 優先）が transaction 開始前 → lookup を transactional 関数内へ移動。
- **W-3 (mid)**: resolved→reopen で payload を空から作り hold 消失 → W-1 の carry-forward を reopen 経路にも適用。
- **C-28 (mid)**: 昇格済みカードの resolve→再停滞 reopen が needs_detected=0・audit 無送出 → reopened は created 相当としてカウントし stagnation_detected を送出。カード副作用 audit の決定的 id に run_key を含める（sha1(card_id:band:run_key)。同一 run 内の crash-retry は dedup、再検知は新規記録）。
- **C-30 (low)**: digest の employee_id が未検証で Firestore doc id へ → Autonomy API と同じ形式検証を digest にも適用（既存 route の同種問題は本フェーズ外・注記のみ）。

## round-7 最終反証（3巡目）の帰結 — 収束

- 判定: **新規 High/Critical ゼロ**（claude 単独。codex は利用上限で省略 — reviews/round-7-codex.md に記録。単一ベンダー検証である旨を最終報告に明記）。W-1/W-2/W-3/C-28/C-30 の修正は有効と確認。
- 残4件（mid 1・low 3）は呼び出し側が直接修正し 322 tests OK:
  - **C-31**: 降格禁止ガードが resolve を飲む → ガード条件に `card.status=="open"` を追加（stale tier の resolve も resolved に遷移）。回帰テスト追加
  - **C-32**: profile_diff audit id の無条件 run_key salt → mail_id のみに戻す（クロス run の crash-retry も dedup）
  - **C-33**: reopen 後の salt 無し重複行 → **audit_epoch 方式**: reopen 時に store の CAS 内で payload["audit_epoch"] を刻印・以後の書込みで carry-forward し、stagnation audit id を (card, tier, epoch) に。同一ライフサイクルは dedup・reopen は新規行・クラッシュ補完（R4-H2）も維持。テスト2件追加
  - **C-34**: employee_id 検証を fullmatch に（末尾改行の穴）
- 検証の限界（既知・受容）: FirestoreStore の CAS 系は mock が transaction 未対応のためオフラインテスト 0 件（InMemory で論理網羅・Firestore は静的読解＋本番実走で確認）。既存 try_confirm_card と同型の受容済みギャップ。

## 解決済み索引（全文は reviews/round-1*.md / round-2*.md / round-3*.md）

- C-13 (high/設計) resolved を terminal に含め再停滞の再検知が不能 → **受理**: resolved は再オープン可（outcome=reopened）。confirmed/dismissed/applied のみ terminal（v4 §3）
- C-14 (mid/設計) ＝ Z-1 (重大/設計) 失敗 claim が running のまま残り retry が 200 で消える → **受理**: 例外時 token 一致で failed 遷移・failed は即再 claim 可・非 done を含む attempt は 500（v4 §2/§3）
- C-15 (mid/設計) sync と mail→profile_diff のゲート範囲未定義 → **受理**: sync は非ゲート（ユーザー自身のデータ更新）・mail LLM 読解は Monitor 配下で OFF 時は未処理のまま残す（喪失なし）（v4 §5.3）
- C-16 (mid/設計) validator 失敗が reject 行として trace に描画 → **受理**: 内部 intent は送信前自己検証・不合格は送信せずログのみ（v4 §3/§6）
- C-17 (low/設計) Monitoring paused とカード行の表示矛盾 → **受理**: digest が effective policy を返し Watching 行を `· Paused` に切替（v4 §8）
- Z-2 (重大/設計) LLM 実行中の policy 変更を古い run の書込みが上書き → **受理**: upsert_card_gated が expected_policy_updated_at を transaction 条件に含める（v4 §3）
- Z-3 (重大/設計) CAS outcome 不明で audit/summary 二重化 → **受理**: outcome（created/updated/reopened/rejected_*）返却・副作用は outcome 駆動（v4 §3）
- Z-4 (重大/設計) 決定的 audit id の無条件上書きで zombie が summary/時刻を偽装 → **受理**: save_message_if_absent（create-only CAS）で最初の書き手が勝つ（v4 §3）
- Z-5 (中/設計) policy_limited.note の自由文が fail-closed でない → **受理**: note を廃し stage enum のみ保存・表示文言は UI 固定表から導出（v4 §6）

- C-8 (high/設計) ＝ Y-2 (重大/設計) 既存 A/B daily job が origin=manual 扱いで policy 迂回 → **受理**: origin は呼び出し宣言・HTTP 既定 "scheduled"（UI のみ明示 manual）。A/B は無改修でゲート下へ。domain API の既定は manual で既存テスト無改修（v3 §1, §5.4）
- C-9 (high/設計) ＝ Y-1 (致命/設計) 決定的 id＋無条件 save_card で terminal 復活・emit-once 不成立 → **受理**: `upsert_card_if_open` CAS（terminal は書かない）＋ sweep_run/policy_limited に決定的 audit_id（再送＝同 doc 上書き）・done-CAS 前送出で欠落/重複とも排除（v3 §3）
- C-10 (mid/設計) Monitor OFF が解消系まで停止 → **受理**: done/below-T1 resolve と evidence 更新は policy 非依存（「新しく始めない・始めたものは畳む」）。policy_hold のスキップ対象はブロック段のみ（v3 §5.3）
- C-11 (mid/設計) 新 intent がタイムラインへ生描画 → **受理**: recordToEvent で明示 null。表示は Last sweep メタ行と compact 行の2箇所のみ（v3 §6）
- C-12 (low/設計) claim エッジ未定義 → **受理**: running 非 stale は実行せず in_progress 応答・TTL 既定 300s（attempt-deadline 180s と整合）・jobs run はヘッダ付きで衝突なし（v3 §3）
- Y-3 (重大/実装) whitelist が閉じていない → **受理**: 新2 type は許可キー完全一致 validator（未知キーで送信拒否・fail-closed）＋表示時 projection の二重防御（v3 §6）
- Y-4 (重大/設計) per-tenant 部分失敗で Scheduler が再試行しない → **受理**: 1件でも失敗なら 500（成功テナントは done 済みで再実行時 dedup）（v3 §2）
- Y-5 (重大/設計) 段順再編で評価と送付 draft の意味的同一性が喪失 → **受理**: full path（全 ON）は現行の draft→preview を完全維持。held path のみ task 由来クエリで counts 用探索・結果破棄。X-3 の解決を本方式で上書き（v3 §5.3）

- C-1 (high/設計) sweep_run.prepared が owner↔候補対応を平文露出 → **受理**: prepared を削除し counts-only に。詳細は非公開の sweep_runs doc へ。残る解釈差は Q-1 として前提質問化（design v2 §6）
- C-2 (high/設計) policy 留め置きで毎 run LLM/audit 重複 → **受理**: card.payload.policy_hold（band＋policy_updated_at）で不変時は T2 分岐スキップ、policy_limited は hold 新規/変更時のみ＋run_key 付与（§5.3）
- C-3 (high/設計) sweep_run セッション境界で Trace 表示破綻 → **受理**: 境界にしない。Last sweep メタ行＋セッション外 compact 行に変更（§6）
- C-4 (mid/設計) ＝ X-1 (致命/前提) system principal の tenant_id 未定義 → **受理**: caller 指定不可・registry 全テナント反復・テナントごとに principal 合成（§2）
- C-5 (mid/実装) _CachingCertsRequest 単一キャッシュの共有汚染 → **受理**: OIDC 用に専用インスタンス（§2）
- C-6 (mid/設計) Monitor OFF で done resolve まで停止・reseed 前提の未記載 → **受理**: done resolve は policy 非依存で実行。reseed 前提を §10/§11 に明記
- C-7 (low/設計) preview_search(evaluate=…) フラグは過剰 → **受理**: メソッド分離（shortlist / evaluate）に置換。hold 中カードへ候補内容は書かない（§5.3）
- X-2 (重大/設計) stale claim の fencing 欠如＋card find→save 非原子 → **受理**: claim_token CAS（summary 更新と sweep_run 送出は token 勝者のみ）＋決定的 card id で構造的に重複不能（§3）
- X-3 (重大/実装) manual が保守 default で停止・Search が Prepare 成果物(q_draft)に依存 → **受理**: policy は scheduled のみゲート（manual は override、spec §2 準拠）。段順を shortlist(task 由来クエリ)→evaluate→prepare に再編。デモ品質ゲート（Marcus 選出不変）を完了条件に追加（§5.3, §12-3）
- X-4 (重大/設計) sweep_run 境界問題＋policy_limited に run_key なし → **受理**: C-3 と同解決＋run_key 付与（§6）
- X-5 (重大/設計) 部分失敗の回復未設計（profile-diff 重複・sweep_run 二重送出） → **受理**: 決定的 card id で同 doc 収束・sweep_run は token CAS で emit-once・回復モデルを §3 に明文化
