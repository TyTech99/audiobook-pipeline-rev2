"""The pipeline's one way to call a model. Configuration only: nothing here depends on which model runs.

Settings (settings.yaml, block `model:`) hold one `default` entry, optional `vision` and `text` entries, and optional
per-job entries under `jobs:`. An entry is laid over the one before it: default, then vision or text, then the job's
own entry, so an empty entry uses the default. Each entry has its own mode, address or model file, context size and key.

  mode: local      llama.cpp's llama-server runs inside the pipeline container with the given model file (plus its
                   mmproj image file for vision). It is started when a batch first needs it and stopped when the batch
                   ends. Before it starts, the voice model is unloaded (the GPU swap rule).
  mode: external   an OpenAI-compatible chat address, such as llama-server on another LAN machine. No GPU swap.
                   A key, if any, is read from the environment variable named in api_key_env.

Every request sends the fixed seed and temperature 0. Every answer is stored in the book folder under
model_outputs/<job>/<key>.json with the mode, model and address that made it, the instruction versions and the
settings; a rerun with the same job, instructions, input and settings reads the stored answer and never calls a model."""
import base64
import copy
import hashlib
import json
import os
import re
import socket
import subprocess
import time

import requests

PIPELINE_DIR = os.path.dirname(os.path.abspath(__file__))
PROMPTS_DIR = os.path.join(PIPELINE_DIR, "prompts")
CONTEXT_FLOOR = 16000      # below this window the run stops

# Which entry kind each job uses.
JOBS = {
    "page_reading": "vision",
    "scan_fix": "vision",
    "cover_check": "vision",
    "figure_sentence": "vision",
    "term_list": "text",
    "translation": "text",
    "chapter_preview": "text",
    "concept_summary": "text",
    "chapter_summary": "text",
    "table_takeaway": "text",
    "book_description": "text",
}
# Verbatim jobs copy the author's words; the shared base rules (spoken English, no Markdown) do not apply to them.
VERBATIM = {"page_reading", "scan_fix", "cover_check"}

ENTRY_FIELDS = ("mode", "address", "model", "model_file", "mmproj_file", "context", "api_key_env", "max_tokens", "extra")


class ModelError(Exception):
    """The model could not be reached or gave no usable answer. A temporary failure: the queue keeps the item."""
    kind = "temporary"


class ModelSettingsError(Exception):
    """The model settings are wrong or incomplete. The run stops with this message."""
    kind = "temporary"


# ---------- settings ----------

def entry(cfg, job):
    """The resolved settings entry for a job (see the module text)."""
    if job not in JOBS:
        raise ModelSettingsError("unknown model job %r" % job)
    m = cfg.get("model") or {}
    e = {}
    for layer in (m.get("default"), m.get(JOBS[job]), (m.get("jobs") or {}).get(job)):
        for k, v in (layer or {}).items():
            if k not in ENTRY_FIELDS:
                raise ModelSettingsError("model setting %r is not known (known: %s)" % (k, ", ".join(ENTRY_FIELDS)))
            if v not in (None, "", {}):
                e[k] = copy.deepcopy(v)
    e.setdefault("mode", "local")
    e.setdefault("extra", {})
    if e["mode"] not in ("local", "external"):
        raise ModelSettingsError("model mode must be local or external, not %r" % e["mode"])
    ctx = int(e.get("context") or 0)
    if ctx < CONTEXT_FLOOR:
        raise ModelSettingsError("model context for %s is %d tokens; the floor is %d. Set model.default.context"
                                 % (job, ctx, CONTEXT_FLOOR))
    e["context"] = ctx
    if e["mode"] == "external" and not e.get("address"):
        raise ModelSettingsError("model mode is external for %s but no address is set" % job)
    if e["mode"] == "local" and not e.get("model_file"):
        raise ModelSettingsError("model mode is local for %s but no model_file is set" % job)
    if e["mode"] == "local" and JOBS[job] == "vision" and not e.get("mmproj_file"):
        raise ModelSettingsError("job %s needs images; local mode needs mmproj_file for it" % job)
    return e


def common(cfg):
    m = cfg.get("model") or {}
    return {"seed": int(m.get("seed", 42)), "timeout_s": float(m.get("timeout_s", 600)),
            "retries": int(m.get("retries", 5)), "retry_wait_s": float(m.get("retry_wait_s", 5)),
            "proxy": os.environ.get("AUDIOBOOK_MODEL_PROXY") or m.get("proxy") or "",
            "local": m.get("local") or {}}


# ---------- instructions ----------

VERSION_RE = re.compile(r"<!--\s*version:\s*(\S+?)\s*-->")


def prompt(name):
    """(text, version) of _pipeline/prompts/<name>.md. The version line is required and is not sent to the model."""
    path = os.path.join(PROMPTS_DIR, name + ".md")
    with open(path, encoding="utf-8") as f:
        raw = f.read()
    m = VERSION_RE.search(raw)
    if not m:
        raise ModelSettingsError("instruction file %s has no '<!-- version: N -->' line" % path)
    return re.sub(r"<!--.*?-->\s*", "", raw, flags=re.S).strip(), m.group(1)


def instructions(job):
    """(system text, {file: version}) for a job: the shared base file plus the job's own file (verbatim jobs: job only)."""
    parts, versions = [], {}
    names = [job] if job in VERBATIM else ["base", job]
    for n in names:
        text, v = prompt(n)
        parts.append(text)
        versions[n] = v
    return "\n\n".join(parts), versions


def user_text(job, template=None, **fields):
    """(text, version) of the job's user-message template prompts/<job>.user.md (or prompts/<template>.user.md) with
    fields filled in."""
    text, v = prompt((template or job) + ".user")
    return text.format(**fields), v


# ---------- stored outputs ----------

def _digest(obj):
    return hashlib.sha256(json.dumps(obj, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def output_path(book_dir, job, key):
    return os.path.join(book_dir, "model_outputs", job, key + ".json")


def stored(book_dir, job, key):
    p = output_path(book_dir, job, key)
    if os.path.exists(p):
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    return None


# ---------- servers ----------

def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class LocalServer:
    """llama-server started inside the pipeline container. Flags as documented in llama.cpp tools/server/README.md:
    -m model, --mmproj image file, -c context, --host, --port, -ngl GPU layers, -s seed, --jinja (the model's own chat
    template, which is what makes enable_thinking mean anything; see textmodel.Session.ask)."""

    def __init__(self, e, local, seed, popen=subprocess.Popen, get=None):
        self.e, self.local, self.seed, self.popen = e, local, seed, popen
        self.get = get or requests.get
        self.proc, self.port = None, None

    def command(self):
        models = self.local.get("models_dir") or ""
        path = lambda f: f if os.path.isabs(f) or not models else os.path.join(models, f)
        cmd = [self.local.get("binary") or "llama-server", "-m", path(self.e["model_file"])]
        if self.e.get("mmproj_file"):
            cmd += ["--mmproj", path(self.e["mmproj_file"])]
        cmd += ["-c", str(self.e["context"]), "--host", "127.0.0.1", "--port", str(self.port),
                "-ngl", str(self.local.get("gpu_layers", "all")), "-s", str(self.seed), "--jinja"]
        return cmd + [str(a) for a in (self.local.get("extra_args") or [])]

    def start(self):
        self.port = int(self.local.get("port") or 0) or _free_port()
        cmd = self.command()
        print("model: starting local llama-server: " + " ".join(cmd))
        try:
            self.proc = self.popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except OSError as e:
            raise ModelError("could not start llama-server (%s): %s" % (cmd[0], e))
        deadline = time.time() + float(self.local.get("startup_timeout_s", 600))
        url = "http://127.0.0.1:%d/health" % self.port
        while time.time() < deadline:
            if self.proc.poll() is not None:
                raise ModelError("llama-server exited while loading (exit code %s)" % self.proc.returncode)
            try:
                if self.get(url, timeout=5).status_code == 200:
                    return "http://127.0.0.1:%d" % self.port
            except requests.RequestException:
                pass
            time.sleep(1)
        self.stop()
        raise ModelError("llama-server did not load within %s s" % self.local.get("startup_timeout_s", 600))

    def stop(self):
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=30)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        self.proc = None


class Session:
    """One batch of model work for a book. Local servers start on first use and stop in close()."""

    def __init__(self, cfg, book_dir, post=None, server_factory=LocalServer, unload_voice=None):
        self.cfg, self.book_dir = cfg, book_dir
        self.c = common(cfg)
        self.post = post or requests.post
        self.server_factory = server_factory
        self.unload_voice = unload_voice
        self.servers = {}          # entry digest -> (LocalServer, base url)
        self.calls = 0             # model requests actually sent (stored answers do not count)
        self.voice_unloaded = False

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def close(self):
        for srv, _ in self.servers.values():
            srv.stop()
        self.servers = {}

    def _swap_out_voice(self):
        if self.voice_unloaded:
            return
        self.voice_unloaded = True
        fn = self.unload_voice
        if fn is None:
            from pipeline.voice import voice
            fn = lambda: voice.unload(self.cfg)
        try:
            fn()
            print("model: voice model unloaded before loading the local model (GPU swap rule)")
        except Exception as e:      # voice server down or already unloaded: the GPU is free either way
            print("model: voice unload skipped (%s)" % e)

    def base_url(self, e):
        if e["mode"] == "external":
            return e["address"].rstrip("/").removesuffix("/v1/chat/completions").removesuffix("/v1")
        k = _digest({f: e.get(f) for f in ("model_file", "mmproj_file", "context")})
        if k not in self.servers:
            # one local model on the GPU at a time: stop any other before loading
            self.close()
            self._swap_out_voice()
            srv = self.server_factory(e, self.c["local"], self.c["seed"])
            self.servers[k] = (srv, srv.start())
        return self.servers[k][1]

    def _send(self, e, url, body):
        headers = {"Content-Type": "application/json"}
        if e.get("api_key_env"):
            key = os.environ.get(e["api_key_env"], "")
            if not key:
                raise ModelSettingsError("environment variable %s (model api_key_env) is empty" % e["api_key_env"])
            headers["Authorization"] = "Bearer " + key
        proxies = {"http": self.c["proxy"], "https": self.c["proxy"]} if self.c["proxy"] and e["mode"] == "external" else None
        last = None
        for attempt in range(1, self.c["retries"] + 1):
            try:
                r = self.post(url + "/v1/chat/completions", data=json.dumps(body), headers=headers,
                              timeout=self.c["timeout_s"], proxies=proxies)
                if r.status_code == 200:
                    return r.json()
                last = "HTTP %d: %s" % (r.status_code, r.text[:200])
                if r.status_code < 500 and r.status_code != 429:
                    break               # a request the server refuses will not succeed on retry
            except (requests.RequestException, ValueError) as ex:
                last = "%s: %s" % (type(ex).__name__, ex)
            if attempt < self.c["retries"]:
                time.sleep(self.c["retry_wait_s"])
        raise ModelError("model at %s did not answer: %s" % (url, last))

    def ask(self, job, text, images=(), extra_inputs=None, max_tokens=None, allow_empty=False):
        """The model's answer for one job input. `text` is the user message; `images` are (bytes, mime) pairs;
        max_tokens: an answer limit for this request (translation: sized to the chunk), else the entry's;
        allow_empty: a page with no readable text on it is a correct empty answer (page_reading on a blank or
        picture-only page); it is stored and the page checks flag it, instead of failing the run.
        Returns the stored record: {'output', 'mode', 'model', ...}."""
        e = dict(entry(self.cfg, job))
        if max_tokens:
            e["max_tokens"] = int(max_tokens)
        system, versions = instructions(job)
        params = {"seed": self.c["seed"], "temperature": 0, "max_tokens": e.get("max_tokens")}
        key = _digest({"job": job, "instructions": versions, "system": system, "text": text,
                       "images": [hashlib.sha256(b).hexdigest() for b, _ in images],
                       "extra_inputs": extra_inputs, "params": params, "extra": e["extra"]})
        rec = stored(self.book_dir, job, key)
        if rec is not None:
            return rec
        content = text
        if images:
            content = [{"type": "image_url", "image_url": {"url": "data:%s;base64,%s" % (mime, base64.b64encode(b).decode())}}
                       for b, mime in images] + [{"type": "text", "text": text}]
        # Thinking off. A chat template that defaults to a reasoning effort (Qwen 3.x: reasoning_effort xhigh) makes the
        # model reason before it answers; a llama.cpp server started with --reasoning off drops that reasoning from the
        # reply, so the answer arrives empty (issue #7). These jobs want the answer, never the reasoning. An entry's
        # `extra` still overrides this, and a template that does not know the switch ignores it.
        body = {"messages": [{"role": "system", "content": system}, {"role": "user", "content": content}],
                "temperature": 0, "seed": self.c["seed"], "cache_prompt": False, "stream": False,
                "chat_template_kwargs": {"enable_thinking": False}}
        if e.get("model"):
            body["model"] = e["model"]
        if e.get("max_tokens"):
            body["max_tokens"] = int(e["max_tokens"])
        body.update(e["extra"])
        url = self.base_url(e)
        self.calls += 1
        resp = self._send(e, url, body)
        usage = resp.get("usage") or {}
        try:
            choice = resp["choices"][0]
            message = choice["message"]
            out = message.get("content")
        except (KeyError, IndexError, TypeError, AttributeError):
            raise ModelError("model at %s gave no answer text" % url)
        if not isinstance(out, str) or not out.strip():
            if allow_empty:
                out = ""                  # a blank or picture-only page: nothing to transcribe is the right answer
            else:
                why = "finish_reason %r" % (choice.get("finish_reason"),)
                thinking = (message.get("reasoning_content") or "").strip()
                if thinking:
                    why += ("; it answered with %d characters of reasoning, which this server keeps out of the answer "
                            "(send chat_template_kwargs enable_thinking false, or start it with --reasoning auto)"
                            % len(thinking))
                raise ModelError("model at %s gave an empty answer for %s (%s)" % (url, job, why))
        if images:
            self._check_images_read(url, job, system, text, len(images), usage)
        rec = {"job": job, "key": key, "output": out.strip(), "mode": e["mode"],
               "model": {"requested": e.get("model") or e.get("model_file"), "reported": resp.get("model")},
               "address": e.get("address") if e["mode"] == "external" else "local:" + (e.get("model_file") or ""),
               "instructions": versions, "settings": params, "extra": e["extra"], "context": e["context"],
               "prompt_tokens": usage.get("prompt_tokens"),
               "input_sha256": hashlib.sha256(text.encode()).hexdigest(),
               "made": time.strftime("%Y-%m-%dT%H:%M:%S%z")}
        p = output_path(self.book_dir, job, key)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            json.dump(rec, f, indent=1, ensure_ascii=False)
            f.write("\n")
        return rec

    @staticmethod
    def _check_images_read(url, job, system, text, count, usage):
        """Stops the run when a server took the images and put none in the prompt. llama.cpp has done this with
        speculative decoding (--spec-type draft-*) and a quantised value cache (--cache-type-v) on a vision model: the
        bitmaps are decoded, the model never sees them, and the answer is fluent invention. A page read that way would
        pass the page checks (it is ordinary English) and be committed as the author's words, so it fails here instead.
        A prompt that carries images is always longer than the same prompt without them."""
        sent = usage.get("prompt_tokens")
        if not sent:
            return                      # a server that reports no usage cannot be checked this way
        floor = 16 + (len(system) + len(text)) // 3    # about the size of the text alone; a real image adds far more
        if sent < floor:
            raise ModelError("model at %s did not read the %d image(s) of %s: the request carried them but the prompt was "
                             "only %d tokens, about the size of the text alone, so the server dropped the images. Check "
                             "the model server's launch flags (llama.cpp: --spec-type draft-* and --cache-type-v have "
                             "done this on a vision model)" % (url, count, job, sent))
