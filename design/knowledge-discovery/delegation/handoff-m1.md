# Handoff: knowledge-discovery マイルストーン1（コアバックエンド）

あなたは Antigravity Builder として、controller 検証済みの headless モードで実装を行う。

## Controller-validated receipt

以下は外側 controller が validator を実行して得た receipt である（controller-validated headless mode の契約に基づく）:

```json
{
  "validator": {
    "approval_fingerprint": "v7:0f4dde9369f9",
    "baseline": "4f0a856768de17b014af65b2bf3160a2db94156d",
    "baseline_commit": "4f0a856768de17b014af65b2bf3160a2db94156d",
    "design_sha256": "0f4dde9369f9ca748922f1ef093c2c0ab43f5d1cfab6e70f286af244e1478da0",
    "design_version": "v7",
    "phase": "build",
    "status": "valid",
    "validator_sha256": "99bde0cd6961842e664044a80b6a00db4b503f8e68618cdce5e6fbd5fda8ab5a"
  },
  "permission_config_sha256": "b59c4ee001888258f9fdcc152e621e0ea63c44b5e9f6c9c2df6c28e222d482b7",
  "preflight_head": "c06253acc659ca573d52eaeba79271ede306ff24"
}
```

## 最初に読むファイル

1. `design/knowledge-discovery/state.json`（契約通り最初に読む）
2. `design/knowledge-discovery/design.md`（設計書 v7。唯一の真実。全実装はこれに従う）

## 今回のスコープ（M1: コアバックエンド。Python）

design.md の以下を、**外部サービスに接続せずテスト可能な形**で実装する:

1. **データモデル**（design.md §3）: `agents` / `profiles` / `messages` の型定義（dataclass等）
2. **ストレージ抽象**: Firestore を直接呼ばず `Store` インターフェース＋インメモリ実装（`InMemoryStore`）を作る。Firestore 実装は M2 で追加するため、今回はインターフェースとインメモリのみ
3. **スキーマレジストリと送信層**（§3）: payload_type ごとの検証、未登録型の `reject_unregistered_type` 拒否、宛先 `supported_intents` 検証による `reject_unsupported_intent` 拒否、全メッセージの監査記録
4. **privateマスク（§3、メッセージ横断の1ルール）**: `cited_item_keys` に `visibility=="private"` を含む全メッセージへの `audit_payload` 生成、`connect_ask`→`connect_ask_private` のintent確定、監査表示のfail-closedホワイトリスト
5. **探索的マッチング2段階**（§2）: LLM を直接呼ばず `ConnectionInferencer` インターフェース＋テスト用フェイク実装を作る。ベクトル類似度も `Embedder` インターフェース＋決定的フェイク（例: 単語重複率）で抽象化。`VECTOR_FLOOR` / `CONNECTION_THRESHOLD` によるOR落選判定、`no_connection` の記録、候補エージェントごとの独立推論の構造（各推論呼び出しに渡るのは質問文と当人のプロフィールのみであることをコード構造で保証）
6. **つながりレーン**（§4-§6）: connect_ask 配送 → consent_reply(granted/declined) → match_proposal（reason_text 非含有、双方宛）／ decline_with_reason（reason + attachment: link/text/doc）。複数同意は全員成立
7. **単体テスト**: design.md §10 のゴール 1, 2, 4, 4b, 5, 6, 7, 8 に対応するテストを `tests/` に書く。**テストはネットワーク・GCP認証・APIキーなしで完走すること**（全てフェイク実装を使う）

## 書き込み許可範囲

- `src/` `tests/` `scripts/` のみ
- `design/` `ledger` `reviews/` `state.json` は**読み取り専用**。変更禁止
- git commit / push / ネットワークアクセス / 秘密情報へのアクセスは禁止

## 完了条件（これを満たすまで complete と報告しない）

1. `python3 -m unittest discover -s tests` が全件パスする（自分で実行して確認すること）
2. 上記スコープ1〜6の各コンポーネントが `src/` に存在する
3. private項目を引用したメッセージ（connect_ask_private / no_connection とも）の監査表示がマスクされることを検証するテストが存在しパスする
4. 未登録payload型・unsupported intent の拒否が監査記録に `rejected=true` で残ることを検証するテストが存在しパスする

## 実装スタイル

- Python 3.11+、標準ライブラリ優先（外部依存を増やさない。M1では google-cloud 系も入れない）
- 過剰な抽象化をしない。M2でFirestore/Gemini実装を差し込むための最小限のインターフェースのみ
- 用語規律: 「承認/approval」という語を識別子・コメント・文字列に使わない。「review/reviewed」「consent」「visibility」を使う（design.md 用語統一）
