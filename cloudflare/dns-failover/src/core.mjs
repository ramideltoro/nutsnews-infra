export const DNS_STATE = Object.freeze({
  PRIMARY: "vps",
  SECONDARY: "vercel",
  MIXED: "mixed",
  UNKNOWN: "unknown",
});

export const DEFAULT_STATE = Object.freeze({
  activeDnsTarget: DNS_STATE.UNKNOWN,
  consecutiveFailureCount: 0,
  consecutiveRecoveryCount: 0,
  lastCheckTimestamp: "",
  lastDnsUpdateTimestamp: "",
  manualLock: false,
  manualLockReason: "",
  lastErrorSummary: "",
  lastDnsAction: "none",
  lastHealthStatus: "unknown",
});

const ALLOWED_RECORD_TYPES = new Set(["A", "AAAA", "CNAME"]);

export function sanitizeSummary(value, fallback = "") {
  const text = String(value ?? fallback).replace(/\s+/g, " ").trim();
  if (!text) {
    return fallback;
  }
  return text.slice(0, 180);
}

export function normalizeState(state = {}) {
  return {
    ...DEFAULT_STATE,
    ...state,
    consecutiveFailureCount: Number.isInteger(state.consecutiveFailureCount)
      ? Math.max(0, state.consecutiveFailureCount)
      : DEFAULT_STATE.consecutiveFailureCount,
    consecutiveRecoveryCount: Number.isInteger(state.consecutiveRecoveryCount)
      ? Math.max(0, state.consecutiveRecoveryCount)
      : DEFAULT_STATE.consecutiveRecoveryCount,
    manualLock: state.manualLock === true,
  };
}

export function normalizeTarget(value) {
  return String(value ?? "")
    .trim()
    .replace(/\.$/, "")
    .toLowerCase();
}

export function parseBoolean(value, defaultValue = false) {
  if (value === undefined || value === null || value === "") {
    return defaultValue;
  }
  return ["1", "true", "yes", "on"].includes(String(value).trim().toLowerCase());
}

export function parseInteger(value, defaultValue, { min = 0, max = Number.MAX_SAFE_INTEGER } = {}) {
  if (value === undefined || value === null || value === "") {
    return defaultValue;
  }
  const parsed = Number.parseInt(String(value), 10);
  if (!Number.isFinite(parsed) || parsed < min || parsed > max) {
    throw new Error(`Invalid integer configuration value: ${value}`);
  }
  return parsed;
}

export function parseDnsRecords(rawRecords, defaultType = "CNAME") {
  if (!rawRecords) {
    return [];
  }
  let parsed;
  try {
    parsed = JSON.parse(rawRecords);
  } catch (error) {
    throw new Error("DNS_RECORDS_JSON must be valid JSON.");
  }
  if (!Array.isArray(parsed) || parsed.length === 0) {
    throw new Error("DNS_RECORDS_JSON must be a non-empty array.");
  }

  const seenNames = new Set();
  return parsed.map((record, index) => {
    if (!record || typeof record !== "object") {
      throw new Error(`DNS record entry ${index} must be an object.`);
    }
    const name = normalizeTarget(record.name);
    const id = String(record.id ?? "").trim();
    const type = String(record.type ?? defaultType).trim().toUpperCase();
    if (!name || !name.includes(".")) {
      throw new Error(`DNS record entry ${index} has an invalid name.`);
    }
    if (!id) {
      throw new Error(`DNS record entry ${index} must include a Cloudflare DNS record id.`);
    }
    if (!ALLOWED_RECORD_TYPES.has(type)) {
      throw new Error(`DNS record entry ${index} has unsupported type ${type}.`);
    }
    if (seenNames.has(name)) {
      throw new Error(`DNS record ${name} is duplicated.`);
    }
    seenNames.add(name);
    return { id, name, type };
  });
}

export function readConfig(env = {}) {
  const recordType = String(env.DNS_RECORD_TYPE || "CNAME").trim().toUpperCase();
  if (!ALLOWED_RECORD_TYPES.has(recordType)) {
    throw new Error(`Unsupported DNS_RECORD_TYPE: ${recordType}`);
  }

  const checkIntervalSeconds = parseInteger(env.CHECK_INTERVAL_SECONDS, 15, { min: 5, max: 300 });
  return {
    controllerName: String(env.CONTROLLER_NAME || "nutsnews-production").trim(),
    healthCheckUrl: String(env.HEALTH_CHECK_URL || "https://vps.nutsnews.com/readyz").trim(),
    expectedReadinessTarget: String(env.EXPECTED_READINESS_TARGET || "production-vps").trim(),
    healthTimeoutMs: parseInteger(env.HEALTH_CHECK_TIMEOUT_MS, 4000, { min: 500, max: 15000 }),
    checkIntervalMs: checkIntervalSeconds * 1000,
    failureThreshold: parseInteger(env.FAILURE_THRESHOLD, 3, { min: 1, max: 20 }),
    recoveryThreshold: parseInteger(env.RECOVERY_THRESHOLD, 1, { min: 1, max: 20 }),
    minDnsUpdateIntervalMs: parseInteger(env.MIN_DNS_UPDATE_INTERVAL_SECONDS, 60, { min: 0, max: 3600 }) * 1000,
    dnsWritesEnabled: parseBoolean(env.AUTOMATIC_DNS_WRITES_ENABLED, false),
    zoneId: String(env.CLOUDFLARE_ZONE_ID || "").trim(),
    dnsRecordType: recordType,
    dnsTtl: parseInteger(env.DNS_TTL, 1, { min: 1, max: 86400 }),
    dnsProxied: parseBoolean(env.DNS_PROXIED, true),
    primaryDnsTarget: normalizeTarget(env.PRIMARY_DNS_TARGET || "vps.nutsnews.com"),
    secondaryDnsTarget: normalizeTarget(env.SECONDARY_DNS_TARGET || "cname.vercel-dns.com"),
    dnsRecords: parseDnsRecords(env.DNS_RECORDS_JSON, recordType),
  };
}

export function classifyDnsRecords(records, config) {
  if (!records || !records.length || !config.dnsRecords.length) {
    return DNS_STATE.UNKNOWN;
  }

  const recordsById = new Map(records.map((record) => [String(record.id || ""), record]));
  const recordsByName = new Map(records.map((record) => [normalizeTarget(record.name), record]));
  const observed = [];

  for (const expected of config.dnsRecords) {
    const record = recordsById.get(expected.id) || recordsByName.get(expected.name);
    if (!record) {
      return DNS_STATE.UNKNOWN;
    }
    if (String(record.type || "").toUpperCase() !== expected.type) {
      observed.push(DNS_STATE.MIXED);
      continue;
    }
    if (record.proxied !== undefined && Boolean(record.proxied) !== Boolean(config.dnsProxied)) {
      observed.push(DNS_STATE.MIXED);
      continue;
    }

    const content = normalizeTarget(record.content);
    if (content === config.primaryDnsTarget) {
      observed.push(DNS_STATE.PRIMARY);
    } else if (content === config.secondaryDnsTarget) {
      observed.push(DNS_STATE.SECONDARY);
    } else {
      observed.push(DNS_STATE.MIXED);
    }
  }

  if (observed.every((state) => state === DNS_STATE.PRIMARY)) {
    return DNS_STATE.PRIMARY;
  }
  if (observed.every((state) => state === DNS_STATE.SECONDARY)) {
    return DNS_STATE.SECONDARY;
  }
  return DNS_STATE.MIXED;
}

function dnsUpdateInCooldown(state, nowMs, config) {
  if (!state.lastDnsUpdateTimestamp) {
    return false;
  }
  const lastUpdateMs = Date.parse(state.lastDnsUpdateTimestamp);
  if (!Number.isFinite(lastUpdateMs)) {
    return false;
  }
  return nowMs - lastUpdateMs < config.minDnsUpdateIntervalMs;
}

function targetContentFor(target, config) {
  if (target === DNS_STATE.PRIMARY) {
    return config.primaryDnsTarget;
  }
  if (target === DNS_STATE.SECONDARY) {
    return config.secondaryDnsTarget;
  }
  throw new Error(`Unsupported DNS target: ${target}`);
}

export function evaluateFailover({ previousState, health, observedDnsState, config, nowMs = Date.now() }) {
  const state = normalizeState(previousState);
  const now = new Date(nowMs).toISOString();
  const dnsState = Object.values(DNS_STATE).includes(observedDnsState) ? observedDnsState : DNS_STATE.UNKNOWN;
  const nextState = {
    ...state,
    activeDnsTarget: dnsState,
    lastCheckTimestamp: now,
    lastHealthStatus: health.ok ? "healthy" : "unhealthy",
    lastErrorSummary: health.ok ? "" : sanitizeSummary(health.error, "VPS health check failed."),
    lastDnsAction: "none",
  };

  let candidate = null;

  if (health.ok) {
    nextState.consecutiveFailureCount = 0;
    nextState.consecutiveRecoveryCount += 1;
    if (dnsState === DNS_STATE.PRIMARY) {
      nextState.lastDnsAction = "none:vps_already_primary";
    } else if (dnsState === DNS_STATE.SECONDARY && nextState.consecutiveRecoveryCount >= config.recoveryThreshold) {
      candidate = {
        reason: "vps_recovered",
        target: DNS_STATE.PRIMARY,
        targetContent: targetContentFor(DNS_STATE.PRIMARY, config),
      };
    } else if (dnsState === DNS_STATE.SECONDARY) {
      nextState.lastDnsAction = "none:recovery_threshold_not_met";
    } else {
      nextState.lastDnsAction = "none:dns_state_not_vercel";
      nextState.lastErrorSummary = "Refusing automatic failback because managed DNS is not fully on Vercel.";
    }
  } else {
    nextState.consecutiveFailureCount += 1;
    nextState.consecutiveRecoveryCount = 0;
    if (dnsState === DNS_STATE.SECONDARY) {
      nextState.lastDnsAction = "none:already_vercel";
    } else if (dnsState === DNS_STATE.PRIMARY && nextState.consecutiveFailureCount >= config.failureThreshold) {
      candidate = {
        reason: "vps_failure_threshold",
        target: DNS_STATE.SECONDARY,
        targetContent: targetContentFor(DNS_STATE.SECONDARY, config),
      };
    } else if (dnsState === DNS_STATE.PRIMARY) {
      nextState.lastDnsAction = "none:failure_threshold_not_met";
    } else {
      nextState.lastDnsAction = "none:dns_state_not_vps";
      nextState.lastErrorSummary = "Refusing automatic failover because managed DNS is not fully on VPS.";
    }
  }

  if (!candidate) {
    return { state: nextState, action: null };
  }

  if (nextState.manualLock) {
    nextState.lastDnsAction = `suppressed:manual_lock:${candidate.reason}`;
    return { state: nextState, action: null };
  }

  if (!config.dnsWritesEnabled) {
    nextState.lastDnsAction = `suppressed:dns_writes_disabled:${candidate.reason}`;
    return { state: nextState, action: null };
  }

  if (dnsUpdateInCooldown(nextState, nowMs, config)) {
    nextState.lastDnsAction = `suppressed:dns_update_cooldown:${candidate.reason}`;
    return { state: nextState, action: null };
  }

  nextState.lastDnsAction = `pending:${candidate.reason}:${candidate.target}`;
  return { state: nextState, action: candidate };
}

export function applyDnsUpdateSuccess(state, action, nowMs = Date.now()) {
  return {
    ...normalizeState(state),
    activeDnsTarget: action.target,
    lastDnsUpdateTimestamp: new Date(nowMs).toISOString(),
    lastDnsAction: `updated:${action.reason}:${action.target}`,
    lastErrorSummary: "",
  };
}

export function publicStatus(state, config, alarmTimestamp = null) {
  return {
    controller: config.controllerName,
    state: normalizeState(state),
    config: {
      healthCheckUrl: config.healthCheckUrl,
      expectedReadinessTarget: config.expectedReadinessTarget,
      checkIntervalSeconds: config.checkIntervalMs / 1000,
      failureThreshold: config.failureThreshold,
      recoveryThreshold: config.recoveryThreshold,
      minDnsUpdateIntervalSeconds: config.minDnsUpdateIntervalMs / 1000,
      dnsWritesEnabled: config.dnsWritesEnabled,
      dnsRecordType: config.dnsRecordType,
      dnsProxied: config.dnsProxied,
      dnsTtl: config.dnsTtl,
      primaryDnsTarget: config.primaryDnsTarget,
      secondaryDnsTarget: config.secondaryDnsTarget,
      managedRecords: config.dnsRecords.map((record) => ({
        name: record.name,
        type: record.type,
      })),
    },
    alarm: {
      scheduledAt: alarmTimestamp ? new Date(alarmTimestamp).toISOString() : null,
    },
  };
}
