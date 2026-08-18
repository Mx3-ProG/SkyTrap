import type { FormEvent } from "react";
import { useEffect, useState } from "react";
import { apiRequest } from "../api/client";
import { useTurnSocket } from "../ws/useTurnSocket";

export function Dashboard({ onLoggedOut }: { onLoggedOut: () => void }) {
  const { connectionState, progress, pendingConfirm, result, startTurn, respondToConfirm } =
    useTurnSocket();
  const [task, setTask] = useState("");
  const [workspace, setWorkspace] = useState("");
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    setError(null);
    setRunning(true);
    try {
      await startTurn(task, workspace);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to start turn");
      setRunning(false);
    }
  }

  useEffect(() => {
    if (result) setRunning(false);
  }, [result]);

  async function handleLogout() {
    await apiRequest("/auth/logout", { method: "POST" });
    onLoggedOut();
  }

  return (
    <div className="screen dashboard">
      <header className="dashboard-header">
        <h1>SkyTrap</h1>
        <span className={`status-dot status-${connectionState}`} title={connectionState} />
        <button className="link" onClick={handleLogout} type="button">
          Déconnexion
        </button>
      </header>

      <form className="task-form" onSubmit={handleSubmit}>
        <label>
          Espace de travail
          <input
            type="text"
            placeholder="/chemin/vers/le/projet"
            value={workspace}
            onChange={(event) => setWorkspace(event.target.value)}
            required
          />
        </label>
        <label>
          Tâche
          <textarea
            placeholder="Décris ce que SkyTrap doit faire..."
            value={task}
            onChange={(event) => setTask(event.target.value)}
            required
          />
        </label>
        {error && <p className="error">{error}</p>}
        <button type="submit" disabled={running || connectionState !== "open"}>
          {running ? "En cours..." : "Lancer"}
        </button>
      </form>

      {progress.length > 0 && (
        <div className="progress-log">
          {progress.map((step, index) => (
            <div className="progress-step" key={index}>
              <strong>{step.tool}</strong>
              <span>{step.observation}</span>
            </div>
          ))}
        </div>
      )}

      {result && (
        <div className={`result result-${result.status}`}>
          {result.status === "done" ? result.result : result.error}
        </div>
      )}

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
