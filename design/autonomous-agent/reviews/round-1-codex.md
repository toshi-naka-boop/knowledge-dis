# round-1 クロスベンダー批評（codex）

critic: codex/gpt-5.6-sol xhigh（~/.codex/config.toml 設定に従う。実測 70,424 tokens）

1. **致命的［前提→設計として受理］X-1** OIDC 検証後に合成する `Principal(mode="system")` の `tenant_id` が未定義。既存ルーティングは tenant 必須のため、実行不能または安全でない tenant 選択になる。（design.md §2）
2. **重大［設計］X-2** stale claim の再取得に fencing token がなく旧実行を停止できない。さらにカードの `find`→ランダムID `save` は非原子的なため、retry・manual・既存 daily sweep の競合で重複生成し得る。（design.md §3）
3. **重大［実装→設計として受理］X-3** policy gate が現行 pipeline と分離できていない。manual も保守的 default の Search OFF で止まり既存挙動（既存テスト含む）を壊すうえ、Search は Prepare が制御する `question_draft` に依存し（現行実装は draft 生成→preview_search(question=q_draft) の順）、`Search ON / Ask・Prepare OFF` を実現できない。（design.md §5.3）
4. **重大［設計］X-4** `sweep_run` は完了時に記録されるのに session 開始境界として扱うため、それ以前の `stagnation_detected`／`policy_limited` が別 session に入る。`policy_limited` に `run_key` もなく、run 単位の追跡が成立しない。（design.md §6）
5. **重大［設計］X-5** 部分失敗の回復策が未設計。card／audit 保存後や profile-diff card 保存後・mail processed 前の失敗で再実行すると重複し、`sweep_run` 記録と `done` 更新の順序次第では完了 trace の欠落または重複も起きる。（secretary.py:669 付近）
