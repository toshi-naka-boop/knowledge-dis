# round-6 再反証（codex）

critic: codex/gpt-5.6-sol xhigh（実測 172,036 tokens）。314 tests OK を確認した上での指摘。

- **High [B] W-1** 書込み内容が依然 transaction 外の prev_tier で決定され、CAS は tier/payload を無条件上書き。競合時に stale notice 書込みが request_draft を notice へ降格し question/preview を消去（再現済み）。4-tuple はカウンタのみ保護し band 遷移自体は未保護。（secretary.py:859, store.py:664）
- **High [B] W-2** FirestoreStore の deterministic/legacy 存在確認と open-preferring lookup が transaction 開始より前（firestore_store.py:432,438 vs :444）。選択後に別 card が作成されると誤 doc 更新・重複が残り得る。
- **Medium [C] W-3** hold 付き card が一度 T1 未満で resolved になると、reopen 時に payload を空から作るため同一 policy_updated_at でも policy_hold が消える（再現済み）→ 次回の探索・policy_limited 重複。（secretary.py:810, :911）
