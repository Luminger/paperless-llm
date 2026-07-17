import { NavLink, Route, Routes } from "react-router-dom";
import Documents from "./pages/Documents";
import Sessions from "./pages/Sessions";
import SessionDetail from "./pages/SessionDetail";
import ProposalRedirect from "./pages/ProposalRedirect";

const nav = [
  { to: "/", label: "Analyses" },
  { to: "/documents", label: "Documents" },
];

export default function App() {
  return (
    <div className="min-h-screen bg-zinc-50 text-zinc-900">
      <header className="border-b border-zinc-200 bg-white">
        <div className="mx-auto flex max-w-6xl items-center gap-6 px-4 py-3">
          <span className="text-lg font-semibold tracking-tight">
            paperless<span className="text-emerald-600">-llm</span>
          </span>
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
          <Route path="/" element={<Sessions />} />
          <Route path="/sessions" element={<Sessions />} />
          <Route path="/sessions/:id" element={<SessionDetail />} />
          {/* Proposals live on their session's timeline; old links redirect. */}
          <Route path="/proposals/:id" element={<ProposalRedirect />} />
          <Route path="/documents" element={<Documents />} />
        </Routes>
      </main>
    </div>
  );
}
