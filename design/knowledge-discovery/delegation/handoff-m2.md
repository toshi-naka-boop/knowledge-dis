# Handoff: knowledge-discovery マイルストーン2（実サービス接続・シード・簡易UI）

あなたは Antigravity Builder として、controller 検証済みの headless モードで実装を行う。M1（コアバックエンド、`src/knowledge_discovery/`、39テストパス済み）の続き。

## Controller-validated receipt

```json
{
  "validator": {
    "approval_fingerprint": "v7:0f4dde9369f9",
    "baseline": "4f0a856768de17b014af65b2bf3160a2db94156d",
    "design_sha256": "0f4dde9369f9ca748922f1ef093c2c0ab43f5d1cfab6e70f286af244e1478da0",
    "design_version": "v7",
    "phase": "build",
    "status": "valid",
    "validator_sha256": "99bde0cd6961842e664044a80b6a00db4b503f8e68618cdce5e6fbd5fda8ab5a"
  },
  "permission_config_sha256": "b59c4ee001888258f9fdcc152e621e0ea63c44b5e9f6c9c2df6c28e222d482b7",
  "preflight_head": "dc56a8eaea81959ea02ac6f365b822fe7f264351"
}
```

## 最初に読むファイル

1. `design/knowledge-discovery/state.json`
2. `design/knowledge-discovery/design.md`（設計書v7。唯一の真実）
3. `design/knowledge-discovery/seed-spec.md`（シードデータ仕様: 舞台・4ペルソナ・デモ3シーン・合成396名の生成ガイド）
4. `src/knowledge_discovery/`（M1実装。**既存のインターフェース（Store / Embedder / ConnectionInferencer）に差し込む形で拡張する。M1のファイルは必要最小限の変更に留める**）

## 実行環境の制約（重要・M1と同じ）

- **シェルコマンドは一切実行できない**。使ってよいのはファイル読み取りと write_file のみ
- テスト実行は外側の controller が行う。静的に正しいと確信できるコードを書き切り、書き終えたら読み直して自己レビューする
- **ネットワークアクセス不可**。Firestore / Gemini への実接続コードは書くが、実行・疎通確認は呼び出し側が後で行う

## 今回のスコープ（M2）

1. **Firestoreアダプタ** (`src/knowledge_discovery/firestore_store.py`): M1の `Store` インターフェースの Firestore 実装（google-cloud-firestore）。コレクション名・フィールドは design.md §3 に従う。ベクトル検索は「全件取得＋コード側で類似度計算」で実装してよい（400件規模。Firestoreのネイティブベクトル検索は使わない——複合インデックス検証の不確実性を避ける）
2. **Geminiアダプタ** (`src/knowledge_discovery/gemini_adapters.py`):
   - `GeminiEmbedder(Embedder)`: Gemini API の埋め込みモデル（gemini-embedding系）を使用
   - `GeminiConnectionInferencer(ConnectionInferencer)`: Gemini 3.7 Flash（モデルIDは環境変数 `GEMINI_MODEL`、既定 `gemini-3.7-flash`）で design.md §2 の接点推論を実装。出力は必ず `{connection: {reason_text, score} | null, no_connection_reason, cited_item_keys}` のJSON。**プロンプトに「意味のある接点が見つからなければ connection: null を返してよい（無理に理由をひねり出さない）」を必ず含める**。structured output（response_schema）を使いパース失敗時は no_connection 扱いにフォールバック
   - APIキーは環境変数 `GEMINI_API_KEY` から。コードへのハードコード禁止
3. **シード生成スクリプト** (`scripts/generate_seeds.py`): seed-spec.md の4ペルソナ（固定データとしてスクリプト内に記述）＋合成396名（Gemini生成、部門分布・重なり設計は seed-spec 準拠）を Firestore に投入。**冪等**（決定的なemployee_idで上書き）。`--dry-run` で件数とサンプル5件のみ表示。4ペルソナは `agents` レジストリにも登録する
4. **APIサーバ** (`src/knowledge_discovery/server.py`): FastAPI。エンドポイント:
   - `POST /api/query` （質問送信→マッチング実行→ask配送）
   - `GET /api/requester/{requester_id}/status` （依頼者向け射影: design §3。候補ID・consent詳細の非開示ルール厳守）
   - `GET /api/candidate/{agent_id}/asks` / `POST /api/candidate/{agent_id}/consent` （同意/辞退＋理由＋添付）
   - `GET /api/audit/messages` （監査ビュー: fail-closedマスク適用済み display_payload）
   - `GET /attachments/{id}` （doc添付の静的配信。design §3のC-24対応）
   - 全体を単一APIキー（環境変数 `DEMO_API_KEY`、ヘッダ `X-API-Key`）で保護
5. **簡易UI**（静的HTML+素のJS。`src/knowledge_discovery/web/` に配置しFastAPIで配信。**デザインは後で全面作り直す前提の最小実装。フレームワーク・ビルドツール禁止**）:
   - `requester.html`: 質問入力＋候補ステータス3状態（返答待ち/つながりました/今回は難しいそうです＋理由・添付リンク）
   - `candidate.html`: エージェント選択（デモ用ドロップダウン）＋届いた打診（質問＋🤖候補理由。private由来は「🔒あなたの非公開項目に関係」表示）＋同意/辞退フォーム（理由＋添付type/content）
   - `audit.html`: **エージェントグループチャット風の監査ビュー**。全メッセージを時系列のチャット吹き出しで表示（送信者ごとに左右・色分け、エージェント名表示）。`rejected=true` は赤のsystemメッセージ、`connect_ask_private` は「🔒 private-item-based ask (content masked)」吹き出し、`no_connection` は灰色の落選理由吹き出し。画面上部に「400 profiles → funnel 20 → dispatched k」の件数ファネル表示。3秒ポーリングで自動更新
6. **依存関係**: `scripts/requirements.txt` に必要パッケージ（fastapi, uvicorn, google-cloud-firestore, google-genai 等）を列挙（インストールは呼び出し側が行う）
7. **単体テスト追加**: サーバのルーティング・射影ルール（依頼者APIがconsent詳細・候補IDを返さないこと）・添付配信を、InMemoryStore＋フェイクでネットワークなしにテストする（FastAPIのTestClient使用可。fastapi未インストール環境でも他のテストが壊れないよう、fastapi系テストは import 失敗時 skipTest にする）。**M1の既存39テストを壊さないこと**

## 書き込み許可範囲

- `src/` `tests/` `scripts/` のみ。`design/` `ledger` `reviews/` `state.json` は読み取り専用
- git操作・ネットワーク・秘密情報へのアクセス禁止。APIキー等の実値をコードに書かない

## 完了条件（これを満たすまで complete と報告しない）

1. `python3 -m unittest discover -s tests` が全件パスする見込み（M1の39件＋M2追加分。実行はcontroller）
2. スコープ1〜6の各ファイルが存在する
3. 依頼者向けAPIのレスポンスに、レーン完結時の回答者ID以外のemployee_id・consent granted/declined の別が含まれないことのテストが存在する
4. audit.html がチャット風表示・赤表示・🔒マスク表示・ファネル件数表示を含む

## 実装スタイル

- Python 3.11+。M2で追加してよい外部依存は fastapi / uvicorn / google-cloud-firestore / google-genai のみ
- 用語規律: 「承認/approval」不使用（review/consent/visibility を使う）
- UIテキストは英語（デモ動画・審査員向け。seed-spec.md 参照）
