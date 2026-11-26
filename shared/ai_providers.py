"""
AI provider access, selected by configuration only: ollama | openai | comfyui.

Copy this file into a project unchanged. It uses the Python standard library only.
The project loads config/config.yaml (plus config.prod.yaml in production) and passes the
`ai:` section to configure(). Secrets never live in YAML: an API key is read from the
environment variable named by the profile's `api_key_env`.

config/config.yaml:

    ai:
      default:                           # profile used when none is named
        provider: ollama
        base_url: http://host:11434      # Ollama root, no /v1
        model: qwen3:30b-a3b-instruct-2507-q8_0
        suppress_thinking: true          # ask reasoning models not to think
        timeout_seconds: 120
      summarize:                         # OpenAI API: hosted OpenAI, vLLM, or any compatible server
        provider: openai
        base_url: http://vllm-host:8000/v1
        model: Qwen/Qwen3-235B-A22B
        api_key_env: LLM_API_KEY         # env var holding the key; omit if the server needs none
      embed:                             # same provider as default; only the model differs
        model: bge-m3:latest
      thumbnail:                         # ComfyUI workflow: image, audio or video (media only)
        provider: comfyui
        enabled: false                   # optional feature switch; turn on where ComfyUI exists
        base_url: http://comfy-host:8188
        timeout_seconds: 600
        # workflow: config/comfyui/thumbnail.json   (default: config/comfyui/<profile>.json)

    Text and embeddings use only ollama or openai profiles. comfyui is an optional add-on for
    media generation: a project without media needs defines no comfyui profile. Any profile can
    carry `enabled: false`; calling it raises AIDisabled (catch it to skip the feature), and
    ai.enabled("thumbnail") tells the app whether to offer the feature at all. A disabled
    profile may leave its required keys empty.

    ComfyUI workflows live in config/comfyui/<profile>.json: in ComfyUI use Workflow ->
    Export (API), save the file there, and replace values that should vary per call with
    placeholders such as "{{prompt}}" or "{{seed}}". Inputs passed to media() fill them; a
    value that is exactly one placeholder keeps the input's type (numbers stay numbers).

    A profile without `provider` inherits every key from `default`, so switching the model
    for one task is one line. A profile that sets `provider` stands alone.

Required keys:  ollama: base_url, model    openai: base_url, model    comfyui: base_url

Usage:

    import ai_providers as ai
    ai.configure(config["ai"], base_dir=PROJECT_ROOT)

    text = ai.chat([{"role": "user", "content": "Summarise ..."}], profile="summarize")
    data = json.loads(ai.chat(messages, json_mode=True))
    vectors = ai.embed(["first text", "second text"], profile="embed")
    files = ai.media("thumbnail", {"prompt": "a desert at dawn", "seed": 42})   # [(filename, bytes)]
    image = ai.media("cover", {"prompt": "...", "width": 1024, "height": 768})  # ollama/openai image model
    status = ai.check("summarize")                                            # {"ok": bool, ...}

Thinking control (suppress_thinking: true):
- ollama: sends "think": false.
- openai: sends chat_template_kwargs.enable_thinking=false, which vLLM understands.
Either is retried once without the field if the server rejects it (hosted OpenAI, older
Ollama). <think>/<thinking>/<reasoning> blocks are always removed from replies (a reply
that is only reasoning raises AIError), and JSON-mode replies lose any Markdown code fence,
so callers can json.loads the result directly.
"""
from __future__ import annotations

import base64
import json
import os
import re
import time
import uuid
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

PROVIDERS = ("ollama", "openai", "comfyui")
_REQUIRED = {"ollama": ("base_url", "model"), "openai": ("base_url", "model"), "comfyui": ("base_url",)}
_ALLOWED = {
    "ollama": {"provider", "enabled", "base_url", "model", "suppress_thinking", "timeout_seconds"},
    "openai": {"provider", "enabled", "base_url", "model", "suppress_thinking", "timeout_seconds", "api_key_env"},
    "comfyui": {"provider", "enabled", "base_url", "workflow", "timeout_seconds"},
}
# <think> is Qwen3/DeepSeek-R1; <thinking>/<reasoning> appear on various fine-tunes.
_THINK_BLOCK = re.compile(r"<(think|thinking|reasoning)\b[^>]*>.*?</\1\s*>\s*", re.S | re.I)
_THINK_OPEN = re.compile(r"<(think|thinking|reasoning)\b[^>]*>", re.I)
_FENCE = re.compile(r"^\s*```[a-zA-Z]*\s*\n?(.*?)\n?\s*```\s*$", re.S)
_PLACEHOLDER = re.compile(r"\{\{\s*(\w+)\s*\}\}")

_profiles: dict = {}
_base_dir = Path(".")


class AIError(RuntimeError):
    """Raised for configuration errors and failed provider calls."""


# --------------------------------------------------------------------- config

def configure(ai_section: dict | None, base_dir: str | Path = ".") -> None:
    """Install the `ai:` section of the project config and validate every profile now,
    so a typo fails at startup rather than on the first request."""
    global _profiles, _base_dir
    _profiles = dict(ai_section or {})
    _base_dir = Path(base_dir)
    for name in _profiles:
        profile(name)


def profile(name: str = "default") -> dict:
    """Resolved profile. A profile without `provider` inherits from `default`."""
    if name not in _profiles:
        raise AIError(f"ai.{name} is not defined in config.yaml")
    own = _profiles[name] or {}
    merged = own if "provider" in own or name == "default" else {**(_profiles.get("default") or {}), **own}
    provider = str(merged.get("provider", "")).lower()
    if provider not in PROVIDERS:
        raise AIError(f"ai.{name}.provider must be one of {PROVIDERS}, got {provider!r}")
    unknown = set(merged) - _ALLOWED[provider]
    if unknown:
        raise AIError(f"ai.{name}: keys {sorted(unknown)} do not apply to provider {provider}")
    enabled = bool(merged.get("enabled", True))
    missing = [k for k in _REQUIRED[provider] if not merged.get(k)]
    if missing and enabled:
        raise AIError(f"ai.{name}: {', '.join(missing)} must be set for provider {provider}")
    return {
        "name": name,
        "provider": provider,
        "enabled": enabled,
        "base_url": str(merged.get("base_url", "")).rstrip("/"),
        "model": str(merged.get("model", "")),
        "workflow": merged.get("workflow"),
        "suppress_thinking": bool(merged.get("suppress_thinking", True)),
        "timeout": float(merged.get("timeout_seconds", 600 if provider == "comfyui" else 120)),
        "api_key": os.environ.get(merged["api_key_env"], "") if merged.get("api_key_env") else "",
    }


class AIDisabled(AIError):
    """The profile exists but is switched off (enabled: false). Callers can catch this to
    skip optional features such as media generation."""


def _enabled_profile(name: str) -> dict:
    cfg = profile(name)
    if not cfg["enabled"]:
        raise AIDisabled(f"ai.{name} is disabled (enabled: false in config)")
    return cfg


def enabled(name: str) -> bool:
    """True when the profile is defined and switched on. Use it to show or hide optional features."""
    return name in _profiles and profile(name)["enabled"]


def _text_profile(name: str) -> dict:
    cfg = _enabled_profile(name)
    if cfg["provider"] == "comfyui":
        raise AIError(f"ai.{name} is a comfyui profile; use media(), not chat()/embed()")
    return cfg


# ----------------------------------------------------------------------- http

def _request(url: str, payload: dict | None = None, *, headers: dict | None = None,
             timeout: float = 60, raw: bool = False):
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(url, data=data, method="POST" if data else "GET")
    req.add_header("Accept", "application/json")
    if data:
        req.add_header("Content-Type", "application/json")
    for key, value in (headers or {}).items():
        req.add_header(key, value)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read()
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:300]
        raise AIError(f"{url} -> HTTP {exc.code}: {detail}") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise AIError(f"{url} -> {exc}") from exc
    return body if raw else json.loads(body or b"null")


def _post_with_optional(url: str, payload: dict, optional: str, **kwargs):
    """POST; if the server rejects the optional field, retry once without it."""
    try:
        return _request(url, payload, **kwargs)
    except AIError as exc:
        if optional not in payload or not re.search(r"HTTP (400|422)", str(exc)):
            raise
        payload = {k: v for k, v in payload.items() if k != optional}
        return _request(url, payload, **kwargs)


def _auth(cfg: dict) -> dict:
    return {"Authorization": f"Bearer {cfg['api_key']}"} if cfg.get("api_key") else {}


def clean_reply(text: str, json_mode: bool = False) -> str:
    """Remove reasoning blocks and, in JSON mode, a surrounding Markdown code fence.
    An opening tag with no close means the reply was cut off mid-thought: everything
    from there on is scratchpad. A reply that was ONLY reasoning raises AIError."""
    original = text or ""
    text = _THINK_BLOCK.sub("", original)
    cut = _THINK_OPEN.search(text)
    if cut:
        text = text[: cut.start()]
    text = text.strip()
    if not text and _THINK_OPEN.search(original):
        raise AIError("The model returned only reasoning and no answer (it ran out of output budget "
                      "while thinking). Raise max_tokens/timeout_seconds or set suppress_thinking: true.")
    if json_mode:
        fenced = _FENCE.match(text)
        if fenced:
            text = fenced.group(1).strip()
    return text


# ----------------------------------------------------------------------- text

def chat(messages: list[dict], profile: str = "default", *, json_mode: bool = False,
         temperature: float | None = None, max_tokens: int | None = None) -> str:
    """Send chat messages with the named profile and return the reply text."""
    cfg = _text_profile(profile)
    if cfg["provider"] == "ollama":
        payload: dict = {"model": cfg["model"], "messages": messages, "stream": False}
        if cfg["suppress_thinking"]:
            payload["think"] = False
        if json_mode:
            payload["format"] = "json"
        options = {k: v for k, v in (("temperature", temperature), ("num_predict", max_tokens)) if v is not None}
        if options:
            payload["options"] = options
        reply = _post_with_optional(f"{cfg['base_url']}/api/chat", payload, "think", timeout=cfg["timeout"])
        if reply.get("done") is False:
            raise AIError("Ollama returned an incomplete response (done=false).")
        return clean_reply((reply.get("message") or {}).get("content", ""), json_mode)

    payload = {"model": cfg["model"], "messages": messages}
    if temperature is not None:
        payload["temperature"] = temperature
    if max_tokens is not None:
        payload["max_tokens"] = max_tokens
    if json_mode:
        payload["response_format"] = {"type": "json_object"}
    if cfg["suppress_thinking"]:
        payload["chat_template_kwargs"] = {"enable_thinking": False}
    reply = _post_with_optional(f"{cfg['base_url']}/chat/completions", payload, "chat_template_kwargs",
                                headers=_auth(cfg), timeout=cfg["timeout"])
    choices = reply.get("choices") or [{}]
    return clean_reply((choices[0].get("message") or {}).get("content", ""), json_mode)


def embed(texts: list[str], profile: str = "default") -> list[list[float]]:
    """Embedding vectors for each text, in order."""
    cfg = _text_profile(profile)
    if cfg["provider"] == "ollama":
        reply = _request(f"{cfg['base_url']}/api/embed", {"model": cfg["model"], "input": texts}, timeout=cfg["timeout"])
        return reply["embeddings"]
    reply = _request(f"{cfg['base_url']}/embeddings", {"model": cfg["model"], "input": texts},
                     headers=_auth(cfg), timeout=cfg["timeout"])
    return [item["embedding"] for item in sorted(reply["data"], key=lambda item: item["index"])]


# ---------------------------------------------------------------------- media

def _fill(node, inputs: dict):
    """Replace {{name}} placeholders in a workflow. A value that is exactly one
    placeholder takes the input's own type (so seeds and sizes stay numbers)."""
    if isinstance(node, dict):
        return {k: _fill(v, inputs) for k, v in node.items()}
    if isinstance(node, list):
        return [_fill(v, inputs) for v in node]
    if isinstance(node, str):
        whole = _PLACEHOLDER.fullmatch(node.strip())
        if whole and whole.group(1) in inputs:
            return inputs[whole.group(1)]
        return _PLACEHOLDER.sub(lambda m: str(inputs.get(m.group(1), m.group(0))), node)
    return node


def _image_from_model(cfg: dict, inputs: dict) -> list[tuple[str, bytes]]:
    """Single image from an image model served by Ollama or an OpenAI-compatible server.
    Inputs: prompt (required), width/height (default 1024)."""
    prompt = inputs.get("prompt")
    if not prompt:
        raise AIError(f"ai.{cfg['name']}: media() needs a 'prompt' input for provider {cfg['provider']}")
    width, height = int(inputs.get("width", 1024)), int(inputs.get("height", 1024))
    if cfg["provider"] == "ollama":
        reply = _request(f"{cfg['base_url']}/api/generate",
                         {"model": cfg["model"], "prompt": prompt, "width": width, "height": height, "stream": False},
                         timeout=cfg["timeout"])
        encoded = reply.get("image") or (reply.get("images") or [None])[0]
        if not encoded:
            raise AIError(f"Ollama returned no image for model {cfg['model']} (is it an image model?)")
        return [("image.png", base64.b64decode(encoded))]
    reply = _request(f"{cfg['base_url']}/images/generations",
                     {"model": cfg["model"], "prompt": prompt, "size": f"{width}x{height}", "n": 1,
                      "response_format": "b64_json"},
                     headers=_auth(cfg), timeout=cfg["timeout"])
    item = (reply.get("data") or [{}])[0]
    if item.get("b64_json"):
        return [("image.png", base64.b64decode(item["b64_json"]))]
    if item.get("url"):
        return [("image.png", _request(item["url"], timeout=cfg["timeout"], raw=True))]
    raise AIError("Image endpoint returned no image (no b64_json or url).")


def _queue_state(base_url: str, prompt_id: str) -> str:
    """Where our job is in the ComfyUI queue, for error messages."""
    try:
        queue = _request(f"{base_url}/queue", timeout=10) or {}
    except AIError:
        return "queue state unknown"
    running = [item[1] for item in queue.get("queue_running", [])]
    pending = [item[1] for item in queue.get("queue_pending", [])]
    if prompt_id in running:
        return "it was still running"
    if prompt_id in pending:
        return f"it was still waiting in the queue behind {len(running) + pending.index(prompt_id)} other job(s)"
    return "it was not in the queue"


def _cancel(base_url: str, prompt_id: str) -> None:
    """Remove our job from the ComfyUI queue, or interrupt it if it is the one running.
    Other users' jobs are never touched."""
    try:
        queue = _request(f"{base_url}/queue", timeout=10) or {}
        if prompt_id in [item[1] for item in queue.get("queue_running", [])]:
            _request(f"{base_url}/interrupt", {"prompt_id": prompt_id}, timeout=10)
        else:
            _request(f"{base_url}/queue", {"delete": [prompt_id]}, timeout=10)
    except AIError:
        pass


def media(profile: str = "media", inputs: dict | None = None, *, workflow: str | None = None) -> list[tuple[str, bytes]]:
    """Generate media with the named profile and return the files as (filename, bytes).

    comfyui: runs a workflow (image, audio or video; every output file). The workflow is
             config/comfyui/<workflow>.json when `workflow` is given, else the profile's
             `workflow` key, else config/comfyui/<profile>.json.
    ollama / openai: one image from an image model; inputs prompt, width, height.
    Raises AIDisabled when the profile has enabled: false."""
    cfg = _enabled_profile(profile)
    if cfg["provider"] != "comfyui":
        return _image_from_model(cfg, inputs or {})
    if workflow and not re.fullmatch(r"[\w.-]+", workflow):
        raise AIError(f"workflow name {workflow!r} must be a plain file name without a path")
    path = Path(f"config/comfyui/{workflow}.json" if workflow else cfg["workflow"] or f"config/comfyui/{profile}.json")
    if not path.is_absolute():
        path = _base_dir / path
    if not path.is_file():
        raise AIError(f"ComfyUI workflow for ai.{profile} not found: {path}")
    workflow = _fill(json.loads(path.read_text(encoding="utf-8")), inputs or {})
    base_url = cfg["base_url"]

    queued = _request(f"{base_url}/prompt", {"prompt": workflow, "client_id": uuid.uuid4().hex}, timeout=30)
    prompt_id = (queued or {}).get("prompt_id")
    if not prompt_id:
        raise AIError(f"ComfyUI rejected the workflow: {json.dumps(queued)[:300]}")

    deadline = time.monotonic() + cfg["timeout"]
    try:
        while True:
            entry = (_request(f"{base_url}/history/{prompt_id}", timeout=30) or {}).get(prompt_id)
            if entry and (entry.get("status") or {}).get("status_str") == "error":
                raise AIError(f"ComfyUI workflow failed: {json.dumps(entry.get('status'))[:300]}")
            if entry and entry.get("outputs"):
                break
            if time.monotonic() > deadline:
                state = _queue_state(base_url, prompt_id)
                raise AIError(f"ComfyUI workflow {prompt_id} did not finish within {cfg['timeout']:.0f}s "
                              f"({state}); it was removed from the ComfyUI queue")
            time.sleep(1)
    except BaseException:
        # Timeout, error or Ctrl+C: never leave our job queued or running on a shared server.
        _cancel(base_url, prompt_id)
        raise

    files = []
    for node_output in entry["outputs"].values():
        for items in node_output.values():
            if not isinstance(items, list):
                continue
            for item in items:
                if isinstance(item, dict) and "filename" in item:
                    query = urllib.parse.urlencode({k: item.get(k, "") for k in ("filename", "subfolder", "type")})
                    files.append((item["filename"], _request(f"{base_url}/view?{query}", timeout=120, raw=True)))
    return files


# --------------------------------------------------------------------- health

def check(profile: str = "default") -> dict:
    """Reachability of a profile's endpoint, without generating anything."""
    try:
        cfg = globals()["profile"](profile)
        if not cfg["enabled"]:
            return {"ok": False, "enabled": False, "provider": cfg["provider"]}
        if cfg["provider"] == "comfyui":
            stats = _request(f"{cfg['base_url']}/system_stats", timeout=10) or {}
            return {"ok": True, "provider": "comfyui", "base_url": cfg["base_url"], "system": stats.get("system", {})}
        if cfg["provider"] == "ollama":
            models = [m["name"] for m in _request(f"{cfg['base_url']}/api/tags", timeout=10).get("models", [])]
        else:
            models = [m["id"] for m in _request(f"{cfg['base_url']}/models", headers=_auth(cfg), timeout=10).get("data", [])]
        available = cfg["model"] in models
        return {"ok": available, "provider": cfg["provider"], "base_url": cfg["base_url"],
                "model": cfg["model"], "model_available": available}
    except AIError as exc:
        return {"ok": False, "error": str(exc)}
