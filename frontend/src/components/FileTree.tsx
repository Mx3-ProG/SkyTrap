import { useEffect, useState } from "react";
import { listFiles, type FileEntry } from "../api/projects";

function DirectoryChildren({
  projectId,
  path,
  onOpenFile,
  activePath,
}: {
  projectId: number;
  path: string;
  onOpenFile: (path: string) => void;
  activePath: string | null;
}) {
  const [entries, setEntries] = useState<FileEntry[] | null>(null);

  useEffect(() => {
    setEntries(null);
    listFiles(projectId, path)
      .then(setEntries)
      .catch(() => setEntries([]));
  }, [projectId, path]);

  if (entries === null) return <div className="file-tree-loading">…</div>;

  return (
    <div className="file-tree-children">
      {entries.map((entry) => {
        const childPath = path === "." ? entry.name : `${path}/${entry.name}`;
        return entry.is_dir ? (
          <Directory
            key={childPath}
            projectId={projectId}
            path={childPath}
            name={entry.name}
            onOpenFile={onOpenFile}
            activePath={activePath}
          />
        ) : (
          <button
            key={childPath}
            type="button"
            className={`file-tree-file ${activePath === childPath ? "active" : ""}`}
            onClick={() => onOpenFile(childPath)}
          >
            {entry.name}
          </button>
        );
      })}
    </div>
  );
}

function Directory({
  projectId,
  path,
  name,
  onOpenFile,
  activePath,
  startExpanded = false,
}: {
  projectId: number;
  path: string;
  name: string;
  onOpenFile: (path: string) => void;
  activePath: string | null;
  startExpanded?: boolean;
}) {
  const [expanded, setExpanded] = useState(startExpanded);
  return (
    <div className="file-tree-dir">
      {name && (
        <button type="button" className="file-tree-dir-toggle" onClick={() => setExpanded((e) => !e)}>
          <span className="file-tree-caret">{expanded ? "▾" : "▸"}</span> {name}
        </button>
      )}
      {expanded && (
        <DirectoryChildren projectId={projectId} path={path} onOpenFile={onOpenFile} activePath={activePath} />
      )}
    </div>
  );
}

export function FileTree({
  projectId,
  onOpenFile,
  activePath,
}: {
  projectId: number;
  onOpenFile: (path: string) => void;
  activePath: string | null;
}) {
  return (
    <div className="file-tree">
      <Directory projectId={projectId} path="." name="" onOpenFile={onOpenFile} activePath={activePath} startExpanded />
    </div>
  );
}
