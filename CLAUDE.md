# knowledge-discovery — Project CLAUDE.md
社員の個人AIエージェント群が「社内の誰に聞けばいい？」を人と人の接続（15分MTG提案）として解くマルチエージェントのデモ。All Things Agentic Hackathon（Fortified Enterprise Fleet）提出用、締切 2026-08-31 17:00 PDT。

## Stack & Commands
- Python 3.12 / FastAPI + uvicorn / Pydantic v2 / Firestore(native) / google-genai SDK。パッケージ管理は venv + pip（`scripts/requirements.txt`）。`src/` レイアウトのため実行時は `PYTHONPATH=src:.`
- セットアップ: `python3 -m venv .venv && .venv/bin/pip install -r scripts/requirements.txt`
- ローカル起動: `USE_FIRESTORE=1 GOOGLE_CLOUD_PROJECT=<ID> GOOGLE_GENAI_USE_VERTEXAI=true DEMO_API_KEY=<key> VECTOR_FLOOR=0.62 PYTHONPATH=src:. .venv/bin/uvicorn 'knowledge_discovery.server:create_app_from_env' --factory --port 8080`（UI: `/requester` `/candidate` `/audit`、全アクセスに `api_key` 必須）
- シード投入: `scripts/generate_seeds.py --use-firestore --project <ID> --embedder gemini [--clear --today YYYY-MM-DD]`
- テスト: `.venv/bin/python -m unittest discover -s tests`（97件、ネットワーク・認証不要。ADK未導入時は `test_secretary_agent` 13件 skip）
- B段（秘書 Agent Runtime）は別 venv: `.venv-agent`（`scripts/requirements-agent.txt`、ピン留め）。`.venv-agent/bin/python -m unittest tests.test_secretary_agent` で skip 0 を確認。デプロイは `scripts/deploy_secretary_agent.py`

## AI Vendor / Model 制約
- ハッカソン技術要件により **Gemini + Google Cloud 限定**（Vertex AI 経由）。他ベンダー（Claude/OpenAI 等）のモデルを製品コードに入れない
- 生成: `gemini-3.7-flash`（env `GEMINI_MODEL`、秘書 Runtime は `global` エンドポイント固定）／埋め込み: `gemini-embedding-2`（`GEMINI_EMBEDDING_MODEL`）。`VECTOR_FLOOR=0.62` は gemini-embedding-2 向けの校正値（既定 0.20 はオフラインテスト用）
- B段のみ google-adk 2.7.1 + google-cloud-aiplatform[agent_engines,adk] 1.165.1（バージョン変更は ledger 記録を伴うこと）

## TDD / テスト方針
- 判定: **ハッカソン（PoC）** だが、design-loop の「検証可能なゴール」（design.md §10）をテストで担保する方針のため、ロジック変更時は既存 unittest スイートを維持・追補する（厳密な TDD は必須ではない）
- 根拠: README「All external services sit behind interfaces with in-memory / deterministic fakes; the suite runs fully offline」。Firestore/Gemini を直接叩くテストは書かない

## Deploy / 環境
- Cloud Run `knowledge-discovery`（asia-northeast1、`gcloud run deploy --source .`、Dockerfile は python:3.12-slim）。シークレットは Secret Manager `demo-api-key`
- 秘書 sweep: Cloud Scheduler A段 `kd-secretary-sweep`（08:00 JST、APIキーヘッダ）＋ B段 `kd-secretary-sweep-runtime`（07:55 JST → Vertex AI Agent Engine `reasoningEngines/4310793666370207744`）。Agent Registry に6件手動登録
- 提出後に README「Teardown」手順で Runtime/Scheduler を削除する（常駐コスト停止）
- TODO（ユーザー確認）: GCP プロジェクト ID・サービスURL（README は `<PROJECT_ID>` プレースホルダのまま）

## Project-specific Notes
- 設計書: `design/knowledge-discovery/design.md`（v11 承認済み）、仕様: `spec.md` v7、シード仕様: `seed-spec.md`。design-loop 台帳: `ledger.md`（反証round-11 でB段クローズ）、状態: `state.json`、批評原文: `reviews/`、Antigravity 委譲: `delegation/`
- 設計不変条件: 候補ごとに独立推論（データ境界＝プロセス境界）／非公開項目の内容は本人以外に出さない（マスクは型システムで決定、LLM自己申告に依存しない）／未レビューのAI生成文を本人名義で流通させない／未登録 intent は送信層で拒否。これらを崩す変更は design-loop の承認CPを経ること
- 用語: 「レビュー（検品）」「同意（consent）」「公開範囲（visibility）」を使い、「承認（approval）」は使わない（spec 決定事項15）
- 日付基準は env `DEMO_TODAY`（収録用リセット手順は README）。シード舞台は架空の Meridian Care Partners Group、プロフィール・UI は英語
- 残タスク（ループ外）は `state.json` の `nextAction` を正とする（デモ台本→収録→アーキ図・英語write-up→GitHub公開→Devpost提出、8/29凍結）
