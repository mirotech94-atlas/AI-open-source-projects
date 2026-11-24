# Alinea handbook

A factual reference on Alinea Invest ("Alinea: Automated Investing", iOS) for Ivan, the ATLAS markets agent, to use when explaining Alinea to Miro.

- Compiled: 2026-09-28. Every source below was read on 2026-09-28 unless another date is given.
- Sources used: Perplexity plus primary sources. Perplexity findings were re-checked against primary documents. Where they conflicted, the primary document wins, and the conflict is noted.
- This is not investment advice. It describes what Alinea says about itself and what its regulators' filings show. It does not say whether to buy, sell, hold, open, close or switch anything.
- Rule for Ivan: the figures here are *documented terms* (fees, timings, legal facts). They are never Miro's balances or returns. For any number about his account, read it fresh from the source (Alinea emails table / app) and state the read time. If a figure here is older than about 90 days, re-read the source before quoting it as current.

---

## 0. Key sources (all read 2026-09-28)

| Tag | Source | URL |
|---|---|---|
| ADV1 | SEC Form ADV Part 1, Alinea Advisory Services LLC, CRD 311782, filed 2026-09-01 (other-than-annual amendment) | https://reports.adviserinfo.sec.gov/reports/ADV/311782/PDF/311782.pdf |
| IAPD | SEC IAPD firm record (JSON) | https://api.adviserinfo.sec.gov/search/firm/311782 |
| ADV2W | Form ADV Part 2A **Wrap** Brochure, dated 2026-03-10 (submitted 2026-03-11) | https://files.adviserinfo.sec.gov/IAPD/Content/Common/crd_iapd_Brochure.aspx?BRCHR_VRSN_ID=1017247 |
| ADV2 | Form ADV Part 2A Brochure, dated 2026-03-10 (near-identical to ADV2W) | https://files.adviserinfo.sec.gov/IAPD/Content/Common/crd_iapd_Brochure.aspx?BRCHR_VRSN_ID=1017246 |
| ADV2-2025 | Superseded ADV Part 2A, Alinea Invest, Inc., dated 2025-02-24 | https://cdn.prod.website-files.com/61c516277e0aeb1a404f91cc/6839fb8afa1811bca0ccaab1_Alinea_Invest_Form_Adv_Part_IIA_2.24.25.pdf |
| CRS | Form CRS, dated 2026-03-10 (this URL now serves the 2026 version) | https://reports.adviserinfo.sec.gov/crs/crs_311782.pdf |
| HOME | alinea-invest.com homepage incl. FAQ + footer disclosures | https://www.alinea-invest.com/ |
| PAY | "Alinea Invest, Inc. Payment Terms & Conditions", effective 2026-04-15 (served at /legal/terms-of-use) | https://www.alinea-invest.com/legal/terms-of-use |
| TOU | Terms of Use, page shows effective date 2020-12-25 | https://www.alinea-invest.com/legal/terms-of-use-2 |
| DW-POL | Alinea Deposits & Withdrawals Policy, effective 2026-04-09 | https://www.alinea-invest.com/legal/alinea---deposits-withdrawals-policy |
| BANK | Alinea Bank Account Eligibility Policy, effective 2026-04-09 | https://www.alinea-invest.com/legal/alinea---bank-account-eligibility-policy |
| ALLIE | AI Allie – Disclosures & Terms of Use, effective 2026-03-03 | https://www.alinea-invest.com/legal/ai-allie-disclosures-terms-of-use |
| EVT | Alinea Event Contracts Fee Disclosure, effective 2026-03-10 | https://www.alinea-invest.com/legal/alinea-event-contracts-fee-disclosure |
| PRIV | Alinea Invest Privacy Policy, effective 2026-01-30 | https://www.alinea-invest.com/legal/alinea-invest-privacy-policy |
| APP | Apple App Store listing, id1554623825 (v12.20.2 shown, "4d ago") | https://apps.apple.com/us/app/alinea-automated-investing/id1554623825 |
| WHAT | Alinea blog "What is Alinea Invest?" (Eve Halimi, 2026-07-21), marketing | https://www.alinea-invest.com/press/what-is-alinea-invest |
| BLOG-AI | Alinea blog "Can AI Actually Invest for You?…" (2026-05-29), marketing | https://www.alinea-invest.com/press/can-ai-actually-invest-for-you-how-alineas-allie-acts-on-your-behalf |
| BLOG-AUTO | Alinea blog "How Alinea's Automated Investing Truly Works for You" (2026-04-24), marketing | https://www.alinea-invest.com/press/how-alineas-automated-investing-truly-works-for-you |
| DW-CAA | DriveWealth Customer Account Agreement, effective 2026-06-24 | https://legal.drivewealth.com/customer-account-agreement |
| DW-FUND | DriveWealth Funding & Withdrawal Policies | https://legal.drivewealth.com/funding-withdrawal-policies |
| DW-REG | DriveWealth Regulatory Fees, effective 2026-10-01 | https://legal.drivewealth.com/regulatory-fees |
| BC | FINRA BrokerCheck search: DRIVEWEALTH, LLC, CRD 165429, active | https://api.brokercheck.finra.org/search/firm?query=drivewealth |
| NFA | NFA BASIC search "Alinea" | https://www.nfa.futures.org/BasicNet/ |
| ZH-PR | GlobeNewswire press release, "Alinea Invest Launches Crypto Trading in Partnership with zerohash", 2026-02-12 | https://www.globenewswire.com/news-release/2026/02/12/3237445/0/en/alinea-invest-launches-crypto-trading-in-partnership-with-zerohash.html |
| ZH-CS | zerohash case study: Alinea Invest | https://zerohash.com/case-studies/alinea-invest |
| DNS | Public DNS for alinea-invest.com (MX/SPF/DMARC/DKIM), queried 2026-09-28 | `dig` |
| META | Meta Ad Library, keyword "alinea invest", US | https://www.facebook.com/ads/library/ |
| BBB | BBB profile, Alinea Invest, Inc. (secondary) | https://www.bbb.org/us/ny/new-york/profile/financial-technology/alinea-invest-inc-0121-87182229 |
| TP | Trustpilot page, including company-written "About" text (secondary host, company text) | https://www.trustpilot.com/review/www.alinea-invest.com |
| FOOL | Motley Fool Money review, updated 2024-08-02 (secondary, dated) | https://www.fool.com/money/buying-stocks/alinea-invest-review/ |

---

## 1. What Alinea is

**Regulated entity.** The SEC-registered investment adviser is **Alinea Advisory Services LLC**, CRD 311782, SEC file 801-120032. [IAPD, ADV1]
- SEC registration approved 2021-01-26. [IAPD]
- It was formerly named Alinea Invest, Inc. The name change took effect January 2026. [ADV2W Item 4]
- It is a Delaware LLC and a wholly owned subsidiary of **Alinea Invest, Inc.** Alinea Invest, Inc. was formed November 2020 and is owned by Anam Lakhani and Eve Halimi, 18.8% each; both are control persons. [ADV2W Item 4]
- Principal office: 46 Howard Street, New York, NY 10013. [IAPD]
- Registers with the SEC under the **Internet adviser exemption**, meaning advice is given exclusively through an interactive website or app. [ADV1 Section 2.A(11)]
- Size per ADV Part 1 (filed 2026-09-01) [ADV1 Item 5.F]:

  | | Assets | Accounts |
  |---|---|---|
  | Discretionary | $47,531,727 | 86,216 |
  | Non-discretionary | $19,074,068 | 29,477 |
  | **Total** | **$66,605,795** | **115,693** |

  - Derived: that averages about $576 per account ($66,605,795 ÷ 115,693).
  - Staff: 10 employees, of whom **1** performs investment advisory functions (including research). [ADV1 Item 5.A–B]
- Comparison: at 2024-12-31 AUM was $7,341,589 discretionary plus $5,229,684 non-discretionary. [ADV2-2025]
- No disciplinary history is reported. [ADV2W Item 9; CRS] A BBB/NFA/BrokerCheck look found no regulatory actions either (NFA shows "No"). That is not proof there are none.

**Entity naming is inconsistent across Alinea's own materials. It is flagged here, not resolved.**

| Where | Name used |
|---|---|
| SEC ADV 2026 | Alinea Advisory Services LLC (subsidiary of Alinea Invest, Inc.) |
| Homepage security section [HOME] | "Alinea Advisory Services LLC is an SEC-registered investment adviser" |
| Homepage footer [HOME] and Terms of Use [TOU] | "Alinea Invest is an SEC registered investment adviser" / "Alinea Invest, Inc., an SEC-registered investment adviser" |
| Payment Terms [PAY] | Fees are charged by "Alinea Invest, Inc." |
| App Store seller [APP] | ALINEA INVEST, LLC |
| App Store copyright [APP] | © 2026 Alinea Invest, Inc. |
| Blog [WHAT] | "Alinea Invest, LLC is an SEC-registered investment adviser" |
| BBB [BBB] | Alinea Invest, Inc., "Type of Entity: LLC" |
| NFA [NFA] | ALINEA INVEST INC (NFA ID 0576947), not an NFA member |

**Custodian / broker: DriveWealth, LLC.**
- DriveWealth is an SEC-registered broker-dealer, member FINRA/SIPC. [HOME footer; ADV2W Item 12]
- FINRA CRD 165429, status active. [BC]
- Alinea *requires* clients to use DriveWealth, and does not allow directed brokerage. [ADV2W Item 12]
- Alinea does not have custody of client funds or securities. [ADV2W Item 15]
- It is **not** Alpaca or Apex. No source mentions either.

**SIPC.** DriveWealth's SIPC membership protects securities customers up to **$500,000, including $250,000 for cash claims**, if the broker fails financially. It does not protect against market losses. [HOME; APP]
- No excess-SIPC insurer is named in any source read.

**Crypto: zerohash.**
- Crypto trading launched in partnership with zerohash on 2026-02-12. [ZH-PR]
- zerohash provides brokerage infrastructure, liquidity and smart order routing, "regulated digital asset custody", wallets for on-chain transfers in and out, and transaction monitoring. [ZH-CS]
- zerohash licensing [ZH-CS]:
  - FinCEN-registered MSB
  - Licensed money transmitter in 52 US jurisdictions
  - NY BitLicense held by zerohash liquidity services llc
  - NMLS ID 1699379
- The press release says zerohash accounts are **not covered by FDIC or SIPC**, and that zerohash is not SEC/FINRA-registered. [ZH-PR]
- Unverified: which specific zerohash entity is the counterparty or custodian for Alinea users, and which coins are offered.

**Event contracts: Alinea Futures LLC.**
- NFA ID 0575219, New York. NFA Member Approved, registered as an **Introducing Broker**, no regulatory actions. [NFA]
- Event contracts are traded "through Alinea Futures LLC". [EVT] See §3 for fees.

**Other facts.**
- Founders and co-CEOs: Anam Lakhani and Eve Halimi. [ADV2W; press release 2026-02-04]
- Y Combinator alumnus. Raised $22.5M in user-acquisition financing (a credit facility) from PvX Partners, announced 2026-02-04. [alinea-invest.com/press/wealth-management-platform-alinea-secures-22-5-million…]
- The company claims "2M+" App Store downloads. [HOME]

---

## 2. Products

### 2.1 Account types
- **Individual taxable accounts only.**
  - The 2026 ADV says Alinea "offers these services to individual taxable accounts only at this time". [ADV2W Item 4]
  - It also says "We do not provide investment advice to IRA or tax-exempt investors." [ADV2W Item 8]
  - The Form CRS says the wrap program is offered "to individual taxable accounts". [CRS]
  - The Motley Fool (secondary, 2024) also reports taxable brokerage only.
- **Correction:** a Perplexity result said Alinea offers **Roth IRA** accounts. The primary sources contradict this (ADV2W 2026-03-10). The 2025 ADV mentions Roth IRAs only in a boilerplate liability sentence, and no Roth IRA launch was found.
- Joint and business accounts: not described by Alinea. Only personal or joint *bank* accounts in your name can be linked for funding; business bank accounts are ineligible. [BANK]
- Eligibility: 18+, US residents who are US citizens, permanent residents or holders of certain visa types. [APP]
- Platform:
  - iPhone app, iOS 18 or later. [APP]
  - The homepage shows an Android invite waitlist. [HOME]
  - No Alinea Invest app was found on Google Play. The "Alinea" app by Sensotec (com.sensotec.alinea) is an unrelated text-to-speech app.
- No account minimum. [ADV2W Item 7] Ads say "Start with as little as $1". [META]

### 2.2 How the managed ("expert-built" / Automated) portfolios are built, per the legal disclosures
- **Onboarding.** A questionnaire collects employment status, income, goals and reasons to invest, time horizon and net assets. [ADV2W Item 4]
- **Portfolio proposal.** From those answers Alinea proposes a plan from among **conservative, moderate, aggressive growth, and opportunistic** portfolios. [ADV2W Item 4]
- **Algorithm.** "Alinea makes use of an algorithm to process a client's answers… and to make an investment recommendation." Alinea does not verify the answers. [ADV2W Item 8]
- **What gets bought.**
  - The program invests "primarily… in what Alinea considers high-quality stocks". [ADV2W Item 4]
  - The CRS calls these "Alinea's proprietary equity strategies". [CRS]
  - ETFs, crypto and "playlists" are **not mentioned** anywhere in the ADV or CRS.
- **Discretion.**
  - Discretionary: Alinea decides type and number of securities without asking each time.
  - Non-discretionary: the client approves each change.
  - Both exist. [ADV2W Item 16; ADV1 shows both]
- **Who picks.**
  - Legal disclosures: "an algorithm", plus one advisory employee. [ADV2W; ADV1]
  - Homepage: portfolios are "Curated by real experts, not algorithms". [HOME]
  - These two statements are in tension.
- **Contact with advisers.**
  - The ADV says "clients will not interact directly with investment advisory personnel", and advice is delivered only through the app. [ADV2W Item 4, Item 8]
  - The homepage FAQ promises "a dedicated human advisor team to review your plan" and "Human advisors… Real people with real licenses". [HOME]
- **Rebalancing.**
  - Marketing says rebalancing is automatic: "Alinea automatically adjusts your holdings to bring them back to your target percentages". [BLOG-AUTO]
  - Playlists Pro portfolios "rebalance automatically". [WHAT]
  - **No legal disclosure states a rebalancing frequency, threshold or method.** Unknown.
- **Cash reserve.**
  - Every managed strategy must hold **1.50% of account value in cash**, used to pay Alinea fees and expenses.
  - If you withdraw cash, Alinea sells other assets to keep the reserve.
  - If fully invested in an illiquid strategy, the cash portion may not be immediately withdrawable. [ADV2W Item 5; CRS]
- **Scope limits.** The service [ADV2W Item 8]:
  - is "not a complete investment service"
  - ignores outside assets, debt and concentration
  - does not consider your tax situation
  - offers a limited number of investments
- **Unverified human review.** The company-written Trustpilot blurb says "AI-supported accounts also receive human review before trades are completed". [TP] No legal page states this.

### 2.3 Playlists and Playlists Pro
- **Playlists.**
  - Themed baskets of stocks and ETFs (earlier also crypto). Users can follow, copy, build and share them. [WHAT; alinea-invest.com/press/stock-playlists-just-got-even-better, 2022-08-11]
  - App Store wording: playlists are "provided for informational purposes and are not recommendations". [APP]
- **Playlists Pro.**
  - "managed portfolios modeled on the moves of politicians, hedge funds, world events, and AI, and they rebalance automatically". [WHAT]
  - Current Meta ads say: "Introducing Playlist Pro: stock portfolios made for you, with a built-in AI researcher". [META, ads started 2026-07-29 onward]
  - Unknown: Playlists Pro price (whether it is inside the $120 or separate), methodology, data sources (for example, how "politician" trades are sourced and how stale they are), and rebalance timing.

### 2.4 "AI", Allie, and "Vibe Investing": what the AI actually does, per Alinea's own disclosures
- **Allie, legal terms.** Per the AI Allie Terms [ALLIE, effective 2026-03-03], Allie:
  - is an automated chat feature giving **"general informational and educational content"**
  - does **not** give investment advice, does not make personalised recommendations, and does not act as a fiduciary
  - "**does not execute transactions, move funds, or control accounts**"
  - may give answers that are incomplete, inaccurate or outdated
  - may have its conversations stored and reviewed
- **App Store wording.** "Allie does not provide personalized investment advice or recommendations." [APP]
- **Underlying model.** The privacy policy says: "We use artificial intelligence providers to power our chat technologies, such as OpenAI." [PRIV]
- **Marketing contradicts the legal terms.**
  - A May 2026 blog says Allie "proactively adjusts your personalized portfolio" and "provides tailored investment advice". [BLOG-AI]
  - An April 2026 blog says Allie "offers tailored advice". [BLOG-AUTO]
  - When marketing and the legal terms conflict, the legal terms [ALLIE] govern what Allie is allowed to do.
- **"Vibe Investing" and "Automated Investing".** These appear only in the company-written Trustpilot text: they "use AI to help develop investment strategies based on their preferences". [TP] No page on alinea-invest.com and no SEC filing describes "Vibe Investing". Its mechanics are unknown.
- **The ADV on algorithms.** The ADV discusses algorithm risk. It says code changes "could have material effects on a client's stock recommendations", and that algorithms "may not perform as intended". [ADV2W Item 8] The ADV does not mention generative AI or LLMs.

### 2.5 Automated investing / recurring deposits
- You pick an amount and a schedule (the homepage calculator offers daily, weekly and monthly). [HOME]
- Recurring and one-time deposits are supported. [WHAT]
- Liquidations in "Automated accounts" happen only during US market hours. [DW-POL]

### 2.6 Fractional shares
- Supported: dollar-based trading. [ADV2W Item 8]
- Fractional shares are **not transferable**. On account closing or transfer they must be liquidated, with possible fees and loss of value. [ADV2W Item 8; DW-CAA §12.7.3]
- No voting rights on the fraction. [ADV2W]
- Dividends on fractional shares must be greater than $0.01 to be credited. [DW-CAA, as summarised; not re-quoted]

### 2.7 Crypto and self-directed trading
- The App Store subtitle is "AI Trades- Stocks, ETF, Crypto". [APP]
- Self-directed "Managed by Me" stock picking is described in the company text on Trustpilot. [TP] The ADV says "some client accounts are self-directed, some are managed by Alinea". [ADV2W Item 8]

---

## 3. Costs

**Advisory / subscription fee (current).**
- **$120.00 per year**, the "standard fee". [ADV2W Item 5; HOME FAQ]
- A monthly option "ranging from **$5.00 to $12.00 per month**" may be offered "in certain instances". [ADV2W Item 5]
- Billing mechanics:
  - Billed yearly in advance and auto-renews. [PAY §1, §4; HOME FAQ]
  - Alinea may change billing cadence or pricing for any user at its discretion. [PAY §1, §5]
  - The fee can be taken from your Alinea account cash, your linked bank account, or **by liquidating assets**. [PAY §3]
- Refunds:
  - "all fees are non-refundable once charged", except where law requires. [PAY §6]
  - Cancellation takes effect at the end of the current annual term, with no refund for unused time. [HOME FAQ]
- Free trial: 7 days (secondary: College Investor search snippet, not directly read, because the site returned 403). BBB reviewers also mention a free trial [BBB]. Treat as unverified.

**Historical (superseded) fee schedule**, from ADV2-2025 (dated 2025-02-24):
- $120/yr for accounts with AUM
- $5/month for AUM between $100 and $6,000
- no charge under $100
- A Perplexity result attributed this to a 2025-03-24 Form CRS at the CRS URL. That URL now serves the 2026-03-10 CRS, which has no such tiers. The tiers are confirmed only in ADV2-2025, and **no longer stated in current filings**.

**Trading costs.**
- $0 commissions on self-directed stock and ETF trades in individual cash accounts. SEC and FINRA fees may apply. [HOME footer]
- The wrap program has no sales loads, mark-ups, mark-downs or market-maker spreads. [ADV2W Item 5]
- Custodian charges may still apply: wire and electronic fund fees, account termination fees. [ADV2W Item 5]
- DriveWealth may charge account transfer fees, inactivity fees and legal transfer charges, and passes through SEC Section 31 and FINRA TAF fees. [DW-CAA]
- The FINRA TAF is set to $0 from 2026-10-01 to 2026-12-31, then reverts. [DW-REG]
- Paper statements come only on request and for a fee. [TOU]

**Conflict of interest in the wrap structure.** Alinea pays for executed trades, so it "may have an incentive to limit its trading activities" in client accounts. [ADV2W Item 5]

**ETF expense ratios:** not stated in any source read. They would be charged inside any ETF held, on top of the $120.

**Crypto fees and spreads.**
- Current (zerohash era, since 2026-02): **not disclosed** in any source read.
- Historical: 2022 coverage (Benzinga, via an investing.com search summary, not opened) reported $0 commission plus a **1% spread** on crypto, across 20 coins in 49 states. It is dated, pre-zerohash, and may not apply now.

**Event contracts** [EVT]:
- Fee per contract, per side:

  | Component | Fee |
  |---|---|
  | Alinea fee | $0.01 |
  | FCM fee | $0.01 |
  | Exchange fee | $0.01 |
  | **Total** | **$0.03** |

- "Per side" means the fee is charged on opening and again on closing.

**Fee drag arithmetic.** A $120/yr flat fee as a percentage of balance, before any market return:

| Balance | $120 as % per year |
|---|---|
| $500 | 24.0% |
| $1,000 | 12.0% |
| $5,000 | 2.4% |
| $10,000 | 1.2% |

(Formula: 120 ÷ balance. For comparison, the average account in ADV1 is about $576, where $120 would be about 20.8%.)

---

## 4. Money movement [DW-POL, effective 2026-04-09, unless noted]

- **Deposits:** allow up to **4 business days** to fully settle. They may appear sooner.
- **ACH holds:** DriveWealth may hold ACH deposits for up to **5 business days**. During the hold you may be unable to trade or withdraw, and the introducing firm may impose a longer hold. [DW-FUND]
- **Withdrawals:** usually **2–3 business days** after processing begins.
- **Processing hours:** 9:00 AM–4:00 PM ET, Monday–Friday. Requests outside these hours start the next business day.
- **Reviews:** some transactions get security or compliance review, handled first-come, first-served in business hours. Documents may be requested (bank statements, letter of good standing, etc.).
- **Automated accounts:** liquidations happen only in US market hours.
- **Return path:** funds deposited by ACH or wire can be withdrawn by those methods once settled and past any hold. [DW-FUND]
- **Chargebacks:** an ACH chargeback can freeze the account. [DW-FUND]
- **Cash reserve:** withdrawing cash triggers sales to keep the 1.5% reserve. [ADV2W Item 5]
- **Limits:** dollar deposit and withdrawal limits are **not published** in any source read.
- **Complaint patterns (secondary, not verified):** withdrawal delays and lockouts are the most common theme in Trustpilot reviews (TrustScore 3.5, 350 reviews). [TP, via fetch summary]

---

## 5. Taxes

- Statements, confirmations and tax forms are accessible electronically in the Alinea platform. [TOU]
- DriveWealth delivers confirmations and statements electronically (by email, website posting or hyperlink) [DW-CAA §32]:
  - a confirmation for each trade
  - a statement each month with activity, and at least quarterly
- Tax reporting documents are among the materials DriveWealth delivers electronically. [DW-CAA §32] The specific form (e.g. a consolidated 1099) and its issue date are **not stated** in sources read.
- Errors: statements are binding unless you object within **10 calendar days** (confirmations: **3 calendar days**). The support contact is support@drivewealth.com. [DW-CAA §33]
- **Tax-loss harvesting:** not offered or mentioned anywhere. The ADV says Alinea "does not consider a client's personal tax situation" and gives no tax advice. [ADV2W Item 8]
- **Wash-sale handling:** not addressed in any Alinea or DriveWealth document read. Cost-basis method: not stated.
- Selling fractional shares to transfer out is a sale, and in a taxable account that is a taxable event. This is general tax mechanics, not advice.

---

## 6. Risks and fine print, as Alinea states them

- **Hypothetical / illustrative performance.**
  - The homepage growth calculator compares a bank at +0.29% with Alinea at +12%.
  - Its disclaimer says the projections are hypothetical and illustrative, "based on historical returns of the S&P 500", and not guarantees. [HOME]
  - The footer says historical, expected or probability projections "are hypothetical in nature", and that holdings shown "are for illustrative purposes only and are not investment recommendations". [HOME; APP]
  - App "Goal Projections" are hypothetical. [APP]
- **Backtests:** not mentioned in any Alinea page or filing read.
- **Algorithm risk:** the algorithm may not perform as intended, and code changes may have unintended effects. [ADV2W Item 8]
- **Risk of loss:** Alinea says it cannot guarantee results, predict tops and bottoms, or insulate clients from crashes. [ADV2W Item 8]
- **Conflicts of interest.**
  - Alinea "has an incentive to encourage customers to increase the assets in their accounts". [CRS; ADV2W]
  - The wrap structure gives an incentive to limit trading. [ADV2W]
  - Employees may trade the same securities. [CRS; ADV2W Item 11]
  - The CRS says there are no proprietary products, revenue sharing, third-party payments or principal trading. [CRS]
  - The Terms say Alinea may be paid when users engage with partner offers in its messages. [TOU §21]
- **Disputes:** binding individual arbitration and a class-action waiver, under New York law. You can opt out within 30 days by emailing info@alinea-invest.com with the subject "30-Day Right to Opt Out". [TOU §22]
- **Service changes:** Alinea may modify or discontinue services at any time. [TOU; ALLIE]
- **Marketing vs disclosures, side by side.**
  - The blog calls Allie a proactive advice-giver [BLOG-AI]. The legal terms say educational only, with no account authority [ALLIE].
  - The homepage says "not algorithms" [HOME]. The ADV says an algorithm makes the recommendation [ADV2W].
- **"ChatGPT and Claude picking stocks" ad claim: neither confirmed nor refuted.**
  - The visible ad text in Meta's library (2026-07 to 2026-09) says "built-in AI researcher", "Playlist Pro", and "We build personalized investing portfolios for you". Some ads run under the page name "Alinea: AI Investing" and a creator page "Quinn Invests". [META]
  - A keyword search "alinea claude" returned Alinea ads, but the matching text was not visible, and the video creatives (voice and on-screen text) were **not reviewed**.
  - No Alinea legal document says ChatGPT or Claude selects securities. The only named AI vendor is OpenAI, for chat. [PRIV]
  - Several Alinea ads are labelled "This ad ran without a required disclaimer" (inactive, July 2026). [META]
- **BBB (secondary)** [BBB, read 2026-09-28]:
  - Rating **F**, not accredited.
  - "22 complaint(s) filed against business" and "Failure to respond to 10 complaint(s)".
  - BBB file opened 2025-09-10.
  - A profile "updated 2026-09-24" date from Perplexity was **not visible** and is unverified.

---

## 7. Email: what Alinea mail looks like (for ATLAS's email reader)

- **Published Alinea addresses:** info@alinea-invest.com (general, billing, arbitration opt-out) and support@example.com (support). [HOME; PAY; TOU] Trustpilot replies come from support@example.com. [TP]
- **alinea-invest.com DNS** [DNS, 2026-09-28]:
  - MX: Google Workspace
  - SPF: `v=spf1 include:_spf.google.com ~all` (softfail)
  - DKIM: selectors `s1` and `s2` are CNAMEs to **SendGrid** (`u23362571.wl072.sendgrid.net`), so bulk and transactional mail is likely sent via SendGrid, signed `d=alinea-invest.com`
  - DMARC: `v=DMARC1; p=none` (monitor only)
- **Security implication:** with DMARC `p=none`, spoofed mail claiming `From: …@alinea-invest.com` is not rejected by receivers. A From-domain match alone is weak evidence. A stronger signal is `Authentication-Results` showing **dkim=pass with header.d=alinea-invest.com** (or DMARC pass). (Descriptive note for ATLAS maintainers; no code was changed.)
- **alineainvest.com** (no hyphen) is the display domain in Meta ads. It 301-redirects to alinea-invest.com and has no MX record. [DNS; curl]
- **DriveWealth mail:**
  - DriveWealth, not Alinea, is required to send account statements "directly to clients". [ADV2W Item 13, Item 15]
  - DriveWealth may send them by email. [DW-CAA §32]
  - The contact is support@drivewealth.com. [DW-CAA §33]
  - So statement, confirmation and tax-form emails may come from a **drivewealth.com** (or DriveWealth vendor) address that an alinea-invest.com-only filter would ignore. The actual sender address is **unverified**.
- **zerohash mail:** whether zerohash emails crypto users directly is unknown.
- **Miro's inbox:** a Gmail search (his main Gmail address, 2026-09-28) for alinea, drivewealth and zerohash found **no Alinea, DriveWealth or zerohash mail**. Miro's Alinea account may use a different address, so no sender could be sampled.

---

## 8. Glossary

- **RIA:** registered investment adviser. Alinea Advisory Services LLC is registered with the SEC.
- **Form ADV / Part 2A / Form CRS:** the adviser's SEC filings.
  - Part 1: firm data.
  - Part 2A: brochure in plain language.
  - Form CRS: 2-page relationship summary.
- **Wrap fee program:** one bundled fee covering advice, execution, custody and reporting. Alinea's is $120/yr standard.
- **Discretionary / non-discretionary:** whether the adviser can trade without asking each time.
- **Custodian / clearing broker:** the firm that actually holds the assets (DriveWealth, LLC).
- **Introducing firm:** the app company in front of the custodian. DriveWealth calls Alinea this.
- **SIPC:** protects against *broker failure* (up to $500k, including $250k cash). It does not cover market losses, adviser failure, or crypto.
- **Cash reserve:** the 1.5% of account value Alinea requires kept in cash to pay fees.
- **Playlist / Playlists Pro:** themed baskets of securities. The Pro versions are "managed" and auto-rebalanced, per marketing.
- **Allie:** Alinea's chat assistant. Educational only, per its terms; powered by an AI provider such as OpenAI.
- **zerohash:** a crypto infrastructure firm handling Alinea's crypto trading and custody. Not SIPC or FDIC.
- **Event contract:** a CFTC-regulated yes/no contract on an outcome, traded via Alinea Futures LLC (NFA introducing broker).
- **Fractional share:** less than one share, bought by dollar amount. It cannot be transferred out.
- **ACH hold:** the period deposited funds are locked before trading or withdrawal.
- **DMARC p=none:** the domain asks receivers to take no action on failed authentication.

---

## 9. Questions Miro may ask, with the factual answer

**Does Alinea rebalance automatically?**
- Alinea's marketing says yes, for managed portfolios and Playlists Pro. [BLOG-AUTO; WHAT]
- No filing states how often or at what drift threshold. [ADV2W silent]
- For self-directed holdings, nothing says it rebalances.

**Who holds my money?**
- DriveWealth, LLC (FINRA CRD 165429) holds stocks, ETFs and cash. Alinea does not have custody. [ADV2W Items 12, 15]
- Crypto is held via zerohash, not DriveWealth. [ZH-CS]

**What happens if Alinea shuts down?**
- Assets are in your name at DriveWealth, not at Alinea. [ADV2W Item 15]
- DriveWealth says it "is not responsible for the conduct of your Introducing Firm". [DW-CAA §3.2]
- On account closure, absent instruction, DriveWealth may liquidate and send you the funds. [DW-CAA §46]
- Fractional shares cannot be transferred and must be sold. [DW-CAA §12.7.3]
- SIPC covers a *DriveWealth* failure, up to the limits, not an Alinea failure or market losses. [HOME]
- Crypto at zerohash has no SIPC or FDIC coverage. [ZH-PR]
- No specific Alinea wind-down plan is published.

**What do I pay per year on $1,000?**
- Standard fee: $120 ÷ $1,000 = **12.0% per year**.
- If on a monthly option: $5–$12 × 12 = $60–$144/yr, which is **6.0%–14.4%**. [ADV2W Item 5]
- Plus possible SEC/FINRA pass-through fees and any ETF expense ratios (not stated).
- Separately, $15 (1.5%) must sit in cash. That is not a fee, but it is uninvested. [ADV2W Item 5]
- On $10,000: $120 is 1.2%.

**Is Allie (the AI) managing my money?**
- Per its legal terms, no. It is educational chat that "does not execute transactions, move funds, or control accounts". [ALLIE]
- Managed-portfolio trades come from Alinea's advisory program: an algorithm driven by your questionnaire, with discretion if granted. [ADV2W]

**Do ChatGPT or Claude pick the stocks?**
- No Alinea legal document says so. The only AI vendor named is OpenAI, for chat. [PRIV]
- Ad video content was not reviewed. See §6.

**Can I open a Roth IRA?**
- Current filings say taxable individual accounts only, and no advice to IRA investors. [ADV2W]

**How long does a withdrawal take?**
- Usually 2–3 business days after processing starts. Processing runs 9–4 ET on weekdays. [DW-POL]
- New ACH deposits can be held up to 5 business days first. [DW-FUND]
- Managed accounts must sell during market hours. [DW-POL]

**Can I get a refund of the $120?**
- The payment terms say fees are non-refundable once charged, except where law requires. [PAY §6]

**Is Alinea regulated?**
- Yes. The adviser is registered with the SEC (CRD 311782). [IAPD]
- The broker is FINRA/SIPC. [BC]
- The event-contract arm is an NFA introducing broker. [NFA]
- Registration "does not imply a certain level of skill or training". [HOME]

**How big is Alinea?**
- $66.6M across 115,693 accounts, per ADV filed 2026-09-01. [ADV1]
- 10 employees, of whom 1 does advisory work.

**Does it do tax-loss harvesting?**
- Not offered or mentioned anywhere. [ADV2W]

---

## 10. Unknowns (could not verify as of 2026-09-28)

1. Rebalancing frequency, thresholds and method for managed portfolios and Playlists Pro.
2. What "Vibe Investing" is and how it works. It is only named in the company's Trustpilot text.
3. Whether "AI-supported accounts receive human review before trades". This is Trustpilot company text only, not in the ADV.
4. Whether any ad claims that ChatGPT or Claude pick stocks. Ad videos were not reviewed.
5. Playlists Pro pricing and methodology, including the source and lag of "politician/hedge fund" data.
6. Current crypto fees and spreads, the coins offered, and the exact zerohash entity acting as custodian or counterparty.
7. Underlying ETF expense ratios, and which securities the "conservative/moderate/aggressive/opportunistic" portfolios hold.
8. Deposit and withdrawal dollar limits.
9. Tax form type and issue dates, cost-basis method, and wash-sale handling.
10. Actual sender addresses for DriveWealth statement, confirmation and 1099 emails, and whether Alinea transactional mail uses subdomains. No sample was available in Miro's Gmail.
11. The free-trial length (7 days per secondary sources only) and when the monthly $5–$12 option applies.
12. Which legal entity name is correct for consumer contracts (Inc. vs LLC). Alinea's own materials are inconsistent.
13. Whether the homepage "human advisor team" means registered persons. ADV1 reports 1 advisory employee and 0 state-registered IARs.
14. Excess-SIPC coverage (none named), and details of the DriveWealth cash sweep for uninvested cash.
15. The BBB profile's last-updated date, and the absence of regulatory actions beyond what NFA, the SEC ADV and BBB show.
