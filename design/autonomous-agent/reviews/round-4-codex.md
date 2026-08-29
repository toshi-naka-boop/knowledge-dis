# round-4 verification（codex）

critic: codex/gpt-5.6-sol xhigh（実測 122,853 tokens）

判定: **新規 Critical なし。新規 High 5件、すべて LOCAL fix で足り、大規模 architecture 変更は不要。**

- **R4-H1 (High)** resolve の非 CAS 経路: 現行は open を読んだ後に無条件 save_card するため、途中で human confirm された `confirmed` を `resolved` へ上書き可能。LOCAL fix: open→resolved 専用 CAS を追加、reopened 時は resolved_reason を明示クリア。（secretary.py:475）
- **R4-H2 (High)** 5-outcome では帯変化を確定できない: `updated` だけでは transaction が帯変更したか判別できず、競合時に audit 重複/欠落。card 保存後・audit 前クラッシュも補完不能。LOCAL fix: transaction 内で transition を確定して返し、カード副作用 audit を決定的 create-only ID で再送可能に。
- **R4-H3 (High)** rejected_policy_changed と mail.processed: policy が mail LLM 実行中に変わり card CAS 拒否でも processed=True にするとメールを消費。LOCAL fix: rejected_policy_changed / LLM 失敗では processed=False 維持の outcome matrix を明記・テスト。（secretary.py:669）
- **R4-H4 (High)** zombie が create-only audit の先着者になれる: sweep_run 送出→done-CAS の順序では失効 token の zombie が先に audit を作成し、正しい retaker summary を永久拒否。LOCAL fix: token 一致で done+summary を先に確定し、audit は確定済み summary から生成（dedup 応答時も補完可能に）。
- **R4-H5 (High)** policy_hold が「policy 変更時のみ再開」のユーザー裁定と矛盾: 「band・policy 不変なら skip」は band 変更だけで探索/LLM 再実行を許す。LOCAL fix: blocked 段の再実行条件を policy_updated_at 変更のみに限定。band 変更時は resolve/evidence/reopen のみ許可。
