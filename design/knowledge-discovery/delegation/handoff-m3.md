# Handoff: knowledge-discovery マイルストーン3（秘書プロアクティブ層・A段）

あなたは Antigravity Builder として、controller 検証済みの headless モードで実装を行う。M1/M2（コアバックエンド＋実サービス接続＋簡易UI、既存テスト全パス済み）の続き。

## Controller-validated receipt

```json
{
  "validator": {
    "approval_fingerprint": "v9:9df6a687bb7b",
    "baseline": "360f2617999b9c9bb52147f44b4d436a63c36b15",
    "design_sha256": "9df6a687bb7bbe589b9d2b476191f8b1cfc9945d1f29907e6310f38d910ec344",
    "design_version": "v9",
    "phase": "build",
    "status": "valid",
    "validator_sha256": "99bde0cd6961842e664044a80b6a00db4b503f8e68618cdce5e6fbd5fda8ab5a"
  },
  "permission_config_sha256": "b59c4ee001888258f9fdcc152e621e0ea63c44b5e9f6c9c2df6c28e222d482b7",
  "preflight_head": "57746c958726e7a43ca9f8c32caeb81a1eff09cb"
}
```

## 最初に読むファイル

1. `design/knowledge-discovery/state.json`
2. `design/knowledge-discovery/design.md`（設計書v9。唯一の真実。**今回のスコープは §14 全体と、§3 の v9 追記（`embedding_public`・`source: "mail_seed"`・cards状態機械）**。§10 の検証ゴール12〜17 が完了条件の実体）
3. `design/knowledge-discovery/seed-spec.md`（4ペルソナ。M3シードは依頼者ペルソナに停滞タスクを持たせる）
4. `src/knowledge_discovery/`（M1/M2実装。**既存の Store / matching の純粋関数 / server / schema registry に差し込む形で拡張する。既存ファイルは必要最小限の変更に留める**）

## 実行環境の制約（重要・M1/M2と同じ）

- **シェルコマンドは一切実行できない**。使ってよいのはファイル読み取りと write_file のみ
- テスト実行は外側の controller が行う。静的に正しいと確信できるコードを書き切り、書き終えたら読み直して自己レビューする
- **ネットワークアクセス不可**。Gemini / Firestore への実接続コードは書くが、実行確認は呼び出し側が行う

## 今回のスコープ（M3・A段）

1. **データモデル拡張**（design §14.2, §3）: `tasks` / `schedules` / `mail_seeds` / `cards` の4コレクションを Store インターフェース＋InMemory実装＋Firestore実装に追加。`profiles` に `embedding_public`（public項目のみから生成）を追加し、embedding を生成・再生成する全経路（シード投入・レビュー確定・差分反映）で両方を更新する。`profiles.items.source` の許容値に `"mail_seed"` を追加
2. **秘書モジュール** (`src/knowledge_discovery/secretary.py`):
   - **停滞スコア**（§14.3）: 5シグナル（期日超過日数・無更新日数・リスケ回数・相対停滞・着手なし）の重み付き和。重み・`T1`・`T2`・`CAP`・`NEGLECT_WINDOW` は環境変数（既定値つき定数）。起動時に `T1 < T2` を assert。**LLMは判定に使わない**。evidence_line は発火シグナルからのテンプレート合成（LLM生成禁止）
   - **sweep**（§14.1, §14.2）: 全ownerのタスクを走査し、cards を §14.2 の状態機械表どおりに生成・昇格・更新・resolved 終了する。dismissed / confirmed のタスクには新規カードを作らない。冪等（同一 (owner, task_id) に open カードは高々1枚）
   - **プレビュー検索**（§14.4）: 既存 matching の**純粋関数を直接呼ぶ**（送信層のコードパスを一切通らない）。1段目ランキングは `embedding_public`、2段目推論コンテキストは public 項目のみ。`VECTOR_FLOOR` はプレビューに適用しない。プレビュー0件なら tier は `notice` に留め「候補が見つかりませんでした」を payload に記録
   - **質問下書き**: タスク title/description から Gemini で生成（AI発言として明示するprefix/フラグをpayloadに持たせる）。生成失敗時は「<タスク名>について相談したい」型のテンプレートにフォールバック
   - **日付基準**（§14.7）: 「今日」は環境変数 `DEMO_TODAY`（ISO日付、未設定なら実日付）
3. **依頼確定**（§14.4）: `POST /api/secretary/confirm`。Firestoreトランザクション（InMemoryでは同等のCAS）で `open → confirmed` を排他遷移し、編集済み質問文を**既存の質問投入処理にそのまま渡す**。`linked_query_audit_id` を同一トランザクション文脈で記録。既に confirmed なら既存 audit_id を返すのみ（質問投入しない）。質問投入失敗時は `open` に戻す
4. **モーニングダイジェスト**（§14.2, §14.8）: `GET /api/secretary/digest?employee_id=`。schedules の期日ルール評価（期日超過→当日→翌日）＋ open な cards を動的に組み立てて返す（永続化・既読管理なし）。requester.html 最上部にダイジェストパネルを追加: リマインド行（表示のみ）→停滞カード（evidence_line・🤖質問下書きの編集textarea・候補＋理由・「Request an intro」確定ボタン・固定文言 "Candidates may differ at request time — the full search runs only after you confirm."）→差分提案カード。停滞カードは日常リマインドと同じ見た目の枠（地続き）
5. **プロフィール差分提案**（§14.5）: sweep が `processed==false` の mail_seeds から差分候補を Gemini で抽出（`{item_key, body_draft} | null`、structured output、失敗時は提案なし）→ `profile_diff` カード生成。`POST /api/secretary/profile-diff/{card_id}/review` で4択（反映/編集して反映/非公開にして反映/見送り）。反映時は `source: "mail_seed"`, `reviewed: true`, visibility既定 public で items に追加し、embedding・embedding_public を再生成。カードは `applied`
6. **監査接続**（§14.6）: 新intent `stagnation_detected` / `preview_search` / `profile_diff_proposed` をスキーマレジストリに登録（from=ownerのagent_id、to="system"、配送なし・記録のみ）。**平文表示ホワイトリストには追加しない**（既存fail-closedマスクに落ちることを利用する）。`preview_search` の payload には候補 employee_id 一覧・スコアを記録（表示はマスクされる）
7. **シード拡張** (`scripts/generate_seeds.py`): `--today` 引数（ISO日付）を追加し、相対日付から絶対日付を計算して投入: 依頼者ペルソナに停滞タスク1件（期日超過＋リスケ2回＋他タスクは更新ありの相対停滞＝T2を確実に超える値）、通常タスク2件、schedules（経費締切・週報・会議準備・ジャーナル各1件以上）、mail_seed 1件（プロフィール差分が抽出できる内容）。冪等（決定的ID上書き）
8. **単体テスト追加**: InMemoryStore＋フェイク（Embedder/Inferencer/LLM）でネットワークなしに検証。**最重要**: (a) sweep後に `connect_ask` / `connect_ask_private` が0件（プレビュー無痕跡）、(b) プレビューの cited_item_keys に private が混入しない、(c) 状態機械（notice→request_draft昇格・done→resolved・dismissed非再生成・冪等）、(d) confirm 二重実行で質問投入が1回、(e) digest の期日ルール、(f) 差分反映で embedding_public 再生成、(g) 新intent 3種が監査でマスク表示になる。**既存テストを壊さないこと**

## 書き込み許可範囲

- `src/` `tests/` `scripts/` のみ。`design/` `ledger` `reviews/` `state.json` は読み取り専用
- git操作・ネットワーク・秘密情報へのアクセス禁止。APIキー等の実値をコードに書かない

## 完了条件（これを満たすまで complete と報告しない）

1. `python3 -m unittest discover -s tests` が全件パスする見込み（既存＋M3追加分。実行はcontroller）
2. スコープ1〜8の各ファイル・変更が存在する
3. スコープ8の (a)〜(g) のテストが存在する
4. requester.html のダイジェストパネルに、リマインド行・停滞カード（編集可能な下書き＋確定ボタン＋固定文言）・差分提案カード（4択）が含まれる

## 実装スタイル

- Python 3.11+。**M3で新規の外部依存を追加しない**（M2までの fastapi / uvicorn / google-cloud-firestore / google-genai で完結させる）
- 用語規律: 「承認/approval」不使用（review / consent / visibility / confirm を使う）
- UIテキストは英語（デモ動画・審査員向け）。秘書は無人格のカード表示（人格化しない。spec未決のため差し替え可能な構造に）
