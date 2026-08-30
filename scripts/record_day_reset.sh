#!/usr/bin/env bash
# 収録日リセット（video-script.md「収録前チェックリスト」の実行形）
# 使い方: scripts/record_day_reset.sh [YYYY-MM-DD]   (省略時は今日)
# 内容: ①自律スイープ停止 → ②Firestore を全消去して収録日でreseed → ③DEMO_TODAY を揃える → ④スイープ再開
# 注意: デモデータを完全に消して作り直す。収録・審査中の状態は失われる。
set -euo pipefail

PROJECT_ID="knowledge-discovery-2026"
REGION="asia-northeast1"
SERVICE="knowledge-discovery"
RECORD_DATE="${1:-$(date +%F)}"

echo "== Recording-day reset =="
echo "  project : ${PROJECT_ID}"
echo "  region  : ${REGION}"
echo "  date    : ${RECORD_DATE}  (seeds --today と Cloud Run DEMO_TODAY を一致させる)"
echo
read -r -p "Firestore のデモデータを全消去して reseed します。続行しますか？ [y/N] " ans
[[ "${ans}" == "y" || "${ans}" == "Y" ]] || { echo "中止しました"; exit 1; }

echo "-- 1/4 pause kd-autonomous-sweep (走ると trace が汚れるため)"
gcloud scheduler jobs pause kd-autonomous-sweep --location="${REGION}" --project="${PROJECT_ID}"

echo "-- 2/4 clean reseed (--clear --today ${RECORD_DATE})"
GOOGLE_GENAI_USE_VERTEXAI=true GOOGLE_CLOUD_PROJECT="${PROJECT_ID}" GOOGLE_CLOUD_LOCATION=global PYTHONPATH=src \
  .venv/bin/python scripts/generate_seeds.py --use-firestore --project "${PROJECT_ID}" \
  --embedder gemini --clear --today "${RECORD_DATE}"

echo "-- 3/4 align DEMO_TODAY on Cloud Run"
gcloud run services update "${SERVICE}" --region="${REGION}" --project="${PROJECT_ID}" \
  --update-env-vars "DEMO_TODAY=${RECORD_DATE}"

echo "-- 4/4 resume kd-autonomous-sweep (Scene 3-B の 'jobs run' に ENABLED が必要)"
gcloud scheduler jobs resume kd-autonomous-sweep --location="${REGION}" --project="${PROJECT_ID}"

cat <<NEXT

== 完了。収録前の確認 ==
1. /login でアクセスコードを入力 → /requester: 地図なしの秘書ホーム（view--calm）で、Watching 行だけが出ていること
   （NEED カードが既に出ていたらこのスクリプトを再実行）
2. Scene 3-B の合図はこれ:
   gcloud scheduler jobs run kd-autonomous-sweep --location=${REGION} --project=${PROJECT_ID}
   数秒待ってから '?reveal=1' 付きでリロード
3. 3-D で Ask した後に撮り直す場合は、このスクリプトからやり直す（confirm 済みカードは再現しない）
NEXT
