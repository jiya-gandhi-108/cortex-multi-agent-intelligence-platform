"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";

import { Logo } from "@/components/logo";
import { Button } from "@/components/ui/button";
import { FieldError, Hint, Input, Label } from "@/components/ui/form";
import { Notice } from "@/components/ui/misc";
import { ApiError, auth } from "@/services/api";

function safeNext(): string {
  const next = new URLSearchParams(window.location.search).get("next") ?? "";
  return next.startsWith("/") && !next.startsWith("//") ? next : "/dashboard";
}

function validatePassword(pw: string): string | undefined {
  if (pw.length < 8) return "Use at least 8 characters.";
  if (pw.length > 128) return "Use at most 128 characters.";
  if (!/[a-zA-Z]/.test(pw) || !/\d/.test(pw)) return "Include at least one letter and one number.";
  if (pw.trim() !== pw) return "Remove spaces at the start or end.";
}

export function AuthForm({ mode }: { mode: "login" | "register" }) {
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [fieldErrors, setFieldErrors] = useState<{ email?: string; password?: string }>({});
  const [error, setError] = useState<string>();
  const [submitting, setSubmitting] = useState(false);
  const isRegister = mode === "register";

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    const errors: typeof fieldErrors = {};
    if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) errors.email = "Enter a valid email address.";
    if (isRegister) errors.password = validatePassword(password);
    else if (!password) errors.password = "Enter your password.";
    setFieldErrors(errors);
    if (errors.email || errors.password) return;

    setSubmitting(true);
    setError(undefined);
    try {
      await (isRegister ? auth.register(email, password) : auth.login(email, password));
      router.replace(safeNext());
    } catch (err) {
      const e = err as ApiError;
      setError(
        e.status === 409
          ? "An account with this email already exists. Sign in instead."
          : e.status === 429
            ? "Too many attempts. Wait a minute and try again."
            : e.message,
      );
      setSubmitting(false);
    }
  }

  return (
    <main className="flex min-h-dvh items-center justify-center px-5 py-12">
      <div className="w-full max-w-sm">
        <Link href="/" className="inline-block">
          <Logo />
        </Link>
        <h1 className="mt-10 text-2xl">{isRegister ? "Create your account" : "Sign in to Cortex"}</h1>
        <p className="mt-2 text-ink-muted">
          {isRegister ? "Start your first research project in a minute." : "Pick up your research where you left it."}
        </p>

        <form onSubmit={onSubmit} noValidate className="mt-8 space-y-5">
          {error && <Notice tone="error">{error}</Notice>}
          <div className="space-y-1.5">
            <Label htmlFor="email">Email</Label>
            <Input
              id="email"
              type="email"
              autoComplete="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              aria-invalid={Boolean(fieldErrors.email)}
              aria-describedby={fieldErrors.email ? "email-error" : undefined}
            />
            <FieldError id="email-error">{fieldErrors.email}</FieldError>
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="password">Password</Label>
            <Input
              id="password"
              type="password"
              autoComplete={isRegister ? "new-password" : "current-password"}
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              aria-invalid={Boolean(fieldErrors.password)}
              aria-describedby={isRegister ? "password-hint password-error" : "password-error"}
            />
            {isRegister && <Hint id="password-hint">At least 8 characters, with a letter and a number.</Hint>}
            <FieldError id="password-error">{fieldErrors.password}</FieldError>
          </div>
          <Button type="submit" className="w-full" size="lg" disabled={submitting}>
            {submitting ? (isRegister ? "Creating account…" : "Signing in…") : isRegister ? "Create account" : "Sign in"}
          </Button>
        </form>

        <p className="mt-8 text-sm text-ink-muted">
          {isRegister ? "Already have an account? " : "New to Cortex? "}
          <Link href={isRegister ? "/login" : "/register"} className="text-iris-strong hover:underline">
            {isRegister ? "Sign in" : "Create an account"}
          </Link>
        </p>
      </div>
    </main>
  );
}
