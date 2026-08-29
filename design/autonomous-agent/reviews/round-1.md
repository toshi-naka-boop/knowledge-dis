critic: claude/opus-5[1m]

## Round 1 — 2026-08-28 — 工程: 批評

対象: `design/autonomous-agent/design.md` v1 / 要求: `spec.md` / 台帳: `ledger.md`（空 = 蒸し返し制約なし）
検証方法: design.md の主張を実装（secretary.py / server.py / auth.py / schemas.py / transmission.py / matching.py / web/audit.html）と突き合わせる静的読解。実測（デプロイ・LLM 実行）は行っていない（low/med は静的判定、high 候補も file:line で確定できたため実測不要）。

### 指摘

- [C-1] 種別: 設計 / 深刻度: high
  - 指摘: `sweep_run` payload の `prepared:[{owner_agent_id, candidate_name}]` を **AUDIT_WHITELIST に載せる**（design §6-1）ことは、「秘書系 intent は whitelist 外＝fail-closed マスク」という既存不変条件を実質的に破る。design §6 冒頭の「マスク不変条件は変更しない」という自己申告は成立していない。
  - 破綻シナリオ: `preview_search` は「どの owner のどのタスクに、どの候補が選ばれたか」を隠すために故意に whitelist 外に置かれている（schemas.py:37-51、MASKED_NOTES:175「preview search run, nothing delivered (content hidden)」）。ところが `sweep_run.prepared` は同じ情報（owner_agent_id ↔ candidate_name の対）を**平文で**返す。かつ `GET /api/audit/messages` には principal ガードが一切ない（server.py:599-621。`_deny_system` も `_deny_human` も `_require_self_employee` も無く、テナント内の誰でも全メッセージを読める）。結果、Jordan が確認する前に、同僚の誰でも `/audit` を開けば「Jordan の agent が Marcus に依頼を用意した」を読める。これは「未レビューの AI 生成マッチを本人の与り知らぬところで流通させない」設計意図の穴でもある。spec §14 は「candidate selected を後から確認できること」を要求しており、**spec §14 と既存マスク不変条件が正面衝突している**が、design はこの衝突を明示せず露出側に倒している。
  - 提案: (a) `sweep_run` は `prepared_count:int` と `card_ids:[]` のみにし、candidate_name/owner 対応は audit_payload（masked）側に落とす。(b) どうしても Bridge Trace に "Request prepared for Marcus Delgado" を出したいなら、**表示元をカード所有者本人の digest（既に本人限定）に変え**、audit は masked 行のままにする。(c) いずれにせよ「audit エンドポイントは誰が見られるのか」を design に明記し、衝突を CP として上げること。

- [C-2] 種別: 設計 / 深刻度: high
  - 指摘: policy でゲートして「notice に留め置く」設計（§5.3 の Search OFF / Ask OFF / Prepare OFF いずれも）は、**score ≥ T2 のカードを毎 run 再処理させる**。既存の副作用抑制ガードは `open_card.tier == "request_draft"` のときにしか効かない（secretary.py:541-555）。run 単位 dedup（§3）は run_key が毎回違うので一切効かない。
  - 破綻シナリオ: Jordan の Riverside タスク（score 17 ≥ T2）で Prepare OFF にすると、カードは notice のまま毎 sweep で全経路を通る → 30分毎に ①`generate_question_draft` の LLM 呼び出し ②`preview_search`（**登録 agent 全件**に対して `infer_connection`、3件溜まるまで break しない: matching.py:272-308。デモの登録 agent は6件 → 最大6 LLM 推論）③`preview_search` 監査行の再送出（secretary.py:650-664）が発生する。すなわち **Bridge Trace に同一 task の `preview_search` 行が 1日48本積まれる**（spec §3「Bridge Trace event の無意味な重複なし」違反）。同じことは policy 無関係でも起きる: 「score ≥ T2 だが候補0件」のタスクは今日も毎 sweep で LLM を叩いており、日次だったものが 48倍になる。design §11 の「LLM 呼出は band 変化時のみで通常 0」というコスト前提は事実に反する。
  - 提案: 帯不変時の抑制ガードを「tier==request_draft」ではなく「(task_id, score band, policy hash) が前回 run と同一なら preview/draft/監査をスキップ」に一般化し、カード payload に `last_evaluated_band` / `last_policy_hash` を持たせる（Message スキーマは触らない）。あわせて §6-2 の `policy_limited` 重複抑制が参照している「`sweep_runs` の直前 run summary と比較」は、§6-1 で定義した summary（counts/prepared のみ）に stage 情報も policy hash も入っていないため**現状の schema では実装不能**。同じ抑制キーに統一すること。

- [C-3] 種別: 設計 / 深刻度: high
  - 指摘: 「Bridge Trace のセッション分割を `query` **または** `sweep_run` 境界に拡張」（§6）は、`sweep_run` を run **完了時**に1件出す設計（§6-1）と両立せず、しかも既存 UI の「最新セッションだけを描画」仕様と組み合わさってデモ画面を消す。
  - 破綻シナリオ: audit.html は `splitIntoSessions()` で境界レコードから次の境界までを1セッションとし、`latestSession()` = 最後のセッションだけを 3秒ポーリングで描画する（audit.html:389-410, 733-734）。(a) sweep_run は自分の run のイベント群の**後ろ**に付くので、境界にすると「Automatic sweep → Stalled work detected → … → Request prepared」という §6 記載の並びは原理的に作れない（その run のイベントは1つ前のセッションに入る）。(b) より深刻: 30分毎の scheduled run は多くの場合「何も起きない」ので、**`sweep_run` 1行だけのセッションが最新セッションになり**、直前に収録した Jordan↔Marcus の match card と Human-first timeline が最大30分で画面から消える。収録中・審査員閲覧中に Bridge Trace が空になる。
  - 提案: セッション境界は `query` のままにし、run メタは「セッション内の先頭に付随表示するメタデータ」として扱う（spec §13「主役にしない」とも整合）。run 開始時刻を出したいなら sweep_run を run 開始時に `status=running` で1件出して完了時に更新する等、順序を設計で確定させる。いずれにせよ「イベント0件の自動 run はセッションを作らない」を明文化すること。

- [C-4] 種別: 設計 / 深刻度: mid
  - 指摘: `/internal/autonomous-sweep` が合成する `Principal(mode="system")` の **tenant_id が未定義**。
  - 破綻シナリオ: 全ルートは `router.for_tenant(principal.tenant_id)` でコンテキストを引き、未知テナントは 403 になる（server.py:318-325）。tenant_id 無指定の Principal では `KeyError` → 403 か、実装者が場当たりに `DEFAULT_TENANT_ID` を埋めることになる。後者だと `TenantRegistry.from_env()` の複数テナント構成では**1テナントしか sweep されない**のに監視は「動いている」ように見える（サイレント欠落）。既存 `/api/secretary/sweep` は「呼び出し元テナントのみ」と明記されており（server.py:635-636）、scheduled 側だけテナント決定規則が無いのは表の穴。
  - 提案: `AUTONOMOUS_SWEEP_TENANT`（既定 `meridian`）を env で明示するか、「registry の全テナントを順に sweep し、失敗は per-tenant で握り潰してカウントする」のどちらかを design に書き切る。§11 の env 追加表にも反映すること。

- [C-5] 種別: 実装 / 深刻度: mid
  - 指摘: §2 の「OIDC 検証コード（`_CachingCertsRequest` 再利用）は IapResolver に既存」は誤り。既存は **IAP 用**（`verify_token` + `IAP_CERTS_URL`(gstatic) + `iss=https://cloud.google.com/iap`、auth.py:194-205）であり、Scheduler の OIDC は `verify_oauth2_token` = Google OAuth2 certs + `iss=accounts.google.com`。両者は鍵ソースが違う。
  - 破綻シナリオ: `_CachingCertsRequest.__call__` は **引数 `url` を無視して単一レスポンスをキャッシュする**（auth.py:141-152: `self._cached_response` はURL別ではない）。同一インスタンスを IAP 用と OAuth2 用で共有すると、先に温まった方の鍵束がもう一方にも返り、署名検証が恒久的に失敗する。IAP デプロイなら「Scheduler が常に 401 → 自動 sweep が静かに止まる」、逆順なら「IAP ログインが全員 401」。fail-closed ではあるが本番停止を招く。
  - 提案: 新エンドポイントは**別インスタンス**（または URL をキーにしたキャッシュへの改修）を使うと design に明記。あわせて「`_deny_human` の表と整合」ではなく「resolver をバイパスする専用 dependency」である以上、§16.1 の permission 表に `/internal/autonomous-sweep` の行を追加する（default-deny 表の外に route が生えるのを防ぐ）。

- [C-6] 種別: 設計 / 深刻度: mid
  - 指摘: (a) Monitor OFF で「その owner のタスクをスキップ（凍結）」すると、**task が done になっても open card が resolve されない**。(b) §5.4 の「既存デモ UX は不変」は *reseed 後にしか成り立たない*のに、§11 のデプロイ手順に「reseed が必須」が入っていない。
  - 破綻シナリオ: (a) 既存ループでは done → `resolved`、score < T1 → `resolved` が同じ per-task ループ内にある（secretary.py:477-500）。owner 単位でスキップすると、Jordan が Monitor を OFF にした瞬間に「完了済みタスクについての NEED DETECTED カード」が My Agent に永久表示され、本人が手で dismiss するまで消えない（審査員デモで最も目に付く壊れ方）。(b) 本番 Firestore には `autonomy_policies` doc が無いので、デプロイ直後・reseed 前は全員 Search OFF に落ちる → 「scheduled sweep は 200 を返すのに NEED DETECTED が出ない」という診断困難な状態になる（§12 ゴール3 が落ちる）。
  - 提案: (a) policy ゲートは「検出・生成の側」だけに掛け、**terminal 化（done/score<T1 の resolve）は policy に関係なく常に走らせる**（安全側は「勝手に増やさない」であって「消さない」ではない）。(b) §11 の適用手順に「デプロイ → reseed（policy doc 生成）→ scheduler 有効化」の順序と、policy doc 欠損時に UI で "Monitoring paused" 相当が出ることを明記。

- [C-7] 種別: 設計 / 深刻度: low
  - 指摘: `preview_search` への `evaluate:boolean` 引数追加（§5.3 実装注）は、**その結果が使えないと design 自身が言っている**処理のために、候補分離の要である関数に分岐を足す過剰実装。
  - 破綻シナリオ: Ask OFF のとき「vector shortlist のみの軽量 preview」を回すが、reason_text が無いので昇格できず card は notice のまま（§5.3 が明記）。つまり出力の使途がゼロ。にもかかわらず (a) `preview_search` は VECTOR_FLOOR を適用しない設計なので shortlist は事実上「全 agent」になり、(b) その未評価リストを card.payload.preview に書くのか捨てるのかが design に書かれていない。書いてしまうと「評価されていない候補名」が所有者のカード payload に載り、UI の描画次第で未評価の人名が露出する。捨てるなら計算は純粋な無駄（C-2 の毎-run 再実行と掛け算になる）。
  - 提案: `ask=false` のときは preview を**呼ばない**（観測可能な結果は同一）。`evaluate` 引数は削除し、既存の候補分離関数には手を触れない。§5.3 の表の OFF 時挙動を「preview を実行しない／`policy_limited(stage=ask)` を1件だけ記録」に書き換える。

### 補足（指摘に含めない観察）
- §3 の run claim は `status=="done"`（返す）と `status=="running" かつ stale`（乗り直す）は定義されているが、**`running` かつ非 stale**（同時配信）の挙動が未定義。実装者が「両方走らせる」を選ぶと C-2 の重複が倍化する。design に「409 相当で `deduplicated:true` を返す」と1行足せば済む。
- §3 の run_key フォールバック「15分粒度に丸める」は schedule が `*/30` なので粒度が噛み合っていない（丸め境界を跨いだ再送は別 run になる）。丸め幅は schedule 間隔以上にすべき。
- spec §16 は「candidate search failure / agent response failure」を明示要求しているが、`run_sweep` の検出ループには per-owner/per-task の try/except が無い（`_sync_owners` にはある: secretary.py:389-400）。Gemini 429 が1件出ると、その run の残り owner は無処理のまま 500 になる。自動化で頻度が48倍になる分、design §16 相当の記述が欲しい。
