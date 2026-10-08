/**
 * The Help section's content, as data.
 *
 * Deliberately not JSX and not HTML strings. Three reasons, in order of how
 * much they cost when ignored:
 *
 * 1. The ⌘K palette searches these articles, and a search needs plain text to
 *    match against — a title buried in a component is unsearchable.
 * 2. An in-app destination has to be a real `<Link>` so the router handles it;
 *    an `<a href>` inside an HTML string reloads the whole app, and this repo
 *    already has a `dangerouslySetInnerHTML` load-order trap on record.
 * 3. Every article is one object, so "is this still true?" is answerable by
 *    reading one file rather than nine components.
 *
 * **Every sentence here must be true of THIS build.** Nothing below names a
 * button, a field or a channel that does not exist: the labels are the labels
 * the components render, the severities are the ones the hub emits, and the
 * score bands are the ones `payloads/security.py` computes. If a surface
 * changes, this file changes with it.
 */

/** One paragraph, list, aside or in-app destination inside an article. */
export type HelpBlock =
  /** A paragraph of prose. */
  | { kind: "p"; text: string }
  /** A numbered do-this-then-that list. Rendered as `<ol>`. */
  | { kind: "steps"; items: string[] }
  /** An unordered list of facts. Rendered as `<ul>`. */
  | { kind: "bullets"; items: string[] }
  /** A caveat worth pulling out of the flow. */
  | { kind: "note"; text: string }
  /** A real in-app route, rendered as a router `<Link>` so it never reloads
   *  the app. `note` is the one-line reason to go there. */
  | { kind: "where"; to: string; label: string; note?: string };

export type HelpArticle = {
  /** URL segment: `/help/<slug>`. Stable — it is a bookmarkable address. */
  slug: string;
  title: string;
  /** One sentence, shown on the index card and as the palette row's detail. */
  summary: string;
  /** Extra words a person might actually type into ⌘K. Needed because title
   *  matching alone fails the obvious cases: someone searching "enrol code"
   *  must find the article titled "Where to find your enrolment code", and
   *  `"enrolment code".includes("enrol code")` is false. */
  keywords: string[];
  /** The question "Ask Vault about this" sends. Phrased as the reader would
   *  ask it, so Vault answers about their own site rather than restating
   *  the article. */
  ask: string;
  blocks: HelpBlock[];
};

export const HELP_ARTICLES: HelpArticle[] = [
  {
    slug: "what-guardian-watches",
    title: "What Vault watches, and what it does not",
    summary: "The four things a sensor reports on, and the boundaries worth knowing before you rely on it.",
    keywords: ["what does guardian do", "scope", "coverage", "limits", "privacy", "what is watched"],
    ask: "What is Vault watching on my site right now, and is anything not covered?",
    blocks: [
      {
        kind: "p",
        text:
          "Vault watches from the inside. A sensor runs on a machine you own, on the network you " +
          "want watched, and reports what it can see from there. Everything in the app is built from " +
          "those reports.",
      },
      {
        kind: "bullets",
        items: [
          "Websites — whether each one answers, when its certificate expires, whether it redirects to " +
            "HTTPS, and which security headers it sets.",
          "Servers — disk, memory and load against your thresholds, plus seven hardening checks: " +
            "key-only SSH, firewall, fail2ban, pending security updates, auto-updates, signs of " +
            "malware, and a pending reboot.",
          "Devices on the network — what is connected, what is new or unrecognised, which ports " +
            "answer, and which devices come and go.",
          "The connection itself — latency, speed tests you ask for, and DNS lookups when the DNS " +
            "filter is in use.",
        ],
      },
      { kind: "where", to: "/overview", label: "Overview", note: "the one-screen answer to “is everything fine?”" },
      {
        kind: "p",
        text:
          "What it does not do matters just as much. A sensor only ever sees the network it is " +
          "installed on — a second network needs its own sensor, or it is simply invisible. Vault " +
          "is not antivirus: it does not run on every phone and laptop, and it cannot see inside " +
          "another device. It records which names were looked up and which ports answered, not the " +
          "contents of anything.",
      },
      {
        kind: "note",
        text:
          "Nothing is watched retroactively. Vault knows about a site, a server or a network from " +
          "the moment it is configured, not before.",
      },
    ],
  },

  {
    slug: "add-a-network",
    title: "How to add another network",
    summary: "Declare the network, then give it a sensor — a network with no sensor reports nothing.",
    keywords: ["add network", "second network", "another network", "subnet", "vlan", "guest wifi", "new network"],
    ask: "How should I set up a second network here, and does it need its own sensor?",
    blocks: [
      {
        kind: "p",
        text:
          "A network is a place Vault watches: your main Wi-Fi, a guest network, an office VLAN. " +
          "Adding one is two jobs — describing it, then putting a sensor on it.",
      },
      { kind: "where", to: "/settings/networks", label: "Config → Networks", note: "where both jobs happen" },
      {
        kind: "steps",
        items: [
          "Press Add network.",
          "Fill in Name (what you will call it), Kind, Subnet — the first three octets, like 192.168.1 — " +
            "Gateway, like 192.168.1.1, and SSID if it is a Wi-Fi network.",
          "Press Save. The network appears in the table above with no sensor yet.",
          "In that row, use the sensor picker to attach a sensor that can reach it. If you have no " +
            "spare sensor, the same page will generate an enrol code and show you the install command.",
        ],
      },
      {
        kind: "note",
        text:
          "Only an admin can add, edit or delete a network. An operator or viewer sees the table and " +
          "the button is disabled for them.",
      },
      {
        kind: "p",
        text:
          "A sensor only ever sees the network it is installed on, so a network without one stays " +
          "empty — it is not a mistake you will get an error about. Deleting a network asks where its " +
          "devices, alerts and history should move to, and your primary network cannot be deleted at " +
          "all.",
      },
      { kind: "where", to: "/help/add-a-sensor", label: "How to add another sensor", note: "the other half of this job" },
    ],
  },

  {
    slug: "add-a-sensor",
    title: "How to add another sensor",
    summary: "Generate an enrol code, run one command on a machine that sits on the network, assign it.",
    keywords: ["add sensor", "install sensor", "second sensor", "new sensor", "agent", "probe", "install command"],
    ask: "Which sensors do I have, and what would a second one add?",
    blocks: [
      {
        kind: "p",
        text:
          "A sensor is a small service on a machine you already own. It watches from where it sits, so " +
          "put it on a machine that stays on and sits on the network you want watched.",
      },
      { kind: "where", to: "/settings/sensors", label: "Config → Sensors", note: "the sensor table and the enrol code" },
      {
        kind: "steps",
        items: [
          "Press Generate enrol code. The page shows the code, a countdown to its expiry, and the exact " +
            "install command.",
          "Press Copy command and run it on the machine that will host the sensor, while the code is " +
            "still live.",
          "On Linux, run it with sudo — it installs a systemd service. On macOS it installs a launchd " +
            "agent and needs no sudo.",
          "The sensor appears in the table within a minute or so. Attach it to the right network in " +
            "Config → Networks.",
        ],
      },
      {
        kind: "note",
        text:
          "macOS and Linux are the two platforms offered. There is no Docker image and no Windows " +
          "installer — the picker only lists platforms that really have one.",
      },
      {
        kind: "p",
        text:
          "The command downloads the installer from this dashboard's own address and nowhere else, and " +
          "the enrol code is the only secret involved. A sensor that is no longer wanted is retired " +
          "with Revoke in its row; it stops being trusted immediately.",
      },
      { kind: "where", to: "/help/enrolment-code", label: "Where to find your enrolment code", note: "if the code has expired" },
    ],
  },

  {
    slug: "enrolment-code",
    title: "Where to find your enrolment code",
    summary: "It is generated on demand from Config → Sensors, lives for a few minutes, and is used once.",
    keywords: [
      "enrol code", "enroll code", "enrolment code", "enrollment code", "sensor code",
      "join code", "install code", "code expired", "where is my code",
    ],
    ask: "Do I have a live enrol code, and how do I get a new one?",
    blocks: [
      {
        kind: "p",
        text:
          "There is no permanent code to look up. Vault mints one when you ask, and it expires " +
          "shortly afterwards — so the answer to “where is my enrol code?” is always “generate a fresh " +
          "one now”.",
      },
      { kind: "where", to: "/settings/sensors", label: "Config → Sensors", note: "press Generate enrol code" },
      {
        kind: "bullets",
        items: [
          "The code appears with a countdown. Once that reaches zero it will not enrol anything — " +
            "generate another, it costs nothing.",
          "Underneath it is the full install command with the code already in it, and a Copy command " +
            "button. That command is the only place the code needs to go.",
          "Config → Networks offers the same thing per network, under “Install a sensor for …”, when " +
            "you are setting a network up.",
          "The first-run wizard mints one for you on its install step, so a brand-new site never has " +
            "to go looking.",
        ],
      },
      {
        kind: "note",
        text:
          "A code enrols a sensor; it is not a login and not a password. Treat it as short-lived either " +
          "way — anyone who runs it before it expires can enrol a sensor onto your site.",
      },
      { kind: "where", to: "/settings/networks", label: "Config → Networks", note: "the per-network version" },
    ],
  },

  {
    slug: "alerts-and-severities",
    title: "What the severities mean, and what each action does",
    summary: "CRIT, WARN and INFO — and the difference between Ack, Resolve, Snooze and Fix.",
    keywords: [
      "severity", "crit", "critical", "warn", "warning", "info", "acknowledge", "ack",
      "resolve", "snooze", "reopen", "fix", "alert actions", "inbox",
    ],
    ask: "Which of my alerts actually need me right now, and why?",
    blocks: [
      { kind: "where", to: "/inbox?filter=alerts", label: "Inbox → Alerts", note: "every open alert, newest first" },
      {
        kind: "bullets",
        items: [
          "CRIT — something is broken or exposed now. A critical alert also raises a toast in the app " +
            "the moment it arrives and is counted on the Vault chat button.",
          "WARN — heading the wrong way, or failing a check that is not an emergency: a certificate " +
            "getting close to expiry, a disk over your threshold.",
          "INFO — worth recording, not worth interrupting you for.",
        ],
      },
      {
        kind: "p",
        text:
          "Each row also carries how sure Vault is that the finding is real, as a percentage badge. " +
          "A low number is a hint to read the evidence before acting on it.",
      },
      {
        kind: "bullets",
        items: [
          "Ack — “I have seen this.” The alert stops counting as unread. Nothing on your network " +
            "changes.",
          "Snooze — hides it for eight hours. It comes back if the condition is still true.",
          "Resolve — closes it. Use it when you fixed the cause yourself.",
          "Reopen — puts a closed alert back, when it turns out not to be resolved.",
          "Fix — appears only when Vault can carry out the repair itself, right now, on a machine " +
            "it has fixed before. It runs one specific action, and says which.",
          "Ask Vault — appears when the remedy needs you. It opens the chat with Vault already " +
            "explaining what it can and cannot do about that alert.",
        ],
      },
      {
        kind: "note",
        text:
          "These buttons need the operator or admin role. A viewer sees every alert and every " +
          "explanation, and no action buttons — reading is never restricted, only changing.",
      },
      {
        kind: "p",
        text:
          "When a repair is more consequential than Fix allows, Vault does not offer a one-click " +
          "button for it. It proposes it in chat and waits for an explicit yes, once, for that exact " +
          "action.",
      },
    ],
  },

  {
    slug: "security-score",
    title: "How to read the security score",
    summary: "Where the number comes from, what the letter grades mean, and which checks moved it.",
    keywords: ["score", "grade", "scorecard", "a b c d f", "deductions", "why is my score low", "security score"],
    ask: "Why is my security score what it is, and what would raise it most?",
    blocks: [
      {
        kind: "p",
        text:
          "The big number on Overview is the score from the latest scan, with the letter grade and the " +
          "three biggest deductions that produced it, plus a thirty-day trend so you can see which way " +
          "it is going.",
      },
      { kind: "where", to: "/overview", label: "Overview", note: "the score, the trend and today's deductions" },
      { kind: "where", to: "/security/scorecards", label: "Security → Scorecards", note: "the same idea, per target" },
      {
        kind: "p",
        text:
          "A scorecard is a pass/fail count turned into a percentage — pass every check and you get " +
          "100. Each card lists the three checks that cost the most, largest first, so the top line is " +
          "always the thing worth doing next.",
      },
      {
        kind: "bullets",
        items: [
          "A website is scored on six equally-weighted checks: HSTS, X-Content-Type-Options, " +
            "X-Frame-Options, CSP, an HTTPS redirect, and modern TLS.",
          "A server is scored on its seven hardening checks: key-only SSH, firewall, fail2ban, pending " +
            "security updates, auto-updates, signs of malware, and a pending reboot.",
          "The home network is scored on six: Wi-Fi encryption, no evil twin, DHCP integrity, gateway " +
            "integrity, no exposed ports, and every device verified.",
        ],
      },
      {
        kind: "bullets",
        items: [
          "A is 90 or above.",
          "B is 80 to 89.",
          "C is 70 to 79.",
          "D is 60 to 69.",
          "F is below 60.",
        ],
      },
      {
        kind: "note",
        text:
          "The score measures what Vault can check, not everything that matters. A perfect grade " +
          "means every check it runs passed — it is not a claim that nothing is wrong.",
      },
    ],
  },

  {
    slug: "notifications",
    title: "Notifications: what gets sent, and where",
    summary: "Pick channels, choose which severities each one receives, and know what a maintenance window silences.",
    keywords: [
      "notifications", "alerts by email", "ntfy", "slack", "discord", "telegram", "teams",
      "webhook", "pagerduty", "home assistant", "push", "quiet hours", "maintenance",
    ],
    ask: "Where are my alerts being sent, and would a critical one reach me right now?",
    blocks: [
      {
        kind: "p",
        text:
          "Vault sends alerts out to channels you connect. Nothing is sent anywhere until you " +
          "connect one.",
      },
      { kind: "where", to: "/settings/integrations", label: "Config → Integrations", note: "connect and test a channel" },
      {
        kind: "bullets",
        items: [
          "Push and chat: ntfy, Slack, Discord, Telegram, Microsoft Teams.",
          "On-call and automation: PagerDuty, Home Assistant, a plain webhook.",
          "Tickets: Jira and ServiceNow.",
        ],
      },
      {
        kind: "p",
        text:
          "Each connected channel chooses which severities it receives. A channel that is not ticked " +
          "for a severity simply never hears about it — which is how you get pages for CRIT and a " +
          "quiet chat room for INFO. Each one has a test, so you find out it works before you need it " +
          "to.",
      },
      {
        kind: "p",
        text:
          "What counts as an alert in the first place is a separate setting: your thresholds decide " +
          "when a disk, a memory figure, a load average, a certificate's remaining days or a latency " +
          "reading is worth raising.",
      },
      { kind: "where", to: "/settings/thresholds", label: "Config → Alert thresholds", note: "when a warning is raised at all" },
      {
        kind: "note",
        text:
          "Starting a maintenance window from the top bar stops delivery while it runs. Alerts are " +
          "still detected and still recorded — you will see them in Inbox — they just are not sent " +
          "out until the window ends.",
      },
    ],
  },

  {
    slug: "reports",
    title: "Reports: building one, sharing one, scheduling one",
    summary: "Build from a preset, share with an expiring link, or have one arrive on a schedule.",
    keywords: ["report", "pdf", "print", "share link", "schedule report", "weekly report", "export", "compliance"],
    ask: "What would a report for the last week say about my site?",
    blocks: [
      { kind: "where", to: "/reports/builder", label: "Reports → Builder", note: "compose one and watch the preview" },
      {
        kind: "steps",
        items: [
          "Pick a Preset — it selects a sensible set of sections for you.",
          "Adjust Sections, set the Period (or Custom days), and give it a Title.",
          "Check the live preview beside the form; it re-renders as you change things.",
          "Press Build report. The finished report lands in the Library.",
        ],
      },
      { kind: "where", to: "/reports/library", label: "Reports → Library", note: "every report you have built" },
      {
        kind: "bullets",
        items: [
          "Open reads it in the app.",
          "Print opens the printable version in a new tab.",
          "Share creates a link for someone without an account, and you choose how long it stays valid.",
          "Delete removes it.",
        ],
      },
      { kind: "where", to: "/reports/schedule", label: "Reports → Schedule", note: "have one built for you, regularly" },
      {
        kind: "p",
        text:
          "A schedule picks the template and the hour it runs. It can also push each new report's link " +
          "to the channels you already use for alerts, so the report arrives where you are rather than " +
          "waiting for you to come and find it.",
      },
      {
        kind: "note",
        text:
          "Sharing and deleting are role-gated: if a button is disabled, hovering it says which role it " +
          "needs.",
      },
    ],
  },
];

/** One article by slug, or `undefined` for an unknown one. */
export function helpArticle(slug: string): HelpArticle | undefined {
  return HELP_ARTICLES.find((a) => a.slug === slug);
}

/** Every word of an article as one lowercase string: title, summary, keywords
 *  and the text of every block. This is what search matches against, so a
 *  reader who remembers a phrase from the body — not the heading — still finds
 *  it. */
export function helpSearchText(article: HelpArticle): string {
  const parts: string[] = [article.title, article.summary, ...article.keywords];
  for (const block of article.blocks) {
    if (block.kind === "p" || block.kind === "note") parts.push(block.text);
    else if (block.kind === "steps" || block.kind === "bullets") parts.push(...block.items);
    else parts.push(block.label, block.note ?? "");
  }
  return parts.join(" ").toLowerCase();
}

/** True when `query` should surface `article`.
 *
 *  Every whitespace-separated word of the query has to appear somewhere in the
 *  article's text — not the query as one substring. That is what makes the
 *  obvious search work: "enrol code" matches "Where to find your **enrol**ment
 *  **code**", which a plain `includes` of the whole phrase does not. */
export function helpMatches(article: HelpArticle, query: string): boolean {
  const words = query.toLowerCase().split(/\s+/).filter(Boolean);
  if (words.length === 0) return false;
  const haystack = helpSearchText(article);
  return words.every((word) => haystack.includes(word));
}

/** The articles `query` should surface, in their authored order. */
export function searchHelp(query: string): HelpArticle[] {
  return HELP_ARTICLES.filter((a) => helpMatches(a, query));
}
