// Legal documents. Statements describe what the product actually does today; anything the operator must decide
// (entity name, address, governing law, subprocessor regions) comes from NEXT_PUBLIC_* settings and is marked in the page.
export const LEGAL = {
  entity: process.env.NEXT_PUBLIC_LEGAL_ENTITY || "LeadX",
  address: process.env.NEXT_PUBLIC_LEGAL_ADDRESS || "",
  email: process.env.NEXT_PUBLIC_CONTACT_EMAIL || "support@leadx.example",
  securityEmail: process.env.NEXT_PUBLIC_SECURITY_EMAIL || process.env.NEXT_PUBLIC_CONTACT_EMAIL || "security@leadx.example",
  law: process.env.NEXT_PUBLIC_GOVERNING_LAW || "",
  updated: "18 November 2026",
};

export type Section = { id: string; title: string; body: string[] };
export type Doc = { path: string; title: string; summary: string; sections: Section[] };

const who = `${LEGAL.entity}${LEGAL.address ? `, ${LEGAL.address}` : ""}`;

export const DOCS: Record<string, Doc> = {
  privacy: {
    path: "/privacy", title: "Privacy Policy", summary: "What personal data LeadX processes, why, and the choices you have.",
    sections: [
      { id: "who", title: "Who we are", body: [`${who} ("LeadX", "we") provides a lead discovery, enrichment and CRM platform for agencies and businesses. This policy covers our website, the LeadX application and the business-contact data we process.`, `Contact: ${LEGAL.email}.`] },
      { id: "data", title: "Data we process", body: [
        "Account data: name, work email, workspace name, role and sign-in events. Passwords are handled by our authentication provider and are never stored by LeadX in readable form.",
        "Workspace data: the companies, contacts, deals, notes, tasks and files you add or import, and the leads delivered to your workspace.",
        "Business lead data: business names, addresses, phone numbers, websites, public ratings and business email addresses collected from public sources (for example OpenStreetMap, business websites and public listings) and from providers you choose to enable. Some of this can identify a person (for example a sole trader's name or an email such as jane@company.com).",
        "Technical data: IP address, browser type, request logs, error reports and security events, kept to run, secure and debug the service.",
      ] },
      { id: "purposes", title: "Why we use it, and our legal bases", body: [
        "To provide the service you signed up for (contract): discovery, enrichment, scoring, CRM, delivery, billing and support.",
        "To keep the platform secure, prevent abuse and improve quality (legitimate interests).",
        "To process business contact data in our lead database (legitimate interests in B2B prospecting, balanced against the individual's rights; every contact can be suppressed, see Your rights).",
        "To meet legal obligations such as tax and accounting records.",
        "We do not sell personal data and we do not use workspace data to train third-party AI models.",
      ] },
      { id: "sharing", title: "Who receives data", body: ["We share data only with service providers that help us operate LeadX, listed on the Subprocessors page, and when the law requires it. When you enable an optional provider (email delivery, search, web extraction or AI writing), the data needed for that feature is sent to that provider."] },
      { id: "retention", title: "Retention", body: ["Workspace data is kept while your account is active. You can export it and ask us to delete it. Suppression records (people who opted out) are kept so that they are not contacted again. Logs and security events are kept for a limited period and then removed or anonymised."] },
      { id: "rights", title: "Your rights and opt-out", body: [
        "Depending on where you live you may have the right to access, correct, delete, restrict or object to our processing of your personal data, and to complain to your data-protection authority.",
        `If your business contact details appear in LeadX and you do not want to be contacted, write to ${LEGAL.email} with the address or number; we will add it to the global suppression list and stop delivering it to customers. Every outreach email must carry a working unsubscribe link, and unsubscribes are applied across workspaces.`,
        "Workspace administrators can erase a contact from their CRM; the erased person is also added to the opt-out list.",
      ] },
      { id: "transfers", title: "International transfers", body: ["Our providers may process data outside your country. Where required we rely on approved safeguards such as standard contractual clauses. The region of our database is listed on the Subprocessors page."] },
      { id: "security", title: "Security", body: ["We apply the controls described on our Security page, including workspace isolation, role-based access, encryption in transit, audit logging and rate limiting."] },
      { id: "cookies", title: "Cookies", body: ["We use only essential cookies for sign-in and security, and a theme preference. See the Cookie Notice."] },
      { id: "children", title: "Children", body: ["LeadX is a business service and is not directed at children under 16."] },
      { id: "changes", title: "Changes", body: [`We will update this page when our practices change and revise the date above (last updated ${LEGAL.updated}). Material changes are notified to workspace owners.`] },
    ],
  },
  terms: {
    path: "/terms", title: "Terms of Service", summary: "The agreement between LeadX and customers using the platform.",
    sections: [
      { id: "agreement", title: "Agreement", body: [`These terms are between ${who} and the person or organisation that creates a LeadX workspace ("you"). By creating an account or using LeadX you accept them. If you accept on behalf of an organisation, you confirm you can bind it.`] },
      { id: "service", title: "The service", body: ["LeadX discovers business leads from public and licensed sources, enriches and scores them, and provides CRM, automation and delivery tools. Data comes from third-party sources, so we do not guarantee that any record is complete, current or accurate, and we do not guarantee results such as replies or revenue."] },
      { id: "accounts", title: "Accounts and security", body: ["Keep your credentials confidential, use accurate information, and tell us promptly about unauthorised access. You are responsible for activity in your workspace, including that of invited teammates."] },
      { id: "use", title: "Acceptable use and outreach compliance", body: [
        "You must follow our Acceptable Use Policy.",
        "You are the sender of any message you send using LeadX and are responsible for lawful outreach: CAN-SPAM, TCPA, CASL, GDPR/ePrivacy (PECR) and similar laws, including having a lawful basis, honouring opt-outs, identifying yourself and including a postal address and unsubscribe mechanism.",
        "You must not use LeadX to contact people on a suppression list, to send unsolicited messages to consumers where consent is required, or to evade provider or legal limits.",
      ] },
      { id: "content", title: "Your data", body: ["You own the data you upload and the records in your workspace. You grant us the rights needed to host and process it to provide the service. Lead data we source remains subject to the source's terms and to applicable law; you may use delivered leads for your own business prospecting but not resell them as a database."] },
      { id: "billing", title: "Plans, billing and cancellation", body: [
        "Paid plans are subscriptions billed in advance, monthly or annually, through Stripe. Each plan includes a monthly allowance of qualified leads shown on the Pricing page; unused allowance does not roll over unless the plan says so.",
        "You can change or cancel your plan in the billing portal. Cancellation takes effect at the end of the paid period unless you choose immediate cancellation. Taxes are added where required.",
        "Leads reported as invalid through the in-app bad-lead report are credited back to your allowance after review. Other refunds are at our discretion except where the law requires otherwise.",
        "If a payment fails we may restrict outbound features after a grace period and will tell you by email.",
      ] },
      { id: "availability", title: "Availability and changes", body: ["We work to keep LeadX available but do not promise uninterrupted service. We may change features, and will give notice of material reductions to paid features."] },
      { id: "termination", title: "Suspension and termination", body: ["You can close your workspace at any time. We may suspend or terminate access for breach of these terms, legal risk or non-payment. After termination you can request an export of your data for a reasonable period."] },
      { id: "liability", title: "Warranties and liability", body: ["The service is provided 'as is'. To the extent the law allows, we exclude implied warranties and are not liable for indirect or consequential loss, and our total liability is limited to the fees you paid in the 12 months before the claim. Nothing limits liability that cannot be limited by law."] },
      { id: "law", title: "Governing law", body: [LEGAL.law ? `These terms are governed by the laws of ${LEGAL.law}.` : "Governing law and venue are set in your order form or, if none, by the laws of the jurisdiction where the LeadX operator is established. [Operator: set NEXT_PUBLIC_GOVERNING_LAW before launch.]"] },
      { id: "contact", title: "Contact", body: [`Questions about these terms: ${LEGAL.email}.`] },
    ],
  },
  dpa: {
    path: "/dpa", title: "Data Processing Addendum", summary: "How LeadX processes personal data on behalf of customers.",
    sections: [
      { id: "scope", title: "Scope and roles", body: ["This addendum applies when LeadX processes personal data that a customer loads into or generates in its workspace (customer CRM data, imported lists, notes). For that data the customer is the controller and LeadX is the processor.", "For the business-lead database that LeadX itself compiles and delivers, LeadX acts as an independent controller; see the Privacy Policy."] },
      { id: "instructions", title: "Processing instructions", body: ["LeadX processes customer data only to provide the service and as documented instructions in the product, these terms and the customer's configuration. We will tell the customer if we believe an instruction breaches data-protection law."] },
      { id: "details", title: "Subject matter, duration and data types", body: ["Subject matter: hosting and processing of CRM and outreach data. Duration: the subscription term plus the deletion period. Data subjects: the customer's prospects, customers and users. Data types: contact details, job titles, company information, communication history and notes."] },
      { id: "confidentiality", title: "Confidentiality and personnel", body: ["Personnel with access to customer data are bound by confidentiality and access is limited to what their role requires."] },
      { id: "security", title: "Security measures", body: ["We maintain the measures on the Security page, including tenant isolation enforced in the API and database, role-based access, encryption in transit, audit logging, rate limiting and secret management outside the application code."] },
      { id: "subprocessors", title: "Subprocessors", body: ["The customer authorises the subprocessors on the Subprocessors page. We will notify workspace owners of new subprocessors before they process customer data and the customer may object on reasonable grounds."] },
      { id: "rights", title: "Assistance with data-subject requests", body: ["We provide tools to export and erase a contact and to suppress future outreach, and will assist the customer with access, correction, deletion and objection requests within a reasonable time."] },
      { id: "breach", title: "Personal-data breaches", body: ["We will notify the customer without undue delay after becoming aware of a breach affecting its personal data and provide the information reasonably needed to meet legal notification duties."] },
      { id: "transfers", title: "International transfers", body: ["Where processing involves a transfer from the EEA, UK or Switzerland to a country without an adequacy decision, the parties rely on the standard contractual clauses (and the UK addendum where relevant), which are incorporated by reference."] },
      { id: "deletion", title: "Return and deletion", body: ["On termination we make customer data available for export and then delete it from active systems within a reasonable period, except where law requires retention. Backups age out in the normal cycle."] },
      { id: "audit", title: "Audits", body: ["On reasonable request, no more than once a year unless there is a breach, we provide information needed to demonstrate compliance and allow a reasonable audit under confidentiality."] },
    ],
  },
  security: {
    path: "/security", title: "Security", summary: "The controls LeadX applies today, and what we do not claim.",
    sections: [
      { id: "isolation", title: "Workspace isolation", body: ["Every request is authenticated and checked against the caller's active workspace membership. Database access is scoped by workspace in the API and Row Level Security policies restrict browser access. Platform-admin access uses a separate identity and is audit-logged."] },
      { id: "auth", title: "Authentication and sessions", body: ["Sign-in uses an email-verified identity provider. Browser sessions use HttpOnly cookies with CSRF protection; tokens are not exposed to page scripts. Admin routes reject workspace credentials."] },
      { id: "data", title: "Data protection", body: ["Traffic is encrypted in transit. Delivery credentials and integration secrets are encrypted with an application key held outside the database. Service-role keys never reach the browser."] },
      { id: "app", title: "Application safeguards", body: ["Input is validated at the API boundary; database access is parameterised. Outbound fetches and webhooks are screened against private and internal addresses (SSRF). Outgoing webhooks are signed with a per-endpoint secret and a timestamp. Rate limits protect login, API and pipeline endpoints. Imports are exported as spreadsheet-safe CSV."] },
      { id: "ops", title: "Operations", body: ["Security-relevant events and admin actions are logged. Dependencies are version-pinned. Production configuration is checked at start-up and the service refuses unsafe settings such as wildcard CORS."] },
      { id: "claims", title: "What we do not claim", body: ["LeadX does not currently hold SOC 2 or ISO 27001 certification, and we do not claim it. Penetration-test summaries are available on request once completed."] },
      { id: "disclosure", title: "Report a vulnerability", body: [`Email ${LEGAL.securityEmail} with details and steps to reproduce. Please do not access data that is not yours or disrupt the service. We acknowledge reports promptly and do not pursue good-faith research. See also /.well-known/security.txt.`] },
    ],
  },
  cookies: {
    path: "/cookies", title: "Cookie Notice", summary: "The few cookies LeadX sets and why.",
    sections: [
      { id: "essential", title: "Essential cookies", body: ["leadx_session (HttpOnly): keeps you signed in. leadx_refresh (HttpOnly): renews the session. leadx_csrf: protects state-changing requests against cross-site forgery. These are required for the service and cannot be switched off."] },
      { id: "prefs", title: "Preferences", body: ["Your light/dark theme and selected workspace are stored in your browser (local storage) so the app opens as you left it."] },
      { id: "none", title: "No advertising or tracking cookies", body: ["LeadX does not set advertising cookies and does not embed third-party analytics on the signed-in app. If we add analytics later we will update this notice and ask for consent where the law requires it."] },
      { id: "control", title: "Control", body: ["You can delete cookies in your browser at any time; you will be signed out."] },
    ],
  },
  "acceptable-use": {
    path: "/acceptable-use", title: "Acceptable Use Policy", summary: "What you may and may not do with LeadX.",
    sections: [
      { id: "allowed", title: "Intended use", body: ["LeadX is for lawful B2B prospecting, enrichment and CRM work."] },
      { id: "prohibited", title: "Not allowed", body: [
        "Sending spam, deceptive or misleading messages, or messages without a working unsubscribe and sender identification.",
        "Contacting anyone on a suppression list, or after they have opted out or asked you to stop.",
        "Using purchased or scraped consumer lists, or collecting sensitive personal data (health, biometric, children's data, government identifiers).",
        "Harassment, fraud, impersonation, or any unlawful activity, including breaching telemarketing and anti-spam laws.",
        "Probing, scanning or attacking the service, bypassing rate limits or tenant boundaries, or reverse engineering it.",
        "Reselling delivered leads as a database or sharing exclusive leads with other parties.",
        "Using the platform to fetch internal or private network addresses.",
      ] },
      { id: "enforcement", title: "Enforcement", body: ["We may throttle, suspend or terminate accounts that breach this policy, and we may report unlawful activity. Bounce, complaint and unsubscribe rates are monitored to protect shared sending reputation."] },
    ],
  },
  subprocessors: {
    path: "/subprocessors", title: "Subprocessors", summary: "Providers that may process data on LeadX's behalf.",
    sections: [
      { id: "core", title: "Core infrastructure", body: ["Supabase: database, authentication and file storage (region is chosen by the operator at project creation).", "Stripe: payment processing and subscription billing. Card data is handled by Stripe and never stored by LeadX.", "Application hosting provider: runs the API, worker and portal [operator: name your host and region before launch]."] },
      { id: "optional", title: "Optional providers (only if enabled for your workspace)", body: ["Amazon Web Services SES: email delivery and bounce/complaint handling.", "Serper: web search results used to find business websites.", "Firecrawl: page extraction when free extraction fails.", "AI providers (OpenRouter, OpenAI, Anthropic, Groq): drafting and parsing features, only when an AI key is configured.", "Contact-data and verification providers you add yourself (for example Hunter, Apollo, ZeroBounce)."] },
      { id: "changes", title: "Changes", body: ["We notify workspace owners before adding a subprocessor that will process customer data."] },
    ],
  },
};
