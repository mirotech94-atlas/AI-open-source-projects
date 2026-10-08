import { useState } from "react";
import type { FormEvent } from "react";
import { Link, useParams } from "react-router-dom";
import { EmptyState } from "../components/EmptyState";
import { Page, PageHeader, StatStrip } from "../design";
import { HELP_ARTICLES, helpArticle } from "../help/articles";
import type { HelpArticle, HelpBlock } from "../help/articles";
import { useAskGuardian } from "../shell/chat/handoff";
import "../help/help.css";

/** Guides grouped by what the reader is trying to do — the index reads as
 *  "I want to…", not as a table of contents in authoring order. A slug not
 *  listed here lands in the last group, so a new article is never hidden. */
const GROUPS: { title: string; lead: string; slugs: string[] }[] = [
  {
    title: "Setting up",
    lead: "Networks, sensors and the code that ties them together.",
    slugs: ["add-a-network", "add-a-sensor", "enrolment-code"],
  },
  {
    title: "Reading what Vault tells you",
    lead: "What it watches, what the colours mean, and what each button does.",
    slugs: ["what-guardian-watches", "alerts-and-severities", "security-score"],
  },
  {
    title: "Getting it to you",
    lead: "Alerts where you already are, and reports you can hand to someone.",
    slugs: ["notifications", "reports"],
  },
];

/** "3 steps · opens Inbox" — derived from the article's own blocks, so the
 *  index tells the reader what kind of guide it is before they open it. */
function meta(article: HelpArticle): string[] {
  const out: string[] = [];
  const steps = article.blocks.filter((b): b is Extract<HelpBlock, { kind: "steps" }> => b.kind === "steps");
  const n = steps.reduce((acc, b) => acc + b.items.length, 0);
  if (n > 0) out.push(`${n} steps`);
  else out.push("explainer");
  const where = article.blocks.find((b): b is Extract<HelpBlock, { kind: "where" }> => b.kind === "where");
  if (where) out.push(`opens ${where.label}`);
  return out;
}

/** "CRIT — something is broken" reads as a definition: set the term. */
function Term({ text }: { text: string }) {
  const i = text.indexOf(" — ");
  if (i <= 0 || i > 40) return <>{text}</>;
  return (
    <>
      <strong>{text.slice(0, i)}</strong>
      {text.slice(i)}
    </>
  );
}

/** One block of an article. Every in-app destination is a router `<Link>`, so
 *  following one never reloads the app. `where` blocks are rendered in the
 *  article's rail, not in the flow — see `ArticleView`. */
function Block({ block }: { block: HelpBlock }) {
  switch (block.kind) {
    case "p":
      return <p className="g-help-p">{block.text}</p>;
    case "steps":
      return (
        <ol className="g-help-steps">
          {block.items.map((item) => (
            <li key={item}>{item}</li>
          ))}
        </ol>
      );
    case "bullets":
      return (
        <ul className="g-help-bullets">
          {block.items.map((item) => (
            <li key={item}>
              <Term text={item} />
            </li>
          ))}
        </ul>
      );
    case "note":
      return (
        <aside className="g-help-note">
          <span className="g-help-note-label">Worth knowing</span>
          <p>{block.text}</p>
        </aside>
      );
    case "where":
      return null;
  }
}

/** The "Ask Vault about this" action every article ends with. It opens the
 *  chat panel and asks the article's own question, so the reader gets the
 *  answer for *their* site rather than the general case. */
function AskAboutThis({ article }: { article: HelpArticle }) {
  const ask = useAskGuardian();
  return (
    <div className="g-help-ask">
      <p className="g-help-ask-q">“{article.ask}”</p>
      <button type="button" className="g-help-ask-btn" onClick={() => ask(article.ask)}>
        Ask Vault about this
      </button>
      <p className="hint">Answered from your latest scan, not from the guide.</p>
    </div>
  );
}

function ArticleView({ article }: { article: HelpArticle }) {
  const idx = HELP_ARTICLES.findIndex((a) => a.slug === article.slug);
  const prev = idx > 0 ? HELP_ARTICLES[idx - 1] : null;
  const next = idx >= 0 && idx < HELP_ARTICLES.length - 1 ? HELP_ARTICLES[idx + 1] : null;
  const wheres = article.blocks.filter((b): b is Extract<HelpBlock, { kind: "where" }> => b.kind === "where");
  return (
    <article className="g-help-article">
      <header className="g-help-article-head">
        <p className="g-help-crumb">
          <Link to="/help">All guides</Link>
        </p>
        <h1>{article.title}</h1>
        <p className="g-help-summary">{article.summary}</p>
      </header>

      <div className="g-help-article-body">
        <div className="g-help-prose">
          {article.blocks.map((block, i) => (
            // Index keys: the block list for an article is authored, static and
            // never reordered at runtime, so there is no identity to preserve.
            <Block key={i} block={block} />
          ))}
        </div>

        <aside className="g-help-rail" aria-label="About this guide">
          {wheres.length > 0 && (
            <section className="g-help-rail-block">
              <h2>Where in Vault</h2>
              {wheres.map((w) => (
                <p key={w.to} className="g-help-where">
                  <Link to={w.to}>{w.label}</Link>
                  {w.note !== undefined && <span className="hint">{w.note}</span>}
                </p>
              ))}
            </section>
          )}
          <section className="g-help-rail-block">
            <h2>Your site, not the manual</h2>
            <AskAboutThis article={article} />
          </section>
        </aside>
      </div>

      <nav className="g-help-pager" aria-label="Other guides">
        {prev ? (
          <Link to={`/help/${prev.slug}`} className="g-help-pager-prev">
            <span className="hint">Previous</span>
            <span>{prev.title}</span>
          </Link>
        ) : (
          <span />
        )}
        {next ? (
          <Link to={`/help/${next.slug}`} className="g-help-pager-next">
            <span className="hint">Next</span>
            <span>{next.title}</span>
          </Link>
        ) : (
          <span />
        )}
      </nav>
    </article>
  );
}

/** The hero: Vault is the help system. A question typed here goes to the
 *  chat panel, answered from the reader's own scan. */
function AskBox() {
  const ask = useAskGuardian();
  const [q, setQ] = useState("");
  function submit(e: FormEvent) {
    e.preventDefault();
    const text = q.trim();
    if (!text) return;
    ask(text);
    setQ("");
  }
  return (
    <form className="g-help-askbox" onSubmit={submit}>
      <label htmlFor="g-help-askbox-input">Ask Vault about your own site</label>
      <div className="g-help-askbox-row">
        <input
          id="g-help-askbox-input"
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder="Why is the break room TV red? · What changed overnight? · Is anything down?"
          autoComplete="off"
        />
        <button type="submit">Ask</button>
      </div>
      <p className="hint">It answers from your latest scan. The guides below explain the general case.</p>
    </form>
  );
}

function Entry({ article }: { article: HelpArticle }) {
  return (
    <li className="g-help-entry">
      <h3 className="g-help-entry-title">
        <Link to={`/help/${article.slug}`}>{article.title}</Link>
      </h3>
      <p className="g-help-entry-summary">{article.summary}</p>
      <p className="g-help-entry-meta">{meta(article).join(" · ")}</p>
    </li>
  );
}

function Index() {
  const bySlug = new Map(HELP_ARTICLES.map((a) => [a.slug, a]));
  const placed = new Set(GROUPS.flatMap((g) => g.slugs));
  const rest = HELP_ARTICLES.filter((a) => !placed.has(a.slug));
  return (
    <Page>
      <PageHeader
        eyebrow="Account · how to do things"
        title="Help & API"
        sub="Short, task-shaped guides, and the API for reading your own site from your own scripts. Everything here reads fine with your network down; ⌘K finds these too."
      />
      <StatStrip items={[
        { label: "Guides", value: String(HELP_ARTICLES.length) },
        { label: "Sections", value: String(GROUPS.length + 1) },
      ]} />
    <div className="g-help-index">

      <AskBox />

      {GROUPS.map((g) => {
        const items = g.slugs.map((s) => bySlug.get(s)).filter((a): a is HelpArticle => a !== undefined);
        if (items.length === 0) return null;
        return (
          <section key={g.title} className="g-help-group" aria-labelledby={`g-help-group-${g.slugs[0]}`}>
            <div className="g-help-group-head">
              <h2 id={`g-help-group-${g.slugs[0]}`}>{g.title}</h2>
              <p className="hint">{g.lead}</p>
            </div>
            <ul className="g-help-entries">
              {items.map((a) => (
                <Entry key={a.slug} article={a} />
              ))}
            </ul>
          </section>
        );
      })}

      {rest.length > 0 && (
        <section className="g-help-group" aria-labelledby="g-help-group-more">
          <div className="g-help-group-head">
            <h2 id="g-help-group-more">More guides</h2>
          </div>
          <ul className="g-help-entries">
            {rest.map((a) => (
              <Entry key={a.slug} article={a} />
            ))}
          </ul>
        </section>
      )}

      <section className="g-help-group" aria-labelledby="g-help-group-dev">
        <div className="g-help-group-head">
          <h2 id="g-help-group-dev">For developers</h2>
          <p className="hint">Read your own site's state from your own scripts.</p>
        </div>
        <ul className="g-help-entries">
          <li className="g-help-entry">
            <h3 className="g-help-entry-title">
              <Link to="/help/api">API guide</Link>
            </h3>
            <p className="g-help-entry-summary">
              Vault's public API, with every endpoint it serves today and the token it needs.
            </p>
            <p className="g-help-entry-meta">reference</p>
          </li>
        </ul>
      </section>
    </div>
    </Page>
  );
}

/**
 * The Help section: an index of guides at `/help`, one guide per route at
 * `/help/<slug>`. The content itself lives in `help/articles.ts` as data — see
 * that file's header for why it is not JSX.
 */
export function Help() {
  const { slug } = useParams<{ slug?: string }>();
  if (slug === undefined || slug === "") return <Index />;
  const article = helpArticle(slug);
  if (article === undefined) {
    return (
      <EmptyState
        title="No such guide."
        body="It may have been renamed. Every guide is listed on the Help page."
        action={{ label: "All guides", href: "/help" }}
      />
    );
  }
  return <ArticleView article={article} />;
}

export default Help;
