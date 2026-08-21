import { type FormEvent, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { listProjects, type Project } from "../api/projects";
import { useTurnSocket } from "../ws/useTurnSocket";

function greeting(): string {
  const hour = new Date().getHours();
  if (hour < 12) return "Good morning.";
  if (hour < 18) return "Good afternoon.";
  return "Good evening.";
}

export function Home() {
  const { connectionState, progress, pendingConfirm, result, startTurn, respondToConfirm } =
    useTurnSocket();
  const [projects, setProjects] = useState<Project[] | null>(null);
  const [projectsError, setProjectsError] = useState<string | null>(null);
  const [task, setTask] = useState("");
  const [selectedPath, setSelectedPath] = useState("");
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    listProjects()
      .then(setProjects)
      .catch((err) => setProjectsError(err instanceof Error ? err.message : "Failed to load projects"));
  }, []);

  useEffect(() => {
    if (result) setRunning(false);
  }, [result]);

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    if (!selectedPath) {
      setError("Choose a project first.");
      return;
    }
    setError(null);
    setRunning(true);
    try {
      await startTurn(task, selectedPath);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to start");
      setRunning(false);
    }
  }

  return (
    <div className="home">
      <h1 className="home-greeting">{greeting()}</h1>
      <p className="home-subtitle">What are we building?</p>

      <form className="ask-box" onSubmit={handleSubmit}>
        <select
          className="ask-project-select"
          value={selectedPath}
          onChange={(e) => setSelectedPath(e.target.value)}
        >
          <option value="">Select a project…</option>
          {projects?.map((p) => (
            <option key={p.id} value={p.path}>
              {p.name}
            </option>
          ))}
        </select>
        <div className="ask-input-row">
          <input
            className="ask-input"
            placeholder="Ask SkyTrap anything…"
            value={task}
            onChange={(e) => setTask(e.target.value)}
            required
          />
          <button type="submit" disabled={running || connectionState !== "open"}>
            {running ? "…" : "↑"}
          </button>
        </div>
        {error && <p className="error">{error}</p>}
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

      <section className="home-section">
        <h2>Recent Projects</h2>
        {projectsError && <p className="error">{projectsError}</p>}
        {projects && projects.length === 0 && (
          <p className="empty-hint">
            No project yet — <Link to="/projects">register one</Link>.
          </p>
        )}
        <div className="project-chip-row">
          {projects?.slice(0, 6).map((p) => (
            <Link className="project-chip" to={`/projects/${p.id}`} key={p.id}>
              {p.name}
            </Link>
          ))}
        </div>
      </section>
    </div>
  );
}
