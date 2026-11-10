"use client";

import { useEffect, useState } from "react";
import { ClipboardList, FileText } from "lucide-react";
import { PortalShell, ReadyPill, StatusPill } from "@/components/portal/shell";
import { Button } from "@/components/ui/button";
import { api, type Brief, type LearningPath, type Student } from "@/lib/api";

type Summary = { student: Student; path: LearningPath | null; error: string | null };

export default function AdvisorPortal() {
  return (
    <PortalShell role="advisor" title="Advisor portal" subtitle="Scan your caseload for skill gaps and the next ready steps. Generate an advising brief grounded in cited evidence; advice is not an enrollment or placement decision.">
      <AdvisorWorkspace />
    </PortalShell>
  );
}

function nextStep(path: LearningPath) {
  return path.steps.find(s => s.ready && s.status !== "satisfied" && !s.waived) ?? null;
}

function AdvisorWorkspace() {
  const [summaries, setSummaries] = useState<Summary[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [brief, setBrief] = useState<Brief | null>(null);
  const [briefBusy, setBriefBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    api<{ items: Student[] }>("/roster")
      .then(async r => {
        const results = await Promise.all(r.items.map(async student => {
          try {
            return { student, path: await api<LearningPath>(`/students/${student.id}/learning-path`), error: null };
          } catch (e) {
            return { student, path: null, error: e instanceof Error ? e.message : "Unavailable" };
          }
        }));
        setSummaries(results);
        if (results[0]) setSelected(results[0].student.id);
      })
      .catch(e => setError(e.message))
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => setBrief(null), [selected]);

  const current = summaries.find(s => s.student.id === selected);

  async function generateBrief() {
    if (!selected) return;
    setBriefBusy(true);
    setError("");
    try {
      setBrief(await api<Brief>("/kag/recommend", {
        method: "POST",
        body: JSON.stringify({ goal: "Algebra I preparation: which skill sequence step should come next?", student_id: selected }),
      }));
    } catch (e) {
      setError(e instanceof Error ? e.message : "The brief could not be generated.");
    } finally {
      setBriefBusy(false);
    }
  }

  return (
    <>
      {error && <p className="notice" role="alert">{error}</p>}
      <div className="portal-grid">
        <section className="panel portal-detail">
          <div className="panel-heading"><div><span className="section-kicker">CASELOAD</span><h2>Students and next steps</h2></div><ClipboardList size={20} /></div>
          {loading && <p className="empty-text">Loading caseload…</p>}
          {!loading && summaries.length === 0 && <p className="empty-text">No students are assigned to you.</p>}
          <div className="table-wrap">
            <table>
              <thead><tr><th>Student</th><th>Mastered</th><th>Blocked steps</th><th>Next ready step</th></tr></thead>
              <tbody>
                {summaries.map(s => (
                  <tr key={s.student.id} className={selected === s.student.id ? "selected-row" : ""} onClick={() => setSelected(s.student.id)}>
                    <td><strong>{s.student.name}</strong><span className="muted"> · grade {s.student.grade}</span></td>
                    <td>{s.path ? `${s.path.mastered} / ${s.path.total}` : "—"}</td>
                    <td>{s.path ? s.path.steps.filter(x => !x.ready).length : "—"}</td>
                    <td>{s.path ? (nextStep(s.path)?.name ?? "None ready") : (s.error ?? "—")}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>

        <section className="panel">
          {!current?.path && <p className="empty-text">Select a student to see their path.</p>}
          {current?.path && (
            <>
              <span className="section-kicker">PATH · {current.student.name.toUpperCase()}</span>
              <h2>Skill gaps</h2>
              <div className="step-list">
                {current.path.steps.map(step => (
                  <article className="step-card" key={step.id}>
                    <div className="step-card-head"><strong>{step.name}</strong><div><StatusPill status={step.status} waived={step.waived} /> <ReadyPill ready={step.ready} /></div></div>
                    <p>{step.reason}</p>
                  </article>
                ))}
              </div>
              <Button onClick={generateBrief} disabled={briefBusy}><FileText size={15} /> {briefBusy ? "Generating brief…" : "Generate advising brief"}</Button>
              {brief && (
                <div className="brief">
                  <p>{brief.answer}</p>
                  {brief.ai_explanation && <div className="ai-explanation"><span className="section-kicker">AI-GENERATED EXPLANATION · ADVISORY</span><p>{brief.ai_explanation}</p></div>}
                  {brief.warning && <p className="warning-note">{brief.warning}</p>}
                  <span className="section-kicker">SOURCES · {brief.evidence.length}</span>
                  <ul className="citation-list-plain">
                    {brief.evidence.map(e => <li key={e.id}>{e.title} <small>{e.source_version}{e.illustrative ? " · illustrative" : ""}</small></li>)}
                  </ul>
                  <small className="muted">{brief.notice}</small>
                </div>
              )}
            </>
          )}
        </section>
      </div>
    </>
  );
}
