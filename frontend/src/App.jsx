import { useEffect, useState } from 'react'
import { Activity, ArrowDownRight, ArrowUpRight, Check, ChevronRight, CircleAlert, Clock3, Database, FileSearch, LoaderCircle, LockKeyhole, Play, RotateCcw, ShieldCheck, Sparkles, Wrench } from 'lucide-react'

const API = ''
const DEMOS = [
  { label: 'Duplicate deliveries', task: 'Investigate the conversion spike for Autumn Launch on 2026-10-04. If duplicate deliveries caused it, show me the evidence, prepare a safe fix, and verify it after I approve.' },
  { label: 'Missing attribution', task: 'Investigate the conversion drop for Creator Referral on 2026-10-04. Check for missing campaign attribution, prepare a safe repair, and verify it after I approve.' },
  { label: 'Ambiguous case', task: 'Investigate the campaign conversion issue for an unknown source today and fix it.' },
]

async function request(path, options = {}) {
  const response = await fetch(`${API}/api${path}`, { headers: { 'Content-Type': 'application/json' }, ...options })
  const payload = await response.json().catch(() => ({}))
  if (!response.ok) throw new Error(payload.detail || `Request failed (${response.status})`)
  return payload
}

function prettyDate(value) {
  if (!value) return '—'
  return new Date(value).toLocaleString('en-IN', { dateStyle: 'medium', timeStyle: 'short' })
}

function App() {
  const [campaigns, setCampaigns] = useState([])
  const [run, setRun] = useState(null)
  const [task, setTask] = useState(DEMOS[0].task)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [answer, setAnswer] = useState('')

  const refreshCampaigns = async () => setCampaigns(await request('/campaigns'))
  useEffect(() => { refreshCampaigns().catch((e) => setError(e.message)) }, [])

  const runTask = async (event) => {
    event?.preventDefault()
    setBusy(true); setError(''); setNotice(''); setRun(null)
    try { setRun(await request('/runs', { method: 'POST', body: JSON.stringify({ task }) })) }
    catch (e) { setError(e.message) }
    finally { setBusy(false); refreshCampaigns().catch(() => {}) }
  }

  const decide = async (decision) => {
    if (!run) return
    setBusy(true); setError('')
    try { setRun(await request(`/runs/${run.run_id}/${decision}`, { method: 'POST' })); refreshCampaigns().catch(() => {}) }
    catch (e) { setError(e.message) }
    finally { setBusy(false) }
  }

  const sendAnswer = async (event) => {
    event.preventDefault(); if (!run || !answer.trim()) return
    setBusy(true); setError('')
    try { setRun(await request(`/runs/${run.run_id}/answer`, { method: 'POST', body: JSON.stringify({ answer }) })); setAnswer('') }
    catch (e) { setError(e.message) }
    finally { setBusy(false) }
  }

  const startFailureDemo = async () => {
    setError(''); setNotice('')
    try { await request('/demo/fail-next-read', { method: 'POST' }); setNotice('One transient failure is armed. Start a task and watch the worker recover.') }
    catch (e) { setError(e.message) }
  }

  const resetDemo = async () => {
    setBusy(true); setError(''); setNotice('')
    try { await request('/demo/reset', { method: 'POST' }); setRun(null); await refreshCampaigns(); setNotice('Demo state reset to the original seeded incidents.') }
    catch (e) { setError(e.message) }
    finally { setBusy(false) }
  }

  const dayMetrics = campaigns.map((c) => Number(c.attributed_conversions || 0))
  const total = dayMetrics.reduce((a, b) => a + b, 0)
  const duplicateAlerts = campaigns.reduce((sum, c) => sum + Number(c.duplicate_deliveries || 0), 0)
  const attributionAlerts = campaigns.reduce((sum, c) => sum + Number(c.attribution_gaps || 0), 0)
  const alertCount = Number(duplicateAlerts > 0) + Number(attributionAlerts > 0)
  const statusClass = run?.status || 'idle'

  return <div className="app-shell">
    <aside className="sidebar">
      <div className="brand"><div className="brand-mark"><Activity size={17} /></div><span>fieldnote<span className="brand-dot">.</span></span></div>
      <div className="workspace-label">WORKSPACE</div>
      <button className="nav-item active"><Activity size={16} /> Campaign health <span className="nav-indicator" /></button>
      <button className="nav-item"><FileSearch size={16} /> Incident runs</button>
      <div className="sidebar-spacer" />
      <div className="sidebar-card"><div className="sidebar-card-icon"><ShieldCheck size={16} /></div><div><strong>Sandbox environment</strong><span>Local data · safe to modify</span></div></div>
      <div className="profile"><div className="avatar">JR</div><div><strong>Jhanvi Rani</strong><span>Operations workspace</span></div><span className="profile-menu">···</span></div>
    </aside>

    <main className="main-content">
      <header className="topbar"><div className="breadcrumb">Workspace <ChevronRight size={14} /> <strong>Campaign health</strong></div><div className="topbar-right"><span className="live-indicator"><i /> Local demo</span><span className="topbar-date">Sunday, October 4, 2026</span></div></header>
      <section className="page-heading"><div><div className="eyebrow">OPERATIONS OVERVIEW <span>·</span> LAST 24 HOURS</div><h1>Campaign health</h1><p>Monitor conversion data and investigate issues with the local operations worker.</p></div><button className="button button-quiet" onClick={resetDemo} disabled={busy}><RotateCcw size={15} /> Reset demo</button></section>
      {error && <div className="banner banner-error"><CircleAlert size={16} /> {error}</div>}
      {notice && <div className="banner banner-info"><Check size={16} /> {notice}</div>}

      <section className="metric-grid">
        <Metric label="Attributed conversions" value={total} note="Across 3 campaigns · Oct 4" icon={<Activity size={17} />} trend="up" />
        <Metric label="Campaigns monitored" value="03" note="All ingestion sources connected" icon={<Database size={17} />} />
        <Metric label="Data quality alerts" value={String(alertCount).padStart(2, '0')} note={`${duplicateAlerts ? '1 spike' : 'No spikes'} · ${attributionAlerts ? `${attributionAlerts} attribution gaps` : 'attribution clear'}`} icon={<CircleAlert size={17} />} trend="alert" />
        <Metric label="Last sync" value="09:42" note="Event stream · 18 sec ago" icon={<Clock3 size={17} />} />
      </section>

      <div className="content-grid">
        <section className="panel campaigns-panel">
          <div className="panel-heading"><div><h2>Campaign performance</h2><p>Conversions recorded on October 4, 2026</p></div><button className="text-button">View data <ChevronRight size={14} /></button></div>
          <div className="table-wrap"><table><thead><tr><th>CAMPAIGN</th><th>CHANNEL</th><th>CONVERSIONS</th><th>QUALITY</th></tr></thead><tbody>
            {campaigns.map((c) => <tr key={c.campaign_id}><td><div className="campaign-name"><span className={`campaign-icon ${c.campaign_id.toLowerCase()}`}>{c.name.slice(0, 1)}</span><div><strong>{c.name}</strong><small>{c.campaign_id}</small></div></div></td><td className="muted-cell">{c.channel}</td><td className="number-cell">{c.attributed_conversions}</td><td>{c.campaign_id === 'CMP-101' ? <span className="quality quality-alert"><i /> Spike detected</span> : c.campaign_id === 'CMP-202' ? <span className="quality quality-warning"><i /> Attribution gap</span> : <span className="quality quality-good"><i /> Healthy</span>}</td></tr>)}
          </tbody></table></div>
          <div className="table-foot"><span><span className="foot-dot" /> Ingestion healthy</span><span>Metrics refreshed just now</span></div>
        </section>

        <section className="panel alerts-panel"><div className="panel-heading"><div><h2>Needs attention</h2><p>Issues detected by data quality rules</p></div><span className="count-pill">{alertCount}</span></div>
          {duplicateAlerts > 0 && <div className="alert-item"><span className="alert-icon alert-red"><ArrowUpRight size={16} /></span><div className="alert-copy"><div className="alert-title">Conversion spike <span className="severity high">HIGH</span></div><p>Autumn Launch · {duplicateAlerts} repeated deliveries detected</p><small>Oct 4, 9:42 AM <span>·</span> 15% above unique order count</small></div><button className="arrow-button" onClick={() => { setTask(DEMOS[0].task); document.getElementById('task-input')?.focus() }}><ChevronRight size={16} /></button></div>}
          {attributionAlerts > 0 && <div className="alert-item"><span className="alert-icon alert-amber"><ArrowDownRight size={16} /></span><div className="alert-copy"><div className="alert-title">Attribution gap <span className="severity medium">MEDIUM</span></div><p>Creator Referral · {attributionAlerts} conversion events unmapped</p><small>Oct 4, 9:38 AM <span>·</span> Metric undercounted</small></div><button className="arrow-button" onClick={() => { setTask(DEMOS[1].task); document.getElementById('task-input')?.focus() }}><ChevronRight size={16} /></button></div>}
          {!alertCount && <div className="alert-empty"><span className="quality quality-good"><i /> All data quality checks clear</span><p>The seeded incidents are resolved.</p></div>}
          <button className="text-button all-alerts">See all alerts <ChevronRight size={14} /></button>
        </section>
      </div>

      <section className="worker-panel panel"><div className="worker-title"><div className="worker-emblem"><Sparkles size={17} /></div><div><h2>Ask the operations worker</h2><p>Describe a supported campaign issue. The worker will investigate and ask before changing data.</p></div><span className="powered-by">LOCAL OLLAMA <span>·</span> SANDBOX</span></div>
        <form onSubmit={runTask} className="task-form"><textarea id="task-input" value={task} onChange={(e) => setTask(e.target.value)} placeholder="e.g. Investigate the conversion spike for Autumn Launch..." rows={2} /><div className="task-form-bottom"><div className="demo-chips">{DEMOS.map((d) => <button key={d.label} type="button" className="demo-chip" onClick={() => setTask(d.task)}>{d.label}</button>)}</div><button className="button button-primary" type="submit" disabled={busy || task.trim().length < 5}>{busy ? <><LoaderCircle size={15} className="spin" /> Working</> : <><Play size={14} fill="currentColor" /> Run investigation</>}</button></div></form>
        <div className="worker-tools"><span>AVAILABLE TOOLS</span><i /><span>Campaign metrics</span><i /><span>Event records</span><i /><span>Runbooks</span><i /><span>Approval-gated repair</span><button className="simulate-button" onClick={startFailureDemo} title="Cause one temporary read error for the next run"><Wrench size={13} /> Simulate transient failure</button></div>
      </section>

      {run && <section className="run-panel panel"><div className="run-heading"><div><div className="eyebrow">INVESTIGATION RUN</div><h2>{run.task}</h2></div><span className={`status-pill ${statusClass}`}><span />{run.status.replaceAll('_', ' ')}</span></div>
        {run.summary && <div className={`run-summary ${run.status}`}><Sparkles size={16} /><span>{run.summary}</span></div>}
        {run.pending_action && <div className="approval-card"><div className="approval-header"><div className="approval-lock"><LockKeyhole size={16} /></div><div><strong>Approval required</strong><span>The worker prepared this change for your review.</span></div></div><div className="approval-details"><div><small>PROPOSED ACTION</small><p>{run.pending_action.preview.description}</p></div><div><small>CAMPAIGN / DATE</small><p>{run.pending_action.preview.campaign_name} · {run.pending_action.preview.event_date}</p></div><div><small>RECORDS AFFECTED</small><p>{run.pending_action.preview.affected_count} event deliveries</p></div><div><small>EVIDENCE IDS</small><p className="evidence-ids">{run.pending_action.preview.delivery_ids.join(', ')}</p></div></div><div className="approval-actions"><span>Review the records above before approving.</span><div><button className="button button-quiet" disabled={busy} onClick={() => decide('reject')}>Reject</button><button className="button button-approve" disabled={busy} onClick={() => decide('approve')}>{busy ? <LoaderCircle size={15} className="spin" /> : <Check size={15} />} Approve and apply</button></div></div></div>}
        {run.status === 'awaiting_input' && <form className="answer-form" onSubmit={sendAnswer}><input value={answer} onChange={(e) => setAnswer(e.target.value)} placeholder="Answer the worker’s question…" /><button className="button button-primary" disabled={busy || !answer.trim()}>Send answer</button></form>}
        <div className="timeline-heading"><span>WORKER ACTIVITY</span><span>{run.events.length} events</span></div>
        <div className="timeline">{run.events.map((event) => <TimelineEvent key={event.event_id} event={event} />)}</div>
      </section>}
      <footer className="footer"><span>Fieldnote Ops Worker <span>·</span> Prototype</span><span>All records are synthetic and stored locally.</span></footer>
    </main>
  </div>
}

function Metric({ label, value, note, icon, trend }) {
  return <div className="metric-card"><div className="metric-top"><span>{label}</span><span className={`metric-icon ${trend || ''}`}>{icon}</span></div><div className="metric-value">{value}</div><div className="metric-note">{note}</div></div>
}

function TimelineEvent({ event }) {
  const data = event.details || {}
  const failed = data.result?.error
  const label = event.kind === 'tool' ? event.tool_name?.replaceAll('_', ' ') : event.kind.replaceAll('_', ' ')
  return <div className={`timeline-event ${failed ? 'has-error' : ''}`}><div className={`timeline-icon ${failed ? 'error' : event.kind === 'approved_and_applied' ? 'success' : ''}`}>{failed ? <CircleAlert size={14} /> : event.kind === 'approved_and_applied' || event.kind === 'completed' ? <Check size={14} /> : event.kind === 'tool' ? <Wrench size={14} /> : <Activity size={14} />}</div><div className="timeline-content"><div className="timeline-label">{label}<time>{prettyDate(event.created_at)}</time></div>{event.kind === 'tool' ? <><p>{failed ? data.result.error : toolDetail(event.tool_name, data.result)}</p><details><summary>View input and output</summary><pre>{JSON.stringify(data, null, 2)}</pre></details></> : <p>{event.details?.summary || event.details?.task || event.details?.reason || event.details?.question || event.details?.description || 'Run state updated.'}</p>}</div></div>
}

function toolDetail(name, result) {
  if (!result) return 'Tool returned no data.'
  if (result.awaiting_approval) return `Prepared a change affecting ${result.preview?.affected_count ?? 0} deliveries.`
  if (result.error) return result.error
  if (name === 'list_campaigns') return `Found ${result.length} campaigns.`
  if (name === 'get_campaign_metrics') return `Read ${result.attributed_conversions} attributed conversions; ${result.unattributed_conversions} remain unmapped.`
  if (name === 'inspect_events') return `Inspected ${result.count} matching event deliveries.`
  if (name === 'search_runbooks') return `Retrieved ${result.length} runbook(s): ${result.map((x) => x.runbook_id).join(', ') || 'no matches'}.`
  return JSON.stringify(result)
}

export default App
