import "@testing-library/jest-dom/vitest";

// jsdom has no EventSource; the SSE hook degrades to a no-op stream.
class MockEventSource {
  onmessage: ((ev: MessageEvent) => void) | null = null;
  onerror: (() => void) | null = null;
  constructor(public url: string) {}
  addEventListener() {}
  removeEventListener() {}
  close() {}
}
// eslint-disable-next-line @typescript-eslint/no-explicit-any
(globalThis as any).EventSource = MockEventSource;
