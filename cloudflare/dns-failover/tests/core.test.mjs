import assert from "node:assert/strict";
import test from "node:test";
import {
  DNS_STATE,
  applyDnsUpdateSuccess,
  classifyDnsRecords,
  evaluateFailover,
  readConfig,
} from "../src/core.mjs";

const env = {
  AUTOMATIC_DNS_WRITES_ENABLED: "true",
  CHECK_INTERVAL_SECONDS: "15",
  CLOUDFLARE_ZONE_ID: "0".repeat(32),
  DNS_RECORDS_JSON: JSON.stringify([
    { id: "a".repeat(32), name: "nutsnews.com", type: "CNAME" },
    { id: "b".repeat(32), name: "www.nutsnews.com", type: "CNAME" },
  ]),
  FAILURE_THRESHOLD: "3",
  PRIMARY_DNS_TARGET: "vps.nutsnews.com",
  RECOVERY_THRESHOLD: "1",
  SECONDARY_DNS_TARGET: "cname.vercel-dns.com",
};

function config(overrides = {}) {
  return readConfig({ ...env, ...overrides });
}

test("classifies managed DNS records without exposing record ids", () => {
  const current = config();
  assert.equal(
    classifyDnsRecords(
      [
        { id: "a".repeat(32), name: "nutsnews.com", type: "CNAME", content: "vps.nutsnews.com", proxied: true },
        { id: "b".repeat(32), name: "www.nutsnews.com", type: "CNAME", content: "vps.nutsnews.com", proxied: true },
      ],
      current,
    ),
    DNS_STATE.PRIMARY,
  );
  assert.equal(
    classifyDnsRecords(
      [
        { id: "a".repeat(32), name: "nutsnews.com", type: "CNAME", content: "cname.vercel-dns.com", proxied: true },
        { id: "b".repeat(32), name: "www.nutsnews.com", type: "CNAME", content: "vps.nutsnews.com", proxied: true },
      ],
      current,
    ),
    DNS_STATE.MIXED,
  );
});

test("first and second consecutive VPS failures do not update DNS", () => {
  let state = {};
  for (const failureCount of [1, 2]) {
    const decision = evaluateFailover({
      previousState: state,
      health: { ok: false, error: `failure ${failureCount}` },
      observedDnsState: DNS_STATE.PRIMARY,
      config: config(),
      nowMs: Date.parse(`2026-07-21T00:00:0${failureCount}Z`),
    });
    assert.equal(decision.action, null);
    assert.equal(decision.state.consecutiveFailureCount, failureCount);
    assert.equal(decision.state.lastDnsAction, "none:failure_threshold_not_met");
    state = decision.state;
  }
});

test("third consecutive VPS failure updates DNS to Vercel", () => {
  const decision = evaluateFailover({
    previousState: { consecutiveFailureCount: 2 },
    health: { ok: false, error: "timeout" },
    observedDnsState: DNS_STATE.PRIMARY,
    config: config(),
    nowMs: Date.parse("2026-07-21T00:00:03Z"),
  });
  assert.deepEqual(decision.action, {
    reason: "vps_failure_threshold",
    target: DNS_STATE.SECONDARY,
    targetContent: "cname.vercel-dns.com",
  });
  assert.equal(decision.state.consecutiveFailureCount, 3);
  assert.equal(decision.state.lastDnsAction, "pending:vps_failure_threshold:vercel");
});

test("while DNS points to Vercel, failed VPS checks continue without duplicate DNS writes", () => {
  const decision = evaluateFailover({
    previousState: { activeDnsTarget: DNS_STATE.SECONDARY, consecutiveFailureCount: 8 },
    health: { ok: false, error: "still down" },
    observedDnsState: DNS_STATE.SECONDARY,
    config: config(),
  });
  assert.equal(decision.action, null);
  assert.equal(decision.state.consecutiveFailureCount, 9);
  assert.equal(decision.state.lastDnsAction, "none:already_vercel");
});

test("healthy VPS fails back only when current DNS is Vercel", () => {
  const decision = evaluateFailover({
    previousState: { activeDnsTarget: DNS_STATE.SECONDARY, consecutiveFailureCount: 3 },
    health: { ok: true },
    observedDnsState: DNS_STATE.SECONDARY,
    config: config(),
    nowMs: Date.parse("2026-07-21T00:01:00Z"),
  });
  assert.deepEqual(decision.action, {
    reason: "vps_recovered",
    target: DNS_STATE.PRIMARY,
    targetContent: "vps.nutsnews.com",
  });
  assert.equal(decision.state.lastDnsAction, "pending:vps_recovered:vps");

  const noOp = evaluateFailover({
    previousState: decision.state,
    health: { ok: true },
    observedDnsState: DNS_STATE.PRIMARY,
    config: config(),
    nowMs: Date.parse("2026-07-21T00:01:15Z"),
  });
  assert.equal(noOp.action, null);
  assert.equal(noOp.state.lastDnsAction, "none:vps_already_primary");
});

test("automatic DNS writes are suppressed until the protected workflow enables them", () => {
  const decision = evaluateFailover({
    previousState: { consecutiveFailureCount: 2 },
    health: { ok: false, error: "timeout" },
    observedDnsState: DNS_STATE.PRIMARY,
    config: config({ AUTOMATIC_DNS_WRITES_ENABLED: "false" }),
  });
  assert.equal(decision.action, null);
  assert.equal(decision.state.lastDnsAction, "suppressed:dns_writes_disabled:vps_failure_threshold");
});

test("manual lock suppresses automatic failback even when VPS is healthy", () => {
  const decision = evaluateFailover({
    previousState: { manualLock: true },
    health: { ok: true },
    observedDnsState: DNS_STATE.SECONDARY,
    config: config(),
  });
  assert.equal(decision.action, null);
  assert.equal(decision.state.lastDnsAction, "suppressed:manual_lock:vps_recovered");
});

test("cooldown suppresses rapid repeated DNS updates", () => {
  const afterUpdate = applyDnsUpdateSuccess(
    { consecutiveFailureCount: 2 },
    { reason: "vps_failure_threshold", target: DNS_STATE.SECONDARY },
    Date.parse("2026-07-21T00:00:00Z"),
  );
  const decision = evaluateFailover({
    previousState: afterUpdate,
    health: { ok: true },
    observedDnsState: DNS_STATE.SECONDARY,
    config: config({ MIN_DNS_UPDATE_INTERVAL_SECONDS: "60" }),
    nowMs: Date.parse("2026-07-21T00:00:15Z"),
  });
  assert.equal(decision.action, null);
  assert.equal(decision.state.lastDnsAction, "suppressed:dns_update_cooldown:vps_recovered");
});
