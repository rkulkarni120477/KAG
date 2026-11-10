"use client";

import { useEffect, useMemo, useState, type FormEvent } from "react";
import { Ban, Plus, Users } from "lucide-react";
import { PortalShell, ReadyPill, StatusPill } from "@/components/portal/shell";
import { Button } from "@/components/ui/button";
import { api, type LearningPath, type PathStep, type Student, type Waiver } from "@/lib/api";

const today = () => new Date().toISOString().slice(0, 10);

export default function TeacherPortal() {
  return (
    <PortalShell role="teacher" title="Teacher portal" subtitle="Review each learner's prerequisite path, the rule groups behind it, and record documented waivers. Decisions remain with the educator.">
      <TeacherWorkspace />
    </PortalShell>
  );
}

function TeacherWorkspace() {
  const [students, setStudents] = useState<Student[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [path, setPath] = useState<LearningPath | null>(null);
  const [waivers, setWaivers] = useState<Waiver[]>([]);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);
  const [form, setForm] = useState({ skill_id: "", reason: "", effective_from: today(), effective_until: "" });

  useEffect(() => {
    api<{ items: Student[] }>("/roster").then(r => {
      setStudents(r.items);
      if (r.items[0]) setSelected(r.items[0].id);
    }).catch(e => setError(e.message));
  }, []);

  useEffect(() => {
    if (!selected) return;
    setError("");
    Promise.all([
      api<LearningPath>(`/students/${selected}/learning-path`),
      api<{ items: Waiver[] }>(`/students/${selected}/waivers`),
    ]).then(([p, w]) => {
      setPath(p);
      setWaivers(w.items);
      setForm(f => ({ ...f, skill_id: f.skill_id || p.steps[0]?.id || "" }));
    }).catch(e => setError(e.message));
  }, [selected]);

  const names = useMemo(() => new Map((path?.steps ?? []).map(s => [s.id, s.name])), [path]);
  const activeWaivers = waivers.filter(w => !w.revoked_at);

  async function recordWaiver(event: FormEvent) {
    event.preventDefault();
    if (!selected) return;
    setBusy(true);
    setError("");
    setNotice("");
    try {
      await api(`/students/${selected}/waivers`, {
        method: "POST",
        body: JSON.stringify({
          skill_id: form.skill_id,
          reason: form.reason,
          effective_from: form.effective_from,
          effective_until: form.effective_until || null,
        }),
      });
      setNotice("Waiver recorded. The path below reflects it.");
      setForm(f => ({ ...f, reason: "" }));
      const [p, w] = await Promise.all([
        api<LearningPath>(`/students/${selected}/learning-path`),
        api<{ items: Waiver[] }>(`/students/${selected}/waivers`),
      ]);
      setPath(p);
      setWaivers(w.items);
    } catch (e) {
      setError(e instanceof Error ? e.message : "The waiver could not be recorded.");
    } finally {
      setBusy(false);
    }
  }

  async function revoke(waiverId: string) {
    if (!selected) return;
    setBusy(true);
    setError("");
    try {
      await api(`/students/${selected}/waivers/${waiverId}`, { method: "DELETE" });
      setNotice("Waiver revoked.");
      const [p, w] = await Promise.all([
        api<LearningPath>(`/students/${selected}/learning-path`),
        api<{ items: Waiver[] }>(`/students/${selected}/waivers`),
      ]);
      setPath(p);
      setWaivers(w.items);
    } catch (e) {
      setError(e instanceof Error ? e.message : "The waiver could not be revoked.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="portal-grid">
      <section className="panel">
        <div className="panel-heading"><div><span className="section-kicker">ROSTER</span><h2>Your students</h2></div><Users size={20} /></div>
        {students.length === 0 && <p className="empty-text">No students are assigned to you.</p>}
        <div className="portal-list" role="listbox" aria-label="Students">
          {students.map(s => (
            <button key={s.id} role="option" aria-selected={selected === s.id} className={`portal-row ${selected === s.id ? "selected" : ""}`} onClick={() => setSelected(s.id)}>
              <strong>{s.name}</strong><span>Grade {s.grade}</span>
            </button>
          ))}
        </div>
      </section>

      <section className="panel portal-detail">
        {error && <p className="notice" role="alert">{error}</p>}
        {notice && <p className="notice" role="status">{notice}</p>}
        {!path && !error && <p className="empty-text">Select a student to review their path.</p>}
        {path && (
          <>
            <div className="panel-heading">
              <div><span className="section-kicker">LEARNING PATH · AS OF {new Date(path.as_of).toLocaleDateString()}</span><h2>{path.student.name}</h2></div>
              <span className="subtle-badge">{path.mastered} of {path.total} mastered · threshold {path.threshold}</span>
            </div>
            <div className="step-list">
              {path.steps.map(step => <StepCard key={step.id} step={step} names={names} />)}
            </div>
            <p className="muted">{path.notice}</p>

            <div className="waiver-block">
              <span className="section-kicker">WAIVERS</span>
              <h3>Document an exception</h3>
              <form className="waiver-form" onSubmit={recordWaiver}>
                <label>Skill
                  <select value={form.skill_id} onChange={e => setForm({ ...form, skill_id: e.target.value })} required>
                    {path.steps.map(s => <option key={s.id} value={s.id}>{s.name}</option>)}
                  </select>
                </label>
                <label>Effective from
                  <input type="date" value={form.effective_from} onChange={e => setForm({ ...form, effective_from: e.target.value })} required />
                </label>
                <label>Effective until (optional)
                  <input type="date" value={form.effective_until} onChange={e => setForm({ ...form, effective_until: e.target.value })} />
                </label>
                <label className="wide">Reason (recorded for audit)
                  <textarea rows={2} minLength={3} maxLength={500} value={form.reason} onChange={e => setForm({ ...form, reason: e.target.value })} required />
                </label>
                <Button disabled={busy || form.reason.trim().length < 3}><Plus size={15} /> Record waiver</Button>
              </form>
              {activeWaivers.length === 0 ? <p className="muted">No waivers recorded for this student.</p> : (
                <ul className="waiver-list">
                  {waivers.map(w => (
                    <li key={w.id} className={w.revoked_at ? "revoked" : ""}>
                      <div><strong>{names.get(w.skill_id) ?? w.skill_id}</strong><span>{w.reason}</span><small>From {w.effective_from.slice(0, 10)}{w.effective_until ? ` until ${w.effective_until.slice(0, 10)}` : ""}{w.revoked_at ? " · revoked" : ""}</small></div>
                      {!w.revoked_at && <Button variant="outline" size="small" disabled={busy} onClick={() => revoke(w.id)}><Ban size={14} /> Revoke</Button>}
                    </li>
                  ))}
                </ul>
              )}
            </div>
          </>
        )}
      </section>
    </div>
  );
}

function StepCard({ step, names }: { step: PathStep; names: Map<string, string> }) {
  return (
    <article className="step-card">
      <div className="step-card-head">
        <strong>{step.name}</strong>
        <div><StatusPill status={step.status} waived={step.waived} /> <ReadyPill ready={step.ready} /></div>
      </div>
      <p>{step.reason}</p>
      {step.requirement_groups.map(group => (
        <div className="requirement" key={group.group_id}>
          <span className="section-kicker">{group.logic === "ALL" ? "ALL OF" : "ANY OF"} · {group.met ? "met" : "not met"}</span>
          <ul>
            {group.requires.map(r => <li key={r.id} className={r.met ? "met" : "unmet"}>{names.get(r.id) ?? r.id}</li>)}
          </ul>
        </div>
      ))}
      {step.assessment && <small className="muted">Evidence: {step.assessment}{step.score !== null ? ` · score ${step.score}` : ""}</small>}
    </article>
  );
}
