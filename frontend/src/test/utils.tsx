import type { ReactElement } from "react";
import { render } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { Proposal } from "../api";

export function renderWithProviders(
  ui: ReactElement,
  { route = "/", path = "/" }: { route?: string; path?: string } = {},
) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[route]}>
        <Routes>
          <Route path={path} element={ui} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

export function makeProposal(overrides: Partial<Proposal> = {}): Proposal {
  return {
    id: 1,
    session_id: 1,
    kind: "update_document_metadata",
    revision: 1,
    supersedes_id: null,
    agent_payload: {
      kind: "update_document_metadata",
      document_id: 7,
      reason: "Better title for the invoice",
      title: "Telarko Rechnung April 2024",
    },
    user_payload: null,
    status: "pending",
    entity_type: "document",
    entity_id: 7,
    created_at: "2026-07-17T10:00:00Z",
    updated_at: "2026-07-17T10:00:00Z",
    applied: false,
    reverted: false,
    ...overrides,
  };
}
