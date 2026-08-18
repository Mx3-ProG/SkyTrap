import type { FormEvent } from "react";
import { useState } from "react";
import { apiJson } from "../api/client";

export function Otp({ email, onVerified }: { email: string; onVerified: () => void }) {
  const [code, setCode] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [resent, setResent] = useState(false);

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    setError(null);
    setLoading(true);
    try {
      await apiJson("/auth/otp/verify", { method: "POST", body: JSON.stringify({ email, code }) });
      onVerified();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Invalid code");
    } finally {
      setLoading(false);
    }
  }

  async function handleResend() {
    setError(null);
    setResent(false);
    try {
      await apiJson("/auth/otp/resend", { method: "POST", body: JSON.stringify({ email }) });
      setResent(true);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not resend code");
    }
  }

  return (
    <div className="screen">
      <div className="card">
        <h1>Vérification</h1>
        <p className="subtitle">Code envoyé à {email}</p>
        <form onSubmit={handleSubmit}>
          <label>
            Code
            <input
              type="text"
              inputMode="numeric"
              autoComplete="one-time-code"
              value={code}
              onChange={(event) => setCode(event.target.value)}
              required
            />
          </label>
          {error && <p className="error">{error}</p>}
          {resent && <p className="hint">Code renvoyé.</p>}
          <button type="submit" disabled={loading}>
            {loading ? "Vérification..." : "Vérifier"}
          </button>
        </form>
        <button className="link" onClick={handleResend} type="button">
          Renvoyer le code
        </button>
      </div>
    </div>
  );
}
