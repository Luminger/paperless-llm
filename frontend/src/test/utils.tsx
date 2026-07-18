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
      title: "Telarko Rechnung April 2024",
    },
    user_payload: null,
    base_snapshot: null,
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

import type { EntityRef, Job, JobPage } from "../api";

/** Full EntityOut with defaults — tests override what they care about. */
export function makeEntity(over: Partial<EntityRef> & { id: number; name: string }): EntityRef {
  return {
    match: "",
    matching_algorithm: 0,
    is_insensitive: true,
    document_count: 0,
    is_inbox_tag: false,
    instructions: "",
    ...over,
  };
}

export function jobPage(results: Job[]): JobPage {
  return { count: results.length, page: 1, page_size: 50, results };
}

import { screen as _screen } from "@testing-library/react";
import _userEvent from "@testing-library/user-event";

/** Interact with a framework (Radix) select: open by accessible name,
 * click an option. */
export async function pickOption(triggerName: string | RegExp, optionName: string | RegExp) {
  await _userEvent.click(_screen.getByRole("combobox", { name: triggerName }));
  await _userEvent.click(await _screen.findByRole("option", { name: optionName }));
}
