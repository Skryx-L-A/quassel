import os
import signal
import socket
import subprocess
import sys
import time

REPO = "<repo>"
sys.path.insert(0, REPO + "/scripts")
sys.path.insert(0, REPO)
from benchmark_stt_mac import word_error_rate  # noqa: E402
from quassel import config  # noqa: E402

BIN = REPO + "/vendor/whisper.cpp/build/bin/whisper-server"
MODEL = os.path.expanduser("~/Library/Application Support/Quassel/models/ggml-large-v3-turbo-q5_0.bin")
VADM = os.path.expanduser("~/Library/Application Support/Quassel/models/ggml-silero-v5.1.2.bin")
HOST, PORT = "127.0.0.1", "8821"
URL = "http://%s:%s" % (HOST, PORT)
PROMPT = ", ".join(config.dictionary_words()[:80])

LIVE_PID = 6506
LIVE_PORT = 8765


def assert_live_untouched():
    r = subprocess.run(["lsof", "-iTCP:%d" % LIVE_PORT, "-sTCP:LISTEN", "-n", "-P"],
                        capture_output=True, text=True)
    assert str(LIVE_PID) in r.stdout, "Live-Server PID %d nicht mehr auf Port %d - ABBRUCH" % (LIVE_PID, LIVE_PORT)


def port_free(port=None):
    port = port or int(PORT)
    s = socket.socket()
    try:
        s.bind((HOST, port))
        return True
    except OSError:
        return False
    finally:
        s.close()


def start_server(extra_args=None, port=None):
    assert_live_untouched()
    port = str(port or PORT)
    if not port_free(int(port)):
        raise RuntimeError("Port %s belegt" % port)
    args = ["-t", "8", "-bs", "5", "--vad", "--vad-model", VADM]
    if extra_args:
        args += extra_args
    cmd = [BIN, "-m", MODEL] + args + ["--host", HOST, "--port", port, "-l", "auto", "-nt"]
    log = open(os.path.join(os.path.dirname(__file__), "logs", "server_%s.log" % port), "ab")
    p = subprocess.Popen(cmd, stdout=log, stderr=subprocess.STDOUT,
                          cwd=os.path.dirname(BIN), start_new_session=True)
    t0 = time.monotonic()
    u = "http://%s:%s/" % (HOST, port)
    while time.monotonic() - t0 < 120:
        if subprocess.run(["curl", "-fsS", "-m", "2", "-o", os.devnull, u],
                           check=False).returncode == 0:
            return p, log
        if p.poll() is not None:
            raise RuntimeError("Server-Prozess starb beim Start, siehe Log")
        time.sleep(0.1)
    raise RuntimeError("Server-Start Timeout")


def stop_server(p, log):
    try:
        os.killpg(p.pid, signal.SIGTERM)
    except OSError:
        p.terminate()
    try:
        p.wait(timeout=10)
    except subprocess.TimeoutExpired:
        os.killpg(p.pid, signal.SIGKILL)
        p.wait()
    log.close()
    assert_live_untouched()


def infer(wav, port=None, audio_ctx=None, beam_size=None, extra_fields=None, timeout=300):
    port = str(port or PORT)
    url = "http://%s:%s/inference" % (HOST, port)
    args = ["curl", "-fsS", "-m", str(timeout), url, "-F", "file=@" + wav,
            "-F", "response_format=text", "-F", "temperature=0.0",
            "-F", "prompt=" + PROMPT]
    if audio_ctx is not None:
        args += ["-F", "audio_ctx=%d" % audio_ctx]
    if beam_size is not None:
        args += ["-F", "beam_size=%d" % beam_size]
    if extra_fields:
        for k, v in extra_fields.items():
            args += ["-F", "%s=%s" % (k, v)]
    t0 = time.monotonic()
    r = subprocess.run(args, capture_output=True, text=True, check=False,
                        encoding="utf-8", errors="replace")
    el = time.monotonic() - t0
    return el, (r.stdout if r.returncode == 0 else ""), r.returncode


def load1():
    try:
        return os.getloadavg()[0]
    except OSError:
        return float("nan")
