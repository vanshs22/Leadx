"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { crm } from "@/lib/api";
import { getTenantId } from "@/lib/tenant";
import { useCrmChange } from "@/lib/crmRealtime";
import { Badge, PageHeader, Notice, Panel, StatusBadge } from "@/components/ui/Primitives";
import { DataState, EmptyState, LoadingRows } from "@/components/ui/States";
import { Icon } from "@/components/ui/Icon";

export default function TeamPage() {
  const [members, setMembers] = useState<any[]>([]), [invites, setInvites] = useState<any[]>([]), [email, setEmail] = useState(""), [role, setRole] = useState("member"), [msg, setMsg] = useState(""), [lastLink, setLastLink] = useState(""), [busy, setBusy] = useState(false), [loading, setLoading] = useState(true), [error, setError] = useState<unknown>(null);
  const load = useCallback(async () => { setLoading(true); setError(null); try { const r = await crm.teamMembers(getTenantId()); setMembers(r.members || []); setInvites(r.invites || []); } catch (caught) { setError(caught); } finally { setLoading(false); } }, []);
  useEffect(() => { void load(); }, [load]);
  useCrmChange(() => { void load(); }, { tables: ["tenant_members", "team_invites"] });
  async function invite() { if (!email.trim()) return; setBusy(true); setMsg(""); setLastLink(""); try { const r: any = await crm.teamInvite(getTenantId(), { email: email.trim(), role, send_email: true }); if (r?.error) setMsg(String(r.error)); else { setLastLink(r.invite_url || ""); setMsg(r.email_sent ? `Invite email sent to ${email} (role: ${role}).` : r.email_hint || r.email_error || "Email not sent. Copy the invite link and share it."); setEmail(""); await load(); } } catch (caught) { setMsg(caught instanceof Error ? caught.message : "Could not send invite."); } finally { setBusy(false); } }
  async function copyLink() { try { await navigator.clipboard.writeText(lastLink); setMsg("Invite link copied."); } catch { setMsg("Copy failed. Select the link and copy it manually."); } }
  const roleTone = (r: unknown) => (String(r).toLowerCase() === "owner" || String(r).toLowerCase() === "admin" ? "brand" : "neutral") as "brand" | "neutral";
  return (
    <div className="page">
      <PageHeader
        eyebrow="Workspace settings"
        title="Team"
        description="Invite teammates and manage workspace roles."
        actions={<button className="btn-secondary" onClick={() => void load()} disabled={loading}><Icon name="refresh" size={14} aria-hidden="true" />Refresh</button>}
        meta={!loading && !error ? <><span className="tabular-nums">{members.length} members</span><span className="tabular-nums">{invites.length} pending invites</span></> : null}
      />
      {msg ? <Notice tone={msg.includes("failed") || msg.includes("Could not") ? "danger" : "neutral"}>{msg}</Notice> : null}
      <DataState loading={loading} error={error} onRetry={() => void load()} skeleton={<div className="grid gap-4 lg:grid-cols-3"><div className="card lg:col-span-2"><LoadingRows rows={5} cols={3} /></div><div className="card"><LoadingRows rows={3} cols={1} /></div></div>}>
        <div className="grid items-start gap-4 lg:grid-cols-3">
          <div className="space-y-4 lg:col-span-2">
            <Panel title="Members" description="People with access to this workspace." bodyClassName="p-0">
              {members.length ? (
                <div className="overflow-x-auto">
                  <table className="data-table !min-w-[480px]">
                    <caption className="sr-only">Workspace members</caption>
                    <thead><tr><th scope="col">Member</th><th scope="col">Role</th><th scope="col">Status</th></tr></thead>
                    <tbody>{members.map((m) => <tr key={m.id || m.email}><td className="font-medium text-ink"><span className="break-all">{m.email}</span></td><td><Badge tone={roleTone(m.role)} className="capitalize">{m.role || "member"}</Badge></td><td><StatusBadge status={m.status} /></td></tr>)}</tbody>
                  </table>
                </div>
              ) : <EmptyState title="No members yet" hint="Workspace members will appear here after an invite is accepted." />}
            </Panel>
            <Panel title="Pending invites" description="Invites that have not been accepted yet." bodyClassName="p-0">
              {invites.length ? (
                <div className="overflow-x-auto">
                  <table className="data-table !min-w-[480px]">
                    <caption className="sr-only">Pending invites</caption>
                    <thead><tr><th scope="col">Email</th><th scope="col">Role</th><th scope="col" className="!text-right">Expires</th></tr></thead>
                    <tbody>{invites.map((i) => <tr key={i.id || i.token}><td className="text-ink"><span className="break-all">{i.email}</span></td><td><Badge tone={roleTone(i.role)} className="capitalize">{i.role || "member"}</Badge></td><td className="text-right tabular-nums text-ink-muted">{String(i.expires_at || "").slice(0, 10) || "unavailable"}</td></tr>)}</tbody>
                  </table>
                </div>
              ) : <EmptyState title="No pending invites" />}
            </Panel>
          </div>
          <div className="space-y-4">
            <Panel
              title="Invite teammate"
              description="They receive an email link to join with the selected role."
              footer={<><span className="min-w-0">Connect a verified domain under <Link href="/settings/email" className="link">Email settings</Link> so invites send from your address.</span><button disabled={busy || !email.trim()} onClick={() => void invite()} className="btn-primary shrink-0">{busy ? "Sending…" : "Send invite"}</button></>}
            >
              <div className="space-y-4">
                <div><label htmlFor="team-invite-email" className="field-label">Email</label><input id="team-invite-email" aria-label="Teammate email" className="input" type="email" placeholder="email@company.com" value={email} onChange={(e) => setEmail(e.target.value)} /></div>
                <div><label htmlFor="team-invite-role" className="field-label">Role</label><select id="team-invite-role" aria-label="Teammate role" className="select" value={role} onChange={(e) => setRole(e.target.value)}><option value="viewer">Viewer</option><option value="member">Member</option><option value="admin">Admin</option></select><p className="field-hint">Viewers can read; members can work leads; admins manage settings and team.</p></div>
              </div>
            </Panel>
            {lastLink ? (
              <Panel title="Invite link" description="Share this link if the email was not delivered.">
                <div className="flex flex-wrap gap-2"><input aria-label="Invite link" className="input min-w-0 flex-1 font-mono text-xs" readOnly value={lastLink} /><button type="button" className="btn-secondary" onClick={() => void copyLink()}>Copy link</button></div>
              </Panel>
            ) : null}
          </div>
        </div>
      </DataState>
    </div>
  );
}
