import http from 'k6/http';
import { check, sleep } from 'k6';

// host.docker.internal lets the k6 container reach NGINX on your laptop (port 8081).
const BASE = __ENV.BASE_URL || 'http://host.docker.internal:8081';
const JSON_HEADERS = { headers: { 'Content-Type': 'application/json' } };

export const options = {
  vus: Number(__ENV.VUS || 5),            // concurrent virtual users
  duration: __ENV.DURATION || '30s',
  thresholds: {
    http_req_failed: ['rate<0.01'],       // under 1% errors
    http_req_duration: ['p(95)<500'],     // 95% of requests under 500 ms
  },
};

export default function () {
  // create -> read -> delete, so the table stays small
  const created = http.post(`${BASE}/tasks`, JSON.stringify({ title: 'load test' }), JSON_HEADERS);
  check(created, { 'create is 201': (r) => r.status === 201 });

  if (created.status === 201) {
    const id = created.json('id');
    const got = http.get(`${BASE}/tasks/${id}`);
    check(got, { 'read is 200': (r) => r.status === 200 });
    http.del(`${BASE}/tasks/${id}`);
  }
  sleep(0.2); // keeps the load laptop-friendly
}