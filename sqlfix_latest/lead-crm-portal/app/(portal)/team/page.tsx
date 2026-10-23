"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { crm } from "@/lib/api";
import { getTenantId } from "@/lib/tenant";
import { useCrmChange } from "@/lib/crmRealtime";

export default function TeamPage() {
  const [members, setMembers] = useState<any[]>([]);
  const [invites, setInvites] = useState<any[]>([]);
  const [email, setEmail] = useState("");
  const [role, setRole] = useState("member");
  const [msg, setMsg] = useState("");
  const [lastLink, setLastLink] = useState("");
  const [busy, setBusy] = useState(false);

  function load() {
    crm
      .teamMembers(getTenantId())
      .then((r) => {
        setMembers(r.members || []);
        setInvites(r.invites || []);
      })
      .catch((e) => setMsg(e.message));
  }

  useEffect(() => {
    load();
  }, []);
  useCrmChange(load, { tables: ["tenant_members", "team_invites"] });

  async function invite() {
    if (!email.trim()) return;
    setBusy(true);
    setMsg("");
    setLastLink("");
    try {
      const r: any = await crm.teamInvite(getTenantId(), {
        email: email.trim(),
        role,
        send_email: true,
      });
      if (r?.error) {
        setMsg(String(r.error));
      } else {
        const link = r.invite_url || "";
        setLastLink(link);
        if (r.email_sent) {
          setMsg(`Invite email sent to ${email} (role: ${role}). They set a password on the accept link.`);
        } else {
          setMsg(
            r.email_hint ||
              r.email_error ||
              "Email not sent (connect & verify domain under Email settings). Copy the invite link and share it — they can still join and set a password."
          );
        }
        setEmail("");
        load();
      }
    } catch (e: any) {
      setMsg(e.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="page">
      <header>
        <h1 className="page-title">Team</h1>
        <p className="page-sub">
          Invite teammates. Email sends from your verified domain (Settings → Email). They open the link, set a password, and join with the role you choose.
        </p>
      </header>

      {msg && (
        <div className="rounded-xl border border-line bg-surface-2 px-4 py-2.5 text-sm text-ink-muted whitespace-pre-wrap">
          {msg}
        </div>
      )}

      {lastLink && (
        <div className="card-pad space-y-2">
          <p className="text-sm font-medium text-ink">Invite link (share if email was not delivered)</p>
          <div className="flex flex-wrap gap-2">
            <input className="input flex-1 min-w-[200px] font-mono text-xs" readOnly value={lastLink} />
            <button aria-label="Action"
              type="button"
              className="btn-secondary"
              onClick={() => {
                navigator.clipboard.writeText(lastLink);
                setMsg("Invite link copied");
              }}
            >
              Copy link
            </button>
          </div>
        </div>
      )}

      <div className="card-pad space-y-3">
        <h2 className="text-sm font-semibold text-ink">Invite teammate</h2>
        <div className="flex flex-wrap gap-2">
          <input
            className="input flex-1 min-w-[200px]"
            type="email"
            placeholder="email@company.com"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
          />
          <select className="input max-w-[140px]" value={role} onChange={(e) => setRole(e.target.value)}>
            <option value="viewer">Viewer</option>
            <option value="member">Member (editor)</option>
            <option value="admin">Admin</option>
          </select>
          <button aria-label="Action" disabled={busy} onClick={invite} className="btn-primary">
            {busy ? "Sending…" : "Send invite"}
          </button>
        </div>
        <p className="text-xs text-ink-faint">
          Roles: <strong>Viewer</strong> read-only · <strong>Member</strong> work leads · <strong>Admin</strong> manage team.
          Link expires in 7 days.{" "}
          <Link href="/settings/email" className="text-brand-600 underline">
            Connect domain
          </Link>{" "}
          so invites send from your address.
        </p>
      </div>

      <div className="card-pad">
        <h2 className="mb-3 text-sm font-semibold text-ink">Members</h2>
        <ul className="divide-y divide-line">
          {members.map((m) => (
            <li key={m.id || m.email} className="flex justify-between py-2.5 text-sm">
              <span className="text-ink">{m.email}</span>
              <span className="text-xs text-ink-faint">
                {m.role} · {m.status}
              </span>
            </li>
          ))}
          {!members.length && <li className="py-2 text-sm text-ink-faint">No members yet</li>}
        </ul>
      </div>

      <div className="card-pad">
        <h2 className="mb-3 text-sm font-semibold text-ink">Pending invites</h2>
        <ul className="divide-y divide-line">
          {invites.map((i) => (
            <li key={i.id || i.token} className="flex justify-between py-2.5 text-sm">
              <span className="text-ink">{i.email}</span>
              <span className="text-xs text-ink-faint">
                {i.role} · expires {String(i.expires_at || "").slice(0, 10)}
              </span>
            </li>
          ))}
          {!invites.length && <li className="py-2 text-sm text-ink-faint">No pending invites</li>}
        </ul>
      </div>
    </div>
  );
}
