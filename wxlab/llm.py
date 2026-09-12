"""Ask a language model the one question the baseline is worst at.

Studies 1-3 showed the morning is efficiently priced and the empirical table
does not beat the market there. Study 5 showed where the market is still
visibly wrong: on days that are *already physically decided*, the winning
bucket stays cheap for one to three hours longer than it should, because the
obvious cooling rule cannot fire yet.

So the task here is not "what will the high be". It is:

    Given the day's published METAR trace up to hour h, what is the
    probability that the running max is already the day's high?

Formally, a distribution over ``delta = daily_max - running_max_now``, capped
at 3+. ``delta = 0`` means the day is over.

This is a fair fight by construction: the model and the baseline see exactly
the same published observations. The baseline compresses them into four binned
features; the model gets the raw hourly sequence, including wind direction and
cloud cover, which is where a sea breeze or an arriving deck would show up.

Design notes that matter for the result being worth anything:

* **No year in the prompt.** Month and day-of-month go in for seasonality; the
  year is withheld so a model cannot in principle recall the actual day.
* **Responses are cached on disk** by (model, prompt) hash. Re-running an
  evaluation costs nothing and produces identical numbers, which is what makes
  the reported figures checkable by someone else.
* **Token usage is recorded per call**, because the honest version of "we need
  API credits" is an arithmetic statement, not an adjective.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import urllib.error
import urllib.request
from dataclasses import dataclass, field

from .data import Metar

CACHE_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "reports", "llm_cache")

MAX_DELTA = 3  # the top bin is "3 or more"
MONTHS = ("January", "February", "March", "April", "May", "June", "July",
          "August", "September", "October", "November", "December")

SYSTEM = """You are a meteorologist reading raw hourly METAR from a single \
airport station. You answer only with the JSON object requested. You never \
explain outside the JSON."""

TASK = """Station: Shenzhen Bao'an (ZGSZ), on the east shore of the Pearl River \
estuary in subtropical south China. Water lies roughly south through west, so \
an afternoon veer into that arc is a sea breeze and usually caps the \
temperature.

Below is today's hourly observation trace, in local time, up to and including \
{hour}:00. Temperatures are whole degrees Celsius, which is also how this \
station reports and how the day's high is recorded.

Date: {day_of_month} {month} (year withheld)
Running maximum so far today: {running_max:.0f} C, first reached at {peak_hour}:00

{table}

Question: how much higher will today's maximum finish, above the {running_max:.0f} C \
already observed?

Give a probability distribution over four outcomes:
  "0"  - the day is over; {running_max:.0f} C is the final maximum
  "1"  - it finishes 1 C higher
  "2"  - it finishes 2 C higher
  "3+" - it finishes 3 C or more higher

Think about the diurnal cycle at this latitude and season, how far past the \
usual peak hour it is, whether the temperature trace has turned over, the \
dewpoint spread, whether cloud has arrived, and the wind direction history.

Respond with only this JSON, probabilities summing to 1:
{{"0": <p>, "1": <p>, "2": <p>, "3+": <p>}}"""


@dataclass
class Prediction:
    pmf: list[float]           # length 4: delta 0, 1, 2, 3+
    input_tokens: int = 0
    output_tokens: int = 0
    cached: bool = False
    raw: str = ""


@dataclass
class Usage:
    calls: int = 0
    cached: int = 0
    input_tokens: int = 0
    output_tokens: int = 0

    def add(self, pred: Prediction):
        self.calls += 1
        if pred.cached:
            self.cached += 1
        else:
            self.input_tokens += pred.input_tokens
            self.output_tokens += pred.output_tokens


def _fmt(value, unit="", nd=0):
    return "-" if value is None else f"{value:.{nd}f}{unit}"


def build_prompt(metar: Metar, day: str, hour: int, start_hour: int = 0) -> str:
    """Render the day's published trace. Never includes an hour after ``hour``.

    The table starts at local midnight, not at dawn: settlement is the maximum
    over the whole local day, and on warm nights the overnight reading can be
    the running max. Truncating the table would show a running max the trace
    does not contain.
    """
    rows = []
    for h in range(start_hour, hour + 1):
        ob = metar.get(day, h)
        if ob is None:
            continue
        sky = ob.sky if ob.sky and ob.sky != "M" else "-"
        wind = "-" if ob.wind_compass is None else f"{ob.wind_compass} {_fmt(ob.wind_kt)}kt"
        rows.append(f"  {h:02d}:00   {ob.temp_c:>4.0f}   {_fmt(ob.dewpoint_c):>4}   "
                    f"{wind:<10} {sky:<4} {_fmt(ob.visibility_mi, nd=1):>5}")
    table = ("  hour    T/C   Td/C   wind       sky   vis/mi\n" + "\n".join(rows))

    running = metar.running_max(day, hour)
    peak_hour = next((h for h in range(start_hour, hour + 1)
                      if metar.get(day, h) is not None
                      and metar.get(day, h).temp_c >= running), start_hour)
    return TASK.format(
        hour=hour, day_of_month=int(day[8:10]), month=MONTHS[int(day[5:7]) - 1],
        running_max=running, peak_hour=peak_hour, table=table)


# --------------------------------------------------------------- providers

class Provider:
    """Minimal HTTP client. No SDK, so wxlab stays dependency-free."""

    name = "abstract"
    model = ""

    def call(self, prompt: str) -> tuple[str, int, int]:
        raise NotImplementedError

    @staticmethod
    def _post(url: str, headers: dict, payload: dict, timeout: int = 120) -> dict:
        body = json.dumps(payload).encode()
        req = urllib.request.Request(url, data=body, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode())
        except urllib.error.HTTPError as exc:  # pragma: no cover - network
            raise RuntimeError(f"{url} -> {exc.code}: {exc.read()[:400].decode()}") from exc


class Anthropic(Provider):
    name = "anthropic"

    def __init__(self, model: str = "claude-sonnet-5", key_env: str = "ANTHROPIC_API_KEY"):
        self.model = model
        self.key = os.environ.get(key_env)
        if not self.key:
            raise RuntimeError(f"{key_env} is not set")

    def call(self, prompt: str):
        data = self._post(
            "https://api.anthropic.com/v1/messages",
            {"x-api-key": self.key, "anthropic-version": "2023-06-01",
             "content-type": "application/json"},
            {"model": self.model, "max_tokens": 300, "system": SYSTEM,
             "messages": [{"role": "user", "content": prompt}]},
        )
        text = "".join(b.get("text", "") for b in data.get("content", []))
        usage = data.get("usage", {})
        return text, usage.get("input_tokens", 0), usage.get("output_tokens", 0)


class OpenAI(Provider):
    name = "openai"

    def __init__(self, model: str = "gpt-5", key_env: str = "OPENAI_API_KEY"):
        self.model = model
        self.key = os.environ.get(key_env)
        if not self.key:
            raise RuntimeError(f"{key_env} is not set")

    def call(self, prompt: str):
        data = self._post(
            "https://api.openai.com/v1/chat/completions",
            {"Authorization": f"Bearer {self.key}", "Content-Type": "application/json"},
            {"model": self.model, "max_completion_tokens": 300,
             "messages": [{"role": "system", "content": SYSTEM},
                          {"role": "user", "content": prompt}]},
        )
        text = data["choices"][0]["message"]["content"]
        usage = data.get("usage", {})
        return text, usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0)


class CliProvider(Provider):
    """Drive a coding-agent CLI in headless mode instead of a billed API key.

    Claude Code (``claude -p``) and Codex (``codex exec``) each answer one
    prompt and exit, against whatever subscription the machine is signed in to.
    Useful for a pilot when no API key exists. Two things make it a trap if you
    reach for it carelessly:

    **These CLIs are agents, not completions.** Left at their defaults they run
    in the directory you launch them from, with file-editing and shell tools
    live. Pointed at this repository they will happily rewrite it instead of
    answering the question -- which is not a hypothetical; it is why every
    subclass here pins tools off, MCP off, its own system prompt, and a
    throwaway working directory.

    **The scaffolding dwarfs the prompt.** Measured on this task, one call
    carries 26k-42k tokens of agent preamble around a ~500 token question, even
    with tools disabled. At list prices that is $0.05-0.17 per call against
    $0.0024 through the API: roughly 80x the tokens for the same answer. Fine
    for tens of calls, wrong for thousands.
    """

    argv: tuple = ()
    timeout = 300

    @staticmethod
    def _sandbox_dir() -> str:
        """A scratch cwd, so an agent that ignores its instructions has nothing
        interesting to reach."""
        import tempfile
        path = os.path.join(tempfile.gettempdir(), "wxlab-cli-sandbox")
        os.makedirs(path, exist_ok=True)
        return path

    def _run(self, argv, prompt: str) -> str:
        """Run the CLI with the prompt on **stdin**, never as an argument.

        On Windows these CLIs are ``.CMD`` shims, so an argument is handed to
        cmd.exe, which silently drops embedded newlines. The prompt here is a
        multi-line observation table; passed as an argument it arrives
        truncated and the model answers that no data was supplied. stdin has no
        such problem and is portable.
        """
        import shutil
        import subprocess

        exe = shutil.which(argv[0])
        if exe is None:
            raise RuntimeError(f"{argv[0]} is not on PATH")
        proc = subprocess.run([exe, *argv[1:]], input=prompt,
                              capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=self.timeout,
                              cwd=self._sandbox_dir())
        if proc.returncode != 0:
            raise RuntimeError(f"{argv[0]} exited {proc.returncode}: "
                               f"{(proc.stderr or proc.stdout)[:300]}")
        return proc.stdout


class ClaudeCode(CliProvider):
    """Claude Code headless.

    ``--system-prompt`` replaces the agent prompt with the same SYSTEM string
    the API path sends, ``--allowed-tools ""`` leaves it no tools,
    ``--strict-mcp-config`` keeps the machine's MCP servers out, and
    ``--output-format json`` returns real token counts.
    """

    name = "claude-code"

    def __init__(self, model: str | None = None):
        self.model = model or "cli-default"
        self.argv = (("claude", "-p",
                      "--system-prompt", SYSTEM,
                      "--allowed-tools", "",
                      "--strict-mcp-config",
                      "--exclude-dynamic-system-prompt-sections",
                      "--output-format", "json")
                     + (("--model", model) if model else ()))
        # no positional prompt: it arrives on stdin

    def call(self, prompt: str):
        raw = self._run(self.argv, prompt)
        try:
            blob = json.loads(raw)
        except json.JSONDecodeError:
            return raw, 0, 0  # fall back to scraping the text
        usage = blob.get("usage", {})
        billed_in = (usage.get("input_tokens", 0)
                     + usage.get("cache_creation_input_tokens", 0)
                     + usage.get("cache_read_input_tokens", 0))
        return blob.get("result", ""), billed_in, usage.get("output_tokens", 0)


class Codex(CliProvider):
    """Codex CLI headless, against a ChatGPT subscription.

    ``--sandbox read-only`` is the equivalent guard: Codex defaults to
    ``danger-full-access`` in exec mode.
    """

    name = "codex"

    def __init__(self, model: str | None = None):
        self.model = model or "cli-default"
        self.argv = (("codex", "exec", "--skip-git-repo-check",
                      "--sandbox", "read-only")
                     + (("--model", model) if model else ())
                     + ("-",))  # trailing "-" tells Codex to read stdin

    def call(self, prompt: str):
        return self._run(self.argv, f"{SYSTEM}\n\n{prompt}"), 0, 0


PROVIDERS = {"anthropic": Anthropic, "openai": OpenAI,
             "claude-code": ClaudeCode, "codex": Codex}


# ------------------------------------------------------------------ parsing

def parse_pmf(text: str) -> list[float] | None:
    """Pull the four probabilities out of a reply and normalise them.

    Scans every ``{...}`` and keeps the **last** one that parses into a
    complete answer. Last rather than first matters for the CLI providers,
    which echo the prompt back -- and the prompt contains a
    ``{"0": <p>, ...}`` template that sits earlier in the stream than the reply.

    Tolerant of fenced code blocks and of a model that adds a sentence anyway,
    but not tolerant of missing keys: a partial answer is discarded rather than
    silently completed with zeros.
    """
    best = None
    for match in re.finditer(r"\{[^{}]*\}", text, re.S):
        try:
            obj = json.loads(match.group(0))
            values = [float(obj[k]) for k in ("0", "1", "2", "3+")]
        except (json.JSONDecodeError, KeyError, TypeError, ValueError):
            continue
        total = sum(values)
        if total <= 0 or any(v < 0 for v in values):
            continue
        best = [v / total for v in values]
    return best


# -------------------------------------------------------------------- cache

def _cache_path(provider: Provider, prompt: str) -> str:
    digest = hashlib.sha256(f"{provider.name}|{provider.model}|{prompt}".encode()).hexdigest()
    return os.path.join(CACHE_DIR, f"{digest[:24]}.json")


def predict(provider: Provider, metar: Metar, day: str, hour: int,
            *, use_cache: bool = True) -> Prediction | None:
    """One forecast. Returns None if the reply could not be parsed."""
    prompt = build_prompt(metar, day, hour)
    path = _cache_path(provider, prompt)

    if use_cache and os.path.exists(path):
        with open(path, encoding="utf-8") as fh:
            blob = json.load(fh)
        pmf = parse_pmf(blob["raw"])
        return Prediction(pmf, cached=True, raw=blob["raw"]) if pmf else None

    text, n_in, n_out = provider.call(prompt)
    os.makedirs(CACHE_DIR, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump({"provider": provider.name, "model": provider.model,
                   "day": day, "hour": hour, "raw": text,
                   "input_tokens": n_in, "output_tokens": n_out}, fh, indent=1)
    pmf = parse_pmf(text)
    return Prediction(pmf, n_in, n_out, cached=False, raw=text) if pmf else None


def estimate_cost(metar: Metar, days, hours, prices_per_mtok=None) -> dict:
    """Token arithmetic for a planned run, before spending anything.

    ``prices_per_mtok`` is {label: (input $/Mtok, output $/Mtok)}; pass current
    published rates rather than trusting a number baked into this file.
    """
    chars = 0
    calls = 0
    for day in days:
        for hour in hours:
            if metar.get(day, hour) is None:
                continue
            chars += len(SYSTEM) + len(build_prompt(metar, day, hour))
            calls += 1
    # ~4 characters per token is close enough for a budget; the real number is
    # recorded per call once a run happens.
    in_tok = chars // 4
    out_tok = calls * 60
    out = {"calls": calls, "input_tokens": in_tok, "output_tokens": out_tok}
    for label, (pin, pout) in (prices_per_mtok or {}).items():
        out[f"usd_{label}"] = round(in_tok / 1e6 * pin + out_tok / 1e6 * pout, 2)
    return out
