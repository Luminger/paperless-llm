import { NavLink, Route, Routes } from "react-router-dom";
import Documents from "./pages/Documents";
import Dashboard from "./pages/Dashboard";
import SessionDetail from "./pages/SessionDetail";
import ProposalRedirect from "./pages/ProposalRedirect";
import Taxonomy from "./pages/Taxonomy";
import Jobs from "./pages/Jobs";
import JobDetail from "./pages/JobDetail";
import EntityPage from "./pages/EntityPage";
import AuditLog from "./pages/AuditLog";

const nav = [
  { to: "/documents", label: "Documents" },
  { to: "/taxonomy", label: "Taxonomy" },
  { to: "/jobs", label: "Jobs" },
  { to: "/log", label: "Log" },
];

export default function App() {
  return (
    <div className="min-h-screen bg-zinc-50 text-zinc-900">
      <header className="border-b border-zinc-200 bg-white">
        <div className="mx-auto flex max-w-6xl items-center gap-6 px-4 py-3">
          <NavLink to="/" className="text-lg font-semibold tracking-tight">
            paperless<span className="text-emerald-600">-llm</span>
          </NavLink>
          <nav className="flex gap-4 text-sm">
            {nav.map((n) => (
              <NavLink
                key={n.to}
                to={n.to}
                end={n.to === "/"}
                className={({ isActive }) =>
                  isActive
                    ? "font-medium text-emerald-700"
                    : "text-zinc-500 hover:text-zinc-800"
                }
              >
                {n.label}
              </NavLink>
            ))}
          </nav>
        </div>
      </header>
      <main className="mx-auto max-w-6xl px-4 py-6">
        <Routes>
          <Route path="/" element={<Dashboard />} />
          <Route path="/sessions/:id" element={<SessionDetail />} />
          {/* Proposals live on their session's timeline; old links redirect. */}
          <Route path="/proposals/:id" element={<ProposalRedirect />} />
          <Route path="/documents" element={<Documents />} />
          <Route path="/documents/:id" element={<EntityPage />} />
          <Route path="/taxonomy" element={<Taxonomy />} />
          <Route path="/taxonomy/:type/:id" element={<EntityPage />} />
          <Route path="/jobs" element={<Jobs />} />
          <Route path="/jobs/:id" element={<JobDetail />} />
          <Route path="/log" element={<AuditLog />} />
        </Routes>
      </main>
    </div>
  );
}
