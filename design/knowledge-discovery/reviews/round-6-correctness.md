critic: claude-opus-5[1m]

## Round 6 — 2026-08-18 — 工程: 反証(正しさ / correctness)

対象: `src/knowledge_discovery/`（models/store/schemas/transmission/matching/service/server/firestore_store/gemini_adapters + web 3画面）、`scripts/generate_seeds.py`、`tests/`（61件パス）
基準: `design.md` v7 / `spec.md` v6 / `seed-spec.md`。台帳 C-1〜C-25 は蒸し返さない（C-18/C-21 に関する指摘 V-1 は「設計方針への異議」ではなく、その方針が**実装で成立していない**ことの反証として提出する）。
実測: high 疑い 2件（V-1, V-2）についてのみコードを実行して再現を確認。ログは各項に添付。

### 指摘

- [V-1] 種別: 実装 / 深刻度: high
  - 指摘: privateマスクがLLMの返す `cited_item_keys` の**文字列一致に無検証で依存**しており、キー表記ゆれ・キー欠落のいずれでも「public扱い」に倒れる（fail-open）。design.md §3 の「判定主体をLLMから型システムに移す」「監査表示はfail-closed」が実装では成立していない。
  - 破綻シナリオ:
    - (a) 表記ゆれ: Gemini が Elena の private 項目 `transition_pipeline` を根拠に reason_text を書き、`cited_item_keys: ["Transition Pipeline"]`（大文字/空白）と返す。`Profile.is_item_private()` は完全一致検索のため False → `TransmissionLayer.send` はマスクを生成せず intent は `connect_ask` のまま → `AUDIT_WHITELIST` に `connect_ask` が入っているので、**NDA案件の内容が監査ダッシュボードに平文表示**される。
      実測（再現済み）: `intent=connect_ask, audit_payload=None, audit view={'reason_text': 'Elena is advising an unannounced clinic succession whose owner is exploring relocation before sale (NDA deal).', 'cited_item_keys': ['Transition Pipeline'], ...}`
    - (b) キー欠落: `gemini_adapters._parse_json_result` は `cited_item_keys` が空/不正のとき `all_item_keys[:1]`（=プロフィール**先頭**キー）を代入する。シードの Elena は items 順が `current_work`(public) → `expertise`(public) → `transition_pipeline`(private) なので、private を根拠に推論しても代入されるのは public キー1件。マスクは永久に発火しない。
      実測（再現済み）: `_parse_json_result({"connection": {...}}, ["current_work","transition_pipeline"]) -> cited_item_keys=['current_work']`
    - これは design.md §0 の統制原則2（非公開項目の内容は本人以外の誰にも開示されない）が破れる唯一の経路であり、デモ台本シーン2（非公開項目打診）の主張そのものを崩す。temperature=0.2 でも構造化出力の欠落は起こり得る。
  - 提案: (1) `_parse_json_result` の未指定時フォールバックを「先頭キー1件」から「プロフィールの**全キー**」に変える（安全側に倒れる）。(2) 送信層で `cited_item_keys` をプロフィール実キー集合に正規化（trim + casefold 一致）し、**解決できないキーが1つでも残ったら private とみなしてマスク**する。(3) `AUDIT_WHITELIST` の `connect_ask` を「プロフィールの全 cited key が実キーに解決でき、かつ全て public」のときのみ有効にする（fail-closed をキー解決レイヤまで延長）。
  - テストの穴: `test_goals.test_goal_4_*` / `test_transmission.test_private_mask_rule_*` はいずれも**実在する private キーを正確に返す**オーバーライドしか流していない。未知キー・空キーのケースが1件もないため、fail-open が61テスト全緑のまま素通りしている。

- [V-2] 種別: 実装 / 深刻度: high
  - 指摘: 依頼者と ask の対応付けが `KnowledgeDiscoveryService._ask_to_requester` / `_query_to_asks`（**プロセス内メモリ**）にしかなく、Firestore 側に復元手段が存在しない（`connect_ask` payload に requester_id も query_audit_id も入っていない。`from` は "system"）。Cloud Run（min-instances=0、複数インスタンス可）ではプロセス再作成で状態が消える。
  - 破綻シナリオ: 質問投下 → 収録中断やアイドルで**インスタンスが落ちる/2台目にルーティングされる** → (a) `/api/requester/{id}/status` が空配列を返し依頼者画面が「No inquiries yet」になる、(b) その後の辞退が `requester_id = self._ask_to_requester.get(ask_audit_id, "requester")` のフォールバックで**存在しない宛先 "requester" 宛**に送られ、依頼者に一生届かない（監査ログにも to="requester" の宙に浮いた行が残る）。デモ収録は複数テイクにまたがるため、踏む確率が高い。
    実測（同一 store・別サービスインスタンスで再現）: `statuses after restart: []` / `decline routed to: requester`
  - 提案: `connect_ask` / `connect_ask_private` の payload に `requester_id`（と `query_audit_id`）を持たせ、`get_requester_status` と `respond_consent` を store からの再構成に切り替える。スキーマ検証は追加フィールドを拒否しないので実装のみで閉じる（design.md §3 の payload 定義には追記が必要）。暫定回避なら Cloud Run を `min-instances=1` かつ `max-instances=1` で収録する。

- [V-3] 種別: 実装 / 深刻度: mid
  - 指摘: Web 3画面が API キー `"demo-key-2026"` を**HTML内にハードコード**しており（`requester.html:116` / `candidate.html:93` / `audit.html:201`）、かつ `/requester` `/candidate` `/audit` `/attachments/{id}` は `verify_api_key` 依存を持たない無保護ルート。design.md §3「UIの保護はデモ用の簡易な全体保護（Cloud Run IAM または単一APIキー）」を実装が満たしていない。
  - 破綻シナリオ: (a) Secret Manager に入れた `DEMO_API_KEY` が `"demo-key-2026"` **以外**なら、デプロイ後に3画面とも表示はされるが全 fetch が 401 になり、質問送信も候補一覧も監査表も空のまま—原因が画面上に出ないため収録直前に詰む。(b) 逆に値を `"demo-key-2026"` に合わせた場合、公開 Cloud Run URL を知る誰でも `/audit` を開くだけで鍵を入手し `/api/audit/messages`（全メッセージ payload）を取得できる。Secret Manager 経由という統制の主張が実体を持たない。
  - 提案: HTML から定数を削り、キーは (i) サーバ側で HTML に注入する、(ii) UI ルート自体を `verify_api_key` 配下に置き `?api_key=` を1回だけ通す、のいずれかにする。最小工数なら (ii) + UI ルートに依存追加。

- [V-4] 種別: 実装 / 深刻度: mid
  - 指摘: 依頼者射影が**質問単位で分離されていない**。`server.get_requester_status` は `query_audit_id` を渡さないため常にフォールバック経路に落ち、その requester の**過去全質問の ask** を返す（`_query_to_asks` は誰からも使われないデッドコード）。さらに辞退の突合が「同一 entity からの最初の `decline_with_reason`」を線形探索で拾う実装（`service.py:245-251`）で、ask との紐付けがない。
  - 破綻シナリオ: 同じ依頼者 `emp_jordan_lee` でシーン1（移転相談）とシーン2（承継＝private打診）を続けて撮ると、シーン2の依頼者画面にシーン1の候補カードが混ざり、design.md §6-5 の「3状態が1画面で見える」台本が崩れる。撮り直しでも同様で、2回目の辞退カードには**1回目の理由と添付**が表示される。
    実測（再現済み）: 2回目の辞退後の status 2件がいずれも `TAKE1: busy` / `attachment=doc_clinic_relocation_guide`（TAKE2 の reason/link は表示されない）。
  - 提案: `/api/requester/{id}/status` に `query_id` クエリパラメータを通し `_query_to_asks`（V-2 対応後は store 由来）で絞る。`decline_with_reason` payload に `ask_audit_id` を持たせ、突合を「最初に見つかった同一送信元」から ask 単位の一致に変える。

- [V-5] 種別: 実装 / 深刻度: mid
  - 指摘: `match_proposal` が `from="system", to="system"` の**1通しか発行されない**（`service.py:165-176`）。design.md §6-1 と検証ゴール5「依頼者・同意者双方に届く」を満たしていない。参加者情報は payload の `participants` にあるだけで、監査ダッシュボードの「誰が・何を」列は system→system としか読めず、「双方に届いた」証拠がログに存在しない。
  - 破綻シナリオ: 監査画面で成立シーンを見せる際、`match_proposal` の行は from/to ともに system になり、ナレーション「MTG提案が双方に届きます」を画面が裏づけない。write-up で「双方に発行」と書くと提出物と実装が食い違う。
  - 提案: 依頼者宛・同意者宛の2通を発行する（payload は同一で可）。または design.md §6-1 を「1通＋participants」に改める（その場合は §6-1 と §10-5 の文言修正が必要＝設計差し戻し）。
  - テストの穴: `test_goal_5_consent_granted_generates_match_proposal_without_reason_text` は docstring に "for both participants (C-19)" と書きながら、検証しているのは `participants` リストの中身と `reason_text` 不在だけで、**宛先が誰かを一度も assert していない**。「双方に届く」は現状どのテストでも検証されていない。

### 補足（指摘に数えない・判断はメインループに委ねる）

- `TransmissionLayer.send` の `supported_intents` 検査（Step 2）は **`connect_ask` → `connect_ask_private` の昇格前** の intent で行われる。シード4体は3 intent すべてを許可しているため現状は顕在化しないが、「レジストリが流量を止める統制」という主張は最も繊細な private 打診の経路では成立していない。加えて `supported_intents` が空リストのエージェントは全 intent 素通り（fail-open）。
- 実行系 API に `reject_unregistered_type` / `reject_unsupported_intent` を発生させる経路がないため（`/api/query` と `/api/candidate/*/consent` しか書き込み口がない）、ゴール4b・7 の「監査画面に赤表示」はデプロイ済みアプリでは再現できずテスト内でしか示せない。デモ §11-3 で赤表示を実演する場合は注入用エンドポイントが要る。
- 埋め込み次元不一致は**サイレント全落選**になる。`generate_seeds.py --embedder` の既定は `deterministic`（128次元）で、サーバは Vertex 有効時に `GeminiEmbedder`（数千次元）を使う。`similarity()` は長さ不一致で 0.0 を返すため、全候補が `vector_floor` で落選し監査画面には「ベクトル類似度(0.000)が下限を下回った」とだけ並ぶ。再シード時に既定フラグのまま流すと原因不明の全落選になるので、起動時に「プロフィール1件の埋め込み次元 vs クエリ埋め込み次元」を突き合わせて即座に落とすガードを推奨。
- design.md §9-2 が要求する「visibilityトグルのみの最小レビュー画面」が実装に存在しない（web は requester/candidate/audit の3画面のみ）。spec FR2/FR3・完了条件の充足を write-up で主張するなら、画面を足すか主張を「Firestore直編集で代替」と正確に書く必要がある。
