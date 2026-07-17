// Humane API error handling. The backend guarantees every error body
// is {"detail": {"code", "message", ...}} — we surface `message` only.

export class ApiError extends Error {
  readonly status: number;
  readonly code: string;

  constructor(status: number, code: string, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
  }
}

/** The one way to turn an unknown thrown value into UI text. */
export function errorMessage(e: unknown): string {
  if (e instanceof ApiError) return e.message;
  if (e instanceof Error) return e.message.replace(/^Error:\s*/, "");
  return String(e);
}
