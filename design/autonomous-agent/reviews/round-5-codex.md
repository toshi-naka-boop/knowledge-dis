# round-5 反証（codex・レンズ: 過剰実装＋回帰）

critic: codex/gpt-5.6-sol xhigh（実測 189,719 tokens）。307 tests OK を確認した上で、並行書込み・band 往復の未網羅を突いた実測ベースの指摘。

1. **High【実装】V-1** R4-H1 未実装: manual path が CAS を使わず stale カードを save_card で無条件保存（secretary.py:599-607, 655-660, 757-767）。並行 confirm/scheduled 成立後も confirmed を open/resolved へ巻き戻せる。乱数 ID 作成（:628-643, :713-730）による二重カードも残存。
2. **High【実装】V-2** R4-H2 未実装: upsert_card_gated の outcome に prev_band→new_band がなく、昇格判定が transaction 外の prev_tier に依存（secretary.py:1088, 1232-1243）→ 並行昇格で cards_promoted / needs_detected 二重計上。
3. **High【実装】V-3** R4-H5 違反: T2 未満へ一度下がると payload 全置換で policy_hold が消える（secretary.py:1111-1116, 1131-1139; store.py:625-629）。実測で同一 policy_updated_at のまま high→notice→high で preview_shortlist が再実行された。
4. **High【実装】V-4** resolve 同士がカードを再 open: 既存 resolved かつ入力も resolved なのに無条件で open へ戻す（store.py:631-636, firestore_store.py:483-486）。後発の重複 resolve が完了カードを再表示。
5. **Medium【実装】V-5** manual origin が監査されない: manual は claim/audit を通らず旧結果を直接返す（secretary.py:539-543, 841-852）。spec §18 #15（trace が manual/scheduled origin を記録）と v4 の manual run_key 契約に反する。テストが sweep_run 不在を正として固定している。
6. **Medium【過剰】V-6** 二重実装禁止（spec §2）違反: 310行の _run_manual_sweep と約490行の scheduled state machine に分岐。CAS・監査・hold が片側だけに実装され drift が現実化。
