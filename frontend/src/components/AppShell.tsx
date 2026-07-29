"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useAuth } from "@/lib/auth";

interface NavItem {
  href: string;
  label: string;
  icon: string;
  adminOnly?: boolean;
  children?: NavItem[];
}

const NAV: NavItem[] = [
  { href: "/swipe", label: "Swipe", icon: "🔥" },
  { href: "/pipeline", label: "My Pipeline", icon: "🚀" },
  { href: "/dashboard", label: "Dashboard", icon: "📊" },
  { href: "/leaderboard", label: "Leaderboard", icon: "🏆" },
  {
    href: "/new-incorps", label: "New Incorps", icon: "✨",
    children: [
      { href: "/new-incorps/high-value", label: "High-value", icon: "💎" },
      { href: "/new-incorps/pipeline", label: "My pipeline", icon: "📋" },
    ],
  },
  { href: "/analytics", label: "Analytics", icon: "📈", adminOnly: true },
  { href: "/admin", label: "Admin", icon: "⚙️", adminOnly: true },
];

export function AppShell({ children }: { children: React.ReactNode }) {
  const { user, logout } = useAuth();
  const pathname = usePathname();
  const router = useRouter();

  const items = NAV.filter((i) => !i.adminOnly || user?.role === "admin");

  function onLogout() {
    logout();
    router.replace("/login");
  }

  return (
    <div className="flex min-h-screen flex-col md:flex-row">
      {/* Sidebar (desktop) / top bar (mobile) */}
      <aside className="flex shrink-0 flex-col border-border bg-surface md:h-screen md:w-60 md:border-r md:sticky md:top-0">
        <div className="flex items-center justify-between border-b border-border px-4 py-4 md:justify-start md:gap-2">
          <span className="text-xl">🔥</span>
          <span className="font-bold">Matchmaker</span>
        </div>

        <nav className="flex gap-1 overflow-x-auto px-2 py-2 md:flex-col md:overflow-visible md:py-3">
          {items.map((item) => {
            const sectionActive = pathname === item.href || pathname.startsWith(item.href + "/");
            const link = (it: NavItem, sub = false) => {
              const active = pathname === it.href || pathname.startsWith(it.href + "/");
              return (
                <Link
                  key={it.href}
                  href={it.href}
                  data-nav={it.href}
                  className={`flex items-center gap-2.5 whitespace-nowrap rounded-lg py-2 text-sm font-medium transition
                    ${sub ? "px-3 md:pl-9 text-xs" : "px-3"}
                    ${active ? "bg-brand/10 text-brand" : "text-muted hover:bg-surface-2 hover:text-foreground"}`}
                >
                  <span>{it.icon}</span>
                  {it.label}
                </Link>
              );
            };
            return (
              <div key={item.href} className="contents md:block">
                {link(item)}
                {/* Sub-items appear once you're in that section. */}
                {item.children && sectionActive && item.children.map((child) => link(child, true))}
              </div>
            );
          })}
        </nav>

        <div className="mt-auto hidden border-t border-border p-3 md:block">
          <div className="mb-2 px-1 text-xs text-muted">
            <div className="font-medium capitalize text-foreground">{user?.username}</div>
            <div className="capitalize">{user?.role}</div>
          </div>
          <button
            onClick={onLogout}
            className="w-full rounded-lg px-3 py-2 text-left text-sm text-muted transition hover:bg-surface-2 hover:text-foreground"
          >
            Log out
          </button>
        </div>

        {/* Mobile logout */}
        <button
          onClick={onLogout}
          className="border-t border-border px-4 py-2 text-left text-sm text-muted md:hidden"
        >
          Log out ({user?.username})
        </button>
      </aside>

      <main className="flex-1 px-4 py-6 md:px-8 md:py-10">
        <div className="mx-auto w-full max-w-3xl">{children}</div>
      </main>
    </div>
  );
}
