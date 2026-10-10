/* Offline driver tests. No connector/network calls or publication gates. */
"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const vm = require("node:vm");
const crypto = require("node:crypto");
const child = require("node:child_process");
const source = fs.readFileSync(path.resolve(__dirname, "../wpvibe_driver.js"), "utf8");
let count = 0;
function check(name, fn) { fn(); count++; }
function runtime(tools = {}) { const context = vm.createContext({tools, Date}); vm.runInContext(source, context); return context; }
const helpers = runtime().sgnewsWpvibeDriverInternals;
const uuid = "a".repeat(32);
const site = "https://sggroup.jp";
const request = (method = "GET") => ({version: 1, type: "request", id: uuid, site_url: site, expected_user_id: 5,
  method, endpoint: method === "GET" ? "/wp/v2/users/me" : "/sgnews-publisher/v1/articles/draft",
  params: method === "GET" ? {context: "edit"} : {}, payload: method === "GET" ? null : {author: 5, categories: [258], html: "正常"}, max_response_bytes: 8388608});
const success = data => ({content: [{type: "text", text: JSON.stringify(data)}], isError: false});
const args = ["--route", "query", "--context", "wpvibe:sggroup.jp:author-5", "--connection-revision", "observed-offline-test", "authenticate"];

check("Unicode/base64 round trip including astral text and shell metacharacters", () => {
  const value = "日本語😀 `$(false)` ' \\\"\n".repeat(30000);
  assert.equal(helpers.decode(helpers.unbase64(helpers.base64(helpers.utf8(value)))), value);
  assert.throws(() => helpers.utf8("\ud800"));
  assert.throws(() => helpers.decode([0xc0, 0xaf]));
});
check("shell quoting preserves literal input without substitution", () => {
  const value = "' `$(printf forbidden)` $HOME\n日本語";
  const command = "python -c 'import sys,json; print(json.dumps(sys.argv[1]))' " + helpers.shell(value);
  const result = child.spawnSync("bash", ["-c", command], {encoding: "utf8"});
  assert.equal(result.status, 0); assert.equal(JSON.parse(result.stdout), value);
});
check("strict route/user/site/category and sensitive-field rejection", () => {
  helpers.requestValid(request(), uuid); helpers.requestValid(request("POST"), uuid);
  for (const change of [{site_url: "https://other.invalid"}, {expected_user_id: 1}, {method: "PUT"},
    {endpoint: "/wp/v2/users/5"}, {params: {headers: "x"}}, {params: {rest_route: "/"}},
    {payload: {author: 1}}, {payload: {categories: [1]}}, {payload: {nested: {authorization: "private"}}}, {extra: 1}]) {
    assert.throws(() => helpers.requestValid({...request("POST"), ...change}, uuid));
  }
});
check("CLI owner/bridge/config override and duplicated options refused", () => {
  helpers.argumentsValid(args);
  for (const extra of [["--expected-user-id", "1"], ["--transport", "direct"], ["--bridge-dir", "/tmp/evil"],
    ["--config", "/tmp/evil"], ["--route", "pretty"], ["diagnose"], ["--authorization", "hidden"]])
    assert.throws(() => helpers.argumentsValid([...args.slice(0, -1), ...extra, "authenticate"]));
});
check("strict full JSON preserves complete large language/source response", () => {
  const data = {id: 5, html: "<p>日本語 ' `$(false)` 😀</p>".repeat(18000), quote: '\"\\\n'};
  const parsed = helpers.parseTool(success(data), "GET");
  assert.equal(parsed.ok, true); assert.deepEqual(JSON.parse(JSON.stringify(parsed.data)), data);
});
check("HTML, unknown footer, truncation and tool errors fail closed", () => {
  for (const value of [{content: [{type: "text", text: '<html><script type="application/ld+json">{"id":5}</script></html>'}]},
    {content: [{type: "text", text: '{"id":5}\nUnknown usage footer'}]},
    {content: [{type: "text", text: '{"id":5'}]}, {...success({id: 5}), truncated: true},
    {...success({id: 5}), isError: true}, {...success({id: 5}), structuredContent: {error_code: "ERROR"}},
    success({id: 5, truncated: true}), success({code: "rest_forbidden", data: {status: 403}})]) {
    const result = helpers.parseTool(value, "POST");
    assert.equal(result.ok, false); assert.equal(result.error.ambiguous_write, true);
    assert(!JSON.stringify(result).includes("<html>"));
  }
});
check("explicit REST client rejection differs from unknown/429/server response", () => {
  for (const status of [401, 403, 404, 429, 500]) {
    const result = helpers.parseTool({isError: true, content: [{type: "text", text: `REST API error (${status}):\n<private body>`}]}, "POST");
    assert.equal(result.error.status, status);
    assert.equal(result.error.ambiguous_write, status >= 500 || status === 429);
    assert(!JSON.stringify(result).includes("private body"));
  }
});
check("remote oversize and output-body/credential redaction rejected", () => {
  const result = helpers.parseTool(success({html: "あ".repeat(2800000)}), "POST");
  assert.equal(result.ok, false); assert.equal(result.error.ambiguous_write, true);
  for (const value of [{html: "private"}, {error: {headers: "private"}}, {nested: {password: "private"}}])
    assert.throws(() => helpers.outputSafe(value));
});

async function integration({post = false, malformed = false, repeated = false, deadOwner = false, toolThrows = false} = {}) {
  let dir, raw, document, toolCalls = 0, written, commandMax = 0;
  const data = {id: 5, content_raw: "<style>p{content:'`$(false)`';}</style><p>日本語😀</p>".repeat(7000)};
  const commandLogs = [];
  const tools = {
    async exec_command(options) {
      commandMax = Math.max(commandMax, Buffer.byteLength(options.cmd));
      if (options.cmd.startsWith("'python' '-m' 'runtime.publish'")) {
        dir = /'--bridge-dir' '([^']+)'/.exec(options.cmd)[1];
        assert(options.cmd.includes("'--expected-user-id' '5'"));
        document = request(post ? "POST" : "GET");
        if (post) document.payload.html = data.content_raw;
        raw = malformed ? Buffer.from('{"version":') : Buffer.from(JSON.stringify(document) + "\n");
        fs.writeFileSync(path.join(dir, ".owner"), JSON.stringify({version: 1, pid: deadOwner ? 999999999 : process.pid,
          site_url: site, expected_user_id: 5}), {mode: 0o600});
        fs.writeFileSync(path.join(dir, "request-" + uuid + ".json"), raw, {mode: 0o600});
        return {output: "", session_id: 42};
      }
      const result = child.spawnSync("bash", ["-c", options.cmd], {cwd: options.workdir, encoding: "utf8", maxBuffer: 32 * 1024 * 1024});
      commandLogs.push(result.stdout);
      return {output: result.stdout, exit_code: result.status};
    },
    async write_stdin(options) {
      if (options.chars === "\u0003") return {output: "", exit_code: 130};
      const target = path.join(dir, "response-" + uuid + ".json");
      assert(fs.existsSync(target)); written = JSON.parse(fs.readFileSync(target, "utf8"));
      assert.equal(written.request_sha256, crypto.createHash("sha256").update(raw).digest("hex"));
      assert.equal(written.id, uuid); assert.equal(written.site_url, site); assert.equal(written.expected_user_id, 5);
      assert.equal(fs.statSync(target).mode & 0o777, 0o600);
      if (repeated) return {output: "", session_id: 42};
      fs.unlinkSync(target); fs.unlinkSync(path.join(dir, "request-" + uuid + ".json")); fs.unlinkSync(path.join(dir, ".owner"));
      return {output: JSON.stringify({passed: written.ok, production_ready: false, identity: {id: 5, roles: ["author"]}}), exit_code: written.ok ? 0 : 2};
    },
    async mcp__codex_apps__wpvibe_rest_api(parameters) {
      toolCalls++;
      assert.equal(parameters.site_url, site); assert.equal(parameters.fields, "all"); assert.equal(parameters.max_length, 8388608);
      assert.equal(parameters.method, post ? "POST" : "GET");
      if (post) assert.deepEqual(JSON.parse(parameters.body), document.payload);
      assert(!("headers" in parameters)); assert(!("auth" in parameters));
      if (toolThrows) throw Error("private remote message");
      return success(data);
    }
  };
  const context = runtime(tools);
  try {
    const result = await context.runWpvibe({args, maxRuntimeSeconds: 10});
    assert(commandMax < 128 * 1024, "Linux per-argument bound");
    if (malformed || deadOwner) { assert.equal(toolCalls, 0); assert.equal(result.passed, false); }
    else if (repeated) { assert.equal(toolCalls, 1); assert.equal(result.error.code, "driver_repeated_request_rejected"); }
    else if (toolThrows) { assert.equal(toolCalls, 1); assert.equal(written.ok, false); assert.equal(written.error.ambiguous_write, post); }
    else { assert.equal(toolCalls, 1); assert.equal(result.exit_code, 0); assert.deepEqual(written.data, data); }
    assert(!JSON.stringify(result).includes(data.content_raw));
    assert(!JSON.stringify(result).includes("private remote message"));
    assert(commandLogs.every(log => !log.includes("<style>")));
  } finally { if (dir && fs.existsSync(dir)) fs.rmSync(dir, {recursive: true, force: true}); }
}

async function actualPythonCli(wrongActor = false) {
  let processHandle, stdout = "", dir, calls = 0;
  const tools = {
    async exec_command(options) {
      if (options.cmd.startsWith("'python' '-m' 'runtime.publish'")) {
        dir = /'--bridge-dir' '([^']+)'/.exec(options.cmd)[1];
        processHandle = child.spawn("bash", ["-c", options.cmd], {cwd: options.workdir,
          env: {...process.env, WP_BASE_URL: site, WP_NEWS_CATEGORY_ID: "258", WP_AUTHORIZATION: "ignored-offline-value"}});
        processHandle.stdout.on("data", data => { stdout += data.toString("utf8"); });
        processHandle.stderr.on("data", () => {});
        return {output: "", session_id: 43};
      }
      const result = child.spawnSync("bash", ["-c", options.cmd], {cwd: options.workdir, encoding: "utf8", maxBuffer: 2 * 1024 * 1024});
      return {output: result.stdout, exit_code: result.status};
    },
    async write_stdin(options) {
      if (options.chars === "\u0003") processHandle.kill("SIGINT");
      await new Promise(resolve => setTimeout(resolve, 100));
      if (processHandle.exitCode === null) return {output: "", session_id: 43};
      return {output: stdout, exit_code: processHandle.exitCode};
    },
    async mcp__codex_apps__wpvibe_rest_api(parameters) {
      calls++; assert.equal(parameters.method, "GET"); assert.equal(parameters.route, "/wp/v2/users/me");
      return success({id: wrongActor ? 1 : 5, roles: ["author"], capabilities: {edit_posts: true, publish_posts: true}});
    }
  };
  try {
    const result = await runtime(tools).runWpvibe({args, maxRuntimeSeconds: 10});
    assert.equal(calls, 1); assert.equal(result.exit_code, wrongActor ? 2 : 0);
    if (wrongActor) { assert.equal(result.result.passed, false); assert.notEqual(result.result.production_ready, true); }
    else { assert.equal(result.result.authenticated, true); assert.equal(result.result.production_ready, false); }
    assert(!JSON.stringify(result).includes("ignored-offline-value"));
  } finally {
    if (processHandle && processHandle.exitCode === null) processHandle.kill("SIGKILL");
    if (dir && fs.existsSync(dir)) fs.rmSync(dir, {recursive: true, force: true});
  }
}

async function main() {
  for (const [name, options] of [
    ["full private FILE GET response >128KiB", {}],
    ["full private FILE POST body/response >128KiB exact and single dispatch", {post: true}],
    ["malformed private request has no remote dispatch", {malformed: true}],
    ["dead Python waiter has no remote dispatch", {deadOwner: true}],
    ["repeated request never replays POST", {post: true, repeated: true}],
    ["unknown tool exception remains ambiguous and has no replay", {post: true, toolThrows: true}]
  ]) { await integration(options); count++; }
  await actualPythonCli(false); count++;
  await actualPythonCli(true); count++;
  console.log(JSON.stringify({passed: true, tests: count, network_calls: 0}));
}
main().catch(error => { console.error(error.stack); process.exitCode = 1; });
