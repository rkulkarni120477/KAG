"use client";

import { useEffect, useState, type ReactNode } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { ClipboardList, GraduationCap, LogOut, ShieldCheck, Sparkles, UserCheck } from "lucide-react";
import { Button } from "@/components/ui/button";
import { api, homeFor, type Role, type SessionUser } from "@/lib/api";

export function PortalShell({ role, title, subtitle, children }: { role: Exclude<Role, "student" | "admin">; title: string; subtitle: string; children: ReactNode }) {
  const router = useRouter();
  const [user, setUser] = useState<SessionUser | null>(null);
  const [loggingOut, setLoggingOut] = useState(false);

  useEffect(() => {
    api<SessionUser>("/auth/me")
      .then(current => {
        if (current.role !== role) router.replace(homeFor(current.role));
        else setUser(current);
      })
      .catch(() => router.replace("/"));
  }, [role, router]);

  async function logout() {
    setLoggingOut(true);
    await api("/auth/logout", { method: "POST" }).catch(() => undefined);
    router.replace("/");
  }

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand"><span className="brand-symbol"><Sparkles size={23} strokeWidth={1.8} /></span><span>northstar<span className="brand-dot">.</span></span></div>
        <div className="workspace">
          <span className="workspace-avatar">N</span>
          <div><strong>Northstar District</strong><span>{role === "teacher" ? "Teacher portal" : "Advisor portal"}</span></div>
        </div>
        <nav className="nav-group" aria-label="Portal navigation">
          <span className="nav-heading">Portal</span>
          <Link className={`nav-item ${role === "teacher" ? "active" : ""}`} href="/teacher"><GraduationCap size={17} /> Teacher</Link>
          <Link className={`nav-item ${role === "advisor" ? "active" : ""}`} href="/advisor"><UserCheck size={17} /> Advisor</Link>
          <Link className="nav-item" href="/"><ClipboardList size={17} /> Student workspace</Link>
        </nav>
        <div className="sidebar-bottom">
          <span className="section-kicker">{user ? user.name : "Signing in"}</span>
          <Button variant="outline" onClick={logout} disabled={loggingOut}><LogOut size={15} /><span>{loggingOut ? "Signing out…" : "Log out"}</span></Button>
        </div>
      </aside>
      <main className="main-shell">
        <header className="topbar">
          <div className="breadcrumb">Workspace<strong>{title}</strong></div>
          <span className="demo-pill"><span />Demo workspace</span>
        </header>
        <div className="portal-intro">
          <div>
            <span className="section-kicker">{role === "teacher" ? "INSTRUCTION" : "ADVISING"}</span>
            <h2>{title}</h2>
            <p className="muted">{subtitle}</p>
          </div>
          <span className="subtle-badge"><ShieldCheck size={13} /> Every view is authorized for this purpose</span>
        </div>
        {user ? children : <p className="empty-text">Checking your session…</p>}
      </main>
    </div>
  );
}

export function StatusPill({ status, waived }: { status: string; waived?: boolean }) {
  if (waived) return <span className="status waived">Waiver recorded</span>;
  if (status === "satisfied") return <span className="status satisfied">Mastered</span>;
  if (status === "not_satisfied") return <span className="status not_satisfied">Practice next</span>;
  return <span className="status unknown">Diagnostic needed</span>;
}

export function ReadyPill({ ready }: { ready: boolean }) {
  return <span className={`status ${ready ? "satisfied" : "blocked"}`}>{ready ? "Prerequisites met" : "Blocked"}</span>;
}
