#!/usr/bin/env bash
# demo-api-key のローテーション（収録後・提出前に実行）
# やること: ①新キー生成→Secret Managerへ ②Cloud Run 新リビジョンで反映
#           ③kd-secretary-sweep のAPIキーヘッダ更新 ④新旧キーの死活確認 ⑤新キーをクリップボードへ
# 新キーは画面に表示しない。Devpost のテスト手順欄にだけ貼ること。
set -euo pipefail

PROJECT_ID="knowledge-discovery-2026"
REGION="asia-northeast1"
SERVICE="knowledge-discovery"
BASE_URL="https://knowledge-discovery-dg6u6zqs7q-an.a.run.app"

read -r -p "demo-api-key をローテーションします。動画の収録は済んでいますか？ [y/N] " ans
[[ "${ans}" == "y" || "${ans}" == "Y" ]] || { echo "中止しました（収録前のローテは、新キーが動画に映るので無意味になります）"; exit 1; }

OLD_KEY="$(gcloud secrets versions access latest --secret=demo-api-key --project="${PROJECT_ID}")"
NEW_KEY="$(openssl rand -hex 24)"

echo "-- 1/5 Secret Manager に新バージョンを追加"
printf '%s' "${NEW_KEY}" | gcloud secrets versions add demo-api-key --data-file=- --project="${PROJECT_ID}" >/dev/null

echo "-- 2/5 Cloud Run 新リビジョンで latest を反映"
gcloud run services update "${SERVICE}" --region="${REGION}" --project="${PROJECT_ID}" \
  --update-labels "key-rotated=$(date +%Y%m%d%H%M)" >/dev/null

echo "-- 3/5 kd-secretary-sweep のAPIキーヘッダを更新（このジョブだけキー認証のため）"
gcloud scheduler jobs update http kd-secretary-sweep --location="${REGION}" --project="${PROJECT_ID}" \
  --update-headers "X-API-Key=${NEW_KEY}" >/dev/null

echo "-- 4/5 死活確認（旧キー→401 / 新キー→200 が正解）"
old_code=$(curl -s -o /dev/null -w "%{http_code}" --max-time 20 "${BASE_URL}/api/agents" -H "X-API-Key: ${OLD_KEY}")
new_code=$(curl -s -o /dev/null -w "%{http_code}" --max-time 20 "${BASE_URL}/api/agents" -H "X-API-Key: ${NEW_KEY}")
echo "   old key -> ${old_code} / new key -> ${new_code}"
if [[ "${old_code}" != "401" || "${new_code}" != "200" ]]; then
  echo "   ⚠️ 期待値と違います。旧キーが200のままなら新リビジョンが未反映（数十秒待って再確認）。"
fi

echo "-- 5/5 新キーをクリップボードへ（Devpost のテスト手順欄 <DEMO_API_KEY> に貼る）"
printf '%s' "${NEW_KEY}" | pbcopy
echo "完了。キー入りURL例: ${BASE_URL}/requester?api_key=<クリップボードの値>"
