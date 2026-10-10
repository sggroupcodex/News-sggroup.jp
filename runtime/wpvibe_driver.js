/* Trusted functions.exec orchestration for the Author 5 connector transport.
 * Load this reviewed local source, then await runWpvibe({args:[...]}); no Node,
 * browser session cookies, credentials, or connector response bodies are logged.
 * The Python CLI owns publication gates and the exclusive private FILE bridge.
 */
(function () {
  "use strict";
  const SITE = "https://sggroup.jp";
  const WORKDIR = "/workspace/sggroup-news-automation";
  const LIMIT = 8388608;
  const ENVELOPE_LIMIT = LIMIT + 16384;
  const CHUNK = 24576;
  const TOOL = "mcp__codex_apps__wpvibe_rest_api";
  const GET = /^\/(?:wp\/v2\/users\/me|wp\/v2\/posts\/[1-9][0-9]*|sgnews-publisher\/v1\/(?:status|articles\/[1-9][0-9]*(?:\/preview)?|operations\/[a-f0-9]{64}))$/;
  const POST = /^\/sgnews-publisher\/v1\/(?:canonicalize|lease(?:\/renew|\/release)?|articles\/(?:draft|publish))$/;
  const FORBIDDEN = new Set(["auth", "authorization", "headers", "cookie", "cookies", "password", "application_password", "username"]);

  function fault(code) { const e = new Error(code); e.code = code; return e; }
  function plain(value) { return value !== null && typeof value === "object" && !Array.isArray(value); }
  function shell(value) { return "'" + String(value).replace(/'/g, "'\\''") + "'"; }
  function utf8(value) {
    const out = new Uint8Array(value.length * 3); let position = 0;
    for (let i = 0; i < value.length; i++) {
      let n = value.charCodeAt(i);
      if (n >= 0xd800 && n <= 0xdbff) {
        const next = value.charCodeAt(++i);
        if (!(next >= 0xdc00 && next <= 0xdfff)) throw fault("bridge_invalid_unicode");
        n = 0x10000 + ((n - 0xd800) << 10) + next - 0xdc00;
      } else if (n >= 0xdc00 && n <= 0xdfff) throw fault("bridge_invalid_unicode");
      if (n < 128) out[position++] = n;
      else if (n < 2048) { out[position++] = 192 | n >> 6; out[position++] = 128 | n & 63; }
      else if (n < 65536) { out[position++] = 224 | n >> 12; out[position++] = 128 | n >> 6 & 63; out[position++] = 128 | n & 63; }
      else { out[position++] = 240 | n >> 18; out[position++] = 128 | n >> 12 & 63; out[position++] = 128 | n >> 6 & 63; out[position++] = 128 | n & 63; }
    }
    return out.subarray(0, position);
  }
  const B64 = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
  function base64(bytes) {
    let result = "";
    for (let i = 0; i < bytes.length; i += 3) {
      const n = bytes[i] << 16 | (bytes[i + 1] || 0) << 8 | (bytes[i + 2] || 0);
      result += B64[n >>> 18] + B64[n >>> 12 & 63] + (i + 1 < bytes.length ? B64[n >>> 6 & 63] : "=") + (i + 2 < bytes.length ? B64[n & 63] : "=");
    }
    return result;
  }
  function unbase64(value) {
    if (!/^(?:[A-Za-z0-9+/]{4})*(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?$/.test(value)) throw fault("bridge_invalid_base64");
    let out = [], acc = 0, bits = 0;
    for (const char of value.replace(/=+$/, "")) {
      acc = acc << 6 | B64.indexOf(char); bits += 6;
      if (bits >= 8) { bits -= 8; out.push(acc >>> bits & 255); }
    }
    return out;
  }
  function decode(bytes) {
    // decodeURIComponent rejects malformed UTF-8, including surrogate encodings.
    const pieces = [];
    for (let i = 0; i < bytes.length; i += CHUNK) {
      let piece = "";
      for (const byte of bytes.slice(i, i + CHUNK)) piece += "%" + byte.toString(16).padStart(2, "0");
      pieces.push(piece);
    }
    try { return decodeURIComponent(pieces.join("")); }
    catch (_) { throw fault("bridge_invalid_utf8"); }
  }
  function safeFields(value) {
    if (Array.isArray(value)) { for (const item of value) safeFields(item); return; }
    if (plain(value)) {
      for (const key of Object.keys(value)) {
        if (FORBIDDEN.has(key.toLowerCase())) throw fault("bridge_sensitive_field_rejected");
        if ((key === "author" || key === "user_id" || key === "expected_user_id") && value[key] !== 5) throw fault("bridge_other_actor_rejected");
        if (key === "categories" && !(Array.isArray(value[key]) && value[key].length === 1 && value[key][0] === 258)) throw fault("bridge_other_category_rejected");
        safeFields(value[key]);
      }
    } else if (typeof value === "number" && !Number.isFinite(value)) throw fault("bridge_nonfinite_number");
  }
  function requestValid(request, id) {
    const keys = ["version", "type", "id", "site_url", "expected_user_id", "method", "endpoint", "params", "payload", "max_response_bytes"];
    if (!plain(request) || Object.keys(request).length !== keys.length || keys.some(key => !(key in request)) ||
        request.version !== 1 || request.type !== "request" || request.id !== id || !/^[a-f0-9]{32}$/.test(id) ||
        request.site_url !== SITE || request.expected_user_id !== 5 || request.max_response_bytes !== LIMIT ||
        !plain(request.params) || !(request.payload === null || plain(request.payload))) throw fault("bridge_request_invalid");
    if (request.method === "GET" ? !GET.test(request.endpoint) || request.payload !== null :
        request.method !== "POST" || !POST.test(request.endpoint)) throw fault("bridge_route_rejected");
    for (const [key, value] of Object.entries(request.params)) {
      if (!/^[a-zA-Z_][a-zA-Z0-9_]*$/.test(key) || !["string", "number", "boolean"].includes(typeof value) ||
          key === "rest_route") throw fault("bridge_query_rejected");
    }
    safeFields(request.params); safeFields(request.payload);
    return request;
  }
  function toolError(result, method) {
    // Error text may contain remote HTML. Extract only an explicit tool status.
    let status = null;
    if (plain(result)) {
      for (const block of Array.isArray(result.content) ? result.content : []) {
        if (block.type !== "text" || typeof block.text !== "string") continue;
        const match = /^REST API error \(([1-5][0-9]{2})\):/.exec(block.text);
        if (match) { status = Number(match[1]); break; }
      }
    }
    const write = method !== "GET";
    return {code: "wpvibe_tool_error", status, retryable: status === 429 || (status !== null && status >= 500),
      ambiguous_write: write && (status === null || status === 429 || status >= 500 || status >= 300 && status < 400)};
  }
  function parseTool(result, method) {
    if (!plain(result) || result.isError === true || result.truncated === true ||
        plain(result.structuredContent) && (result.structuredContent.error_code || result.structuredContent.truncated === true))
      return {ok: false, error: toolError(result, method)};
    // Do not scan HTML or extract an arbitrary embedded object. A success must
    // consist of one complete JSON text block, with no unknown usage footer.
    const content = result.content;
    if (!Array.isArray(content) || content.length !== 1 || content[0].type !== "text" || typeof content[0].text !== "string")
      return {ok: false, error: {code: "wpvibe_response_envelope_invalid", status: null, retryable: false, ambiguous_write: method !== "GET"}};
    const raw = content[0].text.trim();
    if (!(raw.startsWith("{") || raw.startsWith("[")) || utf8(raw).length > LIMIT)
      return {ok: false, error: {code: "wpvibe_response_invalid_or_oversize", status: null, retryable: false, ambiguous_write: method !== "GET"}};
    let data;
    try { data = JSON.parse(raw); }
    catch (_) { return {ok: false, error: {code: "wpvibe_response_incomplete_or_nonjson", status: null, retryable: false, ambiguous_write: method !== "GET"}}; }
    if (!(plain(data) || Array.isArray(data)))
      return {ok: false, error: {code: "wpvibe_response_json_shape_invalid", status: null, retryable: false, ambiguous_write: method !== "GET"}};
    if (plain(data) && (data.truncated === true || data.error_code ||
        typeof data.code === "string" && plain(data.data) && Number.isInteger(data.data.status) && data.data.status >= 400))
      return {ok: false, error: {code: "wpvibe_response_error_or_truncated", status: null, retryable: false, ambiguous_write: method !== "GET"}};
    return {ok: true, status: 200, truncated: false, data};
  }
  function argumentsValid(args) {
    if (!Array.isArray(args) || args.length > 40 || args.some(arg => typeof arg !== "string" || !arg || arg.includes("\0") || arg.length > 4096)) throw fault("driver_cli_arguments_invalid");
    const common = new Set(["--route", "--context", "--connection-revision", "--approved-plugin", "--publisher-code-sha256", "--publisher-policy-version"]);
    const commandFlags = {
      authenticate: new Set(), diagnose: new Set(), canonicalize: new Set(["--language", "--slug", "--input", "--output"]),
      execute: new Set(["--state", "--revision-id", "--bundle", "--browser-artifacts", "--chromium"])
    };
    let command = null; const seen = new Set();
    for (let i = 0; i < args.length; i++) {
      const arg = args[i];
      if (Object.hasOwn(commandFlags, arg)) { if (command) throw fault("driver_duplicate_subcommand"); command = arg; continue; }
      if (seen.has(arg) || !(command ? commandFlags[command].has(arg) : common.has(arg)) || i + 1 >= args.length) throw fault("driver_cli_argument_rejected");
      seen.add(arg); const value = args[++i];
      if (value.startsWith("--")) throw fault("driver_cli_value_invalid");
      if (arg === "--route" && !["pretty", "query"].includes(value)) throw fault("driver_cli_route_invalid");
    }
    if (!command || !seen.has("--route") || !seen.has("--context") || !seen.has("--connection-revision")) throw fault("driver_runtime_binding_required");
    return args.slice();
  }

  // Python helpers touch only the newly created private bridge. Commands use
  // shell-quoted arguments, bounded base64 chunks, O_NOFOLLOW, exact file hashes,
  // regular-file/owner/mode checks, and atomic response replacement.
  const COMMON = `import os,sys,json,stat,hashlib,base64\nLIMIT=8388608+16384\nd=sys.argv[1]\ns=os.lstat(d)\nassert stat.S_ISDIR(s.st_mode) and stat.S_IMODE(s.st_mode)==0o700 and s.st_uid==os.getuid()\ndef read(name):\n assert '/' not in name and name not in ('.','..')\n fd=os.open(os.path.join(d,name),os.O_RDONLY|os.O_NOFOLLOW)\n with os.fdopen(fd,'rb') as f:\n  s=os.fstat(f.fileno())\n  assert stat.S_ISREG(s.st_mode) and stat.S_IMODE(s.st_mode)==0o600 and s.st_uid==os.getuid() and s.st_nlink==1 and s.st_size<=LIMIT\n  data=f.read(LIMIT+1)\n  assert len(data)<=LIMIT\n  return data\n`;
  const PROBE = COMMON + `import re\nnames=[n for n in os.listdir(d) if n.startswith('request-') and n.endswith('.json')]\nassert len(names)<=1\nif not names: print(json.dumps({'pending':None}))\nelse:\n owner=json.loads(read('.owner')); assert owner.get('version')==1 and owner.get('site_url')=='https://sggroup.jp' and owner.get('expected_user_id')==5 and type(owner.get('pid')) is int and owner['pid']>0\n os.kill(owner['pid'],0)\n n=names[0]; m=re.fullmatch(r'request-([a-f0-9]{32})\\.json',n); assert m\n data=read(n)\n print(json.dumps({'pending':{'name':n,'id':m[1],'size':len(data),'sha256':hashlib.sha256(data).hexdigest()}}))\n`;
  const READ = COMMON + `name,sha,offset=sys.argv[2:5]\ndata=read(name); assert hashlib.sha256(data).hexdigest()==sha\nprint(base64.b64encode(data[int(offset):int(offset)+24576]).decode('ascii'))\n`;
  const WRITE = COMMON + `name,sha,target,offset,payload,final=sys.argv[2:8]\ndata=read(name); assert hashlib.sha256(data).hexdigest()==sha\nassert target=='response-'+name[8:]\ntmp=target+'.driver-part'\nflags=os.O_WRONLY|os.O_NOFOLLOW|(os.O_CREAT|os.O_EXCL if offset=='0' else 0)\nfd=os.open(os.path.join(d,tmp),flags,0o600)\nwith os.fdopen(fd,'wb') as f:\n s=os.fstat(f.fileno()); assert stat.S_ISREG(s.st_mode) and stat.S_IMODE(s.st_mode)==0o600 and s.st_uid==os.getuid() and s.st_size==int(offset)\n chunk=base64.b64decode(payload,validate=True); assert len(chunk)<=24576 and s.st_size+len(chunk)<=LIMIT\n f.seek(0,2); f.write(chunk); f.flush(); os.fsync(f.fileno())\nif final=='1':\n assert hashlib.sha256(read(name)).hexdigest()==sha\n assert not os.path.lexists(os.path.join(d,target))\n os.replace(os.path.join(d,tmp),os.path.join(d,target))\nprint('{"passed":true}')\n`;
  const CREATE = "import tempfile,os,json; d=tempfile.mkdtemp(prefix='sgnews-wpvibe-'); os.chmod(d,0o700); print(json.dumps({'bridge_dir':d}))";
  const CLEANUP = COMMON + "assert not os.listdir(d); os.rmdir(d); print('{\"passed\":true}')\n";

  async function execPython(code, argv, max = 4000) {
    const result = await tools.exec_command({cmd: "python -c " + shell(code) + " " + argv.map(shell).join(" "), workdir: WORKDIR,
      yield_time_ms: 1000, max_output_tokens: max});
    if (result.exit_code !== 0 || result.session_id !== undefined || typeof result.output !== "string") throw fault("driver_private_file_operation_failed");
    return result.output.trim();
  }
  async function readRequest(dir, header) {
    if (!plain(header) || !/^[a-f0-9]{32}$/.test(header.id) || header.name !== "request-" + header.id + ".json" ||
        !Number.isInteger(header.size) || header.size < 2 || header.size > ENVELOPE_LIMIT || !/^[a-f0-9]{64}$/.test(header.sha256)) throw fault("driver_request_file_metadata_invalid");
    const bytes = new Uint8Array(header.size);
    for (let offset = 0; offset < header.size; offset += CHUNK) {
      const chunk = unbase64(await execPython(READ, [dir, header.name, header.sha256, String(offset)], 30000));
      if (chunk.length !== Math.min(CHUNK, header.size - offset)) throw fault("driver_request_chunk_truncated");
      bytes.set(chunk, offset);
    }
    let request;
    try { request = JSON.parse(decode(bytes)); }
    catch (_) { throw fault("driver_request_json_invalid"); }
    return requestValid(request, header.id);
  }
  async function writeResponse(dir, header, request, result) {
    const envelope = {version: 1, type: "response", id: request.id, request_sha256: header.sha256,
      site_url: SITE, expected_user_id: 5, method: request.method, endpoint: request.endpoint, ...result};
    let bytes = utf8(JSON.stringify(envelope) + "\n");
    if (bytes.length > ENVELOPE_LIMIT) {
      bytes = utf8(JSON.stringify({...envelope, ok: false, data: undefined, status: undefined, truncated: undefined,
        error: {code: "wpvibe_response_envelope_oversize", status: null, retryable: false, ambiguous_write: request.method !== "GET"}}) + "\n");
    }
    for (let offset = 0; offset < bytes.length; offset += CHUNK) {
      await execPython(WRITE, [dir, header.name, header.sha256, "response-" + request.id + ".json", String(offset),
        base64(bytes.slice(offset, offset + CHUNK)), offset + CHUNK >= bytes.length ? "1" : "0"]);
    }
  }
  function outputSafe(value) {
    if (Array.isArray(value)) { for (const item of value) outputSafe(item); return; }
    if (!plain(value)) return;
    for (const [key, child] of Object.entries(value)) {
      if (FORBIDDEN.has(key.toLowerCase()) || ["html", "content", "content_raw", "raw", "body"].includes(key)) throw fault("driver_cli_output_sensitive_field");
      outputSafe(child);
    }
  }

  async function runWpvibe(options) {
    if (!plain(options) || Object.keys(options).some(key => !["args", "maxRuntimeSeconds"].includes(key))) throw fault("driver_options_invalid");
    const args = argumentsValid(options.args);
    const seconds = options.maxRuntimeSeconds === undefined ? 1800 : options.maxRuntimeSeconds;
    if (!Number.isInteger(seconds) || seconds < 10 || seconds > 7200) throw fault("driver_runtime_limit_invalid");
    if (typeof tools[TOOL] !== "function") throw fault("wpvibe_connector_tool_unavailable");
    const dir = JSON.parse(await execPython(CREATE, [])).bridge_dir;
    if (typeof dir !== "string" || !/^\/tmp\/sgnews-wpvibe-[A-Za-z0-9_-]+$/.test(dir)) throw fault("driver_private_bridge_path_invalid");
    const fixed = ["python", "-m", "runtime.publish", "--transport", "wpvibe", "--bridge-dir", dir, "--expected-user-id", "5", ...args];
    let process = await tools.exec_command({cmd: fixed.map(shell).join(" "), workdir: WORKDIR, yield_time_ms: 1000, max_output_tokens: 4000});
    let stdout = process.output || "", count = 0, lastYield = Date.now(), exitCode = process.exit_code;
    const start = Date.now(), seen = new Set();
    try {
      while (process.session_id !== undefined) {
        if (Date.now() - start > seconds * 1000) throw fault("driver_runtime_deadline_exceeded");
        const probe = JSON.parse(await execPython(PROBE, [dir]));
        if (!plain(probe) || !("pending" in probe)) throw fault("driver_private_probe_invalid");
        if (probe.pending) {
          const header = probe.pending;
          if (seen.has(header.id)) throw fault("driver_repeated_request_rejected");
          const request = await readRequest(dir, header);
          const current = JSON.parse(await execPython(PROBE, [dir])).pending;
          if (!current || current.id !== header.id || current.sha256 !== header.sha256) throw fault("driver_request_no_longer_active");
          seen.add(header.id); count++;
          const params = {site_url: SITE, method: request.method, route: request.endpoint, query: request.params,
            fields: "all", max_length: LIMIT};
          if (request.payload !== null) params.body = JSON.stringify(request.payload);
          let response;
          try { response = parseTool(await tools[TOOL](params), request.method); }
          catch (_) { response = {ok: false, error: {code: "wpvibe_tool_execution_failed", status: null,
            retryable: false, ambiguous_write: request.method !== "GET"}}; }
          await writeResponse(dir, header, request, response);
        }
        process = await tools.write_stdin({session_id: process.session_id, chars: "", yield_time_ms: 1000, max_output_tokens: 4000});
        stdout += process.output || ""; exitCode = process.exit_code;
        if (utf8(stdout).length > 65536) throw fault("driver_cli_output_oversize");
        if (Date.now() - lastYield > 15000 && typeof yield_control === "function") { lastYield = Date.now(); await yield_control(); }
      }
      let result;
      try { result = JSON.parse(stdout.trim()); }
      catch (_) { throw fault("driver_cli_output_invalid"); }
      outputSafe(result);
      try { await execPython(CLEANUP, [dir]); } catch (_) { /* Failed bridge files remain private for recovery. */ }
      return {transport: "wpvibe", expected_user_id: 5, exit_code: exitCode, request_count: count, result};
    } catch (error) {
      // No automatic tool retry or mutation replay. Interrupt only the local
      // bridge waiter; a timed-out remote write remains an ambiguous operation.
      if (process.session_id !== undefined) {
        try { await tools.write_stdin({session_id: process.session_id, chars: "\u0003", yield_time_ms: 1000, max_output_tokens: 1000}); } catch (_) {}
      }
      return {transport: "wpvibe", expected_user_id: 5, exit_code: exitCode === undefined ? null : exitCode,
        request_count: count, passed: false, error: {code: error.code || "wpvibe_driver_failed"},
        private_bridge_retained: true};
    }
  }
  globalThis.runWpvibe = runWpvibe;
  // Pure helpers and bounded file scripts are exposed for offline verification;
  // they cannot install plugins, change roles, or bypass the publication gates.
  globalThis.sgnewsWpvibeDriverInternals = {shell, utf8, base64, unbase64, decode, requestValid, parseTool,
    argumentsValid, outputSafe, READ, WRITE, PROBE, CREATE, CHUNK, LIMIT, ENVELOPE_LIMIT};
})();
