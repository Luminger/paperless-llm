import { useState } from "react";
import ReactDiffViewer, { DiffMethod } from "react-diff-viewer-continued";
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
                ? "bg-amber-600 text-white"
                : "bg-muted text-muted-foreground"
            }`}
          >
            {editing ? "done editing" : "edit new text"}
          </button>
        )}
      </div>

      {editing && onNewTextChange ? (
        <textarea
          aria-label="new content"
          className="h-96 w-full rounded border border-amber-300 p-2 font-mono text-xs"
          value={newText}
          onChange={(e) => onNewTextChange(e.target.value)}
        />
      ) : (
        <div className="max-h-96 overflow-auto rounded border border-border">
          <ReactDiffViewer
            oldValue={oldText}
            newValue={newText}
            useDarkTheme={dark}
            splitView={mode === "side-by-side"}
            leftTitle="Current content (paperless)"
            rightTitle="New content (OCR)"
            compareMethod={DiffMethod.WORDS}
            styles={{
              contentText: { fontSize: "0.75rem", lineHeight: "1.1rem" },
              lineNumber: { fontSize: "0.65rem" },
              titleBlock: { fontSize: "0.7rem", padding: "0.3rem 0.5rem" },
            }}
          />
        </div>
      )}
    </div>
  );
}
