export type MockChange = {
  tenantId: string;
  table: string;
  event: "INSERT" | "UPDATE" | "DELETE" | "reconcile";
  revision: number;
};

export type MockProjection = {
  revision: number;
  status: "queued" | "running" | "completed" | "failed" | "cancelled";
  runId: string;
  requestId: string;
  jobId: string;
  discovered: number;
  delivered: number;
  terminalReason?: string;
};

type Subscriber = (projection: MockProjection, change: MockChange) => void;

const TERMINAL = new Set<MockProjection["status"]>(["completed", "failed", "cancelled"]);

/**
 * In-memory Broadcast + authoritative-read fixture. It intentionally models
 * the portal contract rather than contacting Supabase or any provider.
 */
export class MockRealtimeRuntime {
  private readonly members: Map<string, Set<string>>;
  private readonly subscribers = new Map<string, Set<Subscriber>>();
  private readonly projections = new Map<string, MockProjection>();
  private readonly pending = new Map<string, MockChange>();
  private readonly connected = new Set<string>();
  private readonly hidden = new Set<string>();
  private readonly lastApplied = new Map<string, number>();
  private readonly refreshes = new Map<string, number>();
  private readonly adminRefreshes: number[] = [];
  private adminDeadlineAt = Number.POSITIVE_INFINITY;
  providerCalls = 0;
  creditOperations = 0;

  constructor(members: Record<string, string[]> = {}) {
    this.members = new Map(Object.entries(members).map(([tenant, users]) => [tenant, new Set(users)]));
  }

  seed(tenantId: string, projection: MockProjection) {
    this.projections.set(tenantId, projection);
  }

  subscribe(userId: string, tenantId: string, subscriber: Subscriber) {
    if (!this.members.get(tenantId)?.has(userId)) throw new Error("private tenant topic denied");
    const listeners = this.subscribers.get(tenantId) || new Set<Subscriber>();
    listeners.add(subscriber);
    this.subscribers.set(tenantId, listeners);
    this.connected.add(tenantId);
    return () => listeners.delete(subscriber);
  }

  disconnect(tenantId: string) {
    this.connected.delete(tenantId);
  }

  commit(tenantId: string, change: Omit<MockChange, "tenantId">, projection: MockProjection) {
    const current = this.projections.get(tenantId);
    if (!current || projection.revision >= current.revision) this.projections.set(tenantId, projection);
    if (this.connected.has(tenantId) && !this.hidden.has(tenantId)) this.broadcast({ ...change, tenantId });
  }

  broadcast(change: MockChange) {
    this.pending.set(change.tenantId, change);
  }

  flushDebounce() {
    for (const [tenantId, change] of this.pending) {
      this.pending.delete(tenantId);
      const projection = this.projections.get(tenantId);
      if (!projection) continue;
      this.refreshes.set(tenantId, (this.refreshes.get(tenantId) || 0) + 1);
      this.lastApplied.set(tenantId, Math.max(this.lastApplied.get(tenantId) || 0, projection.revision));
      for (const subscriber of this.subscribers.get(tenantId) || []) subscriber(projection, change);
    }
  }

  reconnect(tenantId: string) {
    this.connected.add(tenantId);
    // A reconnect may have missed any number of Broadcast messages.
    this.broadcast({ tenantId, table: "", event: "reconcile", revision: this.projections.get(tenantId)?.revision || 0 });
  }

  setVisibility(tenantId: string, visible: boolean) {
    if (visible) {
      this.hidden.delete(tenantId);
      this.broadcast({ tenantId, table: "", event: "reconcile", revision: this.projections.get(tenantId)?.revision || 0 });
    } else {
      this.hidden.add(tenantId);
    }
  }

  poll(tenantId: string, nowMs: number, deadlineAt: number) {
    if (this.hidden.has(tenantId) || nowMs >= deadlineAt) return false;
    this.broadcast({ tenantId, table: "", event: "reconcile", revision: this.projections.get(tenantId)?.revision || 0 });
    return true;
  }

  setAdminDeadline(deadlineAt: number) {
    this.adminDeadlineAt = deadlineAt;
  }

  adminPoll(nowMs: number) {
    if (nowMs >= this.adminDeadlineAt) return false;
    this.adminRefreshes.push(nowMs);
    return true;
  }

  refreshCount(tenantId: string) { return this.refreshes.get(tenantId) || 0; }
  adminRefreshCount() { return this.adminRefreshes.length; }
  lastRevision(tenantId: string) { return this.lastApplied.get(tenantId) || 0; }
  projection(tenantId: string) { return this.projections.get(tenantId); }
  isTerminal(tenantId: string) { return TERMINAL.has(this.projections.get(tenantId)?.status || "queued"); }
}
