import Editor from "@monaco-editor/react";
import { type FormEvent, useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import { getProject, readFile, runCommand, writeFile, type Project } from "../api/projects";
import { FileTree } from "../components/FileTree";
import { useTurnSocket } from "../ws/useTurnSocket";

type OpenFile = { path: string; content: string; savedContent: string };

const LANGUAGE_BY_EXTENSION: Record<string, string> = {
  py: "python", ts: "typescript", tsx: "typescript", js: "javascript", jsx: "javascript",
  json: "json", css: "css", html: "html", md: "markdown", rs: "rust", go: "go",
  c: "c", cpp: "cpp", cs: "csharp", rb: "ruby", sh: "shell", yaml: "yaml", yml: "yaml",
};

function languageFor(path: string): string {
  const ext = path.split(".").pop() ?? "";
  return LANGUAGE_BY_EXTENSION[ext] ?? "plaintext";
}

function TerminalPanel({ projectId }: { projectId: number }) {
  const [command, setCommand] = useState("");
  const [running, setRunning] = useState(false);
  const [history, setHistory] = useState<{ command: string; output: string; success: boolean }[]>([]);

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    if (!command.trim()) return;
    setRunning(true);
    try {
      const result = await runCommand(projectId, command);
      setHistory((h) => [...h, { command, output: result.output, success: result.success }]);
    } catch (err) {
      setHistory((h) => [
        ...h,
        { command, output: err instanceof Error ? err.message : "Command failed", success: false },
      ]);
    } finally {
      setCommand("");
      setRunning(false);
    }
  }

  return (
    <div className="terminal-panel">
      <div className="terminal-note">
        Command runner — runs to completion and shows full output (no live PTY streaming yet).
        Destructive commands (rm, git reset/push/checkout, mv) are refused here.
      </div>
      <div className="terminal-output">
        {history.map((entry, index) => (
          <div key={index} className={`terminal-entry ${entry.success ? "" : "terminal-entry-error"}`}>
            <div className="terminal-command">$ {entry.command}</div>
            <pre className="terminal-result">{entry.output}</pre>
          </div>
        ))}
      </div>
      <form className="terminal-input-row" onSubmit={handleSubmit}>
        <span className="terminal-prompt">$</span>
        <input
          className="terminal-input"
          value={command}
          onChange={(e) => setCommand(e.target.value)}
          placeholder="Run a command in this project…"
          disabled={running}
        />
      </form>
    </div>
  );
}

function AgentPanel({ workspacePath }: { workspacePath: string }) {
  const { connectionState, progress, pendingConfirm, result, startTurn, respondToConfirm } =
    useTurnSocket();
  const [task, setTask] = useState("");
  const [running, setRunning] = useState(false);

  useEffect(() => {
    if (result) setRunning(false);
  }, [result]);

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    setRunning(true);
    try {
      await startTurn(task, workspacePath);
      setTask("");
    } catch {
      setRunning(false);
    }
  }

  return (
    <div className="agent-panel">
      <div className="agent-panel-header">
        SkyTrap
        <span className={`status-dot status-${connectionState}`} />
      </div>
      <div className="agent-panel-log">
        {progress.map((step, index) => (
          <div className="progress-step" key={index}>
            <strong>{step.tool}</strong>
            <span>{step.observation}</span>
          </div>
        ))}
        {result && (
          <div className={`result result-${result.status}`}>
            {result.status === "done" ? result.result : result.error}
          </div>
        )}
      </div>
      <form className="agent-panel-input" onSubmit={handleSubmit}>
        <textarea
          value={task}
          onChange={(e) => setTask(e.target.value)}
          placeholder="Ask SkyTrap to do something in this project…"
          required
        />
        <button type="submit" disabled={running || connectionState !== "open"}>
          {running ? "…" : "Send"}
        </button>
      </form>
      {pendingConfirm && (
        <div className="modal-backdrop">
          <div className="modal">
            <h2>Confirmation requise</h2>
            <p className="modal-kind">{pendingConfirm.kind}</p>
            <pre className="modal-preview">{pendingConfirm.preview}</pre>
            <div className="modal-actions">
              <button className="secondary" onClick={() => respondToConfirm(false)} type="button">
                Refuser
              </button>
              <button onClick={() => respondToConfirm(true)} type="button">
                Approuver
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

export function ProjectWorkspace() {
  const { id } = useParams();
  const projectId = Number(id);
  const [project, setProject] = useState<Project | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [openFiles, setOpenFiles] = useState<OpenFile[]>([]);
  const [activePath, setActivePath] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    getProject(projectId)
      .then(setProject)
      .catch((err) => setError(err instanceof Error ? err.message : "Failed to load project"));
  }, [projectId]);

  async function handleOpenFile(path: string) {
    const existing = openFiles.find((f) => f.path === path);
    if (existing) {
      setActivePath(path);
      return;
    }
    const file = await readFile(projectId, path);
    setOpenFiles((files) => [...files, { path, content: file.content, savedContent: file.content }]);
    setActivePath(path);
  }

  function closeFile(path: string) {
    setOpenFiles((files) => files.filter((f) => f.path !== path));
    if (activePath === path) {
      const remaining = openFiles.filter((f) => f.path !== path);
      setActivePath(remaining.length > 0 ? remaining[remaining.length - 1].path : null);
    }
  }

  async function handleSave() {
    const file = openFiles.find((f) => f.path === activePath);
    if (!file) return;
    setSaving(true);
    try {
      await writeFile(projectId, file.path, file.content);
      setOpenFiles((files) =>
        files.map((f) => (f.path === file.path ? { ...f, savedContent: f.content } : f))
      );
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to save");
    } finally {
      setSaving(false);
    }
  }

  useEffect(() => {
    function onKeyDown(event: KeyboardEvent) {
      if ((event.metaKey || event.ctrlKey) && event.key === "s") {
        event.preventDefault();
        handleSave();
      }
    }
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [openFiles, activePath]);

  if (error) return <p className="error">{error}</p>;
  if (!project) return <div className="workspace-loading">Loading…</div>;

  const activeFile = openFiles.find((f) => f.path === activePath);
  const dirty = activeFile ? activeFile.content !== activeFile.savedContent : false;

  return (
    <div className="workspace">
      <div className="workspace-columns">
        <div className="workspace-files">
          <div className="panel-title">{project.name}</div>
          <FileTree projectId={projectId} onOpenFile={handleOpenFile} activePath={activePath} />
        </div>

        <div className="workspace-editor">
          <div className="editor-tabs">
            {openFiles.map((file) => (
              <div
                key={file.path}
                className={`editor-tab ${activePath === file.path ? "active" : ""}`}
                onClick={() => setActivePath(file.path)}
              >
                {file.path.split("/").pop()}
                {file.content !== file.savedContent && <span className="dirty-dot" />}
                <span
                  className="editor-tab-close"
                  onClick={(e) => {
                    e.stopPropagation();
                    closeFile(file.path);
                  }}
                >
                  ×
                </span>
              </div>
            ))}
            {activeFile && (
              <button className="save-button" type="button" onClick={handleSave} disabled={!dirty || saving}>
                {saving ? "Saving…" : "Save"}
              </button>
            )}
          </div>
          <div className="editor-body">
            {activeFile ? (
              <Editor
                key={activeFile.path}
                language={languageFor(activeFile.path)}
                value={activeFile.content}
                theme="vs-dark"
                onChange={(value) =>
                  setOpenFiles((files) =>
                    files.map((f) => (f.path === activeFile.path ? { ...f, content: value ?? "" } : f))
                  )
                }
                options={{ minimap: { enabled: false }, fontSize: 13, automaticLayout: true }}
              />
            ) : (
              <div className="editor-empty">Select a file to open it.</div>
            )}
          </div>
          <TerminalPanel projectId={projectId} />
        </div>

        <AgentPanel workspacePath={project.path} />
      </div>
    </div>
  );
}
