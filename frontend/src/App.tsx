import { useDateTimePrefsSubscription } from "./lib/prefs";
import {
  Navigate,
  NavLink,
  Route,
  Routes,
  useLocation,
  useNavigate,
  type Location,
} from "react-router-dom";
import { ChevronDown, CircleUser, LogOut, Monitor, Moon, Settings2, Sun } from "lucide-react";
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
import { useAuth } from "./lib/auth";
import { api } from "./api";
import { keys } from "./lib/keys";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import Documents from "./pages/Documents";
import Dashboard from "./pages/Dashboard";
import SessionDetail from "./pages/SessionDetail";
import ProposalRedirect from "./pages/ProposalRedirect";
import Taxonomy, { TYPES as TAXONOMY_TYPES } from "./pages/Taxonomy";
import Jobs from "./pages/Jobs";
import JobDetail from "./pages/JobDetail";
import EntityPage from "./pages/EntityPage";
import AuditLog from "./pages/AuditLog";
import {
  SettingsDialog,
  type SettingsSection,
} from "@/components/settings/SettingsDialog";

const nav = [{ to: "/documents", label: "Documents" }];
const navAfterTaxonomy = [
  { to: "/jobs", label: "Jobs" },
  { to: "/log", label: "Log" },
];

/** Taxonomy is a MENU, not a page: the three curated types hang
 * directly off the top nav. */
function TaxonomyMenu() {
  const location = useLocation();
  const active = location.pathname.startsWith("/taxonomy");
  return (
    <DropdownMenu>
      <DropdownMenuTrigger
        className={
          active
            ? "flex items-center gap-1 font-medium text-primary outline-none"
            : "flex items-center gap-1 text-muted-foreground transition-colors outline-none hover:text-foreground"
        }
      >
        Taxonomy <ChevronDown className="size-3.5" />
      </DropdownMenuTrigger>
      <DropdownMenuContent align="start" className="w-44">
        {TAXONOMY_TYPES.map((t) => (
          <DropdownMenuItem key={t.key} asChild>
            <NavLink to={`/taxonomy/${t.key}`}>{t.label}</NavLink>
          </DropdownMenuItem>
        ))}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

function UserMenu({ onOpenSettings }: { onOpenSettings: () => void }) {
  const { theme, setTheme } = useTheme();
  const auth = useAuth();
  const qc = useQueryClient();
  const logout = useMutation({
    mutationFn: api.logout,
    onSuccess: () => qc.invalidateQueries({ queryKey: keys.auth() }),
  });
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
        <DropdownMenuItem onSelect={onOpenSettings} className="gap-2">
          <Settings2 className="size-4" /> Settings
        </DropdownMenuItem>
        <DropdownMenuSeparator />
        <DropdownMenuLabel className="text-xs font-normal text-muted-foreground">
          Signed in as {auth.user}
        </DropdownMenuLabel>
        <DropdownMenuItem onSelect={() => logout.mutate()} className="gap-2">
          <LogOut className="size-4" /> Sign out
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

const SECTION_VALUES = ["preferences", "models", "prompts", "paperless", "system"] as const;

export default function App() {
  // Fresh date/time prefs re-render everything (AUDIT FP-M1).
  useDateTimePrefsSubscription();
  // Settings is a routable modal: /settings opens it, the section
  // travels in the #fragment (/settings#prompts). The page the user
  // came from stays MOUNTED underneath (backgroundLocation pattern:
  // <Routes> renders the remembered background while the URL shows
  // /settings) — open/close never resets list state.
  const location = useLocation();
  const navigate = useNavigate();
  const state = location.state as { background?: Location } | null;
  const background = state?.background;
  const settingsOpen = location.pathname === "/settings";
  const hash = location.hash.replace("#", "");
  const section: SettingsSection = (SECTION_VALUES as readonly string[]).includes(hash)
    ? (hash as SettingsSection)
    : "preferences";
  const openSettings = () =>
    navigate("/settings", { state: { background: location } });
  const closeSettings = () => {
    if (background) navigate(-1);
    else navigate("/");
  };
  return (
    <div className="min-h-screen bg-background text-foreground">
      <header className="border-b bg-card">
        <div className="mx-auto flex max-w-6xl items-center gap-6 px-4 py-2.5">
          <NavLink to="/" className="text-lg font-semibold tracking-tight">
            paperless<span className="text-primary">-llm</span>
          </NavLink>
          <nav className="flex items-center gap-4 text-sm">
            {[...nav].map((n) => (
              <NavLink
                key={n.to}
                to={n.to}
                className={({ isActive }) =>
                  isActive
                    ? "font-medium text-primary"
                    : "text-muted-foreground transition-colors hover:text-foreground"
                }
              >
                {n.label}
              </NavLink>
            ))}
            <TaxonomyMenu />
            {navAfterTaxonomy.map((n) => (
              <NavLink
                key={n.to}
                to={n.to}
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
            <UserMenu onOpenSettings={openSettings} />
          </div>
        </div>
      </header>
      <main className="mx-auto max-w-6xl px-4 py-6">
        <Routes location={(settingsOpen && background) || location}>
          <Route path="/" element={<Dashboard />} />
          <Route path="/sessions/:id" element={<SessionDetail />} />
          {/* Proposals live on their session's timeline; old links redirect. */}
          <Route path="/proposals/:id" element={<ProposalRedirect />} />
          <Route path="/documents" element={<Documents />} />
          <Route path="/documents/:id" element={<EntityPage />} />
          {/* Old landing links land on tags. */}
          <Route path="/taxonomy" element={<Navigate to="/taxonomy/tag" replace />} />
          <Route path="/taxonomy/:type" element={<Taxonomy />} />
          <Route path="/taxonomy/:type/:id" element={<EntityPage />} />
          <Route path="/jobs" element={<Jobs />} />
          <Route path="/jobs/:id" element={<JobDetail />} />
          <Route path="/log" element={<AuditLog />} />
          {/* Backdrop for the settings modal on DIRECT entry only
              (in-app opens keep the origin page as background). */}
          <Route path="/settings" element={<Dashboard />} />
        </Routes>
      </main>
      <SettingsDialog
        open={settingsOpen}
        onOpenChange={(open) => {
          if (!open) closeSettings();
        }}
        section={section}
        onSectionChange={(sec) =>
          navigate(`/settings#${sec}`, { replace: true, state: location.state })
        }
      />
    </div>
  );
}
