# Research and Decision Log

**Question:** What open-source framework would fill a *real* gap in finance and could be *widely adopted* by developers and companies?
**Outcome (13 Nov 2026):** an open-source reconciliation engine. See [PRODUCT.md](PRODUCT.md).

> Caveat: much of the evidence comes from vendor blogs, trade press and project READMEs found by web search in Nov 2026. Star counts, adoption figures and dates were not all checked against primary sources. Re-verify anything before quoting it publicly.

---

## 1. How the decision evolved

| Step | Framing | Candidate | Why it was superseded |
|---|---|---|---|
| 0 | Startup | InvoiceAgent: WhatsApp agent helping Indian MSMEs finance invoices on TReDS (private archive) | The user's goal is open source, not a startup. Most of its value was partnerships and ops, not code. |
| 1 | Open source, from the InvoiceAgent idea | India receivables toolkit: canonical invoice schema, GST/e-invoice checks, Tally/e-invoice connectors | Real gap, but **India-only adoption**. Also, the integration surface is gated: TReDS has no public APIs, GST APIs go through licensed intermediaries, and Account Aggregator access needs a regulated data user. |
| 2 | Open source within the Finternet stack | Finternet "asset origination / attestation" layer (registry data → verified, ledger-agnostic asset bundle), invoices first | Better aligned, but **adoption depends on tokenisation pilots** still at an early stage. UNITS is hosted and closed, and its specs are licensed non-commercial. |
| 3 | Open source across finance, real gap | Open receivable-identity and double-financing prevention standard and registry | The gap is real and important, but **needs network effects**: a registry is useless until many lenders join. Proprietary incumbents have distribution (MonetaGo over SWIFT, Setpoint). |
| 4 | Real gap **and** wide adoption | **Open-source reconciliation engine** | Chosen. Universal demand, no network effects or gatekeepers, adopted step by step, and it is the conspicuous missing layer in the open finance stack. |

---

## 2. Landscape: what open source already covers

| Area | Open-source projects | Verdict |
|---|---|---|
| Ledger / financial transactions DB | TigerBeetle (Apache-2.0), Formance ledger (MIT), Blnk (Apache-2.0) | Covered |
| Payments orchestration | Juspay Hyperswitch (claims 42k+ stars, vendor figure) | Covered |
| Billing | Lago, Kill Bill | Covered |
| AML / fraud monitoring | Marble (open-core), Tazama (Linux Foundation Charities and Gates Foundation) | Covered |
| Sanctions | OpenSanctions / yente | Covered |
| E-invoicing formats and validation | GOBL (Apache-2.0, 30+ countries), Peppol tooling, India Compliance for ERPNext (GPL-3.0) | Covered |
| Agent guardrails / human approval | Microsoft Agent Governance Toolkit, Agent Hooks, LangGraph, statepilot, AgentTrust, agent-policy-engine | Crowded |
| Agent payments | AP2 (now under FIDO Alliance), OpenAI/Stripe ACP, x402, ERC-8004; NPCI Unified Agent Protocol (on hold) | Owned by standards bodies |
| Credit networks (India) | OCEN 4.0, ONDC FIS12 invoice-based loans (beckn), RBI ULI | Owned by institutions |
| Tokenisation ledgers | Finternet UNITS (hosted; specs CC BY-NC-SA), Canton/Daml, ERC-3643, Avalanche, RBI UMI | Owned by large players |
| Tally ↔ AI | Several community MCP servers (e.g. dhananjay1405/tally-mcp-server v7.12) | Covered |
| Account Aggregator | Sahamati OpenAPI specs, finarkein/fiul (FIU layer) | Partly covered; regulated access needed |
| **Reconciliation** | Formance reconciliation is **Enterprise Edition only**; Blnk reconciliation works **only inside Blnk**; Reconify ~10 stars; Lerian Matcher repo gone (404) | **Open gap** |
| Sales tax / VAT calculation engine | None found. Every result was commercial (Avalara, Vertex, Sovos…) | Gap, but heavy on maintained data |
| Transaction categorisation / enrichment | No open model found; only synthetic Hugging Face datasets | Gap, but needs private data |
| Double-financing / pledge registry | Proprietary: MonetaGo, Setpoint PledgeCheck, Surecomp, Duality. Open source: only a university prototype (Duplera) | Gap, but needs network effects |
| Bank statement PDF parsing | Transtractor, Camelot/Tabula, several small parsers | Partly covered; generic document AI eats it |
| EU AI Act / model-risk documentation | eu-ai-act-compliance-kit, EuConform, ZenML demo | Emerging |

---

## 3. Scoring (judgment, 1–5, higher is better)

| Candidate | Real gap | Breadth of adoption | Standalone value (no network needed) | Feasible for a small team plus community | Durable | Total |
|---|---|---|---|---|---|---|
| **Reconciliation engine** | 4 | 5 | 5 | 4 | 5 | **23** |
| Open transaction categorisation (model + merchant data) | 5 | 4 | 4 | 2 | 3 | 18 |
| Open sales tax / VAT engine | 5 | 5 | 4 | 1 | 2 | 17 |
| India receivables / Finternet origination toolkit | 4 | 2 | 4 | 4 | 3 | 17 |
| Bank statement / financial document parsing | 2 | 4 | 5 | 4 | 2 | 17 |
| Double-financing registry / standard | 5 | 2 | 1 | 2 | 4 | 14 |
| EU AI Act / model-risk documentation kits | 3 | 2 | 4 | 3 | 2 | 14 |
| Agent guardrails, payments, "know your agent" | 1 | 4 | 3 | 3 | 2 | crowded |

### Finternet-stack directions considered in step 2

| Direction | Total (same criteria) | Note |
|---|---|---|
| Own open ledger (UNITS alternative) | 7 | Institutions and big players occupy it |
| UNITS chain adapters | 12 | Depends on one hosted, closed platform |
| UNITS token templates | 11 | Same dependency |
| "Know your agent" / agent permissions | 14 | Real demand (SBI's proposal at GFF 2026), but NPCI and RBI will define it |
| Interledger bridge | 10 | Very hard, already contested |
| ZK selective-disclosure credentials | 13 | Research-heavy; a C4GT project already exists |
| UNITS developer tools / emulator | 12 | Dependent on Finternet |
| Wallet / user app | 12 | A product, not a framework |
| Asset origination / attestation, invoices first | 21 | Best within Finternet, but adoption hinges on tokenisation |

---

## 4. Key evidence for the decision

**Demand for reconciliation**
- 27% of finance teams rely primarily on spreadsheets for reconciliations and adjustments; only 13% have a fully automated close (CFO.com 2026 CFO Report).
- Cash reconciliation takes 20–50 hours a month; 94% of teams use Excel in the month-end close (Ledge, 2025).
- Cross-system data reconciliation is the #1 manual task finance teams want to eliminate (28%) (Leapfin, 2025).
- 92% of CFOs still rely on manual effort to close (Redwood, Mar 2026).

**The gap**
- Formance documents reconciliation as an Enterprise Edition feature, even though its ledger is MIT.
- Blnk's reconciliation matches against Blnk ledger data only.
- Reconify (MIT, Go): about 10 stars, 3 forks.
- LerianStudio/matcher was listed by aggregators but returns 404 on GitHub. Others have seen the gap, and nobody has filled it.

**Why not the double-financing registry first** (still important, now planned as a later module)
- First Brands (alleged ~$2.3bn factoring fraud; founder indicted 29 Jan 2026) and Tricolor showed lenders can't check collateral uniqueness across each other.
- The industry calls for privacy-preserving shared registries and common standards (IFA Commercial Factor, Nov 2025, written by a MonetaGo MD).
- Proprietary incumbents are gaining distribution: MonetaGo's Trade Financing Validation Service went live over SWIFT in Feb 2026; it also serves the Association of Banks in Singapore, SIDBI and India Factoring. Setpoint launched PledgeCheck in Oct 2025.
- A registry's value depends on participation. Open-source code alone doesn't create the network.

**Why not India-only or Finternet-first**
- TReDS exchanges publish no developer APIs. GST API access goes through licensed GSPs/ASPs. Account Aggregator data needs a regulated FIU.
- Finternet UNITS is a hosted service. Public repos are specs (CC BY-NC-SA 4.0), a Claude skill (MIT) and a demo. The chain adapter spec connects UNITS to blockchains, not to real-world registries.
- Tokenisation in India is at pilot stage: RBI UMI (tokenised CDs), SEBI/RBI Demat 2.0 (tokenised bonds, Sep 2026), and Arya.ag + Finternet + Avalanche warehouse receipts (Sep 2026).

---

## 5. Sources

**Reconciliation and finance operations**
- CFO.com, 2026 CFO Report — https://www.cfo.com/news/only-3-percent-of-finance-leaders-are-skeptical-of-future-ai-payoffs-roi-cfo/820537/
- Ledge, month-end close benchmarks — https://ledge.co/content/month-end-close-benchmarks-for-2025
- Leapfin, state of automation for revenue accounting 2025 — https://www.leapfin.com/state-of-automation-for-revenue-accounting-2025
- Redwood, CFO blind spot — https://www.redwood.com/press-releases/cfo-blind-spot-manual-efforts-to-close-despite-potential-to-automate/
- Formance reconciliation (Enterprise) — https://docs-git-main-formance.vercel.app/deploy/self-hosted/modules/reconciliation
- Blnk reconciliation guide — https://www.mintlify.com/blnkfinance/blnk/guides/reconciliation
- Reconify — https://github.com/reconifyhq/reconify
- Primer, reconciliation gap in payment stacks — https://primer.io/blog/solving-the-reconciliation-gap-in-modern-payment-stacks

**Open-source finance stack**
- Blnk vs Formance — https://openalternative.co/compare/blnk/vs/formance
- Hyperswitch — https://hyperswitch.io/blog/juspay-hyperswitch-open-source-payments-platform-expands-to-us-europe-and-uk
- Open-source fincrime building blocks (OpenSanctions) — https://www.opensanctions.org/docs/opensource/fincrime
- Marble — https://github.com/checkmarble/marble
- GOBL / Invopop — https://www.borncapital.vc/portfolio/invopop/
- Microsoft Agent Governance Toolkit — https://opensource.microsoft.com/blog/2026/04/02/introducing-the-agent-governance-toolkit-open-source-runtime-security-for-ai-agents/
- Agent Hooks — https://commandline.microsoft.com/agent-hooks-framework-neutral-ai-governance-contract/

**Other candidate gaps**
- Transaction categorisation datasets — https://huggingface.co/datasets/DoDataThings/us-bank-transaction-categories-v2
- Avalara alternatives (all commercial) — https://thecfoclub.com/tools/best-avalara-alternatives/
- Bank statement parsers compared — https://dev.to/urios/parsing-bank-statement-pdfs-5-tools-compared-for-developers-2026-4b70
- EU AI Act credit-scoring guide — https://www.openlayer.com/blog/credit-scoring-eu-ai-act-compliance-guide

**Double financing**
- First Brands case study — https://blog.actuaries.org.uk/2026/7/private-credit-first-brands-case-study/
- Double-pledging crisis explainer — https://alterest.co/en-us/resources/explainers/double-pledging-crisis
- IFA call for collaboration and standardisation — https://magazine.factoring.org/magazine-articles/data-led-fraud-defense-in-us-invoice-factoring-a-call-for-collaboration-and-standardization
- MonetaGo over SWIFT — https://ffnews.com/newsarticle/swift-and-monetago-deliver-major-milestone-in-fight-against-trade-finance-fraud
- MonetaGo selected by Association of Banks in Singapore — https://www.tradefinanceglobal.com/posts/monetago-selected-by-the-association-of-banks-in-singapore-to-deliver-trade-finance-registry-and-combat-duplicate-invoice-fraud/
- Setpoint PledgeCheck — https://www.abfjournal.com/setpoint-launches-industry-standard-to-stop-double-pledging/
- Duplera prototype — https://github.com/SkullCap23/Duplera
- TradeTrust (IMDA) — https://www.imda.gov.sg/how-we-can-help/digital-utilities/tradetrust

**India and Finternet**
- Finternet GitHub — https://github.com/finternet-io ; specs — https://github.com/finternet-io/specs ; UNITS skill — https://github.com/finternet-io/units-skills
- Nilekani at GFF 2026 — https://india.entrepreneur.com/business-news/5-things-to-know-from-nandan-nilekanis-talk-at-global-fintech-fest-2026
- GFF 2026 launches — https://rango.design/articles/global-fintech-fest-2026-launches
- Arya.ag tokenised grain receipts — https://cointelegraph.com/news/arya-ag-tests-tokenized-grain-receipts-avalanche
- SBI "know your agent" — https://www.newsbytesapp.com/news/business/challa-sreenivasulu-setty-urges-know-your-agent-for-ai/tldr
- NPCI AiNxt / AtOM — https://www.angelone.in/news/economy/npci-launches-ainxt-and-atom-agentic-ai-platforms-for-upi-ecosystem
- RBI UMI / Demat 2.0 — https://www.moneylife.in/article/sebi-rbi-launch-demat-20-pilot-for-tokenised-corporate-bonds-3-issuers-raise-1025-crore/81626.html
- OCEN — https://pn.ispirt.in/tag/ocen-2025/ ; ONDC financial services specs — https://github.com/beckn/financial-services
- India Compliance (ERPNext) — https://github.com/resilient-tech/india-compliance
- Tally MCP server — https://github.com/dhananjay1405/tally-mcp-server
- GSP/ASP access model — https://tallysolutions.com/gst/asp-vs-gsp-in-gst-what-is-the-difference-and-how-do-they-work-together/
- Sahamati FIU — https://sahamati.org.in/financial-information-user-fiu
- TReDS dedup (MonetaGo) — https://www.tradefinanceglobal.com/posts/indian-treds-platforms-advance-rapid-invoice-deduplication-new-global-standards/
- Indian GST invoice datasets (synthetic) — https://github.com/Soundar-rajan916/invoice

Sources for the archived startup plan are kept with that private document.
