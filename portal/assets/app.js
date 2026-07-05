const DATA_URL = "/data/status.json";

const $ = (id) => document.getElementById(id);

let currentData = null;

function text(value, fallback = "unknown") {
  if (value === null || value === undefined || value === "") {
    return fallback;
  }
  return String(value);
}

function escapeHtml(value) {
  return text(value, "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function clamp(value, min = 0, max = 100) {
  const number = Number(value);
  if (!Number.isFinite(number)) {
    return min;
  }
  return Math.min(Math.max(number, min), max);
}

function bytes(value) {
  const number = Number(value || 0);
  if (number < 1024) {
    return `${number} B`;
  }
  const units = ["KiB", "MiB", "GiB", "TiB"];
  let size = number;
  let unit = "B";
  for (const nextUnit of units) {
    size /= 1024;
    unit = nextUnit;
    if (size < 1024) {
      break;
    }
  }
  return `${size.toFixed(size >= 10 ? 1 : 2)} ${unit}`;
}

function duration(seconds) {
  const total = Number(seconds || 0);
  const days = Math.floor(total / 86400);
  const hours = Math.floor((total % 86400) / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  if (total < 60) {
    return `${Math.round(total)}s`;
  }
  if (days > 0) {
    return `${days}d ${hours}h`;
  }
  if (hours > 0) {
    return `${hours}h ${minutes}m`;
  }
  return `${minutes}m`;
}

function percent(value) {
  const number = Number(value || 0);
  return `${number.toFixed(1)}%`;
}

function shortCommit(value) {
  const commit = text(value);
  return commit.length > 12 ? commit.slice(0, 12) : commit;
}

function levelClass(level) {
  const normalized = String(level).toLowerCase();
  if (["critical", "failed", "inactive", "exited", "unhealthy", "send failed"].includes(normalized)) {
    return "pill--danger";
  }
  if (["warning", "degraded", "unknown", "misconfigured", "disabled"].includes(normalized)) {
    return "pill--warn";
  }
  if (["ok", "active", "running", "healthy", "enabled", "configured", "sent", "success"].includes(normalized)) {
    return "pill--ok";
  }
  return "pill--muted";
}

function stateFromPercent(value, warn = 70, danger = 85) {
  const number = Number(value || 0);
  if (number >= danger) {
    return "danger";
  }
  if (number >= warn) {
    return "warn";
  }
  return "ok";
}

function pill(value) {
  const label = text(value);
  return `<span class="pill ${levelClass(label)}">${escapeHtml(label)}</span>`;
}

function metric(label, value, hint = "") {
  return `
    <article class="metric">
      <div class="metric__label">${escapeHtml(label)}</div>
      <div class="metric__value">${escapeHtml(value)}</div>
      ${hint ? `<div class="metric__hint">${escapeHtml(hint)}</div>` : ""}
    </article>
  `;
}

function stat(label, value, hint = "") {
  return `
    <article class="stat-card">
      <span>${escapeHtml(label)}</span>
      <strong>${escapeHtml(value)}</strong>
      ${hint ? `<small>${escapeHtml(hint)}</small>` : ""}
    </article>
  `;
}

function gauge(label, rawValue, hint = "", options = {}) {
  const value = clamp(rawValue);
  const state = options.state || stateFromPercent(value, options.warn ?? 70, options.danger ?? 85);
  const display = options.display || percent(value);
  return `
    <article class="gauge-card gauge-card--${state}" style="--gauge-value: ${value}">
      <div class="gauge-ring" aria-hidden="true">
        <div class="gauge-ring__inner">${escapeHtml(display)}</div>
      </div>
      <div class="gauge-card__body">
        <div class="metric__label">${escapeHtml(label)}</div>
        ${hint ? `<div class="metric__hint">${escapeHtml(hint)}</div>` : ""}
      </div>
    </article>
  `;
}

function temperature(label, rawValue, state, hint = "") {
  const value = clamp(rawValue);
  return `
    <article class="temperature-card temperature-card--${state}" style="--temperature-value: ${value}">
      <div class="temperature-card__scale" aria-hidden="true">
        <span></span>
      </div>
      <div>
        <div class="metric__label">${escapeHtml(label)}</div>
        <div class="temperature-card__value">${escapeHtml(percent(value))}</div>
        ${hint ? `<div class="metric__hint">${escapeHtml(hint)}</div>` : ""}
      </div>
    </article>
  `;
}

function sparkline(values) {
  const numbers = values.map((item) => Number(item || 0));
  const max = Math.max(...numbers, 1);
  return `
    <span class="sparkline" aria-hidden="true">
      ${numbers
        .map((item) => `<i style="--bar-height: ${Math.max(8, (item / max) * 100)}%"></i>`)
        .join("")}
    </span>
  `;
}

function renderMetrics(id, items) {
  $(id).innerHTML = items.map((item) => metric(item.label, item.value, item.hint)).join("");
}

function renderStats(id, items) {
  $(id).innerHTML = items.map((item) => stat(item.label, item.value, item.hint)).join("");
}

function renderTable(id, rows, emptyMessage, colspan = 7) {
  $(id).innerHTML = rows.length
    ? rows.join("")
    : `<tr><td colspan="${colspan}">${escapeHtml(emptyMessage)}</td></tr>`;
}

function renderLinks(id, links) {
  $(id).innerHTML = (links || [])
    .map((link) => `<li><a href="${escapeHtml(link.url)}" rel="noreferrer">${escapeHtml(link.name)}</a></li>`)
    .join("");
}

function nonOkAlerts(data) {
  return (data.alerts?.items || []).filter((alert) => String(alert.level).toLowerCase() !== "ok");
}

function healthScore(data) {
  const resources = data.resources || {};
  const memory = resources.memory || {};
  const swap = resources.swap || {};
  const disk = resources.disk || {};
  const services = data.services || [];
  const alerts = nonOkAlerts(data);
  let score = 100;

  for (const alert of alerts) {
    score -= String(alert.level).toLowerCase() === "critical" ? 30 : 12;
  }

  score -= Math.max(0, Number(memory.used_percent || 0) - 70) * 0.35;
  score -= Math.max(0, Number(disk.used_percent || 0) - 70) * 0.35;
  score -= Math.max(0, Number(disk.inode_used_percent || 0) - 70) * 0.25;
  score -= Math.max(0, Number(swap.used_percent || 0) - 25) * 0.2;

  const importantInactive = services.filter(
    (service) =>
      ["ssh.service", "docker.service", "fail2ban.service"].includes(service.name) &&
      !["active", "activating"].includes(service.active),
  );
  score -= importantInactive.length * 20;

  return Math.round(clamp(score));
}

function overallState(data) {
  const alerts = data.alerts?.items || [];
  if (alerts.some((alert) => alert.level === "critical")) {
    return "critical";
  }
  if (alerts.some((alert) => alert.level === "warning")) {
    return "warning";
  }
  return healthScore(data) >= 80 ? "ok" : "warning";
}

function serviceHealthPercent(data) {
  const services = data.services || [];
  if (!services.length) {
    return 0;
  }
  const active = services.filter((service) => ["active", "activating"].includes(service.active)).length;
  return Math.round((active / services.length) * 100);
}

function alertPressure(data) {
  const alerts = nonOkAlerts(data);
  if (alerts.some((alert) => alert.level === "critical")) {
    return 100;
  }
  if (alerts.some((alert) => alert.level === "warning")) {
    return 62;
  }
  return 5;
}

function renderOverview(data) {
  const host = data.host || {};
  const gitops = data.gitops || {};
  const lastApply = gitops.last_apply || {};
  const resources = data.resources || {};
  const memory = resources.memory || {};
  const disk = resources.disk || {};
  const score = healthScore(data);
  const state = overallState(data);

  $("health-gauge-grid").innerHTML = gauge("Health Score", score, `Overall state ${state}`, {
    state: score >= 85 ? "ok" : score >= 65 ? "warn" : "danger",
    display: String(score),
  });

  renderMetrics("overview-grid", [
    { label: "Hostname", value: text(host.hostname), hint: text(host.fqdn) },
    { label: "Uptime", value: duration(host.uptime_seconds), hint: text(host.os) },
    { label: "Public IPv4", value: text(host.public_ipv4), hint: `IPv6 ${text(host.public_ipv6)}` },
    { label: "Kernel", value: text(host.kernel), hint: text(host.architecture) },
    { label: "Infra Commit", value: shortCommit(gitops.deployed_commit), hint: text(gitops.repository) },
    { label: "Last Apply", value: text(lastApply.status), hint: text(lastApply.run_url || lastApply.recorded_at) },
  ]);

  const services = serviceHealthPercent(data);
  const alerts = alertPressure(data);
  $("hotspot-grid").innerHTML = [
    temperature("Memory Pressure", memory.used_percent, stateFromPercent(memory.used_percent), `${bytes(memory.used_bytes)} used`),
    temperature("Disk Pressure", disk.used_percent, stateFromPercent(disk.used_percent), `${bytes(disk.free_bytes)} free`),
    temperature("Service Health", services, services >= 90 ? "ok" : services >= 70 ? "warn" : "danger", `${services}% active`),
    temperature("Alert Level", alerts, alerts >= 90 ? "danger" : alerts >= 40 ? "warn" : "ok", `${nonOkAlerts(data).length} active issue(s)`),
  ].join("");

  $("portal-policy").textContent = text(data.portal?.management_policy, "Read-only. Production changes still go through GitOps.");
}

function renderEmailReporting(data) {
  const reporting = data.email_reporting || {};
  const alerts = data.alerts?.items || [];
  const enabledLabel = reporting.enabled ? "enabled" : "disabled";
  const configuredLabel = reporting.configured ? "configured" : "misconfigured";
  const lastSuccess = reporting.last_report_success_at || reporting.last_report_sent_at || "never";
  const lastRun = reporting.last_report_run_at || reporting.updated_at || "never";

  $("email-state").innerHTML = `${pill(enabledLabel)} ${pill(configuredLabel)} ${pill(reporting.status)}`;
  renderMetrics("email-status-grid", [
    { label: "Email Reporting", value: enabledLabel, hint: text(reporting.status) },
    { label: "SMTP Configured", value: reporting.smtp_host_configured ? "yes" : "no", hint: `${text(reporting.recipients_count, "0")} recipient(s)` },
    { label: "Next Report", value: text(reporting.next_report_run_at), hint: text(reporting.timer || "health report timer") },
    { label: "Last Run", value: text(lastRun), hint: `mode ${text(reporting.mode)}` },
    { label: "Last Success", value: text(lastSuccess), hint: "health report email" },
    { label: "Last Error", value: text(reporting.last_error, "none"), hint: text(reporting.email_config_source) },
  ]);
  renderStats("reporting-strip", [
    { label: "Pending Alerts", value: text(reporting.pending_alerts, "0"), hint: text(reporting.last_alert_check_at) },
    { label: "Suppressed", value: text(reporting.suppressed_alerts, "0"), hint: `cooldown ${duration(reporting.cooldown_seconds)}` },
    { label: "Timer", value: text(reporting.timer_active), hint: text(reporting.timer_sub_state) },
    { label: "Updated", value: text(reporting.updated_at), hint: "reporting snapshot" },
  ]);
  $("alerts-list").innerHTML = alerts
    .map((alert) => `<li class="alert--${escapeHtml(alert.level)}">${pill(alert.level)} <span>${escapeHtml(alert.message)}</span></li>`)
    .join("");
}

function renderResources(data) {
  const resources = data.resources || {};
  const memory = resources.memory || {};
  const swap = resources.swap || {};
  const disk = resources.disk || {};
  const nutsnewsDisk = resources.nutsnews_disk || {};
  const load = resources.load_average || {};
  const network = resources.network || {};

  $("resource-gauges").innerHTML = [
    gauge("CPU", resources.cpu_percent ?? 0, "Short sample"),
    gauge("RAM", memory.used_percent, `${bytes(memory.used_bytes)} of ${bytes(memory.total_bytes)}`),
    gauge("Root Disk", disk.used_percent, `${bytes(disk.free_bytes)} free`),
    gauge("Swap", swap.used_percent, `${bytes(swap.used_bytes)} of ${bytes(swap.total_bytes)}`, { warn: 50, danger: 75 }),
    gauge("Root Inodes", disk.inode_used_percent, `${text(disk.inode_used)} of ${text(disk.inode_total)}`),
  ].join("");

  renderMetrics("resource-grid", [
    {
      label: "Load",
      value: `${text(load.one)} / ${text(load.five)} / ${text(load.fifteen)}`,
      hint: "1m / 5m / 15m",
    },
    { label: "Load Shape", value: `${text(load.one)}`, hint: `${text(load.five)} five-minute ${text(load.fifteen)} fifteen-minute` },
    { label: "NutsNews Disk", value: percent(nutsnewsDisk.used_percent), hint: text(nutsnewsDisk.path) },
    { label: "Host Received", value: bytes(network.rx_bytes), hint: "Interface counters since boot" },
    { label: "Host Sent", value: bytes(network.tx_bytes), hint: "Interface counters since boot" },
    { label: "Load Trend", value: "compact", hint: `${load.one ?? 0} ${load.five ?? 0} ${load.fifteen ?? 0}` },
  ]);
  $("load-sparkline").innerHTML = sparkline([load.one, load.five, load.fifteen]);
}

function processRows(processes, primaryMetric) {
  return (processes || []).map((process) => {
    const command = text(process.command || process.name);
    const app = text(process.name);
    const primary = primaryMetric === "cpu" ? percent(process.cpu_percent) : bytes(process.memory_bytes);
    const secondary = primaryMetric === "cpu" ? bytes(process.memory_bytes) : percent(process.cpu_percent);
    const search = `${app} ${process.pid} ${process.user} ${command}`.toLowerCase();
    return `
      <tr data-search="${escapeHtml(search)}">
        <td title="${escapeHtml(command)}">${escapeHtml(app)}</td>
        <td>${escapeHtml(process.pid)}</td>
        <td>${escapeHtml(process.user)}</td>
        <td>${escapeHtml(primary)}</td>
        <td>${escapeHtml(secondary)}</td>
        <td>${escapeHtml(process.threads)}</td>
        <td>${escapeHtml(duration(process.cpu_time_seconds))}</td>
        <td>${escapeHtml(duration(process.elapsed_seconds))}</td>
        <td>${escapeHtml(duration(process.idle_seconds))}</td>
      </tr>
    `;
  });
}

function applyProcessFilter() {
  const query = $("process-filter").value.trim().toLowerCase();
  for (const tableId of ["process-memory-table", "process-cpu-table"]) {
    for (const row of $(tableId).querySelectorAll("tr[data-search]")) {
      row.hidden = Boolean(query) && !row.dataset.search.includes(query);
    }
  }
}

function renderProcessVisibility(data) {
  const processes = data.processes || {};
  $("process-method").textContent = text(
    processes.method,
    "CPU percent is best-effort process attribution from the local collector.",
  );
  renderTable("process-memory-table", processRows(processes.top_memory, "memory"), "No process data found.", 9);
  renderTable("process-cpu-table", processRows(processes.top_cpu, "cpu"), "No process data found.", 9);
  applyProcessFilter();
}

function renderDisk(data) {
  const diskUsage = data.disk_usage || {};
  $("disk-hotspots-method").textContent = `${text(diskUsage.method, "Cached folder scan.")} ${
    diskUsage.from_cache ? "Serving cached data." : "Fresh scan."
  }`;
  const rows = (diskUsage.top_folders || []).map(
    (folder) => `
      <tr>
        <td>${escapeHtml(folder.path)}</td>
        <td>${escapeHtml(bytes(folder.size_bytes))}</td>
      </tr>
    `,
  );
  renderTable("disk-hotspots-table", rows, diskUsage.errors?.join(" | ") || "No disk scan data found.", 2);
  renderStats("disk-stats", [
    { label: "Scan Cache", value: diskUsage.from_cache ? "cached" : "fresh", hint: `${duration(diskUsage.cache_seconds)} TTL` },
    { label: "Scan Roots", value: text((diskUsage.scan_roots || []).length, "0"), hint: (diskUsage.scan_roots || []).join(", ") },
    { label: "Largest Entry", value: bytes(diskUsage.top_folders?.[0]?.size_bytes), hint: text(diskUsage.top_folders?.[0]?.path) },
  ]);
}

function renderNetwork(data) {
  const network = data.resources?.network || {};
  const processNetwork = data.process_network || {};
  renderMetrics("network-grid", [
    { label: "Host Received", value: bytes(network.rx_bytes), hint: "Interface counters since boot" },
    { label: "Host Sent", value: bytes(network.tx_bytes), hint: "Interface counters since boot" },
    {
      label: "Per-App Network",
      value: processNetwork.available ? "available" : "not available",
      hint: text(processNetwork.method),
    },
    { label: "Telemetry Label", value: "honest", hint: text(processNetwork.note) },
  ]);

  const rows = (network.interfaces || []).map(
    (iface) => `
      <tr>
        <td>${escapeHtml(iface.name)}</td>
        <td>${escapeHtml(bytes(iface.rx_bytes))}</td>
        <td>${escapeHtml(bytes(iface.tx_bytes))}</td>
      </tr>
    `,
  );
  renderTable("network-interface-table", rows, "No interface counter data found.", 3);
}

function renderDocker(data) {
  const containers = data.docker?.containers || [];
  const rows = containers.map(
    (container) => `
      <tr>
        <td>${escapeHtml(container.name)}</td>
        <td>${pill(container.state)}</td>
        <td>${pill(container.health)}</td>
        <td>${escapeHtml(container.restart_count)}</td>
        <td>${escapeHtml(container.image)}</td>
        <td>${escapeHtml(container.ports || "none")}</td>
        <td>${escapeHtml(container.compose_project || "none")}</td>
      </tr>
    `,
  );
  renderTable("docker-table", rows, data.docker?.error || "No containers found.");
}

function renderServices(data) {
  const services = data.services || [];
  const rows = services.map(
    (service) => `
      <tr>
        <td>${escapeHtml(service.name)}</td>
        <td>${pill(service.active)}</td>
        <td>${pill(service.enabled)}</td>
      </tr>
    `,
  );
  renderTable("services-table", rows, "No service status found.", 3);
}

function filterLogLines(lines) {
  const query = $("log-filter").value.trim().toLowerCase();
  if (!Array.isArray(lines) || !lines.length) {
    return ["No recent lines."];
  }
  if (!query) {
    return lines;
  }
  const filtered = lines.filter((line) => line.toLowerCase().includes(query));
  return filtered.length ? filtered : ["No matching lines."];
}

function renderLogs(data) {
  const logs = data.logs || {};
  $("log-redaction").textContent = text(logs.redaction, "Recent logs with secret redaction.");
  $("logs-caddy").textContent = filterLogLines(logs.caddy).join("\n");
  $("logs-journal").textContent = filterLogLines(logs.journal_warnings).join("\n");
  $("logs-auth").textContent = filterLogLines(logs.auth).join("\n");
}

function renderSecurity(data) {
  const security = data.security || {};
  const updates = security.pending_updates || {};
  const failed = security.failed_logins || {};
  const ssh = security.ssh_hardening || {};
  const firewall = Array.isArray(security.firewall) ? security.firewall.join(" | ") : "unknown";
  renderMetrics("security-grid", [
    { label: "Firewall", value: firewall || "unknown", hint: "UFW verbose status" },
    { label: "Pending Updates", value: text(updates.count, "0"), hint: `${text(updates.security_count, "0")} security updates` },
    { label: "Last Reboot", value: text(security.last_reboot), hint: "UTC" },
    { label: "Failed Logins", value: text(failed.recent_failed_login_lines, "0"), hint: `${text(failed.invalid_user_lines, "0")} invalid user lines` },
    { label: "SSH Password Auth", value: text(ssh.password_authentication), hint: `Root login ${text(ssh.permit_root_login)}` },
    { label: "SSH Forwarding", value: text(ssh.allow_tcp_forwarding), hint: `Keyboard interactive ${text(ssh.kbd_interactive_authentication)}` },
  ]);
  $("open-ports").textContent = Array.isArray(security.open_ports) && security.open_ports.length
    ? security.open_ports.join("\n")
    : "No open port data found.";
}

function renderBackups(data) {
  const backups = data.backups || {};
  const latest = backups.latest || {};
  renderMetrics("backup-grid", [
    { label: "Backup Directory", value: text(backups.directory), hint: `${bytes(backups.size_bytes)} used` },
    { label: "Latest Backup", value: text(latest.path, "placeholder"), hint: text(latest.updated_at || backups.latest_status) },
    { label: "Snapshot Reminder", value: "planned", hint: text(backups.snapshot_reminder) },
  ]);
}

function renderGitopsAndRunbooks(data) {
  const gitops = data.gitops || {};
  const lastApply = gitops.last_apply || {};
  renderMetrics("gitops-grid", [
    { label: "Repository", value: text(gitops.repository), hint: "Source of truth" },
    { label: "Commit", value: shortCommit(gitops.deployed_commit), hint: text(gitops.deployed_commit) },
    { label: "Apply Run", value: text(lastApply.status), hint: text(lastApply.run_url || "No protected apply marker yet") },
    { label: "Drift", value: "watch", hint: text(gitops.drift_warning) },
  ]);
  renderLinks("workflow-links", gitops.workflow_links);
  renderLinks("runbook-links", data.runbooks);
}

function render(data) {
  const state = overallState(data);
  $("overall-state").className = `pill ${levelClass(state)}`;
  $("overall-state").textContent = state;
  $("generated-at").textContent = `Snapshot ${text(data.generated_at)}`;
  renderOverview(data);
  renderEmailReporting(data);
  renderResources(data);
  renderProcessVisibility(data);
  renderDisk(data);
  renderNetwork(data);
  renderDocker(data);
  renderServices(data);
  renderLogs(data);
  renderSecurity(data);
  renderBackups(data);
  renderGitopsAndRunbooks(data);
}

async function load() {
  try {
    const response = await fetch(`${DATA_URL}?t=${Date.now()}`, { cache: "no-store" });
    if (!response.ok) {
      throw new Error(`status ${response.status}`);
    }
    currentData = await response.json();
    render(currentData);
  } catch (error) {
    $("overall-state").className = "pill pill--danger";
    $("overall-state").textContent = "unavailable";
    $("generated-at").textContent = `Collector data failed to load: ${error.message}`;
  }
}

$("process-filter").addEventListener("input", applyProcessFilter);
$("log-filter").addEventListener("input", () => {
  if (currentData) {
    renderLogs(currentData);
  }
});

load();
window.setInterval(load, 60000);
