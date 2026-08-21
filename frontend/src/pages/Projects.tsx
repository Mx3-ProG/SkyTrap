import { type FormEvent, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { listProjects, registerProject, removeProject, type Project } from "../api/projects";

export function Projects() {
  const [projects, setProjects] = useState<Project[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [name, setName] = useState("");
  const [path, setPath] = useState("");
  const [registering, setRegistering] = useState(false);

  function refresh() {
    listProjects()
      .then(setProjects)
      .catch((err) => setError(err instanceof Error ? err.message : "Failed to load projects"));
  }

  useEffect(refresh, []);

  async function handleRegister(event: FormEvent) {
    event.preventDefault();
    setError(null);
    setRegistering(true);
    try {
      await registerProject(name, path);
      setName("");
      setPath("");
      refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to register project");
    } finally {
      setRegistering(false);
    }
  }

  async function handleRemove(id: number) {
    await removeProject(id);
    refresh();
  }

  return (
    <div className="projects-page">
      <h1>Projects</h1>

      <form className="register-project-form" onSubmit={handleRegister}>
        <input
          placeholder="Name"
          value={name}
          onChange={(e) => setName(e.target.value)}
          required
        />
        <input
          placeholder="Absolute path to an existing local directory"
          value={path}
          onChange={(e) => setPath(e.target.value)}
          required
        />
        <button type="submit" disabled={registering}>
          {registering ? "…" : "Add project"}
        </button>
      </form>
      {error && <p className="error">{error}</p>}

      <div className="project-list">
        {projects?.map((project) => (
          <div className="project-row" key={project.id}>
            <Link to={`/projects/${project.id}`} className="project-row-name">
              {project.name}
            </Link>
            <span className="project-row-path">{project.path}</span>
            <button className="icon-button" type="button" onClick={() => handleRemove(project.id)}>
              Remove
            </button>
          </div>
        ))}
        {projects && projects.length === 0 && <p className="empty-hint">No projects registered yet.</p>}
      </div>
    </div>
  );
}
