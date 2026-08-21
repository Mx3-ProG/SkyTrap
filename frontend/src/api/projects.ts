import { apiJson, apiRequest } from "./client";

export type Project = {
  id: number;
  name: string;
  path: string;
  created_at: string;
};

export type FileEntry = {
  name: string;
  is_dir: boolean;
};

export function listProjects(): Promise<Project[]> {
  return apiJson("/projects");
}

export function registerProject(name: string, path: string): Promise<Project> {
  return apiJson("/projects", { method: "POST", body: JSON.stringify({ name, path }) });
}

export function getProject(id: number): Promise<Project> {
  return apiJson(`/projects/${id}`);
}

export async function removeProject(id: number): Promise<void> {
  await apiRequest(`/projects/${id}`, { method: "DELETE" });
}

export function listFiles(projectId: number, path = "."): Promise<FileEntry[]> {
  return apiJson(`/projects/${projectId}/files?path=${encodeURIComponent(path)}`);
}

export function readFile(projectId: number, path: string): Promise<{ path: string; content: string }> {
  return apiJson(`/projects/${projectId}/files/content?path=${encodeURIComponent(path)}`);
}

export function writeFile(
  projectId: number,
  path: string,
  content: string
): Promise<{ path: string; bytes_written: number }> {
  return apiJson(`/projects/${projectId}/files/content`, {
    method: "PUT",
    body: JSON.stringify({ path, content }),
  });
}

export function gitStatus(projectId: number): Promise<{ success: boolean; output: string }> {
  return apiJson(`/projects/${projectId}/git/status`);
}

export function runCommand(projectId: number, command: string): Promise<{ success: boolean; output: string }> {
  return apiJson(`/projects/${projectId}/run`, {
    method: "POST",
    body: JSON.stringify({ command }),
  });
}
