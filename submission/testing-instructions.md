# Devpost テスト手順欄の文面（draft v1）

Devpost の "Testing instructions for judges" 欄に貼る英文。`<DEMO_API_KEY>` は提出直前にローテーションした実キー（＝アクセスコード）に置き換える（この欄以外——公開リポジトリ・writeup——には書かない）。

---

**Live demo (Cloud Run):**
`https://knowledge-discovery-dg6u6zqs7q-an.a.run.app/`

**Access code:** `<DEMO_API_KEY>`

Open the URL, enter the access code once on the sign-in page, and you're in — the session is a secure HttpOnly cookie, so no credential ever rides in a URL.

**Suggested 5-minute tour**

1. `/requester` — Jordan's screen (My Agent). If a "YOUR AGENT NOTICED" card is showing, click **Find someone who can help**, review the drafted question, and **Ask for 15 min**. Click the chart on the right to open the full **Company Atlas** — the dashed line is a route AI found; no bridge exists yet.
2. `/candidate` — the introduction arrives on Marcus's side as a letter. Accept it, share a resource instead, or decline quietly (declining is invisible to the requester).
3. `/audit` — Bridge Trace, the audit trail. Automatic sweeps are recorded as **counts only** before human approval; names appear only after.

**Make it your own:** type your own question into "Ask your agent" at the bottom of `/requester` — the full pipeline (400 profiles → similarity floor → isolated per-candidate evaluations → asks) runs live on whatever you ask.

**Shared world:** all judges share one demo tenant, and an autonomous sweep also runs unattended every 30 minutes (Cloud Scheduler + OIDC — shown in the demo video). If the featured introduction has already been accepted by another judge, the story continues in `/audit` — or press **Run sweep** / use a demo preset to start a new one. All data is synthetic (a fictional company, 401 employees); you can't break anything.

---

## 参考訳（日本語）

**ライブデモ（Cloud Run）**: URL を開き、サインインページでアクセスコードを1回入力。以降は HttpOnly Cookie のセッションで、URL に認証情報は載らない。

**5分ツアー**: ① `/requester`（Jordan の画面）で「YOUR AGENT NOTICED」カード → Find someone → ドラフト確認 → Ask for 15 min。右の海図をクリックするとフルアトラス。② `/candidate` で Marcus 側に紹介状が届く。承諾・資料共有・静かな辞退（依頼者には見えない）。③ `/audit`（Bridge Trace）で監査証跡——承認前は件数のみ、承認後に実名。

**自分の質問で試す**: `/requester` 下部の Ask your agent に自由に質問を打てば、400人へのファネルが本番の経路で走る。

**共有ワールド**: 審査員全員が同じテナントを共有し、30分ごとの自律スイープも動き続けている。目玉の紹介が既に成立していたら /audit で続きを、または Run sweep／デモプリセットで新しい依頼を。データはすべて合成。壊れるものはない。
