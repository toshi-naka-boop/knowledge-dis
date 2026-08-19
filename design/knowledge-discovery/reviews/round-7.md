critic: claude design-critic（claude-opus-5[1m]）, codex/gpt-5.6-sol xhigh（クロスベンダー並走。原文: round-7-codex-raw.txt、指摘X-1〜X-5として台帳に記録）

## Round 7 — 2026-08-19 — 工程: 批評（design.md v8 / M3秘書プロアクティブ層追補）

対象: design.md v8 §14全体、および v8 で追記された §8対応表トリガー行・§10ゴール12〜18・§11デモ構成・§12対応表FR16〜24・§15のM3未決項目。照合元 spec.md v7 FR16〜24。
既存コード（M1/M2実装済み）を裏取りに使用: `src/knowledge_discovery/matching.py`, `service.py`, `server.py`, `store.py`, `README.md`。

### 指摘

- [C-26] 種別: 設計 / 深刻度: high
  - 指摘: プレビュー検索の「public限定」が2段目推論にしか掛かっておらず、1段目のベクトル検索は全項目（public+private）から生成した既存 `profiles.embedding` を使う。FR19「公開プロフィール索引のみを対象」に1段目で違反しており、かつ public 限定に直すと実測較正済みの `VECTOR_FLOOR=0.62`（ledger E-1: 無関係0.59 / 関連0.68〜0.84）が無効になる。どちらに倒しても壊れる分岐が設計に書かれていない。
  - 破綻シナリオ:
    - (a) 現状の記述どおり全項目embeddingで1段目を回す場合 — 候補Cのpublic項目は接点が薄く、private項目「前職で医療モールの用地選定」が主因で類似度0.66→プレビュー上位に浮上。2段目はpublicのみを見るので `connection: null`。カードには候補が出ない。だが本人が「依頼する」を押すと正式実行はprivate込みなのでCが `connect_ask_private` として打診される。**カードに一度も名前が出なかった候補に、本人の確定直後に打診が飛ぶ**。デモ台本の非公開打診シーン（承継案件モチーフ = private接点前提）を停滞カード起点にすると、この不一致は確実に発生する。§14.4の「シード較正により同一候補になることを確認して収録する」は、片方がprivate込み・片方がpublic限定である限り原理的に保証できない。
    - (b) プレビュー用に public 項目だけで embedding を再計算する場合 — `VECTOR_FLOOR=0.62` は全項目embedding上で較正された値なので、public限定ベクトルでは関連候補も0.60前後に落ちて第1関門で全滅。sweep が `score ≥ T2` を検出しても candidates が空の「つながりリクエスト案」カードが出る（この空ケースの仕様も未定義。C-30参照）。
  - 提案: プレビュー用に `public_embedding` を profiles に第2ベクトルとして持たせ、`PREVIEW_VECTOR_FLOOR` を別env定数として別途較正する（シード投入時に両方生成すれば実装は数行）。そのうえで§14.4に「プレビュー候補と確定後の候補は集合として一致しない」ことを明記し、デモ台本では**停滞カード起点の質問はpublic接点のみで成立する候補構成にする**（非公開打診シーンは従来どおり手入力質問から始める）と規定する。

- [C-27] 種別: 設計 / 深刻度: high
  - 指摘: §14.7 A段の「Cloud Scheduler → OIDC認証付きHTTPターゲット（Scheduler専用SA・Cloud Run invoker権限）」が、実装済み・デプロイ済みの認証構成と両立しない。README のデプロイコマンドは `--allow-unauthenticated` であり、`server.py` は全エンドポイントに `Depends(verify_api_key)`（`X-API-Key` ヘッダ or `?api_key=` クエリ）を掛けている。allow-unauthenticated のサービスでは Cloud Run 側は IAM を検証せず OIDC トークンを素通しするため、invoker権限は実効を持たない。
  - 破綻シナリオ:
    - Scheduler が OIDC トークンだけを付けて `POST /api/secretary/sweep` を叩く → アプリの `verify_api_key` が `X-API-Key` も `api_key` も見つけられず **401**。Scheduler はリトライ後に諦め、ジョブ画面にしかエラーが残らない。朝のダイジェストは空のまま、本人UIには何も出ない。ゴール18は「Scheduler経由でsweepが実行されることを確認」なので、これに気づくのは検証段階＝8/29凍結の直前。
    - 逆に OIDC を実効化するため `--allow-unauthenticated` を外すと、ブラウザから `?api_key=` で開く既存UI（`/requester` `/candidate` `/audit`、round-6 V-3で確定した方式）が Cloud Run のIAM層で全て 403 になり、**デモ収録そのものが不可能**になる。
  - 提案: A段は「`--allow-unauthenticated` 維持＋Schedulerのジョブ定義に `X-API-Key` ヘッダを載せる（デモ脅威モデルでは既存の単一キー保護の内側）」に確定し、OIDCはB段（Agent Runtime）側の記述に移す。OIDCをA段で使うなら、アプリ側で `/api/secretary/sweep` のみ Google発行IDトークンを検証する分岐が必要である旨を明記する（実装量が増えるため非推奨）。§8のトリガー行も「Cloud Scheduler（OIDC）」の表記を実構成に合わせる。

- [C-28] 種別: 設計 / 深刻度: high
  - 指摘: M3のシグナルは全て日付演算（期日超過日数・無更新日数・期日リマインドの超過/当日/翌日）に依存するのに、「今日」の基準とシード日付の与え方が design に一切書かれていない。§14.2 は `schedules` を「具体日付のインスタンスをシード投入」と固定日で規定し、§15 は「デモ用シードは `T2` を確実に超える値で作り込む」とだけ書く。
  - 破綻シナリオ: 8/22にシード投入（`due_date` を8/23・8/24等の固定日で作成、`last_updated_at` を8/17に設定して無更新5日＝`score ≥ T2` になるよう較正）→ 収録が8/28にずれる、あるいは審査員が9月に再現手順を実行する → (1) 期日リマインドが「当日」「翌日」の行を1件も生成せず全て「超過」に倒れ、ゴール15の「超過→当日→翌日の順で表示」が満たせない、(2) 無更新日数が5→11日に伸びて `min(無更新日数, CAP)` が CAP に張り付き、`T1 ≤ score < T2` の気づきカード（低スコア側）が二度と再現しない、(3) 逆に投入直後（0日経過）にsweepを叩くと全カードが出ない。**デモ動画の冒頭30秒が収録日次第で成立しなくなる**。
  - 提案: (a) シード投入スクリプトを「投入時刻からの相対日数で `due_date` / `last_updated_at` / `status_changed_at` を生成する」方式に規定する、かつ (b) `SECRETARY_NOW_OVERRIDE`（ISO日付、未設定なら実時刻）を env に置き、sweep と digest の日付評価を全てこの1関数経由にする、の両方を §14.2 に明記する。再現手順（README）にも基準日の固定方法を書く。

- [C-29] 種別: 設計 / 深刻度: mid
  - 指摘: §14.4 の「既存ディスカバリ層を関数として切り出し `deliver=False` で呼ぶ」は、M3最大の主張（ゴール12「無痕跡」）をブール引数1個の fail-open な条件分岐に預ける設計であり、しかも既存コードの構造より退化している。実装済みの `MatchingEngine.run_matching()`（matching.py:276）は送信を一切行わない純粋関数で、`connect_ask` / `no_connection` の送信は `KnowledgeDiscoveryService.submit_query()`（service.py:65）側にある。切り出しもフラグも不要な状態が既に成立している。
  - 破綻シナリオ: 「切り出して `deliver=False`」の指示どおりに実装すると、`submit_query` に `deliver` 引数が生え、プレビュー経路と正式経路が同一関数を共有する。将来の改修・マージ・リトライ実装のいずれかで引数の引き回しを1箇所落とせば、sweep が実配送を行い候補者の受信箱に `connect_ask` が届く。これは「候補者は本人確定まで何も知らない」という M3 唯一のFortified主張の全損であり、しかも**sweepはScheduler駆動なので誰も見ていない時間帯に起きる**。ゴール12のassert（sweep後に `connect_ask` 0件）は正常系の1パスしか踏まないため検出できない。
  - 提案: §14.4を「プレビューは `matching_engine.run_matching()` をそのまま呼ぶ。秘書モジュールは送信層（`transmission.send`）を `stagnation_detected` / `preview_search` / `profile_diff_proposed` の3intent以外に対して呼び出さない」と書き換え、`deliver` フラグを設計から削除する。加えて送信層に「秘書モジュール由来の呼び出しで `connect_ask` 系intentが来たら例外」というガード（intent許可リスト）を1つ置き、構造的にfail-closedにする。

- [C-30] 種別: 設計 / 深刻度: mid
  - 指摘: `cards` の状態遷移が `open` 前提でしか定義されておらず、(a) `dismissed`/`confirmed` 後の再sweep、(b) プレビュー候補0件、(c) confirm途中失敗、の3分岐が全て未定義。冪等性の規定（§14.1）も検証ゴール14も `open` カードしか対象にしていない。
  - 破綻シナリオ:
    - (a) 本人が停滞カードを「見送り」→ `dismissed`。翌日のsweepでは同一 `(owner, task_id, stagnation)` の **open カードが存在しない**ため冪等ガードを素通りし、新カードが生成される。タスクは停滞したままなので score はむしろ上昇し、毎回再出現する。`confirmed`（=依頼済み）でも同様で、既に配送・同意まで進んだタスクについて「手がかりを持っていそうな人を探してあります」が翌朝また出る。デモで sweep を2回叩けば（収録のやり直しは普通に起きる）ダイジェストに同じカードが2枚並ぶ。
    - (b) `score ≥ T2` でプレビューを回したが全候補が `connection: null`（C-26(b)で常態化しうる） → `payload.preview.candidates` が空の「つながりリクエスト案」カードが生成される。UIは候補ゼロで「依頼する」ボタンだけを出すのか、気づきカードに降格するのか、カード自体を作らないのかが未定義。
    - (c) confirm で既存 `/api/query` を呼ぶ最中に Gemini が 429 を返す → カードを先に `confirmed` にしていればカードはダイジェストから消え、`linked_query_audit_id` は null、質問は配送されず、本人は「依頼した」と思ったまま何も起きない。逆に後で更新するなら二重確定の窓が開く。
  - 提案: 冪等キーを「`(owner, task_id, card種別)` の open カード」から「**status を問わない最新カード**」に変更し、`dismissed` は同一 `task_id` に対する再提示を `RESUPPRESS_DAYS` 日間抑止、`confirmed` は当該タスクについて再提示しない（タスクが `done` になるか、`linked_query_audit_id` の質問が全候補辞退で終わるまで）と規定する。(b) は「候補0件ならカード種別を `stagnation`（気づき）にフォールバックする」と明記。(c) は「`/api/query` 成功後にカードを `confirmed`＋`linked_query_audit_id` へ更新（失敗時は `open` のまま・UIにエラー表示）」と順序を固定する。

### 妥当と判断した点（根拠つき）

- **fail-closedマスクの流用（§14.6）は成立している**。新intent 3種を平文表示ホワイトリストに追加しない限り、§3の「`audit_payload == null` かつホワイトリスト外 → 表示不可」が既定で効く。ただしこの成立は「新intentに既存 `payload_type` を再利用しない」ことに依存しており、そこが最も危うい前提（`payload_type` 単位のホワイトリストのため、例えば `preview_search` に `query` 型を流用すると平文化する）。実装時に `payload_type` を専用名で新設する旨を§14.6に一言足すと閉じる。
- **停滞スコアのLLM不使用・evidence_lineのテンプレ合成（§14.3）は妥当**。5シグナルが全て `tasks` の値から決定的に計算でき、「相対停滞」が休暇・繁忙による全タスク停止を除外する条件になっている点は誤検知の実害（監視ツール化）に直接効く。
- **B段（Agent Runtime載せ替え）の規律は過剰実装ではない**。spec制約でGEAP採用が確定しており、かつ「A段完了後にのみ着手」「8/27未着手なら記載のみ」という降り方が§14.7・§15の両方に書かれている。8/29凍結との整合が取れている。

### 未提起にした論点（記録のみ、指摘枠外）

- `GET /api/secretary/digest?employee_id=` は既存の単一APIキー保護下で任意の `employee_id` を指定でき、他人の停滞タスク名・質問下書き・候補名が読める（FR20「本人以外に通知されない」の主張範囲外）。round-6 S-3（1人4役ドロップダウンをデモ用意図的仕様として採用）と同型のため指摘としては起票しない。ただしwrite-upで「検知事実は本人にしか見えない」と書くと虚偽になるため、§4-4と同じ「主張範囲」の但し書きを§14.4末尾に置くことを推奨する。
- §14.5-3 の追加項目 `{key, body, source: "mail_seed", visibility, reviewed: true}` は `visibility` の既定値が未記載。4択に「公開範囲を非公開にして反映」がある以上既定はpublicと読めるが、デモ舞台が事業承継（未公表・守秘）である以上、メール由来項目が既定publicで索引に載り他者への `reason_text` に平文引用される経路が開く。**指摘枠が5件のため落としたが、優先度はC-30と同等**（1行で `visibility: "public"` を既定と明記するか、`source=="mail_seed"` は既定privateとするかの判断を§14.5に入れること）。
