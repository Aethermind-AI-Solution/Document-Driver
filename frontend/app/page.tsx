"use client";
import { useEffect, useRef, useState } from "react";
import { Activity, ArrowUpRight, Bot, CheckCircle2, CircleDashed, Clock3, FileSearch, ShieldCheck, TriangleAlert, UploadCloud } from "lucide-react";
import { api, downloadFile, me, pollDocument } from "../lib/api";
import { canUpload, canReview, isAdmin, type Role } from "../lib/auth";
import { deriveAgentTimeline, traceToSteps, type AgentStatus, type AgentStep, type TraceEntry } from "../lib/agent-timeline";
import ConfirmDialog from "./components/ConfirmDialog";
type Schema={key:string,name:string,fields:unknown[]}; type Doc={id:number,filename:string,document_type:string,status:string,confidence?:number,upload_date:string,review_required:boolean,processing_time?:number|null,pipeline_trace?:TraceEntry[]|null,anomalies?:string[]|null};
type AgentEvent={id:number;agent:string;message:string;status:AgentStatus};
const badges:Record<string,string>={approved:"bg-emerald-50 text-emerald-700",rejected:"bg-rose-50 text-rose-700",review_required:"bg-amber-50 text-amber-700",processed:"bg-blue-50 text-blue-700",error:"bg-red-50 text-red-700",uploaded:"bg-slate-100 text-slate-600"};
export default function Home(){
 const [schemas,setSchemas]=useState<Schema[]>([]),[docs,setDocs]=useState<Doc[]>([]),[type,setType]=useState("auto"),[selected,setSelected]=useState<any>(null),[loading,setLoading]=useState(false),[message,setMessage]=useState(""),[agentEvents,setAgentEvents]=useState<AgentEvent[]>([]),[retrying,setRetrying]=useState<Set<number>>(new Set()); const input=useRef<HTMLInputElement>(null); const activityId=useRef(0);
 const [q,setQ]=useState(""),[statusFilter,setStatusFilter]=useState(""),[offset,setOffset]=useState(0),[total,setTotal]=useState(0),[stats,setStats]=useState<{total:number,review_required:number,avg_processing_time:number|null}|null>(null);
 const DEFAULT_DOCUMENT_TYPE="invoice";
 const PAGE_SIZE=20;
 const STATUS_OPTIONS=["","uploaded","processed","review_required","approved","rejected","error"];
 const stepsToEvents=(steps:AgentStep[]):AgentEvent[]=>steps.map(s=>({id:++activityId.current,agent:s.agent,message:s.message,status:s.status}));
 const openDocument=async(id:number)=>{
   const doc=await api(`/document/${id}`);
   setSelected(doc);
   const schemaName=schemas.find(s=>s.key===doc.document_type)?.name||doc.document_type.replaceAll("_"," ");
   const steps=doc.pipeline_trace?.length?traceToSteps(doc.pipeline_trace):deriveAgentTimeline({schemaName,fields:doc.fields});
   setAgentEvents(stepsToEvents(steps));
 };
 const [role,setRole]=useState<Role|null>(null);
 const refresh=async()=>{try{
   const [docsRes,statsRes]=await Promise.all([
     api(`/documents?q=${encodeURIComponent(q)}&status=${statusFilter}&limit=${PAGE_SIZE}&offset=${offset}`),
     api("/documents/stats"),
   ]);
   setDocs(docsRes.items);setTotal(docsRes.total);setStats(statsRes);
 }catch(e:any){if(e?.status===401){window.location.href="/login"}else{setMessage("API unavailable — start the FastAPI service to connect the workspace.")}}};
 useEffect(()=>{me().then((u:any)=>{setRole(u.role);api("/schemas").then(setSchemas).catch(()=>{})}).catch((e:any)=>{if(e?.status===401){window.location.href="/login"}else{setMessage("API unavailable — start the FastAPI service to connect the workspace.")}})},[]);
 useEffect(()=>{const t=window.setTimeout(refresh,250);return()=>window.clearTimeout(t);},[q,statusFilter,offset]);
 const begin=(agent:string,message:string)=>{const id=++activityId.current;setAgentEvents(events=>[...events,{id,agent,message,status:"working"}]);return id};
 const finish=(id:number,message:string,status:AgentStatus="complete")=>setAgentEvents(events=>events.map(event=>event.id===id?{...event,message,status}:event));
 const STEP_DELAY_MS=700;
 const wait=(ms:number)=>new Promise<void>(res=>window.setTimeout(res,ms));
 async function playTimeline(steps:AgentStep[]){
   for(const step of steps){
     const id=++activityId.current;
     setAgentEvents(events=>[...events,{id,agent:step.agent,message:"Working…",status:"working"}]);
     await wait(STEP_DELAY_MS);
     setAgentEvents(events=>events.map(e=>e.id===id?{...e,status:step.status,message:step.message}:e));
   }
 }
 async function upload(file:File){
   setLoading(true);setMessage("Uploading document…");setAgentEvents([]);
   try{
     const intakeId=++activityId.current;
     setAgentEvents([{id:intakeId,agent:"Intake Agent",message:"Working…",status:"working"}]);
     const hint=type==="auto"?DEFAULT_DOCUMENT_TYPE:type;
     const form=new FormData();form.append("file",file);
     const doc=await api(`/upload?document_type=${hint}`,{method:"POST",body:form});
     setAgentEvents(events=>events.map(e=>e.id===intakeId?{...e,status:"complete",message:"Document received"}:e));
     setMessage("Extracting fields with AI — this can take a few seconds…");
     const procId=++activityId.current;
     setAgentEvents(events=>[...events,{id:procId,agent:"Extraction Agent",message:"Reading the document…",status:"working"}]);
     await api(`/process/${doc.id}`,{method:"POST"});
     const complete=await pollDocument(doc.id);
     if(complete.status==="error")throw new Error("Processing failed");
     setAgentEvents(events=>events.filter(e=>e.id!==procId));
     const schemaName=schemas.find(s=>s.key===hint)?.name||hint.replaceAll("_"," ");
     const steps=(complete.pipeline_trace?.length?traceToSteps(complete.pipeline_trace):deriveAgentTimeline({schemaName,fields:complete.fields})).filter((s:AgentStep)=>s.agent!=="Intake Agent");
     await playTimeline(steps);
     setSelected(complete);
     setMessage("Processing complete. Review flagged fields before approval.");
     refresh();
   }catch(e:any){
     setAgentEvents(events=>events.map(ev=>ev.status==="working"?{...ev,status:"attention",message:e.message||"Failed"}:ev));
     setMessage(e.message||"Upload failed. Please retry.");
   }finally{setLoading(false)}
 }
 async function retry(id:number){
   if(retrying.has(id))return;
   setRetrying(s=>new Set(s).add(id));
   try{
     await api(`/process/${id}`,{method:"POST"});
     const done=await pollDocument(id);
     await refresh();
     if(done.status!=="error")openDocument(id);
   }catch(e:any){setMessage(e?.message||"Retry failed.")}
   finally{setRetrying(s=>{const n=new Set(s);n.delete(id);return n})}
 }
 const processed=stats?.total??0, reviewed=stats?.review_required??0; const rateLabel=processed?`${Math.round((1-reviewed/processed)*100)}%`:"—"; const avgTime=stats?.avg_processing_time!=null?`${stats.avg_processing_time.toFixed(1)}s`:"—";
 return <main className="min-h-screen"><header className="border-b border-slate-200 bg-white"><div className="mx-auto flex max-w-7xl items-center justify-between px-6 py-4"><div className="flex items-center gap-3"><div className="rounded-lg bg-slate-900 p-2 text-white"><FileSearch size={20}/></div><div><h1 className="font-semibold">Aethermind Document Intelligence</h1><p className="text-xs text-slate-500">Operations workspace</p></div></div><div className="flex items-center gap-4 text-sm text-slate-500">{role&&isAdmin(role)&&<a href="/users" className="font-semibold text-slate-700 hover:underline">Users</a>}{role&&isAdmin(role)&&<a href="/webhooks" className="font-semibold text-slate-700 hover:underline">Webhooks</a>}{role&&isAdmin(role)&&<a href="/schemas/suggested" className="font-semibold text-slate-700 hover:underline">Suggested Schemas</a>}<span className="flex items-center gap-2"><ShieldCheck size={16}/> Human-in-the-loop controls</span></div></div></header>
 <div className="mx-auto grid max-w-7xl gap-6 px-6 py-8 lg:grid-cols-[1.15fr_.85fr]">
 <section><div className="mb-6"><p className="label">Document Intelligence Engine</p><h2 className="mt-2 text-3xl font-semibold tracking-tight">Process every document, with the right schema.</h2><p className="mt-2 max-w-2xl text-slate-600">Route invoices, shipping records, claims, medical documents, and custom forms through configurable AI extraction and validation.</p></div>
 <div className="mb-6 grid grid-cols-2 gap-3">{[["Documents",processed,FileSearch],["Automation",rateLabel,Activity],["Avg. time",avgTime,Clock3],["Reviews",reviewed,CheckCircle2]].map(([a,b,Icon]:any)=><div key={a} className="card p-4"><Icon size={17} className="mb-3 text-slate-500"/><p className="label">{a}</p><p className="mt-1 text-2xl font-semibold">{b}</p></div>)}</div>
 {role&&canUpload(role)&&<div className="card p-6"><div className="flex items-center justify-between"><div><p className="label">New intake</p><h3 className="mt-1 text-lg font-semibold">Upload a document</h3></div><select className="rounded-lg border border-slate-200 px-3 py-2 text-sm" value={type} onChange={e=>setType(e.target.value)}><option value="auto">Auto-detect</option>{schemas.map(s=><option key={s.key} value={s.key}>{s.name}</option>)}</select></div><button disabled={loading} onClick={()=>input.current?.click()} className="mt-5 flex w-full flex-col items-center rounded-xl border-2 border-dashed border-slate-200 px-6 py-10 text-center hover:border-slate-400 disabled:opacity-60"><UploadCloud size={28} className="mb-3 text-slate-500"/><span className="font-medium">{loading?"Processing document…":"Drop a document or click to browse"}</span><span className="mt-1 text-sm text-slate-500">PDF, PNG, JPEG · Schema selected before extraction</span></button><input ref={input} hidden type="file" accept=".pdf,.png,.jpg,.jpeg" onChange={e=>e.target.files?.[0]&&upload(e.target.files[0])}/></div>}
 {message&&<p className="mt-3 text-sm text-slate-600">{message}</p>}
 <div className="mt-6 card overflow-hidden"><div className="flex items-center justify-between border-b border-slate-100 px-5 py-4"><div><p className="label">Processing queue</p><h3 className="font-semibold">Recent documents</h3></div><button onClick={refresh} className="text-sm font-semibold text-slate-700">Refresh</button></div>
 <div className="flex flex-wrap gap-2 border-b border-slate-100 px-5 py-3"><input aria-label="Search documents" placeholder="Search filename…" value={q} onChange={e=>{setOffset(0);setQ(e.target.value)}} className="flex-1 rounded-lg border border-slate-200 px-3 py-1.5 text-sm"/><select aria-label="Filter by status" value={statusFilter} onChange={e=>{setOffset(0);setStatusFilter(e.target.value)}} className="rounded-lg border border-slate-200 px-3 py-1.5 text-sm">{STATUS_OPTIONS.map(s=><option key={s} value={s}>{s===""?"All statuses":s.replaceAll("_"," ")}</option>)}</select></div>
 <div>{docs.map(d=><div className="flex items-center justify-between border-b border-slate-100 last:border-0" key={d.id}><button onClick={()=>openDocument(d.id)} className="flex flex-1 items-center justify-between px-5 py-4 text-left hover:bg-slate-50"><div><p className="font-medium">{d.filename}</p><p className="mt-1 text-xs text-slate-500">{d.document_type.replaceAll("_"," ")} · {new Date(d.upload_date).toLocaleDateString()}</p></div><span className={`rounded-full px-2.5 py-1 text-xs font-semibold ${badges[d.status]||badges.uploaded}`}>{d.status.replaceAll("_"," ")}</span></button>{d.status==="error"&&<button onClick={()=>retry(d.id)} disabled={retrying.has(d.id)} className="mr-5 shrink-0 rounded-lg border border-slate-200 px-2 py-1 text-xs font-semibold hover:bg-slate-50 disabled:opacity-60">{retrying.has(d.id)?"…":"Retry"}</button>}</div>)}{!docs.length&&<p className="px-5 py-8 text-sm text-slate-500">{q||statusFilter?"No documents match your filters.":"No documents yet. Start with an upload."}</p>}</div>
 {total>PAGE_SIZE&&<div className="flex items-center justify-between border-t border-slate-100 px-5 py-3 text-sm"><button disabled={offset===0} onClick={()=>setOffset(Math.max(0,offset-PAGE_SIZE))} className="rounded-lg border border-slate-200 px-3 py-1 font-semibold disabled:opacity-40">Prev</button><span className="text-slate-500 tabular-nums">{total===0?"0 of 0":`${offset+1}–${Math.min(offset+PAGE_SIZE,total)} of ${total}`}</span><button disabled={offset+PAGE_SIZE>=total} onClick={()=>setOffset(offset+PAGE_SIZE)} className="rounded-lg border border-slate-200 px-3 py-1 font-semibold disabled:opacity-40">Next</button></div>}
 </div></section>
 <aside className="space-y-6"><AgentActivity events={agentEvents}/><Review key={selected?.id} role={role} document={selected} onSaved={()=>{const review=begin("Review Agent","Saving human approval");finish(review,"Document approved by human reviewer");refresh();setSelected(null)}} onRejected={()=>{const review=begin("Review Agent","Recording rejection");finish(review,"Document rejected","attention");refresh();setSelected(null)}} onSavedPending={()=>{const review=begin("Review Agent","Saving reviewer edits");finish(review,"Saved — pending approval");refresh()}} onExport={()=>{const exportEvent=begin("Export Agent","Generating ERP-ready CSV payload");finish(exportEvent,"ERP payload generated")}}/></aside></div></main> }
function AgentActivity({events}:{events:AgentEvent[]}){return <section className="card overflow-hidden"><div className="flex items-center gap-2 border-b border-slate-100 p-5"><div className="rounded-lg bg-slate-900 p-1.5 text-white"><Bot size={16}/></div><div><p className="label">Live orchestration</p><h3 className="font-semibold">Agent Activity</h3></div></div><div className="p-4">{events.length?events.map(event=><div key={event.id} className="mb-3 flex gap-3 last:mb-0"><div className={`mt-0.5 ${event.status==="attention"?"text-amber-600":event.status==="complete"?"text-emerald-600":"text-slate-500"}`}>{event.status==="attention"?<TriangleAlert size={17}/>:event.status==="complete"?<CheckCircle2 size={17}/>:<CircleDashed className="animate-spin" size={17}/>}</div><div><p className="text-sm font-semibold">🤖 {event.agent}</p><p className="text-sm text-slate-600">{event.message}</p></div></div>):<p className="text-sm leading-6 text-slate-500">Agent events will appear here as a document moves through intake, extraction, validation, review, and export.</p>}</div></section>}
function FieldBody({f,editable}:{f:any,editable:boolean}){let rows:any[]|null=null;try{const p=JSON.parse(f.field_value);if(Array.isArray(p)){const objs=p.filter((r:any)=>r&&typeof r==="object");if(objs.length)rows=objs}}catch{}
 if(rows){const cols=Object.keys(rows[0]);return <div className="mt-2 overflow-x-auto rounded border border-slate-200 bg-white"><table className="w-full border-collapse text-xs"><thead><tr>{cols.map(c=><th key={c} className="border-b border-slate-200 px-2 py-1.5 text-left font-semibold uppercase tracking-wide text-slate-400">{c.replaceAll("_"," ")}</th>)}</tr></thead><tbody>{rows.map((r,i)=><tr key={i}>{cols.map(c=><td key={c} className="border-b border-slate-100 px-2 py-1.5 tabular-nums">{r[c]??"—"}</td>)}</tr>)}</tbody></table></div>}
 if(!editable)return <p className="mt-2 w-full rounded border border-slate-200 bg-white px-2 py-1.5 text-sm">{f.field_value||"—"}</p>;
 return <input className="mt-2 w-full rounded border border-slate-200 bg-white px-2 py-1.5 text-sm" defaultValue={f.field_value||""} onChange={e=>f.field_value=e.target.value}/>}
function Review({role,document,onSaved,onRejected,onSavedPending,onExport}:{role:Role|null,document:any,onSaved:()=>void,onRejected:()=>void,onSavedPending:()=>void,onExport:()=>void}){const [saving,setSaving]=useState(false);const [reason,setReason]=useState("");const [error,setError]=useState("");const [showAudit,setShowAudit]=useState(false); if(!document)return <section className="card h-fit p-6"><p className="label">Human review</p><h3 className="mt-1 text-lg font-semibold">Select a processed document</h3><p className="mt-2 text-sm leading-6 text-slate-600">Low-confidence values are highlighted for an operator to verify, correct, and approve.</p></section>; const editable=!!role&&canReview(role); const submit=async(action:"approve"|"reject"|"save",done:()=>void)=>{setSaving(true);setError("");try{await api(`/document/${document.id}`,{method:"PUT",headers:{"Content-Type":"application/json"},body:JSON.stringify({action,reason:reason.trim()||null,fields:document.fields.map((f:any)=>({field_name:f.field_name,field_value:f.field_value,validated:action==="approve"?true:f.validated}))})});setReason("");done()}catch(e:any){setError(e?.message||"Action failed. Please retry.")}finally{setSaving(false)}};const exportCsv=async()=>{onExport();try{await downloadFile(`/export/${document.id}?format=csv`,`document-${document.id}.csv`)}catch{setError("Export failed. Please retry.")}};return <section className="card h-fit overflow-hidden"><div className="border-b border-slate-100 p-5"><p className="label">Human review · {document.document_type.replaceAll("_"," ")}</p><h3 className="mt-1 font-semibold">{document.filename}</h3><p className="mt-1 text-sm text-slate-500">Confidence {Math.round((document.confidence||0)*100)}%</p>{document.anomalies?.length>0&&<div className="mt-2 flex flex-wrap gap-1.5">{document.anomalies.map((a:string,i:number)=><span key={i} className="flex items-center gap-1 rounded-full bg-amber-50 px-2.5 py-1 text-xs font-semibold text-amber-700"><TriangleAlert size={12}/>{a}</span>)}</div>}</div><div className="max-h-[420px] overflow-auto p-3">{document.fields.map((f:any)=><label key={f.id} className={`mb-2 block rounded-lg p-3 ${f.confidence<.8?"bg-red-50":f.confidence<.9?"bg-amber-50":"bg-slate-50"}`}><span className="flex items-center justify-between text-xs font-semibold uppercase tracking-wide text-slate-500"><span>{f.field_name.replaceAll("_"," ")}</span><span className="flex items-center gap-2">{f.grounded&&<span className={`rounded px-1.5 py-0.5 text-[10px] font-semibold normal-case tracking-normal ${f.grounded==="grounded"?"bg-emerald-100 text-emerald-700":f.grounded==="ungrounded"?"bg-rose-100 text-rose-700":f.grounded==="unverified"?"bg-amber-100 text-amber-700":"bg-slate-200 text-slate-500"}`}>{f.grounded}</span>}<span>{Math.round(f.confidence*100)}%</span></span></span><FieldBody f={f} editable={editable}/>{f.source_quote&&<p className="mt-1 truncate text-[11px] text-slate-400" title={f.source_quote}>from: “{f.source_quote}”</p>}</label>)}</div>{editable&&<div className="space-y-3 border-t border-slate-100 p-4"><textarea value={reason} onChange={e=>setReason(e.target.value)} rows={2} placeholder="Optional reason (for reject or save)…" className="w-full rounded border border-slate-200 px-2 py-1.5 text-sm"/>{error&&<p className="text-sm font-medium text-rose-600">{error}</p>}<div className="flex gap-2"><button className="btn-primary flex-1" onClick={()=>submit("approve",onSaved)} disabled={saving}>{saving?"Saving…":"Approve"}</button><button className="btn-secondary" onClick={()=>submit("save",onSavedPending)} disabled={saving}>Save pending</button><button className="rounded-lg bg-rose-600 px-3 py-2 text-sm font-semibold text-white hover:bg-rose-700 disabled:opacity-60" onClick={()=>submit("reject",onRejected)} disabled={saving}>Reject</button></div><button className="btn-secondary flex w-full items-center justify-center" onClick={exportCsv}>Export CSV <ArrowUpRight size={14} className="ml-1"/></button></div>}{document.audit?.length>0&&<div className="border-t border-slate-100 p-4"><button onClick={()=>setShowAudit(v=>!v)} className="flex w-full items-center justify-between text-sm font-semibold text-slate-700"><span>Activity</span><span className="text-xs text-slate-400">{showAudit?"Hide":`${document.audit.length} events`}</span></button>{showAudit&&<ul className="mt-3 space-y-2">{[...document.audit].reverse().map((a:any,i:number)=><li key={i} className="text-xs"><span className="font-semibold text-slate-700">{a.action}</span><span className="text-slate-400"> · {a.actor_email||"system"} · {new Date(a.timestamp).toLocaleString()}</span>{a.details&&<p className="text-slate-500">{a.details}</p>}</li>)}</ul>}</div>}</section>}
