import { useEffect, useState } from "react";
import { apiRequest } from "./api/client";
import { Login } from "./pages/Login";
import { Otp } from "./pages/Otp";
import { Dashboard } from "./pages/Dashboard";

type View = "checking" | "login" | "otp" | "dashboard";

export function App() {
  const [view, setView] = useState<View>("checking");
  const [pendingEmail, setPendingEmail] = useState("");

  useEffect(() => {
    apiRequest("/auth/me")
      .then((response) => setView(response.ok ? "dashboard" : "login"))
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
    return <Otp email={pendingEmail} onVerified={() => setView("dashboard")} />;
  }

  return <Dashboard onLoggedOut={() => setView("login")} />;
}

export default App;
