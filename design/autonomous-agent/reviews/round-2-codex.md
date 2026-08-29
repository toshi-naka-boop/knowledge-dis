# round-2 クロスベンダー批評（codex）

critic: codex/gpt-5.6-sol xhigh（実測 113,335 tokens）

1. **致命［設計］Y-1** claim token が副作用を fence していない。完了 CAS だけでは zombie が決定的 ID へ `status="open"` を上書きし confirmed/dismissed カードを復活させ得る（`save_card()` は無条件全体上書き）。CAS 後 `sweep_run` 送信前クラッシュで永久欠落／送信後 CAS なら重複で emit-once 不成立。カード更新 CAS と transactional outbox／決定的 audit ID が必要。（design.md §3, store.py:387, firestore_store.py:294, transmission.py:120）
2. **重大［設計］Y-2** 既存の自動実行が policy を迂回する。A段 Scheduler と B段 Agent Engine はいずれも `/api/secretary/sweep` を自動実行しており、v2 の「既存 endpoint は常に origin=manual」では Search/Ask/Prepare OFF でも毎日全段が走り server-side enforcement と spec tests 6–10 を破る。（design.md §1, README.md:240, client.py:51, server.py:631）
3. **重大［実装］Y-3** counts-only whitelist が閉じていない。現行 validator は必須フィールドのみ検査し余分キーを許し、whitelist 対象は payload 全体を表示する。`sweep_run` 登録だけでは将来混入した task_id・候補名も平文流通。許可キー完全一致検証または表示時 projection が必要。（schemas.py:59, :192）
4. **重大［設計］Y-4** per-tenant 部分失敗と Scheduler retry の契約欠落。失敗テナントを 200 応答に並べるだけでは Scheduler は再試行しない。1件でも失敗なら非 2xx とする集約ステータス規則が必要。（design.md §2, tenancy.py:184）
5. **重大［設計］Y-5** 段順再編後、候補評価と人間が送る draft の意味的同一性が失われる。現行は同一 `q_draft` を ranking/evaluation に使いカードへ保存する。task 本文で評価→別の draft 生成では「WHY MARCUS?」が実際に送る質問を支持しない候補を表示し得る。最終 draft による再評価か canonical query 契約が必要。（secretary.py:557, matching.py:235, design.md §5.3）
