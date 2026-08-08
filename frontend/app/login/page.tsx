"use client";
import { useState } from "react";
import { login } from "../../lib/api";

export default function LoginPage() {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setError("");
    try {
      await login(email, password);
      window.location.href = "/";
    } catch {
      setError("Invalid email or password");
    }
  }
  return (
    <form onSubmit={submit} className="mx-auto mt-24 flex max-w-sm flex-col gap-4 p-6">
      <h1 className="text-xl font-semibold">Sign in to Aethermind</h1>
      <label className="flex flex-col gap-1 text-sm">Email
        <input aria-label="Email" type="email" value={email}
               onChange={(e) => setEmail(e.target.value)} className="rounded border p-2" />
      </label>
      <label className="flex flex-col gap-1 text-sm">Password
        <input aria-label="Password" type="password" value={password}
               onChange={(e) => setPassword(e.target.value)} className="rounded border p-2" />
      </label>
      {error && <p className="text-sm text-red-600">{error}</p>}
      <button type="submit" className="rounded bg-black p-2 text-white">Sign in</button>
    </form>
  );
}
