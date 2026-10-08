import { usePreferences } from "./lib/preferences";
import { BuildWatch } from "./lib/buildwatch";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { BrowserRouter, Navigate, Route, Routes, useParams } from "react-router-dom";
import ApiTokens from "./pages/ApiTokens";
import Assets from "./pages/Assets";
import ApiGuide from "./pages/ApiGuide";
import Dns from "./pages/Dns";
import Connections from "./pages/Connections";
import Help from "./pages/Help";
import Support from "./pages/Support";
import OwnerShell from "./owner/OwnerShell";
import Ledger from "./owner/Ledger";
import OwnerOverview from "./owner/Overview";
import OwnerFleet from "./owner/Fleet";
import OwnerBilling from "./owner/Billing";
import OwnerAccount from "./owner/Account";
import OwnerAudit from "./owner/Audit";
import OwnerTickets from "./owner/Tickets";
import OwnerSettings from "./owner/Settings";
import OwnerIncidents from "./owner/Incidents";
import OwnerMaintenance from "./owner/Maintenance";
import OwnerAssets from "./owner/Assets";
import OwnerAssetDetail from "./owner/AssetDetail";
import OwnerSites from "./owner/Sites";
import OwnerCertificates from "./owner/Certificates";
import OwnerReports from "./owner/Reports";
import { isOwnerHost } from "./lib/ownerHost";
import Inbox from "./pages/Inbox";
import Integrations from "./pages/Integrations";
import Jobs from "./pages/Jobs";
import Leads from "./pages/Leads";
import Login from "./pages/Login";
import Onboarding from "./pages/Onboarding";
import History from "./pages/History";
import IncidentDetail from "./pages/IncidentDetail";
import Overview from "./pages/Overview";
import Reports from "./pages/Reports";
import Security from "./pages/Security";
import Settings from "./pages/Settings";
import Topology from "./pages/Topology";
import Updates from "./pages/Updates";
import Shell from "./shell/Shell";
import "./styles/tabs.css";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      retry: false,
      refetchOnWindowFocus: false,
    },
  },
});

// Every tab below is a scaffold placeholder — the real page for each lands in
// a later task (24–26, 32–35 per the plan's track map) and replaces this
// component wholesale. `Shell` supplies the sidebar/top bar/chat chrome, so a
// placeholder only has to fill the content area.
function PagePlaceholder({ title, note }: { title: string; note?: string }) {
  return (
    <section className="card">
      <h1>{title}</h1>
      <p className="hint">{note ?? "This tab is being built."}</p>
    </section>
  );
}

function AssetsHostPlaceholder() {
  const { name } = useParams<{ name: string }>();
  return <PagePlaceholder title="Host" note={`Host detail for ${name}.`} />;
}

function SecuritySitePlaceholder() {
  const { name } = useParams<{ name: string }>();
  return <PagePlaceholder title="Site" note={`Site detail for ${name}.`} />;
}

function InboxRowPlaceholder() {
  const { key } = useParams<{ key: string }>();
  return <PagePlaceholder title="Inbox item" note={`Alert ${key}.`} />;
}

function PreferredHome() {
  const prefs = usePreferences();
  if (!prefs.ready) return <p role="status">Loading your view…</p>;
  const destinations = {customer:"/inbox", owner:"/overview", technician:"/activity"};
  return <Navigate to={destinations[prefs.value.view]} replace />;
}

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        {/* Once, above both shells: the owner console and the customer
            portal are one bundle, so one watcher covers both. */}
        <BuildWatch />
        <Routes>
          <Route path="/login" element={<Login />} />
          {/* Chrome-free by design — no sidebar/top bar while a fresh
              tenant is still naming their site and installing the sensor —
              so this sits outside `<Shell>`'s route tree, not under it. */}
          <Route path="/welcome" element={<Onboarding />} />

          {/* The owner console. Same bundle, its own hostname and frame:
              `isOwnerHost()` decides whether the root lands here, and the
              API still gates every read on `is_superadmin`, so serving this
              route to a customer's browser grants nothing. */}
          <Route path="/owner" element={<OwnerShell />}>
            {/* Overview is the index: the console opens on what needs the operator,
                not on a table they must read all of first. The Ledger keeps its own
                route so it stays linkable. */}
            <Route index element={<OwnerOverview />} />
            {/* The reference's network screens, each reading the customer the
                bar's picker has selected. Asset detail keeps Assets lit in the
                rail, which the NavLink's prefix match does on its own. */}
            <Route path="incidents" element={<OwnerIncidents />} />
            <Route path="maintenance" element={<OwnerMaintenance />} />
            <Route path="assets" element={<OwnerAssets />} />
            <Route path="assets/:kind/:id" element={<OwnerAssetDetail />} />
            <Route path="sites" element={<OwnerSites />} />
            <Route path="certificates" element={<OwnerCertificates />} />
            <Route path="reports" element={<OwnerReports />} />
            <Route path="ledger" element={<Ledger />} />
            <Route path="fleet" element={<OwnerFleet />} />
            <Route path="billing" element={<OwnerBilling />} />
            {/* One customer's own configuration, keyed by tenant id rather than
                slug: the audit trail carries ids, so a row there links straight
                here without a lookup. */}
            <Route path="accounts/:tid" element={<OwnerAccount />} />
            <Route path="audit" element={<OwnerAudit />} />
            <Route path="requests" element={<Leads />} />
            {/* One route for the queue: the thread lives in the same pane, so
                a ticket id in the URL would be a second way to express the
                same selection. The ticket LETTERS link to /owner/tickets/:id,
                which this catches and opens on the queue. */}
            <Route path="tickets" element={<OwnerTickets />} />
            <Route path="tickets/:id" element={<OwnerTickets />} />
            <Route path="settings" element={<OwnerSettings />} />
          </Route>

          <Route element={<Shell />}>
            <Route path="/overview" element={<Overview />} />
            <Route path="/inbox" element={<Inbox />} />
            <Route path="/inbox/:key" element={<InboxRowPlaceholder />} />
            {/* An incident's own reloadable URL (Task 83/A2) -- narrative,
                timeline, the assets it touched, a scoped graph and the
                evidence rows. */}
            <Route path="/incidents/:iid" element={<IncidentDetail />} />
            <Route path="/assets" element={<Assets />} />
            <Route path="/assets/devices/:mac" element={<Assets />} />
            <Route path="/assets/hosts/:name" element={<AssetsHostPlaceholder />} />
            <Route path="/assets/:view" element={<Assets />} />
            <Route path="/topology" element={<Topology />} />
            <Route path="/history" element={<History />} />
            <Route path="/activity" element={<Jobs />} />
            {/* Each `:section` is one of the sidebar dropdown's children. The
                page draws every panel either way and `useSectionAnchor()`
                scrolls the named one into view — see `lib/section.ts`. */}
            <Route path="/security" element={<Security />} />
            <Route path="/security/sites/:name" element={<SecuritySitePlaceholder />} />
            <Route path="/security/:section" element={<Security />} />
            <Route path="/updates" element={<Updates />} />
            <Route path="/updates/:section" element={<Updates />} />
            <Route path="/dns" element={<Dns />} />
            <Route path="/dns/:section" element={<Dns />} />
            <Route path="/connections" element={<Connections />} />
            <Route path="/reports" element={<Reports />} />
            <Route path="/reports/:section" element={<Reports />} />
            <Route path="/settings" element={<Settings />} />
            <Route path="/settings/integrations" element={<Integrations />} />
            {/* Above the `/settings/:section` catch-all below, or that
                would swallow it into `Settings`. Superadmin-only, and no
                sidebar entry yet — the nav is being rebuilt in a parallel
                task, so the link is added there rather than here. */}
            <Route path="/settings/leads" element={<Leads />} />
            {/* Also above the `/settings/:section` catch-all. Admin-only, and
                no sidebar entry yet for the same reason as Requests above —
                the one nav line to add is quoted in the page's own docstring. */}
            <Route path="/settings/api-tokens" element={<ApiTokens />} />
            <Route path="/integrations" element={<Integrations />} />
            <Route path="/settings/:section" element={<Settings />} />
            {/* `/help/api` before the `:slug` route: the API guide is its
                own page, not an article in `help/articles.ts`, and React
                Router would otherwise hand "api" to `Help` as a slug. */}
            {/* Support is its own tab, not a page under Help: Help is
                documentation, this is a conversation with a person. Any
                signed-in member may raise a ticket -- the person who notices
                a problem is not always an admin. */}
            <Route path="/support" element={<Support />} />
            <Route path="/support/:id" element={<Support />} />
            <Route path="/help" element={<Help />} />
            <Route path="/help/api" element={<ApiGuide />} />
            <Route path="/help/:slug" element={<Help />} />
          </Route>

          {/* Two front doors on one bundle: on admin.guarder.io the root is
              the owner console; everywhere else it is the tenant's own
              remembered landing page rather than a fixed route, which is what
              `PreferredHome` resolves. */}
          <Route
            path="/"
            element={isOwnerHost() ? <Navigate to="/owner" replace /> : <PreferredHome />}
          />
          <Route path="*" element={<Navigate to="/overview" replace />} />
        </Routes>
      </BrowserRouter>
    </QueryClientProvider>
  );
}
