"use client";
import { setVisibleInterval, clearVisibleInterval } from "@/lib/live";
import { useEffect, useRef, useState } from "react";
import { support } from "@/lib/api";
import { Icon } from "@/components/ui/Icon";
export function SupportChat({ admin = false }: { admin?: boolean }) {
  const [open,setOpen]=useState(false),[conversationId,setConversationId]=useState<string|null>(null),[messages,setMessages]=useState<any[]>([]),[input,setInput]=useState(""),[busy,setBusy]=useState(false); const endRef=useRef<HTMLDivElement>(null);
  useEffect(()=>{if(!open||admin)return; support.conversations().then(r=>{const c=r.conversations?.find((x:any)=>x.status!=="closed")||r.conversations?.[0];if(c)setConversationId(c.id)}).catch(()=>{})},[open,admin]);
  useEffect(()=>{if(!conversationId||!open||admin)return; const load=()=>support.messages(conversationId).then(r=>setMessages(r.messages||[])).catch(()=>{}); load(); const t=setVisibleInterval(load,5000); return()=>clearVisibleInterval(t)},[conversationId,open,admin]);
  useEffect(()=>{endRef.current?.scrollIntoView({behavior:"smooth"})},[messages]);
  async function send(){const body=input.trim();if(!body||busy)return;setBusy(true);try{let id=conversationId;setMessages(m=>[...m,{sender_type:"user",body,created_at:new Date().toISOString()}]);setInput("");
    // Greetings and simple Help prompts do not need a persisted conversation.
    // Use the stateless endpoint first so a DB/support-storage hiccup cannot
    // turn a basic "hi" into an Internal Server Error.
    if (/^(hi|hello|hey|yo|help)[!?. ]*$/i.test(body)) {
      const fallback=await support.ask(body);
      setMessages(m=>[...m,{sender_type:"bot",body:fallback.answer,created_at:new Date().toISOString(),metadata:{escalated:fallback.escalated}}]);
      return;
    }
    if(!id){try{const r=await support.createConversation();id=r.conversation.id;setConversationId(id)}catch{const fallback=await support.ask(body);setMessages(m=>[...m,{sender_type:"bot",body:fallback.answer,created_at:new Date().toISOString(),metadata:{escalated:fallback.escalated}}]);return}}
    try{const r=await support.sendMessage(id!,body);setMessages(m=>[...m,{sender_type:"bot",body:r.answer,created_at:new Date().toISOString(),metadata:{escalated:r.escalated}}])}catch{const fallback=await support.ask(body);setMessages(m=>[...m,{sender_type:"bot",body:fallback.answer,created_at:new Date().toISOString(),metadata:{escalated:fallback.escalated}}])}
  }catch(e:any){setMessages(m=>[...m,{sender_type:"system",body:"LeadX Help is temporarily unavailable. Please try again."}])}finally{setBusy(false)}}
  if(admin)return null;
  return <>
    <div role="dialog" aria-label="LeadX Help" className={open?"fixed bottom-[4.5rem] right-4 z-[80] flex h-[min(600px,calc(100dvh-100px))] w-[min(380px,calc(100vw-32px))] flex-col overflow-hidden rounded-xl border border-line bg-surface-2 shadow-lift":"hidden"}>
      <div className="flex items-center gap-3 border-b border-line px-4 py-3">
        <span className="brand-mark">LX</span>
        <div className="min-w-0 flex-1">
          <div className="section-title">LeadX Help</div>
          <div className="truncate text-[11px] text-ink-faint">FAQ copilot · human support when needed</div>
        </div>
        <button aria-label="Close LeadX Help" type="button" className="icon-button h-8 w-8 border-transparent bg-transparent" onClick={()=>setOpen(false)}><Icon name="close" size={15} /></button>
      </div>
      <div className="flex-1 space-y-3 overflow-y-auto bg-surface/40 p-4" aria-live="polite">
        {messages.length===0&&<div className="rounded-lg border border-line bg-surface-2 p-4">
          <div className="section-title">How can I help?</div>
          <p className="mt-1 text-xs leading-5 text-ink-muted">Ask about leads, discovery, quotas, CRM, email sequences, replies, opt-outs, billing, reports, team access, or anything else in LeadX.</p>
          <div className="mt-3 grid gap-1.5">{["How does my monthly lead limit work?","How do email follow-ups work?","Why is my lead request pending?"].map(q=><button aria-label={`Ask: ${q}`} type="button" key={q} onClick={()=>setInput(q)} className="flex items-center justify-between gap-2 rounded-md border border-line bg-surface-2 px-3 py-2 text-left text-xs text-ink transition-colors hover:border-brand-300 hover:bg-brand-50/50 dark:hover:border-brand-500/40 dark:hover:bg-brand-500/10"><span>{q}</span><Icon name="chevron" size={12} className="shrink-0 text-ink-faint" /></button>)}</div>
        </div>}
        {messages.map((m,i)=><div key={i} className={`flex ${m.sender_type==="user"?"justify-end":"justify-start"}`}>
          <div className={`max-w-[88%] whitespace-pre-wrap break-words rounded-lg px-3.5 py-2.5 text-[0.8125rem] leading-5 ${m.sender_type==="user"?"bg-brand-700 text-white dark:bg-brand-300 dark:text-brand-900":m.sender_type==="system"?"border border-rust-200 bg-rust-50 text-rust-700 dark:border-rust-400/25 dark:bg-rust-400/10 dark:text-rust-200":"border border-line bg-surface-2 text-ink"}`}>
            {m.body}
            {m.metadata?.escalated&&<div className="mt-2 inline-flex items-center gap-1 text-[11px] font-semibold opacity-75"><Icon name="support" size={11} /> Escalated to support</div>}
          </div>
        </div>)}
        {busy&&<div className="flex items-center gap-2 text-xs text-ink-faint" role="status"><span className="live-dot" aria-hidden="true" />LeadX is checking the help knowledge base…</div>}
        <div ref={endRef}/>
      </div>
      <div className="border-t border-line p-3">
        <div className="flex items-end gap-2 rounded-lg border border-line-strong bg-surface-2 p-1.5 focus-within:border-brand-500 focus-within:ring-[3px] focus-within:ring-brand-500/15 dark:focus-within:border-brand-300">
          <textarea aria-label="Message LeadX Help" value={input} onChange={e=>setInput(e.target.value)} onKeyDown={e=>{if(e.key==="Enter"&&!e.shiftKey){e.preventDefault();send()}}} rows={2} placeholder="Ask anything about LeadX…" className="min-h-10 flex-1 resize-none bg-transparent px-1.5 py-1 text-[0.8125rem] text-ink outline-none placeholder:text-ink-faint"/>
          <button aria-label="Send" type="button" onClick={send} disabled={!input.trim()||busy} className="btn-primary btn-sm h-8 shrink-0">Send</button>
        </div>
        <div className="mt-1.5 flex items-center gap-1.5 text-[11px] text-ink-faint"><span className="kbd">Enter</span> to send <span className="kbd">Shift</span>+<span className="kbd">Enter</span> new line</div>
      </div>
    </div>
    <button aria-label="Open LeadX Help" aria-expanded={open} type="button" onClick={()=>setOpen(v=>!v)} className="fixed bottom-4 right-4 z-[79] inline-flex h-10 items-center gap-2 rounded-full border border-brand-800 bg-brand-700 px-4 text-[0.8125rem] font-semibold text-white shadow-lift transition-colors hover:bg-brand-800 dark:border-brand-200 dark:bg-brand-300 dark:text-brand-900 dark:hover:bg-brand-200"><Icon name={open?"close":"support"} size={15} /> Help</button>
  </>;
}
