import { useEffect, useState } from "react";
import { BrowserRouter, Route, Routes } from "react-router-dom";
import { apiRequest } from "./api/client";
import { Login } from "./pages/Login";
import { Otp } from "./pages/Otp";
import { Shell } from "./app/Shell";
import { Home } from "./pages/Home";
import { Projects } from "./pages/Projects";
import { ProjectWorkspace } from "./pages/ProjectWorkspace";

type View = "checking" | "login" | "otp" | "app";

export function App() {
  const [view, setView] = useState<View>("checking");
  const [pendingEmail, setPendingEmail] = useState("");

  useEffect(() => {
    apiRequest("/auth/me")
      .then((response) => setView(response.ok ? "app" : "login"))
      .catch(() => setView("login"));
  }, []);

  if (view === "checking") return <div className="screen" />;

  if (view === "login") {
    return (
      <Login
        onOtpSent={(email) => {
          setPendingEmail(email);
          setView("otp");
        }}
      />
    );
  }

  if (view === "otp") {
    return <Otp email={pendingEmail} onVerified={() => setView("app")} />;
  }

  return (
    <BrowserRouter>
      <Routes>
        <Route element={<Shell onLoggedOut={() => setView("login")} />}>
          <Route path="/" element={<Home />} />
          <Route path="/projects" element={<Projects />} />
          <Route path="/projects/:id" element={<ProjectWorkspace />} />
        </Route>
      </Routes>
    </BrowserRouter>
  );
}

export default App;
