import { useState } from "react";
import ReactDiffViewer, { DiffMethod } from "react-diff-viewer-continued";
import { Textarea } from "@/components/ui/textarea";
import { useTheme } from "../lib/theme";

type Mode = "side-by-side" | "unified";

const MODE_KEY = "pllm.diffMode";

/** OCR gate diff (rendered by react-diff-viewer-continued: one table,
 * so old/new scroll together by construction). The new text is editable
 * in place; the diff recomputes live. */
export function DiffView({
  oldText,
  newText,
  onNewTextChange,
}: {
  oldText: string;
  newText: string;
  // Omit for read-only diffs (e.g. superseded OCR runs).
  onNewTextChange?: (t: string) => void;
}) {
  const { dark } = useTheme();
  const [mode, setMode] = useState<Mode>(
    () => (localStorage.getItem(MODE_KEY) as Mode) || "side-by-side",
  );
  const [editing, setEditing] = useState(false);

  const switchMode = (m: Mode) => {
    setMode(m);
    localStorage.setItem(MODE_KEY, m);
  };

  return (
    <div>
      <div className="mb-2 flex items-center gap-2 text-xs">
        {(["side-by-side", "unified"] as Mode[]).map((m) => (
          <button
            key={m}
            onClick={() => switchMode(m)}
            aria-pressed={mode === m}
            className={`rounded px-2 py-1 ${
              mode === m
                ? "bg-primary text-primary-foreground"
                : "bg-muted text-muted-foreground"
            }`}
          >
            {m}
          </button>
        ))}
        {onNewTextChange && (
          <button
            onClick={() => setEditing(!editing)}
            aria-pressed={editing}
            className={`ml-auto rounded px-2 py-1 ${
              editing
                ? "bg-primary text-primary-foreground"
                : "bg-muted text-muted-foreground"
            }`}
          >
            {editing ? "done editing" : "edit new text"}
          </button>
        )}
      </div>

      {editing && onNewTextChange ? (
        // The diff table is render-only (react-diff-viewer-continued
        // offers no inline editing), so edit mode swaps in a plain
        // textarea — styled like every other input, nothing special.
        <Textarea
          aria-label="new content"
          className="h-96 w-full resize-y font-mono text-xs leading-5"
          value={newText}
          onChange={(e) => onNewTextChange(e.target.value)}
        />
      ) : (
        <div className="max-h-96 overflow-auto rounded border border-border">
          <ReactDiffViewer
            oldValue={oldText}
            newValue={newText}
            useDarkTheme={dark}
            // The summary bar (fold-all button + change count + diffstat
            // squares) only earns its place on mostly-unchanged diffs;
            // OCR gates are mostly-different, leaving a dead-looking
            // button. Per-block "Expand N lines" folds keep working.
            hideSummary
            splitView={mode === "side-by-side"}
            leftTitle="Current content (paperless)"
            rightTitle="New content (OCR)"
            compareMethod={DiffMethod.WORDS}
            styles={{
              contentText: { fontSize: "0.75rem", lineHeight: "1.1rem" },
              lineNumber: { fontSize: "0.65rem" },
              titleBlock: { fontSize: "0.7rem", padding: "0.3rem 0.5rem" },
              // The library's stock dark palette (wine red / teal) fights
              // the app's near-black + emerald theme. Rebuild it from our
              // own tokens: quiet tinted line backgrounds, word-level
              // highlights carry the emphasis (the light palette is fine
              // as shipped).
              variables: {
                dark: {
                  diffViewerBackground: "#0b0b0c",
                  diffViewerColor: "#d4d4d8",
                  addedBackground: "rgba(16, 185, 129, 0.09)",
                  addedColor: "#d4d4d8",
                  wordAddedBackground: "rgba(16, 185, 129, 0.30)",
                  addedGutterBackground: "rgba(16, 185, 129, 0.14)",
                  addedGutterColor: "#6ee7b7",
                  removedBackground: "rgba(248, 113, 113, 0.09)",
                  removedColor: "#d4d4d8",
                  wordRemovedBackground: "rgba(248, 113, 113, 0.28)",
                  removedGutterBackground: "rgba(248, 113, 113, 0.12)",
                  removedGutterColor: "#fca5a5",
                  gutterBackground: "#111113",
                  gutterBackgroundDark: "#0e0e10",
                  gutterColor: "#71717a",
                  emptyLineBackground: "#0f0f11",
                  highlightBackground: "#1f1f22",
                  highlightGutterBackground: "#1f1f22",
                  codeFoldBackground: "#131316",
                  codeFoldGutterBackground: "#18181b",
                  codeFoldContentColor: "#71717a",
                  diffViewerTitleBackground: "#131316",
                  diffViewerTitleColor: "#a1a1aa",
                  diffViewerTitleBorderColor: "#27272a",
                },
              },
            }}
          />
        </div>
      )}
    </div>
  );
}
