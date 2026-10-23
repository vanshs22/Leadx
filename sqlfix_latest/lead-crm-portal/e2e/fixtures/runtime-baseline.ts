/**
 * Synthetic portal responses for runtime hardening tests.
 *
 * This fixture contains no tokens, tenant data, or live URLs. It is shared by
 * future Playwright/component checks for state exclusivity and bounded polling.
 */
export type BaselinePortalResponse = {
  label: "loading" | "empty" | "success" | "dependency-error" | "unauthorized";
  status: number | null;
  requestId?: string;
  retryable?: boolean;
};

export const BASELINE_PORTAL_RESPONSES: readonly BaselinePortalResponse[] = [
  { label: "loading", status: null },
  { label: "empty", status: 200 },
  { label: "success", status: 200 },
  { label: "dependency-error", status: 503, requestId: "baseline-request-001", retryable: true },
  { label: "unauthorized", status: 401, requestId: "baseline-request-002", retryable: false },
];

export function finitePollingResponses(
  terminal: Exclude<BaselinePortalResponse["label"], "loading"> = "dependency-error",
  attempts = 3,
): BaselinePortalResponse[] {
  const safeAttempts = Math.max(1, Math.floor(attempts));
  return Array.from({ length: safeAttempts }, (_, index) =>
    index === safeAttempts - 1
      ? BASELINE_PORTAL_RESPONSES.find((response) => response.label === terminal)!
      : BASELINE_PORTAL_RESPONSES[0],
  );
}
