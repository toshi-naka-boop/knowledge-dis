# round-3 クロスベンダー批評（codex）

critic: codex/gpt-5.6-sol xhigh（実測 137,600 tokens）

1. **重大［設計］Z-1** 失敗後の Scheduler retry が誤って 200 になる。tenant 例外で初回 500 でも claim は `running` のまま残り、TTL 内の再試行は `in_progress:true` の 200 で成功扱い → 「失敗 tenant だけ再実行」が不成立。例外時の `failed` 遷移か、非完了 tenant を 500 扱いにする契約が必要。（design.md §2/§3）
2. **重大［設計］Z-2** open 限定 CAS では full/held 間の古い書込みを防げない。full run が全 ON policy を読んで LLM 実行中に Search OFF へ変更され held run が policy_hold を保存しても、遅れて完了した full run が open card を上書きし request_draft 昇格・hold 消去を起こせる（run_key が異なるため claim では排他されない）。policy version を CAS 条件に含める必要。（design.md §3/§5.3, secretary.py:633）
3. **重大［設計］Z-3** CAS 結果から created/updated/rejected を判別できず副作用が重複する。現行コードは事前 read を根拠に保存後そのまま件数加算と audit 送出を行う。upsert が outcome を返さないと card は1件でも audit・summary は二重化。（secretary.py:504, :589, :678）
4. **重大［設計］Z-4** 決定的 audit ID への上書きは zombie を無害化しない。save_message は同 ID を無条件上書きし timestamp も再生成 → 遅い zombie が勝者の summary を古い値で上書きし Last sweep 時刻も偽装。条件付き保存（create-only / token 条件）が必要。（transmission.py:120, firestore_store.py:132）
5. **中［設計］Z-5** strict-key＋projection でも `policy_limited.note` の自由文字列は fail-closed にならない。note を保存せず stage（enum）から表示時に固定文言を導出すべき。（schemas.py:192）

origin 既定・Monitor-OFF 解消系・preview public-only 不変条件には新規指摘なし。
