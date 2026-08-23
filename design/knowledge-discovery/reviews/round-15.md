critic: claude design-critic（model: claude-opus-5[1m]）

## Round 15 — 2026-08-24 — 反証(再検証: round-14 修正のクローズ確認 + 回帰)

対象: `6f2353b..b171a22`（round-14 の7論点の修正）／基準: design.md v15 §16・§10 ゴール23〜30、ledger.md 冒頭「反証round-14のルーティング」
検証環境: オフラインのみ（`/Users/toshixa/dev/Knowledge-discovery/.venv/bin/python3`、src/ 変更なし）。`PYTHONPATH=src:. .venv/bin/python3 -m unittest discover -s tests` = **208 tests, OK (skipped=13)**（round-14 時点 199 → 追加9件、既存の期待値変更なし）
再現スクリプト: `<scratchpad>/repro.py`（A〜D）・`repro2.py`（鍵キャッシュ・台帳正規化・api_key_env）

---

## 1. round-14 指摘のクローズ確認

### V-11 / E-11（high、ゴール28が構造的に不成立）— **クローズ確認（実測）**
`_sync_owners` は `self_only` を「フィルタ」から「対象の指定」に変更（secretary.py:365-370）。空 InMemoryStore（agents 0・profiles 0）＋所有者を無視するコネクタ＋`GWS_SELF_EMPLOYEE_ID=emp_self` で実測:

```
fetch called for: ['emp_self']
sweep: {'sync_tasks': 1, 'sync_schedules': 1, 'sync_mails': 1, 'sync_skipped_owners': 0, 'sync_skipped_mails': 0, 'sync_errors': 0}
tasks in store: 1
digest reminders: 1 stagnation cards: 1 profile_diff cards: 1
```
round-14 原文の `fetch called for: []` / `digest reminders: 0 cards: 0` は消滅。ゴール28（空ストアでの偽陽性防止つき実データ確認）が成立可能になった。回帰テスト `test_self_employee_id_mode_syncs_owner_with_no_agent_or_profile_registered` も同条件を実行しており、モックで迂回していない。

### V-13 / S-11（high、未設定時の fail-open で他人へ帰属）— **クローズ確認（実測）**
`build_connector_from_env()` が `SOURCE_CONNECTOR=google_workspace` かつ `GWS_SELF_EMPLOYEE_ID` 未設定のとき `_MisconfiguredGwsConnector`（fetch が常に RuntimeError）を返す。所有者3名登録・未設定で実測:

```
build_connector_from_env -> _MisconfiguredGwsConnector
sweep: {'sync_tasks': 0, ..., 'sync_errors': 3}
  emp_alice: tasks=[] schedules=[] mails=[]
  emp_bob:   tasks=[] schedules=[] mails=[]
  emp_carol: tasks=[] schedules=[] mails=[]
```
round-14 原文の `author mail ended up owned by: emp_alice` / `author task ended up owned by: emp_carol` は消滅（同スクリプトで実コネクタを**直接注入**した対照区では従来どおり奪い合いが再現するので、消滅の理由がガードであることも確認済み）。実運用の唯一の生成点は `server.py:282` の `build_connector_from_env()`、`scripts/gws_probe.py` は `GWS_SELF_EMPLOYEE_ID` を自身で強制するため、ガードを迂回する経路は無い。S-11 の提案にあった「`fetch()` 側の二重化」は未実施だが、到達可能な経路が塞がっているため未クローズとはしない。

### V-12（high、seed 戻しでの gws データ破壊＋801クエリ）— **クローズ確認（実測）**
`_sync_owners` 冒頭の `isinstance(self.connector, SeedConnector)` 短絡（secretary.py:351-358）。gws 同期済みストアに対し `SOURCE_CONNECTOR=seed` で sweep:

```
after gws sweep: task status = todo  schedules = 1
task status after a 'seed' sweep: todo | gws schedules remaining: 1
store call counts (seed sweep): {'list_tasks': 1, 'list_mail_seeds': 2}
```
round-14 原文の `task status after a 'seed' (no-op) sweep: done` / `gws schedules remaining: 0` は消滅。所有者ごとの `list_tasks(source=)`・`list_schedules(source=)` も 0 回（原文の 401+400=801 クエリが消滅）で、Firestore 経路の sweep 所要時間への上乗せもゼロ。`apply_fetch_result` 自体が呼ばれないことをテスト（`side_effect=AssertionError`）が担保。

### E-12 / S-12（high/low、鍵キャッシュの 2×TTL 猶予）— **クローズ確認（実測）**
auth.py:152-156 の猶予分岐を削除。実測:
```
first fetch ok: 200 cached ttl: 100.0
within window (no network): True
past ttl + failed fetch -> RuntimeError network down
1.5x ttl (old grace window) -> RuntimeError network down
```
旧コードが受理していた 1.5×TTL でも例外が伝播（=`IapResolver` は 401 で fail-closed）。失敗後も `_fetched_at` を更新しないため、以後の呼び出しも毎回再取得→失敗→401 で、失効鍵の受理窓は残っていない。design §16.1 の文言と実装が一致。

### E-13（mid、平文 api_key 経路）— **クローズ確認（実測）**
`_config_from_entry` は `api_key_env` のみ受理。実測: 平文 `api_key` のみの台帳は `RuntimeError: Tenant 'a' must set 'api_key_env'.`、`api_key_env` が未設定の env を指す場合も `has no api_key configured.` で起動失敗。`TENANTS_JSON` 未設定のデモ既定経路は不変（`meridian` / `(default)` / `meridian-care.example` / `DEMO_API_KEY` で鍵照合成功）。リポジトリ内に平文 `api_key` を渡す deploy スクリプト・手順は残っていない（`TENANTS_JSON` の grep で README・design・tests のみ）。

### V-14 / S-13（low、台帳の大文字小文字未正規化）— **クローズ確認（実測）**
`_normalize_strings`（strip+lower）を `_config_from_entry` と `single()` の双方に適用。実測: `MERIDIAN-CARE.EXAMPLE` と `meridian-care.example` を別テナントに書いた台帳は起動時に `Duplicate email_domain in tenant ledger: 'meridian-care.example'` で拒否、大文字混じり SA（`KD-Scheduler-SA@Proj...`）は小文字化された JWT email で `resolve_by_system_account` がヒット。S-13(a)(b) 両方の破綻シナリオが消滅。

### E-14（mid、probe の見せかけ経路）— **クローズ確認（読解＋テスト実行）**
`--apply-to-memory` は実 `SecretaryService` を組み、`run_sweep()` → `get_morning_digest()` を通す（`GWS_SELF_EMPLOYEE_ID` を `--owner` に強制）。出力は `run_sweep` の戻り値（件数キーのみ。secretary.py:708-717 に本文・題名は含まれない）と reminder の kind/due_category・card の type/tier のみ。`tests/test_gws_probe.py` は機密文字列入りのフェイク応答で stdout に題名・本文が出ないことを実測しており、見せかけではない。

### E-15（low、README「Every route」の誇張）— **クローズ確認（読解）**
README:69-76 が「Every `/api/*` route」に限定され、静的UI 4画面と `GET /attachments/{id}` を「認証なし（部品A以前から不変）」と明記。§16.1 の権限表の実態と一致。

### V-15（low、sync_skipped 合算・cancelled_ids 未使用）— **部分クローズ**
前半（カウンタ分離）は**クローズ確認（実測）**: 自分＋他2名の登録下で `sync_skipped_owners: 2`、2回目 sweep の重複メールは `sync_skipped_mails: 1` と別勘定になり、ゴール26/28 の読み違いは起きない。外部消費者（B段 `secretary_agent`・UI・README）が旧キー `sync_skipped` を参照していないことも grep で確認。
後半（`cancelled_ids`）は**未クローズ**: 台帳のルーティング決定は「cancelled 削除」だったが、実装は逆に `cancelled_ids` を機能させ、しかも完全性バリアの**例外**にした（base.py:279-291）。下の R-5 に集約する。

---

## 2. 新規欠陥（diff 6f2353b..b171a22 に限定）

- [R-3] 種別: 実装 / 深刻度: low
  - 指摘: 単独モードが「フィルタ」から「対象指定」に変わった結果、`GWS_SELF_EMPLOYEE_ID` が**そのストアに存在しない employee_id でも無条件に同期先になる**（secretary.py:365-367）。未登録所有者への同期はゴール28に必要な仕様だが、「未登録＝設定ミス」の場合と区別する手がかりが sweep 応答に無い。
  - 破綻シナリオ: シード投入済みのデモ環境で `GWS_SELF_EMPLOYEE_ID` を打ち間違える（`emp_00i` 等）と、作者の実 Tasks/Calendar/Gmail が**存在しない従業員の所有物として Firestore に書き込まれ**、その ghost 所有者の停滞カード・profile_diff カードが生成される（実測A で agents 0/profiles 0 の所有者に対しカード生成まで到達することを確認済み）。sweep 応答は `sync_tasks=1, sync_errors=0` で緑に見え、収録中に画面に出している本人の digest だけが空になる。原因の切り分けは Firestore を直接見るまでできない。複数テナント構成では、同じ env のまま各テナントの sweep を回すと作者データが各テナントDBに複製される（round-14 E-11 が指摘した複製の残り半分）。
  - 提案: `self_only` が `registered_owner_ids` に無く、かつストアが空でない（`registered_owner_ids` が非空）ときだけ `sync_errors`＋ログ1行で警告する（3行。ゴール28の空ストア経路は非空条件で除外されるので影響しない）。

- [R-4] 種別: 実装 / 深刻度: low
  - 指摘: `_MisconfiguredGwsConnector` は所有者ごとに例外を投げるため、`_sync_owners` は**登録所有者数ぶんループして同じ設定ミスを数え続ける**（`self_only` 未設定なので `target_owners` は全所有者のまま）。実測では所有者3名で `sync_errors: 3`。
  - 破綻シナリオ: 本番シード（400プロフィール）で `SOURCE_CONNECTOR=google_workspace` だけを設定すると、毎朝の sweep が 400 回の例外生成を回し `sync_errors: 400` を返す。`errors` は件数のみで理由文字列を持たない設計のため、運用者から見て「設定ミス」と「Google API が 400 回失敗した」が同じ見え方になり、Scheduler は 200 で成功扱いのまま（fail-closed なのでデータ被害は無いが、原因追跡が README を読むまで進まない）。
  - 提案: `SeedConnector` と同じく `_sync_owners` 冒頭で `_MisconfiguredGwsConnector` を短絡し、`sync_errors: 1` で1回だけ返す（3行）。または `logger.warning` を1回だけ出す。

- [R-5] 種別: 設計 / 深刻度: low
  - 指摘: 修正が design.md v15 §16.3 の記述と3点ずれ、design.md 側が更新されていない（本コミットに design.md の差分なし）。(a) 「Calendar も同様に**全ページ成功時のみ**『窓外・取消の schedule を削除』」に対し、実装は `cancelled_ids` を完全性バリアの外に出した（base.py:279-291。ledger のルーティング決定は「cancelled 削除」で、実装はその逆）。(b) §16.3 の単独モードは「その所有者だけ同期し他は skipped」までで、**未設定時の fail-closed 必須**が書かれていない（README にはある）。(c) sweep 応答のキー名が `sync_skipped` → `sync_skipped_owners`/`sync_skipped_mails` に変わった。
  - 破綻シナリオ: design.md が唯一の真実である以上、次の批評・実装工程が §16.3 を読んで「取消もバリア内」と解釈し、`cancelled_ids` の例外を「バリア破り」として差し戻す／逆に元に戻して V-15 を再燃させる。さらに (a) の根拠として置かれた「取消は source からの積極的シグナル」は現状の実装では成立しない: `_fetch_calendar` は `showDeleted` を指定していない（google_workspace.py:252-257）ため Calendar API は既定で cancelled イベントを返さず、`cancelled_ids` は実質的に常に空＝テスト（`test_calendar_cancelled_event_deleted_even_on_incomplete_sync`）だけが通る死んだ経路になっている。8/29 凍結後に「取消が同期されない」バグとして再調査されるコストが残る。
  - 提案: どちらか一方に寄せる。(1) V-15 のルーティング決定どおり `cancelled_ids` を削除し（`FetchResult` のフィールドごと）、取消は現行どおり「取得結果に無い＝窓外扱い」でバリア内に統一する、または (2) §16.3 に「`cancelled_ids` は完全性バリアの例外」「`showDeleted=true` を付ける」の2行を追記して実装と設計を揃える。(b)(c) は §16.3 に各1行追記で足りる。

---

## 3. 反論なしとした観点（確認して問題なしと判断したもの）

- **既存機能の回帰なし**: 208件 OK（skip 13）。tests の差分は追加＋round-14 で誤って仕様化されていた猶予テストの書き換えのみで、M1〜M3・B段・マスク系の期待値変更はゼロ。`schemas.py` / `transmission.py` / `matching.py` / `server.py` / `store.py` / `src/secretary_agent` は本コミットで差分ゼロ（§16.4「触らない」を維持）。
- **デモ経路の不変**: `TENANTS_JSON` 未設定＝既定1テナントの解決経路、`SOURCE_CONNECTOR` 未設定＝`SeedConnector` の no-op（Store クエリ増加ゼロ）を実測で確認。round-14 の修正は demo/seed の機能結果を一切変えていない。
- **probe の情報取扱い**: `run_sweep` の戻り値は件数キーのみ、digest からは kind/due_category/tier のみを集計して印字。機密文字列入りフェイク応答で stdout に漏れないことをテストが実測。なお probe は `fetch()` を2回叩く（表示用＋sweep 内）ため API 呼び出しが2倍になるが、上限20件・1日1回の手動ゲート用途では実害なしと判断。
- **fail-closed の方向性**: 今回の3つの修正（gws 未設定・鍵期限切れ・平文鍵）はいずれも「拒否側」に倒しており、可用性を落とす代わりに誤帰属・失効鍵受理・秘密の台帳埋め込みを構造的に排除している。sweep は例外を所有者単位で捕捉して続行するため、fail-closed 化によるサーバ停止・sweep 全停止は起きない（実測B: 設定ミスでも既存データに対する検知は継続し、カードは生成された）。
