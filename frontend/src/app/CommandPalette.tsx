import { useEffect, useMemo, useRef, useState } from "react";

type Command = { label: string; to: string };

const COMMANDS: Command[] = [
  { label: "Go to Home", to: "/" },
  { label: "Go to Projects", to: "/projects" },
];

/** Minimal, dependency-free command palette — a filtered list + keyboard nav,
 * not a component-library modal. Wired to real navigation only; commands for
 * sections that don't exist yet (Run tests, Open terminal, ...) aren't listed
 * here rather than pretending to do something. */
export function CommandPalette({
  onClose,
  onNavigate,
}: {
  onClose: () => void;
  onNavigate: (to: string) => void;
}) {
  const [query, setQuery] = useState("");
  const [highlighted, setHighlighted] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);

  const results = useMemo(
    () => COMMANDS.filter((c) => c.label.toLowerCase().includes(query.toLowerCase())),
    [query]
  );

  useEffect(() => {
    inputRef.current?.focus();
  }, []);

  useEffect(() => {
    setHighlighted(0);
  }, [query]);

  function onKeyDown(event: React.KeyboardEvent) {
    if (event.key === "Escape") {
      onClose();
    } else if (event.key === "ArrowDown") {
      event.preventDefault();
      setHighlighted((h) => Math.min(h + 1, results.length - 1));
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      setHighlighted((h) => Math.max(h - 1, 0));
    } else if (event.key === "Enter" && results[highlighted]) {
      onNavigate(results[highlighted].to);
    }
  }

  return (
    <div className="palette-backdrop" onClick={onClose}>
      <div className="palette" onClick={(e) => e.stopPropagation()}>
        <input
          ref={inputRef}
          className="palette-input"
          placeholder="Type a command…"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          onKeyDown={onKeyDown}
        />
        <div className="palette-results">
          {results.length === 0 && <div className="palette-empty">No matching command</div>}
          {results.map((command, index) => (
            <button
              key={command.to}
              type="button"
              className={`palette-result ${index === highlighted ? "active" : ""}`}
              onMouseEnter={() => setHighlighted(index)}
              onClick={() => onNavigate(command.to)}
            >
              {command.label}
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}
