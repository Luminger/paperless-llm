import { NavLink, Route, Routes } from "react-router-dom";
import { CircleUser, Monitor, Moon, Settings2, Sun } from "lucide-react";
import { Link } from "react-router-dom";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { useTheme, type Theme } from "./lib/theme";
import Documents from "./pages/Documents";
import Dashboard from "./pages/Dashboard";
import SessionDetail from "./pages/SessionDetail";
import ProposalRedirect from "./pages/ProposalRedirect";
import Taxonomy from "./pages/Taxonomy";
import Jobs from "./pages/Jobs";
import JobDetail from "./pages/JobDetail";
import EntityPage from "./pages/EntityPage";
import AuditLog from "./pages/AuditLog";
import Settings from "./pages/Settings";

const nav = [
  { to: "/documents", label: "Documents" },
  { to: "/taxonomy", label: "Taxonomy" },
  { to: "/jobs", label: "Jobs" },
  { to: "/log", label: "Log" },
];

function UserMenu() {
  const { theme, setTheme } = useTheme();
  const ThemeIcon = theme === "dark" ? Moon : theme === "light" ? Sun : Monitor;
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button variant="ghost" size="icon" aria-label="user menu">
          <CircleUser className="size-5" />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="w-44">
        <DropdownMenuLabel className="flex items-center gap-2 text-muted-foreground">
          <ThemeIcon className="size-4" /> Theme
        </DropdownMenuLabel>
        <DropdownMenuRadioGroup
          value={theme}
          onValueChange={(v) => setTheme(v as Theme)}
        >
          <DropdownMenuRadioItem value="system">System</DropdownMenuRadioItem>
          <DropdownMenuRadioItem value="light">Light</DropdownMenuRadioItem>
          <DropdownMenuRadioItem value="dark">Dark</DropdownMenuRadioItem>
        </DropdownMenuRadioGroup>
        <DropdownMenuSeparator />
        <DropdownMenuItem asChild>
          <Link to="/settings" className="flex items-center gap-2">
            <Settings2 className="size-4" /> Settings
          </Link>
        </DropdownMenuItem>
        <DropdownMenuSeparator />
        <DropdownMenuLabel className="text-xs font-normal text-muted-foreground">
          Signed in locally
        </DropdownMenuLabel>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

export default function App() {
  return (
    <div className="min-h-screen bg-background text-foreground">
      <header className="border-b bg-card">
        <div className="mx-auto flex max-w-6xl items-center gap-6 px-4 py-2.5">
          <NavLink to="/" className="text-lg font-semibold tracking-tight">
            paperless<span className="text-primary">-llm</span>
          </NavLink>
          <nav className="flex gap-4 text-sm">
            {nav.map((n) => (
              <NavLink
                key={n.to}
                to={n.to}
                end={n.to === "/"}
                className={({ isActive }) =>
                  isActive
                    ? "font-medium text-primary"
                    : "text-muted-foreground transition-colors hover:text-foreground"
                }
              >
                {n.label}
              </NavLink>
            ))}
          </nav>
          <div className="ml-auto">
            <UserMenu />
          </div>
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
          <Route path="/settings" element={<Settings />} />
        </Routes>
      </main>
    </div>
  );
}
