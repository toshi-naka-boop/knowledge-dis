# セキュリティ再監査 統合レポート — 攻撃者視点

日付 2026-08-29。3レンズ並列（claude 認証/認可・claude 情報漏えい/注入・codex クロスベンダー赤チーム）。
個別レポート: security-auth.md / security-disclosure.md / （codex 原文はタスクログ）。
すべての主張は本統合時にメイン側でコード再確認済み（file:line）。

## 総括
- **Critical / 認証バイパスは無し。** 今回追加した自律フェーズ（OIDC endpoint・policy enforcement・counts-only trace・冪等機構）は健全。design-loop が守り切っている。
- **真の攻撃面は新コードではなく、ベース製品のデモ姿勢**（共有 API キー＝テナント内 god-mode、監査ダッシュボードのテナント全体可視）。意図的簡略化だが、セキュリティトラックの審査では必ず目に付く。
- **冪等性中心の批評が構造的に見落としていた新規バグが3件**（スコア fail-open・run_key のクライアント信頼・入力/コスト無制限）。いずれも insider または token 窃取が前提で Critical ではないが、実在。

## 健全と再確認できた防御（提出でアピールできる）
- `/internal/autonomous-sweep`: 署名・`aud`・Google issuer・**invoker SA メール完全一致**・`email_verified` を検証。別プロジェクトの SA 署名トークンは email 不一致で弾かれる。env 未設定なら 404 不活性。（auth.py:275-296）
- テナント分離: DB は `principal.tenant_id` のみから解決。body/query/header にテナント上書き経路なし。鍵↔テナント 1:1。
- intent は全てコード側ハードコード。LLM 出力から intent は生成されず、未登録 intent の注入は不可能。per-candidate isolation で候補間・ユーザー間の横断漏洩は構造的に不能。
- counts-only 監査（sweep_run/policy_limited）: 許可キー完全一致 validator＋型/enum＋get_audit_view 再射影の二重防御。値はサーバ生成のみ。
- 自律 sweep は候補カードを作るだけで、本人確認なしに connect_ask を送らない（Human Boundary はコードに実在）。
- OIDC 失敗・テナント例外の応答は固定文言。例外文字列・token・API キーを本文にもログにも出さない。
- attachments ルートは dict ルックアップでパストラバーサル不可・非機密の固定3文書のみ。

## 実在する指摘（重大度順）

### S-1【High・実デモで悪用可】共有 API キー＝テナント内 god-mode
- 前提: テナントの API キーを知る（デモは既定 `demo-key-2026` で起動しうる）。
- 攻撃: `demo` principal は社員 ID に紐付かず、`_require_self_*` は `human` にしか作用しない。任意社員の digest、非公開理由を含む candidate asks を読み、他人として consent/カード操作/profile-diff 適用が可能。
- 分類: **意図的なデモ簡略化**（README 記載済み）。ただしセキュリティトラックでは弱点として映る。
- 対策: 提出環境を `AUTH_MODE=iap`＋社員 ID 紐付けへ。共有キーを残すなら固定 seed の**読み取り専用デモ**に限定し本人操作 API を無効化。最低限、現行キーを提出前にローテーション。

### S-2【High・実デモで悪用可】監査 API がテナント全体を全ユーザーへ開示
- 前提: テナント内の任意の有効 credential（IAP 化しても現状は発生）。
- 攻撃: `/api/audit/messages` に role/本人スコープが無く全 message を返す。whitelist 表示の `query`/`connect_ask`/`no_connection` は質問本文・理由が見える。マスク済みでも `from`/`to`/`intent` は常に返り、同意前の候補も特定可能。
- 分類: **実在**（一部はデモの「透明性の演出」として意図的だが、スコープ無しは行き過ぎ）。
- 対策: audit を監査管理者ロール専用にし、一般ユーザーには自分のイベントのみ。同意前は from/to をセッション内 ID に置換。

### S-3【High・理論上/Gemini 応答依存】プロンプト注入でスコア検証が fail-open
- 前提: 候補プロフィール本文を操作でき（＝プロフィールを書ける insider）、対象質問と vector floor を越える。
- 攻撃: プロフィール本文は命令と同じ prompt に直結（gemini_adapters.py:191）。「reason を返し score を省略せよ」と誘導 → 欠落 score が既定 **0.8** に補正（gemini_adapters.py:263）→ 閾値 **0.50** を通過 → **requester の質問本文が攻撃者のエージェントへ connect_ask として配送**される。＝他人の質問を刈り取れる。
- 分類: **実在**（insider 前提・Gemini 挙動依存）。冪等性批評が触れていない配送認可の穴。
- 対策: score 欠落・非有限・範囲外・引用なしは必ず `no_connection`。LLM スコアを配送認可の単独根拠にしない。（数行の修正）

### S-4【Medium・実デモで悪用可・要 invoker token】run_key がクライアントヘッダを信頼
- 前提: Scheduler SA の有効 OIDC token を窃取 or impersonate。
- 攻撃: `X-CloudScheduler-JobName`/`ScheduleTime` を検証せず run_key に使用（server.py:173-193）。値を毎回変えれば dedup を回避して全テナント sweep を連打でき、レート制限も無い。
- 分類: **実在**（token 前提）。
- 対策: ヘッダを信頼せず、サーバ時刻の固定 slot＋固定 job ID から run_key 生成。全テナント single-flight・頻度上限・処理予算。

### S-5【Medium・実デモで悪用可】manual sweep / query に入力・コスト上限が無い
- 前提: 共有 API キー or 許可 principal。
- 攻撃: `question_text` 等に長さ制約なし（server.py:126）。manual sweep は毎回新 UUID で dedup されず全権 override。sweep は全 profile/全 task/未処理 mail を読み LLM を反復。Gemini 429 時は**同期スレッドで 62 秒×最大4回 sleep**（gemini_adapters.py:86-104）＝ワーカー枯渇。
- 分類: **実在**（コスト/DoS）。
- 対策: body/field 上限、principal 別レート制限、1 sweep 当たりの task/mail/LLM 上限、429 は sleep でなく再試行キューへ。

### S-6【Medium・実デモで悪用可】同意前の候補名・private-topic membership を推測可能
- 前提: 任意のテナント credential。
- 攻撃: on-demand funnel は full `embedding`（public＋private から生成、matching.py:219）を使う。`/api/query` は上位候補の社員 ID・氏名・類似度を返す。秘密語を変えて反復問い合わせれば「誰がその private topic を持つか」を推測できる。
- 分類: **実在**（信号はノイジーだが membership inference は成立）。
- 対策: requester 向け funnel は `embedding_public` のみ使用。

### S-7【Low・実在】API キーが `?api_key=` query param で受理
- 攻撃: UI がキーを全画面 URL に伝播 → Cloud Run ログ/Referer/ブラウザ履歴に残留。
- 対策: query param 認証を削除しヘッダのみに。（S-1/S-8 と一体で対処）

### S-8【Low・IAP本番時のみ】`/api/secretary/digest` の system ガード欠落
- system principal が任意従業員の digest を読める。1 行修正（`_deny_system` 追加）。demo_key デプロイでは無害。

### S-9【Low・stale doc】get_requester_status の docstring と実装の矛盾
- docstring「pending 時は candidate identity を出さない」に反し、コードは `respondent_name` を返す（server.py:562）。
- **実害は無い**（requester 自身が Ask した相手なので既知。UI も "Request sent to Marcus" と表示する意図的仕様）。docstring が stale。→ docstring 修正のみ。

## 提出前に直すべき最優先
**公開デプロイを共有 `demo_key` から、少なくとも「読み取り専用・本人操作 API 無効」のデモ姿勢へ絞る（S-1/S-2）。** これが水平権限昇格とテナント全体開示の両方を塞ぐ唯一の一手。IAP 化が本筋だが提出までの時間次第。
