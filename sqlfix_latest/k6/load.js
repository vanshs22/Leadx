import http from 'k6/http';
import { check, sleep } from 'k6';
export const options = { vus: Number(__ENV.VUS || 10), duration: __ENV.DURATION || '30s', thresholds: { http_req_duration: ['p(95)<800'], http_req_failed: ['rate<0.01'] } };
export default function () {
  const base = (__ENV.LEADX_URL || 'http://localhost:8000').replace(/\/$/, '');
  const token = __ENV.LEADX_TOKEN; const tenant = __ENV.LEADX_TENANT_ID;
  const path = token && tenant ? `/crm/${tenant}/dashboard` : '/health/ready';
  const res = http.get(`${base}${path}`, token ? { headers: { Authorization: `Bearer ${token}` } } : {});
  check(res, token && tenant ? { 'protected endpoint 2xx': r => r.status >= 200 && r.status < 300 } : { 'readiness 2xx': r => r.status >= 200 && r.status < 300 });
  sleep(1);
}
