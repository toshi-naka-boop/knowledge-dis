# デモビデオ台本 — Knowledge Discovery（All Things Agentic Hackathon）

尺: 約4分。Devpost 要件 = ①課題の概要 ②価値提案 ③アプリの動作デモ ④バックエンドが Google Cloud 上で稼働している証明。
ナレーション: 英語（作者のクローン音声 / narration-tts）。画面は英語 UI。
数字の裏付けは `submission/problem-evidence.md`（全件一次資料で検証済み）。
改訂 2026-08-30 (v2): Scene 3 を Company Atlas UI（羊皮紙の海図）に合わせて全面改訂（3-B′ 新設・autonomy 常時表示・レター形式・成立はフルアトラス）。Scene 1/2/4 は変更なし、Scene 5 は背景色のみ更新。

## 尺の配分

| # | セクション | 時間 | 累計 |
|---|---|---|---|
| 1 | 課題（Problem） | 0:00–0:48 | 0:48 |
| 2 | 価値提案（Value proposition） | 0:48–1:15 | 1:15 |
| 3 | デモ（Product in action） | 1:15–3:25 | 3:25 |
| 4 | Google Cloud で動いている証明 | 3:25–3:50 | 3:50 |
| 5 | クロージング | 3:50–4:00 | 4:00 |

---

# Scene 1 — 課題（0:00–0:48）★本ドキュメントで確定させる部分

## 1-A（0:00–0:12）前提: 企業は「繋がり」のために出社させた

**画面**: 実写ではなくタイポグラフィ。中央に大きく `92%`。下に小さく出典。
**画面テキスト**:
```
92% of employers rank in-person collaboration
among the top benefits of office attendance
                          WeWork, 2023 · 110 companies · 200,000+ employees
```

**ナレーション（EN）**:
> Companies brought people back to the office for one reason above all others.
> Ninety-two percent of employers say in-person collaboration is a top benefit of being there.

**日本語（参考訳）**:
> 企業が従業員をオフィスに戻した理由は、何よりもまずこれだった。
> 92%の企業が「対面での協働」を出社のメリット上位に挙げている。

---

## 1-B（0:12–0:28）否定: しかし「同じ建物にいること」は繋がりではない

**画面**: 同心円のアニメーション。中心に人物アイコン、半径150mの円が描かれ、その外側に多数の人物アイコンがグレーで並ぶ。円の外は接続線が生えない。
**画面テキスト**:
```
New working relationships formed — but only within 150 meters.
Beyond that: no measurable effect.
        Carmody et al., Nature Computational Science, 2022
        MIT · 2,834 researchers · 18 months of daily network data
```

**ナレーション（EN）**:
> But being in the building is not the same as being connected.
> Researchers at MIT tracked how new working relationships actually form.
> Proximity mattered — but only within about a hundred and fifty meters. Past that, the effect disappeared.
> In a company of thousands, the person who has your answer is almost always past a hundred and fifty meters.

**日本語（参考訳）**:
> しかし「同じ建物にいること」は「繋がっていること」ではない。
> MIT の研究者が、新しい仕事上の関係が実際にどう生まれるかを追跡した。
> 近接は効いていた——ただし約150メートル以内だけ。それを超えると効果は消えた。
> 数千人規模の企業では、あなたの答えを持っている人はほぼ確実に150メートルの外側にいる。

> **注**: 「150メートル」は MIT のキャンパス（faculty・postdoc 2,834名）で観測された値。画面に MIT と明示して、企業データの一般化ではないことを誠実に示す。

---

## 1-C（0:28–0:48）コスト: だから人は「探し回る」ことに時間を溶かす

**画面**: 週のカレンダーグリッド。1マスずつ塗りつぶされ、最後に `1h 42m / week` が残る。
**画面テキスト**:
```
1 hour 42 minutes every week
— just finding the right person to ask.
        APQC, 2021 · 982 knowledge workers
        (searching for information is counted separately)

28% say: "the organization is too large to know who has the answer"
        Forrester Consulting, 2022 · organizations of 3,000+
```

**ナレーション（EN）**:
> So we do the next best thing. We ask around.
> Knowledge workers spend an hour and forty-two minutes every week just trying to find the right person to ask.
> Not reading the answer. Just finding who has it.
> Twenty-eight percent say their organization is simply too large to know who holds the answer.

**日本語（参考訳）**:
> だから我々は次善の策をとる——聞いて回るのだ。
> 知識労働者は毎週1時間42分を、ただ「聞くべき適切な相手を探すこと」だけに費やしている。
> 答えを読む時間ではない。誰が持っているかを探す時間だ。
> 28%は「組織が大きすぎて、そもそも誰が答えを持っているか分からない」と答えている。

---

# Scene 2 — 価値提案（0:48–1:15）

## 2-A（0:48–1:02）転回: 近接の正体は「探索コスト」だった

**画面**: 150mの円が消え、代わりに組織全体のノードが薄く広がる。中心の人物から細い線が伸びて1人に届く。
**画面テキスト**:
```
Proximity was never magic. It was low search cost.

Labs placed side by side collaborated 3.5x more —
but the effect came from the pairs who were hardest to find.
        Catalini, Management Science, 2018
```

**ナレーション（EN）**:
> Proximity was never magic. It was low search cost.
> And search cost can be lowered another way.

**日本語（参考訳）**:
> 近接は魔法ではなかった。探索コストの低さだった。
> そして探索コストは、別の手段でも下げられる。

> **圧縮済み（承認 2026-08-29）**: Catalini の3文を2文に圧縮。ラボ隣接3.5倍の詳細は画面テキストに残し、音声からは落とす。

---

## 2-B（1:02–1:15）解: だから、人ではなく人を探す

**画面**: プロダクト名 + タグライン。My Agent 画面へフェード。
**画面テキスト**:
```
Knowledge Discovery
Every employee gets a personal AI agent.
It doesn't answer for you — it finds the person who can.

AI shouldn't replace human connections. It should create them.
```

**ナレーション（EN）**:
> Knowledge Discovery gives every employee a personal AI agent.
> The agent does not answer the question for you. It finds the colleague who can — and asks their agent for fifteen minutes of their time.
> AI shouldn't replace human connections. It should create them.

**日本語（参考訳）**:
> Knowledge Discovery は、すべての従業員に個人の AI エージェントを与える。
> エージェントはあなたの代わりに答えない。答えられる同僚を見つけ、その人のエージェントに「15分」を打診する。
> AI は人と人との繋がりを置き換えるべきではない。繋がりを生み出すべきだ。

---

# Scene 3 — デモ（1:15–3:25）

**改訂 2026-08-30 (v2)**: Company Atlas UI（羊皮紙の海図・状態別レイアウト）対応。旧版からの主な変更＝①朝は地図なしの秘書全面 ②検知で秘書/海図が50/50に ③新カット 3-B′（フルアトラス＝署名画面）を追加 ④autonomy は常時表示カードに（`?autonomy=1` 廃止）⑤受け手側はレター形式 ⑥成立は「実線の橋」のフルアトラス。

**通しで1本撮りする。** 画面は 1920×1080 推奨（アトラスの原寸）、ブラウザは chrome をフルスクリーン（アドレスバーは Scene 4 で使うので Scene 3 では隠さない）。
収録前の状態づくりは末尾「収録前チェックリスト」を参照。

## 3-A（1:15–1:31 / 16秒）Jordan の朝 — 秘書としての日常

**操作**: `/requester?api_key=...` を開いた状態から開始。スクロールなし。**地図は画面に存在しない**（静けさの演出）。
**画面で見せるもの**: 中央カラムに `Good morning, Jordan` / バッジ行 `● Your agent · monitoring automatically · last sweep 12 min ago` / Watching 1行カード `Allied Health Clinician Credentialing Verification — No updates for 2 days · Monitoring` / TODAY の4行（メール由来の `YOUR AGENT SUGGESTS` カードが出ていればそのまま映してよい。操作はしない）。
**カメラ**: バッジ行に 1秒ズーム。次に Watching 行に 1.5秒。

**ナレーション（EN）**:
> This is Jordan's morning. Deadlines, reminders — and one line that matters: her agent is monitoring automatically.
> It has noticed a task that hasn't moved in two days. It is just watching.

**日本語（参考訳）**:
> これが Jordan の朝だ。締め切り、リマインダー、そして重要な一行——エージェントが自動で監視している。
> 2日間動いていないタスクに気づいている。ただ見ているだけだ。

> **設計上の主張**: 平常時のホームは純粋な秘書画面で、繋がりの UI は一切出ない。この「静けさ」が後の検知と海図の出現を際立たせる。

---

## 3-B（1:31–1:51 / 20秒）自律検知 — クリックなしで Need と海図が現れる

**操作**:
1. ターミナル（またはCloud Schedulerコンソール）に切り替え、`gcloud scheduler jobs run kd-autonomous-sweep --location=asia-northeast1` を実行。**この画面は2〜3秒だけ映す**（Scene 4 の GCP 証明の伏線になる）。
2. ブラウザに戻り、URL を `/requester?api_key=...&reveal=1` にして**リロードするだけ**。マウスでカードを操作しない。
3. 画面が変形する: 秘書レールが左半分に、**右半分に「自分のコーナーの海図」がスライドイン**。破線のスイープ航路がチャート外へ伸びる。

**画面で見せるもの**: 左＝`INTRODUCTION PREPARED — NOT SENT` / `Marcus Delgado` / `REAL ESTATE` / 斜体の根拠文＋脚注 `²` / `QUESTION DRAFT — EDIT BEFORE ASKING` / `[Ask Marcus for 15 min]`。右＝海図（Jordan のドット・信号の波紋・破線航路）と下部チップ `● Introduction prepared — see the route on the atlas ›`。
**カメラ**: カード全体 → 根拠文に 2秒。

**ナレーション（EN）**:
> Thirty minutes later, on a schedule, the agent runs on its own. No one clicked anything.
> It searched four hundred profiles across the company, evaluated the candidates, and prepared a request.
> Marcus Delgado, from commercial real estate. Jordan has never worked with him.

**日本語（参考訳）**:
> 30分後、スケジュールに従ってエージェントが自律的に動く。誰もクリックしていない。
> 社内400名のプロフィールを探索し、候補を評価し、依頼を準備した。
> Marcus Delgado——商業用不動産の担当。Jordan は彼と一緒に働いたことがない。

> **ここが「no-click」の核。** リロード以外の操作を映さないこと。マウスカーソルはカードから離しておく。

---

## 3-B′（1:51–2:05 / 14秒）★新カット: アトラス — 組織は海図、繋がりは橋

**操作**: 右半分の海図を**どこでもよいのでクリック**（下部チップが誘導）。フルアトラス（Bridge Trace）へ遷移。操作はそこで止め、画面を泳がせない。
**画面で見せるもの**: 羊皮紙の海図全面。5つの島（STAFFING / REAL ESTATE / CORPORATE / ADVISORY / EXECUTIVE）/ Jordan と Marcus だけがセリフの実名、他の415人は匿名のインクドット / **破線の proposed crossing** と斜体キャプション `proposed crossing — no bridge exists between these islands` / released 候補（Elena Vasquez・Tom Whitfield が 60% 透過）/ 右上に Introduction card / 下部 TRACE ドロワー。
**カメラ**: 全景 2秒 → 破線とキャプションへゆっくりズーム 3秒。

**ナレーション（EN）**:
> Her agent drew a map. Departments are islands. People are ink.
> And between two islands, a dashed line — a route that exists, but a bridge that doesn't. Nothing has been sent.

**日本語（参考訳）**:
> エージェントは海図を描いた。部署は島。人はインク。
> そして2つの島の間に破線——航路は見つかったが、橋はまだ架かっていない。何も送信されていない。

> **これが Devpost のスクリーンショットと同じ画面**（署名画面）。「Humans are large, agents are small」——人名だけがセリフ体で大きく、エージェントの活動は細い破線と脚注番号でしか現れないことがひと目で伝わる構図。

---

## 3-C（2:05–2:21 / 16秒）境界 — どこまでを任せ、どこから人間か

**操作**: 下部ドロワーの `‹ my agent` で秘書画面へ戻り、左レールの **AUTONOMY POLICY カード**（常時表示・クリック不要）を映す。3秒静止。
**画面で見せるもの**:
```
AUTONOMY POLICY
[x] Monitor my work automatically
[x] Search the organization automatically
[x] Ask candidate agents automatically
[x] Prepare an introduction
▨ Contacting a person — always ask me first   ← 緑のハッチ枠（Human Boundary）
```
トップバー右の常設チップ `▨ Contacting people always asks you first` も同一フレームに入る。
**カメラ**: ハッチ枠の行に 2秒ズーム。

**ナレーション（EN）**:
> Jordan decides how far her agent may go on its own. Monitor. Search. Ask other agents. Prepare a draft.
> And one line that cannot be switched off: contacting a person always asks her first.
> Observe, detect, explore, prepare — that is the agent's half. Reaching another human is hers.

**日本語（参考訳）**:
> Jordan は、自分のエージェントがどこまで自律的に動いてよいかを決める。監視する。探索する。他のエージェントに尋ねる。下書きを準備する。
> そして、オフにできない一行——人に連絡することは、必ず本人に先に確認する。
> 観察し、検知し、探索し、準備する。ここまでがエージェントの領分。人間に到達することは、Jordan の領分だ。

> **審査への訴求点**（Fortified Enterprise Fleet）: 自律性は「制御可能な段階」として実装され、最終段は**構造的に**人間側にある。サーバ側で強制しており、UI の飾りではない。ハッチ模様（Human Boundary）は全画面で同じ意匠。

---

## 3-D（2:21–2:35 / 14秒）人間の承認 — Ask Marcus for 15 min

**操作**: レール上の候補カード（または ATLAS に戻って Introduction card）の `QUESTION DRAFT` テキストエリアを1度クリックし、**末尾に数語だけタイプして編集できることを見せる**（例: ` Any leads welcome.`）。その後 `[Ask Marcus for 15 min]` をクリック。
**画面**: アトラス上では破線が**進行するアニメーション**（asked 状態）に変わり、カードは `INTRODUCTION SENT — Waiting quietly` へ。レール側では `Request sent to Marcus Delgado / Their agent is reviewing it.`。
**カメラ**: クリック直後の破線アニメーションに 2秒（アトラスで撮る場合）。

**ナレーション（EN）**:
> The draft is hers to edit. Nothing was sent while she was away.
> She reads it, changes a line, and asks for fifteen minutes.

**日本語（参考訳）**:
> 下書きは彼女のものだ。彼女がいない間に送信されたものは何もない。
> 目を通し、一行を直し、15分を打診する。

---

## 3-E（2:35–2:57 / 22秒）受け手側 — 紹介状。何が共有され、何が守られたか

**操作**: `/candidate?api_key=...` へ（既定で Marcus のインボックス）。上部に海図ストリップ、中央にレター。ゆっくり1画面ぶんだけスクロール。
**画面で見せるもの**:
- 海図ストリップ: `SOMEONE FOUND A ROUTE TO YOUR ISLAND` / 右端の自分の海岸に `You`（赤いアウトラインの点）/ 西から届く破線
- レター: `AN INTRODUCTION · PREPARED BY JORDAN LEE'S AGENT` / `Jordan Lee needs your perspective.` / 斜体の依頼文 / `JORDAN LEE · 15 MINUTES · THIS WEEK` / `[Accept the introduction]` `[Decline quietly]` / `Declining is invisible to Jordan.`
- 脚注（罫線の下）: private 項目は内容非開示のまま適合判定された旨 / `Shared between agents: the question and this note. Kept private: your profile's private items, calendars, and messages.`
**カメラ**: 脚注ブロックに 3秒静止。その後 `Accept the introduction` をクリック。

**ナレーション（EN）**:
> Marcus doesn't get a ticket. He gets a letter. His own agent screened it first.
> He can see exactly what crossed between the two agents — the question, and a short note on why him. His calendar, his messages, his private items did not.
> Declining would be invisible. He accepts.

**日本語（参考訳）**:
> Marcus に届くのはチケットではなく、一通の紹介状だ。彼自身のエージェントが先に選別している。
> 2つのエージェントの間で何が行き交ったかが正確に見える——質問と、なぜ彼なのかという短いメモ。カレンダーもメッセージも非公開項目も渡っていない。
> 断っても Jordan には見えない。彼は承諾する。

---

## 3-F（2:57–3:15 / 18秒）監査 — 承認の前と後で見え方が変わる

**操作**: `Bridge Trace`（/audit）へ。上部の `Last sweep: Automatic · HH:MM` と counts 行 → タイムライン → MATCH FOUND の順にゆっくりスクロール。（この画面は旧デザインのまま。技術的・分析的な画面として意匠を分けている）
**画面で見せるもの**:
- ヘッダ: `Last sweep: Automatic · 10:00`
- counts 行: `Automatic sweep — 400 profiles explored · 1 need prepared, awaiting the owner's review`
- タイムライン: `Need received — your agent starts searching` → `Request sent to Marcus Delgado` → `Marcus Delgado accepted` → `Connection made`
- 下部: `MATCH FOUND — Marcus Delgado / Connected with Jordan Lee · 15 min`
**カメラ**: counts-only 行と named タイムラインが同一画面に収まる位置でスクロールを止める。

**ナレーション（EN）**:
> Every run is auditable. Before Jordan approved, the automatic sweep is recorded as counts only — how many profiles, how many needs prepared. No names.
> Names appear after a human said yes. Private recommendation, human approval, then an auditable, named interaction.

**日本語（参考訳）**:
> すべての実行が監査可能だ。Jordan が承認する前、自動実行は件数だけで記録される——何件のプロフィールを見て、何件の依頼を準備したか。名前は出ない。
> 名前が現れるのは、人間が「はい」と言った後だ。非公開の推薦、人間の承認、そして監査可能な実名のやり取り。

> **この18秒が本プロダクトの統治構造そのもの。**

---

## 3-G（3:15–3:25 / 10秒）成立 — 橋だけが残る

**操作**: `/requester?api_key=...&view=atlas` を開く。ロード直後に**探索の痕跡（波紋・航路・匿名ドット・released 候補）が約2秒でフェードアウトする遷移**が自動再生される。操作しない。
**画面で見せるもの**: 静かになった海図に2つの島だけ / **実線の橋** / 斜体 `a path that didn't exist yesterday` / 傾いたスタンプ `INTRODUCED · HH:MM` / 上部 `the search is over — 399 people were never disturbed` / 下部ドロワーは `LEDGER` に切替。
**カメラ**: フェード遷移をそのまま見せ、最後にスタンプへ寄る。

**ナレーション（EN）**:
> The dashes become a bridge. Three hundred ninety-nine people were never disturbed.
> Two people who had never worked together now have fifteen minutes on the calendar. That is the whole product.

**日本語（参考訳）**:
> 破線が橋になる。399人は一度も煩わされなかった。
> 一度も一緒に働いたことのない2人に、15分の予定ができた。それがこのプロダクトのすべてだ。

---

# 収録前チェックリスト（Scene 3）

```bash
# 1) スケジューラを止めてからシードを入れ替える（走ると trace が汚れる）
gcloud scheduler jobs pause kd-autonomous-sweep --location=asia-northeast1

# 2) クリーン reseed（収録日に合わせる）
GOOGLE_GENAI_USE_VERTEXAI=true GOOGLE_CLOUD_PROJECT=<PROJECT_ID> GOOGLE_CLOUD_LOCATION=global PYTHONPATH=src \
  .venv/bin/python scripts/generate_seeds.py --use-firestore --project <PROJECT_ID> \
  --embedder gemini --clear --today YYYY-MM-DD

# 3) 本番の日付を揃える
gcloud run services update knowledge-discovery --region=asia-northeast1 --update-env-vars DEMO_TODAY=YYYY-MM-DD

# 4) スケジューラを戻す（3-B で jobs run するため ENABLED が必要）
gcloud scheduler jobs resume kd-autonomous-sweep --location=asia-northeast1
```

- **3-A の前**: `/requester?api_key=...` を開き、**地図が出ていない**こと（view--calm）と Watching 行を確認。NEED カードが既に出ていたら reseed からやり直す。
- **3-B**: `jobs run` の直後は数秒待ってから **`&reveal=1` を付けて**リロード。レイアウトが 50/50 に変わり、候補が展開済みで表示される（クリック不要）。`last sweep just now` になる。
- **3-B′**: 海図はどこをクリックしても遷移する。下部チップにカーソルを寄せてからクリックすると意図が伝わりやすい。
- **3-C**: `?autonomy=1` は**廃止（無害な no-op）**。AUTONOMY POLICY カードは常時表示なので操作不要。
- **3-D**: アトラス側の Introduction card で撮ると破線アニメーションが同一フレームに入る。レール側カードで撮る場合は送信後にステータスカードへ。
- **3-E**: `/candidate` は既定で Marcus のインボックス。ペルソナ切替の操作は映さなくてよい。
- **3-G**: `&view=atlas` 付きで開き直すとフェード遷移がロード時に再生される。1回で決まらなければリロードで何度でも再生可能（サーバ状態は変わらない）。
- **失敗時のやり直し**: 3-D で Ask した後に撮り直す場合は、reseed からやり直す（confirm 済みカードは terminal 状態のため再現しない）。

---

# Scene 3 ナレーション canonical（TTS 用）

```
This is Jordan's morning. Deadlines, reminders, and one line that matters: her agent is monitoring automatically. It has noticed a task that hasn't moved in two days. It is just watching.

Thirty minutes later, on a schedule, the agent runs on its own. No one clicked anything. It searched four hundred profiles across the company, evaluated the candidates, and prepared a request. Marcus Delgado, from commercial real estate. Jordan has never worked with him.

Her agent drew a map. Departments are islands. People are ink. And between two islands, a dashed line: a route that exists, but a bridge that doesn't. Nothing has been sent.

Jordan decides how far her agent may go on its own. Monitor. Search. Ask other agents. Prepare a draft. And one line that cannot be switched off: contacting a person always asks her first. Observe, detect, explore, prepare, that is the agent's half. Reaching another human is hers.

The draft is hers to edit. Nothing was sent while she was away. She reads it, changes a line, and asks for fifteen minutes.

Marcus doesn't get a ticket. He gets a letter. His own agent screened it first. He can see exactly what crossed between the two agents: the question, and a short note on why him. His calendar, his messages, his private items did not. Declining would be invisible. He accepts.

Every run is auditable. Before Jordan approved, the automatic sweep is recorded as counts only: how many profiles, how many needs prepared. No names. Names appear after a human said yes. Private recommendation, human approval, then an auditable, named interaction.

The dashes become a bridge. Three hundred ninety-nine people were never disturbed. Two people who had never worked together now have fifteen minutes on the calendar. That is the whole product.
```

語数 約305語 / 想定 約114秒（160wpm）。枠 130秒に対し**約16秒の余白**＝操作の間・海図のズーム・フェード遷移に充てる。ナレーションと操作は同期させず、操作を先行させて音を被せる。
さらに詰める場合の調整代: 3-A「It is just watching.」/ 3-B′「Nothing has been sent.」（画面キャプションと重複）/ 3-F 最終文。


# Scene 4 — Google Cloud 稼働の証明（3:25–3:50）

Devpost の必須要件。**4カット × 各6秒前後**。すべて実物のコンソール画面（モックアップ不可）。
アドレスバーは常に映す（`console.cloud.google.com` と `.run.app` が写っていること自体が証拠）。

## 4-A（3:25–3:31）Cloud Run — サービスが動いている

**画面**: Cloud Run コンソール › `knowledge-discovery` › リビジョン一覧。
**確認済みの実値（2026-08-29 時点）**:
```
Service : knowledge-discovery
Region  : asia-northeast1
Revision: knowledge-discovery-00008-zjw  (100% traffic)
URL     : https://knowledge-discovery-dg6u6zqs7q-an.a.run.app
```
**カメラ**: URL とリビジョン名にズーム。

## 4-B（3:31–3:37）Cloud Scheduler — 自律実行のスケジュール

**画面**: Cloud Scheduler の一覧（asia-northeast1）。3ジョブすべて ENABLED。
```
kd-autonomous-sweep         ENABLED  */30 * * * *   ← Scene 3-B で動いたもの
kd-secretary-sweep          ENABLED  0 8 * * *
kd-secretary-sweep-runtime  ENABLED  55 7 * * *
```
**カメラ**: `kd-autonomous-sweep` の行 → クリックして詳細を開き、**Auth header が OIDC トークン**であることを見せる（`--oidc-service-account-email` の設定画面）。

## 4-C（3:37–3:44）★中核: ログが「無人で回り続けている」ことを示す

**画面**: Cloud Run のログエクスプローラで `/internal/autonomous-sweep` をフィルタ。
**実際に記録されている連続実行（2026-08-29 実測）**:
```
09:00:03  POST /internal/autonomous-sweep  200
08:30:03  POST /internal/autonomous-sweep  200
08:00:03  POST /internal/autonomous-sweep  200
07:30:03  POST /internal/autonomous-sweep  200
```
**カメラ**: 30分刻みのタイムスタンプが並んでいる列に3秒静止。可能なら、未認証アクセスが `401` で拒否されている行も同一画面に入れる。

> **このカットが Scene 3-B の裏取りになる。** 「デモのために1回叩いた」のではなく、**30分ごとに認証付きで回り続けている**ことが時系列で見える。ナレーションはこの点だけを言う。

## 4-D（3:44–3:50）GEAP / Firestore — 推論と状態

**画面**: 2分割またはクイックカット。
```
GEAP › Agent Runtime    : reasoningEngines/4310793666370207744  (kd-secretary-runtime)
GEAP › Models           : gemini-3.7-flash / gemini-embedding-2
Firestore (native)       : agents / profiles / cards / messages / autonomy_policies / sweep_runs
```
**カメラ**: Firestore の `autonomy_policies` と `sweep_runs` コレクションを一瞬見せる（今回追加した永続化の実物）。

**ナレーション（EN, Scene 4 全体）**:
> All of this runs on Google Cloud. The service is on Cloud Run, in Tokyo. Reasoning and embeddings come from Gemini on GEAP — formerly Vertex AI — and the secretary also runs as an agent on GEAP Agent Runtime.
> The autonomous sweep you just saw is not a demo script. Cloud Scheduler calls it every thirty minutes with an authenticated identity token — and the logs show it has been running on its own, unattended.

**日本語（参考訳）**:
> これらはすべて Google Cloud 上で動いている。サービスは東京リージョンの Cloud Run。推論と埋め込みは GEAP（旧 Vertex AI）上の Gemini、秘書エージェント自体も GEAP Agent Runtime 上で動いている。
> 先ほどの自律実行はデモ用のスクリプトではない。Cloud Scheduler が30分ごとに認証済み ID トークンで呼び出しており、ログはそれが無人で回り続けていることを示している。

---

# Scene 5 — クロージング（3:50–4:00）

**画面**: 黒背景ではなく、プロダクトと同じ羊皮紙の背景（`#E4D5AC`、Company Atlas の海の色）。タグラインはセリフ体（Source Serif 4）。下部に URL とリポジトリ。
```
AI shouldn't replace human connections.
It should create them.

Knowledge Discovery
https://knowledge-discovery-dg6u6zqs7q-an.a.run.app
github.com/toshi-naka-boop/knowledge-dis
```

**ナレーション（EN）**:
> Knowledge Discovery. Your agent doesn't answer for you — it finds the person who can.

**日本語（参考訳）**:
> Knowledge Discovery。あなたのエージェントは代わりに答えない——答えられる人を見つける。

> **タグラインは Scene 2-B で既に一度言っている。** ここでは画面に大きく出し、ナレーションは別の一文にして重複を避ける。

---

# Scene 4 撮影用リンク（収録直前に開いておく）

```
Cloud Run       https://console.cloud.google.com/run/detail/asia-northeast1/knowledge-discovery/revisions?project=knowledge-discovery-2026
Scheduler       https://console.cloud.google.com/cloudscheduler?project=knowledge-discovery-2026
Logs            https://console.cloud.google.com/logs/query?project=knowledge-discovery-2026
                クエリ: resource.type="cloud_run_revision" httpRequest.requestUrl:"/internal/autonomous-sweep"
Agent Engine    https://console.cloud.google.com/vertex-ai/agents/agent-engines?project=knowledge-discovery-2026
                ※ Vertex AI は 2026-05 に GEAP（Gemini Enterprise Agent Platform）へ改称。旧URLはリダイレクトされる想定だが、収録直前に実際のコンソールURL・画面表記を確認する
Firestore       https://console.cloud.google.com/firestore/databases/-default-/data?project=knowledge-discovery-2026
```

**注意**: コンソールにプロジェクト ID・請求情報・個人のメールアドレスが写る。**プロジェクト ID は公開して問題ない**が、アカウント名を出したくない場合は右上のアバターをぼかす。

---

# TTS 用 canonical 原稿（Scene 1–2 のみ・読み上げ対象のみ）

```
Companies brought people back to the office for one reason above all others. Ninety-two percent of employers say in-person collaboration is a top benefit of being there.

But being in the building is not the same as being connected. Researchers at MIT tracked how new working relationships actually form. Proximity mattered, but only within about a hundred and fifty meters. Past that, the effect disappeared. In a company of thousands, the person who has your answer is almost always past a hundred and fifty meters.

So we do the next best thing. We ask around. Knowledge workers spend an hour and forty-two minutes every week just trying to find the right person to ask. Not reading the answer. Just finding who has it. Twenty-eight percent say their organization is simply too large to know who holds the answer.

Proximity was never magic. It was low search cost. And search cost can be lowered another way.

Knowledge Discovery gives every employee a personal AI agent. The agent does not answer the question for you. It finds the colleague who can, and asks their agent for fifteen minutes of their time. AI shouldn't replace human connections. It should create them.
```

語数 約215語 / 想定 約81秒（160wpm）。Scene 1–2 の枠 75秒にほぼ収まる（間の取り方で調整）。
2-A の Catalini は承認により2文へ圧縮済み。さらに詰める必要が出た場合の次の削り所は 1-B の4文目（"In a company of thousands..."）→ 画面テキストのみ。

---

# 表現規則（収録時に守る）

1. **数字は必ず画面に出典を出す**。ナレーションで出典名を読み上げるのは MIT のみ（音で信頼性が上がるため）。
2. **「1日1.8〜2.5時間を情報探索に費やす」は使わない**（IDC 2001 由来の神話。`problem-evidence.md` §4）。
3. **「出社は無意味」と言わない**。主張は「出社は繋がりの十分条件ではない」。反証（近接がメンタリングを改善する等）を否定しない。
4. **AI を主語にしない**。「エージェントが繋いだ」ではなく「Jordan と Marcus が繋がった」。
5. **承認前に候補者名を出さない**（プロダクトの不変条件と同じ規律を語りにも適用）。


---

# Scene 4–5 ナレーション canonical（TTS 用）

```
All of this runs on Google Cloud. The service is on Cloud Run, in Tokyo. Reasoning and embeddings come from Gemini on GEAP — formerly Vertex AI — and the secretary also runs as an agent on GEAP Agent Runtime.

The autonomous sweep you just saw is not a demo script. Cloud Scheduler calls it every thirty minutes with an authenticated identity token, and the logs show it has been running on its own, unattended.

Knowledge Discovery. Your agent doesn't answer for you. It finds the person who can.
```

語数 約85語 / 想定 約32秒（160wpm）。枠 35秒（3:25–4:00）に収まる。

---

# 全体の尺サマリ（160wpm 換算）

| セクション | 語数 | 想定 | 枠 |
|---|---|---|---|
| Scene 1–2 | 約215語 | 約81秒 | 75秒 |
| Scene 3 | 約305語 | 約114秒 | 130秒 |
| Scene 4–5 | 約85語 | 約32秒 | 35秒 |
| **合計** | **約605語** | **約227秒（3分47秒）** | **240秒** |

**4分に収まる。** 操作の間・海図のズーム・フェード遷移の余白は Scene 3 内に確保済み（約16秒）。実測で超過したら Scene 3 canonical 末尾の調整代から削る。
