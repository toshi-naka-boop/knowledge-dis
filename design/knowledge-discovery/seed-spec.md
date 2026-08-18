# seed-spec: シードデータ仕様（M2）

舞台: **Meridian Care Partners Group**（架空）— 米国の医療人材紹介（healthcare staffing）を中核に、ヘルスケア不動産仲介、医療機関の事業承継（practice transition/M&A advisory）支援、管理部門を持つ複数事業グループ。従業員約400名。

言語: プロフィール・チャット表示は英語（デモ動画・審査員向け）。

## 完全実装4名（agents レジストリ登録）

### 1. Rachel Kim — Senior Account Manager, Healthcare Staffing Division
- current_work (public): "Manages staffing accounts for 30+ hospital and clinic clients across the metro area. Handles nurse and allied-health placement contracts, client escalations, and renewal negotiations. Longest-tenured account manager on the medical corporation side; keeps informal notes on each client's decision-makers and hiring history."
- expertise (public): "Client relationship history: which medical groups changed hiring policy after ownership changes, which facilities had early-turnover disputes, how each director of nursing prefers candidates presented."
- background (public): "8 years in healthcare staffing; started as a recruiter before moving to account management."
- 役割: メイン質問シーンの**辞退→資料添付**担当（「今週は法人対応で動けないが、過去のクリニック移転メモを共有します」）

### 2. Marcus Delgado — Commercial Broker, Healthcare Real Estate Division
- current_work (public): "Brokers medical office buildings and clinic sites. Currently working two ambulatory-surgery-center site searches; tracks zoning, parking-ratio, and ADA requirements that medical tenants hit during relocation. Maintains a private list of off-market properties suitable for healthcare use."
- expertise (public): "Knows which sites can physically and legally host a clinic: zoning categories, conversion costs from retail to medical use, landlord attitudes toward medical tenants."
- background (public): "Former hospital facilities coordinator; moved to brokerage 6 years ago."
- 役割: メイン質問（医療法人の移転・土地探し）の**同意→マッチ成立**担当

### 3. Elena Vasquez — Transition Advisor, Practice Transition (M&A) Support
- current_work (public): "Advises independent physician practices on succession planning: valuation prep, buyer search, and post-transition staffing continuity."
- expertise (public): "Practice succession patterns: when owners start considering exit, what kills deals late, how staffing contracts transfer."
- **transition_pipeline (private)**: "Currently advising two unannounced clinic succession deals, including one whose owner is also exploring relocation before sale. Details under NDA."
- background (public): "CPA background; 5 years in healthcare M&A advisory."
- 役割: **非公開項目打診シーン**担当（承継関連の質問が来ると、本人にだけ「あなたの非公開項目に関係しそうな質問です」と打診が届く。監査チャットでは🔒マスク）

### 4. Tom Whitfield — Senior Accountant, Corporate Services
- current_work (public): "Prepares consolidated monthly closes, quarterly financial statements, and tax filings for the group's entities. Coordinates the annual external audit."
- expertise (public): "GAAP reporting, intercompany reconciliation, audit documentation."
- background (public): "12 years in corporate accounting."
- 役割: **確実に落ちる1体**（デモ質問と語彙・意味の重なりを意図的にゼロにする。no_connection として監査チャットに落選理由付きで表示）

## 質問者（エージェント非登録・依頼者ロール）

- Jordan Lee — Account Manager, Healthcare Staffing Division（若手。Rachel の同僚）

## デモ質問（3シーン）

1. **メイン（事業横断）**: "A hospital client of mine wants to relocate one of their clinics. Who in our group knows how to find sites that can actually host a medical facility — zoning, conversion, that kind of thing?" → Marcus 同意（マッチ成立）、Rachel 辞退＋資料リンク添付、Tom 落選（no_connection）
2. **非公開項目打診**: "The owner of a small clinic I work with mentioned she's thinking about retiring in a few years. Who has experience with practice succession conversations?" → Elena の private 項目（transition_pipeline）が接点として検出され、Elena 本人にのみ打診。監査チャットは🔒マスク表示
3. **統制**: 未登録 payload 型の送信テスト → 赤の system メッセージ

## 合成396名分の生成ガイド（scripts/generate_seeds.py）

- 部門分布: Healthcare Staffing 40% / Real Estate 15% / Transition Advisory 10% / Corporate Services(経理・人事・IT・法務) 25% / Executive・その他 10%
- 各人: name（多様な英語名）, role, current_work（2〜3文・具体的）, expertise（1〜2文）, background（1文）。Gemini 3.7 Flash で生成し Firestore へ投入
- visibility: 合成分は全て public（reviewed=false のまま。配送対象外なので打診には使われない）
- デモ質問1・2の語彙と強く重なるプロフィールを合成側に**意図的に少数（各5名程度）含める**: 画面用ファネルの上位20件に「エージェント未登録の有望候補」が映ることで、「全社展開ならここまで探索できる」のスケール訴求が実データで裏付けられる
- 生成スクリプトは冪等（再実行で同一IDを上書き）。embedding は項目本文のみから生成（M1の実装に一致）
