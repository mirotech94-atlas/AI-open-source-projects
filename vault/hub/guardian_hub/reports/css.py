"""Report print CSS -- a port of Nexus's inline `css` string
(`~/.claude/nexus-agent/ui/server.py:225-234`): navy/blue + white, inline
only (no external assets), with an `@page` print rule so a browser
print-preview holds the same layout as the screen render.
"""

REPORT_CSS = """
@font-face{font-family:"Manrope";font-style:normal;font-weight:400 800;font-display:swap;
  src:url("/fonts/manrope-latin-var.woff2") format("woff2")}
@font-face{font-family:"IBM Plex Mono";font-style:normal;font-weight:400;font-display:swap;
  src:url("/fonts/ibm-plex-mono-latin-400.woff2") format("woff2")}
@font-face{font-family:"IBM Plex Mono";font-style:normal;font-weight:600;font-display:swap;
  src:url("/fonts/ibm-plex-mono-latin-600.woff2") format("woff2")}
@page{margin:16mm 15mm 18mm}
:root{--ink:#14202e;--ink-soft:#4b5563;--muted:#6b7280;--faint:#9ca3af;--line:#e3e8ef;--line-strong:#c9d2de;
  --navy:#0b3d91;--paper:#fff;--inset:#f6f8fb;--ok:#166534;--ok-bg:#dcfce7;--warn:#92400e;--warn-bg:#fef3c7;
  --bad:#991b1b;--bad-bg:#fee2e2}
*{box-sizing:border-box}
body{margin:0;background:var(--paper);color:var(--ink);font-family:"Manrope",-apple-system,"Segoe UI",Roboto,sans-serif;
  font-size:12.5px;line-height:1.5;-webkit-font-smoothing:antialiased}
.page{max-width:900px;margin:0 auto;padding:40px 44px 56px}
.muted{color:var(--muted)}
.mono,.kpi .v,.count,.toc-n,.num,td.n{font-family:"IBM Plex Mono",ui-monospace,Menlo,monospace;font-variant-numeric:tabular-nums}

/* Cover: a title page, not a header strip. */
.cover{padding:8px 0 28px;margin-bottom:28px;border-bottom:2px solid var(--ink)}
.cover .brand{display:flex;align-items:baseline;gap:12px;margin-bottom:34px;color:var(--muted);font-size:12px}
.cover .brand b{font-family:"IBM Plex Mono",monospace;font-weight:600;font-size:13px;letter-spacing:.14em;color:var(--navy)}
.cover h1{font-size:30px;font-weight:700;letter-spacing:-.02em;line-height:1.15;margin:0 0 26px;max-width:22ch}
.cover-meta{margin:0;display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:12px 32px}
.cover-meta div{display:flex;flex-direction:column;gap:2px}
.cover-meta dt{font-family:"IBM Plex Mono",monospace;font-size:10px;letter-spacing:.12em;text-transform:uppercase;color:var(--faint)}
.cover-meta dd{margin:0;font-size:13px}

/* Contents. */
.toc{margin:0 0 34px;padding:18px 22px;background:var(--inset);border:1px solid var(--line);border-radius:6px}
.toc h2{margin:0 0 10px;font-size:11px;letter-spacing:.12em;text-transform:uppercase;color:var(--muted)}
.toc ol{list-style:none;margin:0;padding:0;columns:2;column-gap:32px}
.toc li{break-inside:avoid;margin:0 0 4px}
.toc a{display:flex;gap:10px;color:var(--ink);text-decoration:none;font-size:12.5px}
.toc a:hover{color:var(--navy)}
.toc-n{color:var(--faint);font-size:11px;width:22px;flex:0 0 auto;padding-top:1px}

/* Parts and sections. */
.part{margin:38px 0 14px;padding-top:14px;border-top:1px solid var(--line-strong);display:flex;align-items:baseline;gap:12px}
.part-n{font-family:"IBM Plex Mono",monospace;font-size:10.5px;letter-spacing:.14em;text-transform:uppercase;color:var(--navy)}
.part-t{font-size:16px;font-weight:700;letter-spacing:-.01em}
.sec{margin:0 0 30px}
.sec h2{font-size:15.5px;font-weight:700;margin:0 0 12px;padding-bottom:8px;border-bottom:1px solid var(--line);letter-spacing:-.005em}
.sec h2 .num{display:inline-block;width:34px;color:var(--faint);font-weight:600;font-size:12px}
.sec h3{font-size:10.5px;text-transform:uppercase;letter-spacing:.1em;color:var(--muted);margin:18px 0 8px;font-weight:700}
.sec h3 .count{margin-left:6px;color:var(--ink);font-size:12px}
.netblock{margin:0 0 18px}
.lede{font-size:16px;line-height:1.5;font-weight:600;letter-spacing:-.005em;margin:0 0 16px;max-width:60ch}
.panels{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin:0 0 16px}
.panel{border:1px solid var(--line);border-radius:6px;padding:12px 14px}
.panel h3{margin:0 0 8px}
.panel.decide{border-color:#f1d5c4;background:#fdf6f2}.panel.decide h3{color:var(--bad)}
.panel.handled{border-color:#cfe3d5;background:#f2f8f4}.panel.handled h3{color:var(--ok)}
.plain{list-style:none;margin:0;padding:0;display:flex;flex-direction:column;gap:6px;font-size:12.5px}
.plain li b{font-family:"IBM Plex Mono",monospace;font-weight:600}

/* Figures. */
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px;margin:0 0 10px}
.kpi{border:1px solid var(--line);border-radius:6px;padding:10px 12px;border-left:3px solid var(--faint);background:var(--paper)}
.kpi.ok{border-left-color:#16a34a}.kpi.warn{border-left-color:#d99a00}.kpi.bad{border-left-color:#dc2626}
.kpi .k{font-size:10px;color:var(--muted);text-transform:uppercase;letter-spacing:.1em;font-weight:700}
.kpi .v{font-size:22px;font-weight:600;margin-top:3px;color:var(--ink);line-height:1.1}
.kpi .s{font-size:11px;color:var(--muted);margin-top:3px}

/* Tables: one rule under the head, hairlines between rows, no zebra. */
table{width:100%;border-collapse:collapse;margin:4px 0 8px}
th{text-align:left;font-family:"IBM Plex Mono",monospace;font-size:10px;color:var(--muted);font-weight:600;text-transform:uppercase;
  letter-spacing:.1em;padding:7px 8px;border-bottom:1px solid var(--line-strong);background:var(--inset)}
td{padding:7px 8px;border-bottom:1px solid var(--line);vertical-align:top;font-size:12px}
table.small td,table.small th{font-size:11px;padding:5px 6px}
.pill{display:inline-block;padding:1px 7px;border-radius:3px;font-family:"IBM Plex Mono",monospace;font-size:10px;font-weight:600;
  letter-spacing:.04em;text-transform:uppercase}
.pill.ok{background:var(--ok-bg);color:var(--ok)}.pill.warn{background:var(--warn-bg);color:var(--warn)}
.pill.bad{background:var(--bad-bg);color:var(--bad)}.pill.muted{background:#eef1f5;color:var(--ink-soft)}
.hbr{display:flex;gap:1.5px;height:14px;min-width:120px}.hbr i{flex:1;border-radius:1px}
.notes{margin:10px 0 0 18px}.notes li{margin:4px 0}
svg{max-width:100%;height:auto}

/* Chrome. */
.foot{margin-top:36px;padding-top:12px;border-top:1px solid var(--line);color:var(--muted);font-size:11px;display:flex;
  justify-content:space-between;gap:16px;flex-wrap:wrap}
.toolbar{position:sticky;top:0;z-index:2;background:var(--paper);border-bottom:1px solid var(--line);padding:8px 44px;font-size:12px;
  display:flex;gap:12px;align-items:center;color:var(--muted)}
.toolbar button{background:var(--navy);color:#fff;border:none;border-radius:4px;padding:6px 12px;cursor:pointer;font-family:inherit;font-weight:600}
@media print{
  .toolbar{display:none}.page{padding:0;max-width:none}
  .cover{break-after:page;padding-top:40mm}
  .toc{break-after:page;background:none;border:0;padding:0}
  .part{break-before:page;border-top:0;margin-top:0}
  .sec,.kpi,.panel,tr{break-inside:avoid}
  h2,h3{break-after:avoid}
  a{color:inherit;text-decoration:none}
}
@media (max-width:640px){.page{padding:24px 18px 40px}.panels{grid-template-columns:1fr}.toc ol{columns:1}.cover h1{font-size:24px}}
"""


# --------------------------------------------------------------------------
# Executive report -- ported from `design/4-executive-report.html`'s own
# <style> block, for `builder.build_executive_report`.
#
# Palette reconciliation with `web/src/styles/tokens.css` (asked for
# explicitly -- this is not a from-scratch palette):
#   - KEPT the mockup's warm paper/ink palette (--paper/--ink/--signal/
#     --handled below) instead of the product's dashboard blue+white. This
#     is the one place in Vault that is a printed document, not a UI
#     surface, and the mockup's colours were chosen for that job
#     specifically -- --signal is a clay red picked because it still reads
#     as "attend to this" after a mono laser printer turns it to grey,
#     which the dashboard's saturated --color-alert (#e02424) was never
#     asked to survive. Reusing --color-alert/--color-good here would look
#     like an unstyled dashboard printed on paper, which is exactly what
#     the brief says not to build.
#   - KEPT the product's two existing self-hosted faces as-is: Manrope
#     (labels, headings) and IBM Plex Mono (every figure -- score, deltas,
#     counts, dates), same files `web/src/styles/fonts.css` already loads,
#     referenced here by the same `/fonts/*.woff2` paths so nothing new
#     ships for either face.
#   - ADDED "Source Serif 4" for body prose only, self-hosted the same way
#     (`web/public/fonts/source-serif-4-latin-*.woff2`, fetched once and
#     committed, same as the other two faces already were) -- Vault has
#     to render on a network with no internet, so a Google Fonts <link>
#     was never an option. This is the one face the product's UI does not
#     use anywhere else: a report that will be read start to finish and
#     printed behaves like a memo, and a text serif at book weight is what
#     makes a long paragraph feel written rather than exported from a
#     dashboard.
#
# Dark mode: the owner asked for it specifically for the on-screen read, so
# both `prefers-color-scheme` and the product's own explicit `data-theme`
# toggle (see `builder._theme_boot_script`) are honoured on screen. Print is
# the deliberate exception -- `@media print` below pins the light paper
# palette regardless of which skin the screen was showing, because a report
# that prints on a home inkjet in "console dark" would either waste a full
# page of black toner or, worse, print white-on-white if the printer's own
# "no background graphics" setting strips the dark fill but not the light
# text. A report is meant to be read on paper exactly as easily as a real
# printed memo, in every skin the app supports on screen.
# --------------------------------------------------------------------------

EXEC_REPORT_CSS = """
@font-face{font-family:"Manrope";font-style:normal;font-weight:400 800;font-display:swap;
  src:url("/fonts/manrope-latin-var.woff2") format("woff2")}
@font-face{font-family:"IBM Plex Mono";font-style:normal;font-weight:400;font-display:swap;
  src:url("/fonts/ibm-plex-mono-latin-400.woff2") format("woff2")}
@font-face{font-family:"IBM Plex Mono";font-style:normal;font-weight:600;font-display:swap;
  src:url("/fonts/ibm-plex-mono-latin-600.woff2") format("woff2")}
@font-face{font-family:"Source Serif 4";font-style:normal;font-weight:400 700;font-display:swap;
  src:url("/fonts/source-serif-4-latin-var.woff2") format("woff2")}
@font-face{font-family:"Source Serif 4";font-style:italic;font-weight:400;font-display:swap;
  src:url("/fonts/source-serif-4-latin-italic.woff2") format("woff2")}

:root{
  --paper:#fbfaf7; --ink:#1c1b19; --ink-soft:#55524c; --line:#d9d5cc;
  --signal:#8a3b2b; --signal-bg:#f4e8e3; --handled:#3d5a45; --handled-bg:#e7ede4;
  --card-bg:#ffffff; --shadow:0 1px 2px rgba(28,27,25,0.06),0 8px 24px rgba(28,27,25,0.05);
}
@media (prefers-color-scheme:dark){
  :root:not([data-theme="light"]){
    --paper:#171613; --ink:#ece9e2; --ink-soft:#b7b2a6; --line:#3a3833;
    --signal:#e0a08e; --signal-bg:#2c1e1a; --handled:#9dc2a9; --handled-bg:#1c261e;
    --card-bg:#201f1b; --shadow:none;
  }
}
:root[data-theme="dark"]{
  --paper:#171613; --ink:#ece9e2; --ink-soft:#b7b2a6; --line:#3a3833;
  --signal:#e0a08e; --signal-bg:#2c1e1a; --handled:#9dc2a9; --handled-bg:#1c261e;
  --card-bg:#201f1b; --shadow:none;
}
*{box-sizing:border-box}
html,body{margin:0;background:var(--paper);color:var(--ink)}
body{font-family:"Source Serif 4",Georgia,"Times New Roman",serif;font-size:17px;line-height:1.62;
  -webkit-font-smoothing:antialiased}
@media (prefers-reduced-motion:reduce){*{animation-duration:.001ms!important;transition-duration:.001ms!important}}
.page{max-width:800px;margin:0 auto;padding:56px 48px 64px}
a{color:inherit}
:focus-visible{outline:2px solid var(--signal);outline-offset:2px}
.label{font-family:"Manrope",sans-serif;font-weight:700;font-size:11.5px;letter-spacing:.06em;color:var(--ink-soft)}
.masthead{display:flex;justify-content:space-between;align-items:baseline;padding-bottom:14px;
  border-bottom:2px solid var(--ink)}
.masthead .brand{font-family:"Manrope",sans-serif;font-weight:800;font-size:19px;letter-spacing:.01em}
.masthead .period{font-family:"IBM Plex Mono",monospace;font-weight:600;font-size:14px;color:var(--ink-soft)}
.subhead{margin-top:10px;display:flex;justify-content:space-between;align-items:flex-end;flex-wrap:wrap;
  gap:8px 24px;padding-bottom:20px;border-bottom:1px solid var(--line)}
.subhead h1{font-family:"Manrope",sans-serif;font-weight:700;font-size:22px;margin:0;letter-spacing:-.01em}
.subhead .meta{font-family:"Manrope",sans-serif;font-size:12.5px;color:var(--ink-soft);text-align:right;
  line-height:1.5}
.verdict{margin:30px 0 32px}
.verdict p{font-family:"Source Serif 4",serif;font-weight:600;font-size:26px;line-height:1.38;
  letter-spacing:-.003em;margin:0;max-width:34ch}
.verdict .byline{margin-top:10px;font-family:"Manrope",sans-serif;font-size:13px;color:var(--ink-soft)}
.row{display:grid;grid-template-columns:240px 1fr;gap:20px;margin-bottom:30px}
.card{background:var(--card-bg);border:1px solid var(--line);border-radius:10px;box-shadow:var(--shadow);
  padding:18px 20px}
.score-card .score-top{display:flex;align-items:baseline;gap:10px}
.score-card .score-num{font-family:"IBM Plex Mono",monospace;font-weight:700;font-size:44px;line-height:1}
.score-card .score-grade{font-family:"IBM Plex Mono",monospace;font-weight:600;font-size:17px;color:var(--ink-soft)}
.score-card .score-out{font-family:"IBM Plex Mono",monospace;font-size:13px;color:var(--ink-soft)}
.score-card .trend-line{margin-top:10px;font-family:"Manrope",sans-serif;font-size:12.5px;color:var(--ink-soft);
  display:flex;align-items:center;gap:8px}
.score-card svg{display:block}
.ded-card .label{margin-bottom:10px;display:block}
.ded-list{list-style:none;margin:0;padding:0;display:flex;flex-direction:column;gap:12px}
.ded-list li{display:grid;grid-template-columns:46px 1fr;gap:12px;align-items:baseline}
.ded-list .ded-amt{font-family:"IBM Plex Mono",monospace;font-weight:700;font-size:15px;color:var(--signal);
  text-align:right}
.ded-list .ded-text{font-size:14.5px;line-height:1.5}
.ded-list .ded-text b{font-weight:600}
.ded-list .ded-text .fix{display:block;color:var(--ink-soft);font-size:13px;margin-top:2px}
.panel{border-radius:10px;padding:20px 22px;margin-bottom:26px;border:1px solid var(--line)}
.panel-handled{background:var(--handled-bg);border-color:var(--handled)}
.panel-decision{background:var(--signal-bg);border-color:var(--signal)}
.panel .panel-head{display:flex;align-items:baseline;justify-content:space-between;gap:12px;margin-bottom:12px;
  flex-wrap:wrap}
.panel h2{font-family:"Manrope",sans-serif;font-weight:700;font-size:15.5px;margin:0;letter-spacing:.01em}
.panel-handled h2{color:var(--handled)}
.panel-decision h2{color:var(--signal)}
.panel .count{font-family:"IBM Plex Mono",monospace;font-weight:700;font-size:15px}
.panel-handled .count{color:var(--handled)}
.panel-decision .count{color:var(--signal)}
.plain-list{list-style:none;margin:0;padding:0;display:flex;flex-direction:column;gap:9px}
.plain-list li{font-size:15px;line-height:1.55;padding-left:18px;position:relative}
.plain-list li::before{content:"";position:absolute;left:0;top:.62em;width:6px;height:6px;border-radius:50%;
  background:currentColor;opacity:.55}
.decision-list{list-style:none;margin:0;padding:0;display:flex;flex-direction:column;gap:14px}
.decision-list li{display:grid;grid-template-columns:1fr;gap:3px}
.decision-list .d-title{font-size:15px;font-weight:600;font-family:"Manrope",sans-serif}
.decision-list .d-body{font-size:14.5px;line-height:1.55}
.decision-list .d-if{font-size:13px;color:var(--ink-soft);font-style:italic}
.decision-tally{font-family:"Manrope",sans-serif;font-size:13px;color:var(--ink-soft);margin-top:4px}
.strip{display:flex;flex-wrap:wrap;gap:6px 28px;padding:16px 0 0;border-top:1px solid var(--line);
  font-family:"IBM Plex Mono",monospace;font-size:12.5px;color:var(--ink-soft)}
.strip span b{color:var(--ink);font-weight:600}
.footpage{margin-top:18px;display:flex;justify-content:space-between;font-family:"Manrope",sans-serif;
  font-size:11.5px;color:var(--ink-soft)}
.no-data{font-family:"Manrope",sans-serif;font-size:13px;color:var(--ink-soft)}
.toolbar{position:sticky;top:0;background:var(--paper);border-bottom:1px solid var(--line);padding:8px 48px;
  font-size:12px;font-family:"Manrope",sans-serif;display:flex;gap:10px;align-items:center;color:var(--ink-soft)}
.toolbar button{background:var(--ink);color:var(--paper);border:none;border-radius:6px;padding:6px 12px;
  cursor:pointer;font-family:"Manrope",sans-serif}
@media print{
  .toolbar{display:none}
  @page{size:A4;margin:16mm 16mm 18mm}
  html,body{background:#fff!important}
  /* Every custom property the dark skin redefines, restated here with
     `!important` -- not just the five that visibly differ from the base
     (light) values. `:root[data-theme="dark"]` is a higher-specificity
     selector than the plain `:root` a `@media print` block can write (an
     attribute selector always outranks none), so without `!important` this
     block would lose the cascade for every property `:root[data-theme=
     "dark"]` also sets, `@media print` or not -- print would silently stay
     dark for a reader who had picked the dark skin, exactly the failure
     this rule exists to prevent. */
  :root{--paper:#ffffff!important;--ink:#1c1b19!important;--ink-soft:#55524c!important;
    --line:#d9d5cc!important;--signal:#8a3b2b!important;--signal-bg:#f6ece8!important;
    --handled:#3d5a45!important;--handled-bg:#edf1ea!important;--card-bg:#ffffff!important;
    --shadow:none!important}
  .page{max-width:none;padding:0;margin:0}
  .card,.panel{box-shadow:none;break-inside:avoid}
  .row{break-inside:avoid}
  h1,h2,.verdict p{break-after:avoid}
  .ded-list li,.plain-list li,.decision-list li{break-inside:avoid}
  a{text-decoration:none}
}
@media (max-width:640px){
  .page{padding:32px 20px 40px}
  .row{grid-template-columns:1fr}
  .verdict p{font-size:22px}
  .subhead{flex-direction:column;align-items:flex-start}
  .subhead .meta{text-align:left}
}
"""
