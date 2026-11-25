# Devpost テスト手順欄の文面（draft v1）

Devpost の "Testing instructions for judges" 欄に貼る英文。`<DEMO_API_KEY>` は提出直前にローテーションした実キー（＝アクセスコード）に置き換える（この欄以外——公開リポジトリ・writeup——には書かない）。

---

**Live demo (Cloud Run):**
`https://knowledge-discovery-dg6u6zqs7q-an.a.run.app/`

**Access code:** `<DEMO_API_KEY>`

Open the URL, enter the access code once on the sign-in page, and you're in — the session is a secure HttpOnly cookie, so no credential ever rides in a URL.

**Suggested 5-minute tour**

1. `/requester` — Jordan's screen (My Agent). If a "YOUR AGENT NOTICED" card is showing, click **Find someone who can help**, review the drafted question, and **Ask for 15 min**. Click the chart on the right to open the full **Company Atlas** — the dashed line is a route AI found; no bridge exists yet.
2. `/candidate` — the introduction arrives on Marcus's side as a letter. Accept it, share a resource instead, or decline (the requester then sees your name and the reason you give — declines are never hidden).
3. `/audit` — Bridge Trace, the audit trail. Automatic sweeps are recorded as **counts only**; names appear only once a person has confirmed a request.

**Make it your own:** type your own question into "Ask your agent" at the top of `/requester` (write it in English — the profiles are English). Your question is compared with all 400 profiles for the similarity list on `/audit`, but only the 4 registered agents (Marcus, Rachel, Elena, Tom) can actually be asked: each of them goes through the similarity floor and one isolated model evaluation, and up to 3 are asked. Someone you have already asked (pending or accepted) is not asked again.

**Shared world:** all judges share one demo tenant, and an autonomous sweep also runs unattended every 30 minutes (Cloud Scheduler + OIDC — shown in the demo video). If the featured introduction has already been accepted by another judge, the story continues in `/audit` — or press **Run sweep** / use a demo preset to start a new one. All data is synthetic (a fictional company with 400 synthetic employee profiles), so nothing real is at stake. But the tenant is shared: another judge's actions (dismissing the featured card, declining a request) change what you see, and the audit page shows every name and decline reason in the tenant to anyone who has the access code. Questions are limited to 2000 characters.

---

## 参考訳（日本語）

**ライブデモ（Cloud Run）**: URL を開き、サインインページでアクセスコードを1回入力。以降は HttpOnly Cookie のセッションで、URL に認証情報は載らない。

**5分ツアー**: ① `/requester`（Jordan の画面）で「YOUR AGENT NOTICED」カード → Find someone → ドラフト確認 → Ask for 15 min。右の海図をクリックするとフルアトラス。② `/candidate` で Marcus 側に紹介状が届く。承諾・資料共有・辞退（辞退すると、依頼者に名前と理由が見える。隠れない）。③ `/audit`（Bridge Trace）で監査証跡——自動スイープは件数のみ、人が確認して依頼を送った後に実名。

**自分の質問で試す**: `/requester` 上部の Ask your agent に英語で質問を打つ。質問は400件のプロフィールとの類似度一覧（/audit に表示）に使われるが、実際に依頼できるのは登録済みの4人（Marcus・Rachel・Elena・Tom）だけで、その4人が類似度フロアと1人ずつの評価を通り、最大3人に依頼が届く。すでに依頼中・承諾済みの相手には重ねて依頼されない。

**共有ワールド**: 審査員全員が同じテナントを共有し、30分ごとの自律スイープも動き続けている。目玉の紹介が既に成立していたら /audit で続きを、または Run sweep／デモプリセットで新しい依頼を。データはすべて合成。ただしテナントは共有なので、他の審査員の操作で見える内容が変わり、/audit にはアクセスコードを持つ全員に名前と辞退理由が見える。
