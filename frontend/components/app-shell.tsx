"use client";

import { FilePlus2, History, LayoutDashboard, LogOut, Menu, Settings, X } from "lucide-react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { Suspense, useState } from "react";

import { Logo } from "@/components/logo";
import { Skeleton } from "@/components/ui/misc";
import { useApi } from "@/hooks/use-api";
import { cn } from "@/lib/utils";
import { auth } from "@/services/api";

const NAV = [
  { href: "/dashboard", label: "Dashboard", icon: LayoutDashboard },
  { href: "/research/new", label: "New research", icon: FilePlus2 },
  { href: "/history", label: "History", icon: History },
  { href: "/settings", label: "Settings", icon: Settings },
];

function NavLinks({ pathname, onNavigate }: { pathname: string | null; onNavigate?: () => void }) {
  const isActive = (href: string) =>
    pathname !== null &&
    (href === "/research/new" ? pathname === href : pathname === href || pathname.startsWith(`${href}/`));
  return (
    <nav aria-label="Main" className="flex flex-col gap-0.5">
      {NAV.map(({ href, label, icon: Icon }) => (
        <Link
          key={href}
          href={href}
          onClick={onNavigate}
          aria-current={isActive(href) ? "page" : undefined}
          className={cn(
            "flex h-9 items-center gap-3 rounded-[var(--radius-control)] px-3 text-sm text-ink-muted transition-colors hover:bg-raised hover:text-ink",
            isActive(href) && "bg-raised text-ink",
          )}
        >
          <Icon className={cn("size-4", isActive(href) && "text-iris")} aria-hidden />
          {label}
        </Link>
      ))}
    </nav>
  );
}

/** Highlights the current section; the pathname is runtime data, so it streams in. */
function CurrentNav({ onNavigate }: { onNavigate?: () => void }) {
  return <NavLinks pathname={usePathname()} onNavigate={onNavigate} />;
}

function PageFallback() {
  return (
    <div className="space-y-6" aria-busy="true">
      <Skeleton className="h-10 w-2/3" />
      <Skeleton className="h-24 w-full" />
      <Skeleton className="h-64 w-full" />
    </div>
  );
}

export function AppShell({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const { data: user } = useApi("me", auth.me);

  async function signOut() {
    await auth.logout().catch(() => undefined);
    router.replace("/login");
  }

  const nav = (
    <Suspense fallback={<NavLinks pathname={null} />}>
      <CurrentNav onNavigate={() => setOpen(false)} />
    </Suspense>
  );

  const account = (
    <div className="border-t border-line pt-4">
      <p className="truncate px-3 text-sm text-ink-muted" title={user?.email}>
        {user?.email ?? " "}
      </p>
      <button
        type="button"
        onClick={signOut}
        className="mt-1 flex h-9 w-full items-center gap-3 rounded-[var(--radius-control)] px-3 text-sm text-ink-muted hover:bg-raised hover:text-ink"
      >
        <LogOut className="size-4" aria-hidden />
        Sign out
      </button>
    </div>
  );

  return (
    <div className="min-h-dvh lg:grid lg:grid-cols-[15rem_1fr]">
      <aside className="sticky top-0 hidden h-dvh flex-col justify-between border-r border-line px-3 py-5 lg:flex">
        <div className="space-y-8">
          <Link href="/dashboard" className="block px-3">
            <Logo />
          </Link>
          {nav}
        </div>
        {account}
      </aside>

      <header className="sticky top-0 z-30 flex h-14 items-center justify-between border-b border-line bg-canvas/95 px-4 backdrop-blur lg:hidden">
        <Link href="/dashboard">
          <Logo />
        </Link>
        <button
          type="button"
          aria-label={open ? "Close menu" : "Open menu"}
          aria-expanded={open}
          onClick={() => setOpen((v) => !v)}
          className="flex size-9 items-center justify-center rounded-[var(--radius-control)] text-ink-muted hover:bg-raised"
        >
          {open ? <X className="size-5" /> : <Menu className="size-5" />}
        </button>
      </header>
      {open && (
        <div className="fixed inset-x-0 top-14 z-20 space-y-4 border-b border-line bg-canvas px-3 py-4 lg:hidden">
          {nav}
          {account}
        </div>
      )}

      <main className="min-w-0 px-4 py-8 sm:px-8 lg:px-12 lg:py-10">
        <div className="mx-auto max-w-6xl">
          <Suspense fallback={<PageFallback />}>{children}</Suspense>
        </div>
      </main>
    </div>
  );
}
