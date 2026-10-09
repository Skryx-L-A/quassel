"""Command-line interface for Quassel without GUI or microphone access."""
import argparse
import datetime as dt
import json
import os
import shutil
import subprocess
import sys

from . import config, profile, state, whisperclient

EXIT_USAGE = 2
EXIT_FAILED = 1
EXIT_UNAVAILABLE = 5


def _model_name(path):
    name = os.path.basename(path or "")
    if name.startswith("ggml-") and name.endswith(".bin"):
        return name[5:-4]
    return None


def _daemon_status():
    """Use the established Linux service mechanism; do not create an IPC API."""
    if sys.platform.startswith("linux"):
        result = subprocess.run(["systemctl", "--user", "is-active", "--quiet", "quasseld"],
                                check=False)
        return "running" if result.returncode == 0 else "stopped"
    return "unknown"


def status_data():
    env = config.read_serverenv()
    return {
        "daemon": _daemon_status(),
        "server": "running" if whisperclient.server_up() else "stopped",
        "model": _model_name(env.get("MODEL_PATH")) or "none",
        "profile": profile.name() or "default",
        "state": state.state_read().get("state", "unknown"),
    }


def _emit(value, as_json=False):
    if as_json:
        print(json.dumps(value, ensure_ascii=False))
    elif isinstance(value, str):
        print(value)
    else:
        for key, item in value.items():
            print(f"{key}: {item}")


def _control(command):
    if not sys.platform.startswith("linux"):
        print("daemon control is unavailable on this platform", file=sys.stderr)
        return EXIT_UNAVAILABLE
    installed = shutil.which("quassel-ctl")
    development = os.path.join(os.path.dirname(os.path.dirname(__file__)), "bin", "quassel-ctl")
    control = installed or development
    try:
        return subprocess.run([control, command], check=False).returncode
    except OSError as exc:
        print(f"could not run quassel-ctl: {exc}", file=sys.stderr)
        return EXIT_FAILED


def _raw_last():
    """Ask the running daemon to put the raw text of the last dictation on the
    clipboard. The CLI never sees the text: it lives only in the daemon's
    memory, and the daemon sets the clipboard itself (SIGHUP, as the Control
    Center button does). The pill shows whether there was any raw text."""
    if not sys.platform.startswith("linux"):
        # macOS/Windows: the daemon belongs to the menu bar/tray app and has no
        # known PID for a second process; a SIGHUP to a guessed process would
        # end any Quassel that does not handle it.
        print('use "Raw text back" in the Quassel menu bar or tray menu on this platform',
              file=sys.stderr)
        return EXIT_UNAVAILABLE
    if _daemon_status() != "running":
        print("Quassel is not running, so there is no last dictation", file=sys.stderr)
        return EXIT_UNAVAILABLE
    result = subprocess.run(["systemctl", "--user", "kill", "-s", "HUP", "quasseld"],
                            check=False)
    if result.returncode != 0:
        print("could not reach the dictation service", file=sys.stderr)
        return EXIT_FAILED
    print("requested: the raw text goes to the clipboard if the cleanup changed the last dictation")
    return 0


def _dict_add(word):
    word = word.strip()
    if not word:
        print("dictionary word must not be empty", file=sys.stderr)
        return EXIT_USAGE
    words = config.dictionary_words()
    if word not in words:
        words.append(word)
        config.dictionary_save("\n".join(words))
    print(word)
    return 0


def _dict_remove(word):
    words = config.dictionary_words()
    kept = [item for item in words if item != word]
    if len(kept) == len(words):
        print("dictionary word not found", file=sys.stderr)
        return EXIT_FAILED
    config.dictionary_save("\n".join(kept))
    return 0


def _download_model(name, target):
    from .net import download
    os.makedirs(os.path.dirname(target), exist_ok=True)
    return download(config.MODEL_URL.format(name), target)


def _model_use(name, download=False):
    if name not in config.MODELS:
        print(f"unknown model: {name}", file=sys.stderr)
        return EXIT_USAGE
    target = os.path.join(config.MODELDIR, f"ggml-{name}.bin")
    if not os.path.isfile(target):
        if not download:
            print("model is not installed; pass --download to download it", file=sys.stderr)
            return EXIT_UNAVAILABLE
        if not _download_model(name, target):
            print("model download failed", file=sys.stderr)
            return EXIT_FAILED
    env = config.read_serverenv()
    env["MODEL_PATH"] = target
    config.write_serverenv(env)
    print(name)
    return 0


def _history_since(value):
    if not value:
        return 0
    try:
        return dt.datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        try:
            return float(value)
        except ValueError:
            raise argparse.ArgumentTypeError("--since must be ISO-8601 or Unix seconds")


def _history_list(since):
    if not config.Cfg().history_enabled:
        print("history is disabled in Quassel settings", file=sys.stderr)
        return EXIT_UNAVAILABLE
    for entry in config.history_read():
        if entry.get("ts", 0) >= since:
            print(json.dumps(entry, ensure_ascii=False))
    return 0


def _transcribe(path, output):
    if not os.path.isfile(path):
        print("file not found", file=sys.stderr)
        return EXIT_FAILED
    from .transcribe_file import render_srt, render_vtt, transcribe_file
    transcript = transcribe_file(path, config.Cfg(), output_format="verbose_json")
    if transcript is None:
        print("transcription failed (unsupported file, ffmpeg, or local server unavailable)", file=sys.stderr)
        return EXIT_FAILED
    if output == "json":
        result = {"text": transcript.text, "segments": list(transcript.segments)}
        if not transcript.segments:
            result["warning"] = "timestamps are unavailable from the current speech backend"
        _emit(result, as_json=True)
    elif output in ("srt", "vtt") and transcript.segments:
        print(render_srt(transcript.segments) if output == "srt" else render_vtt(transcript.segments), end="")
    else:
        print(transcript.text)
        if output in ("srt", "vtt"):
            print("timestamps are unavailable from the current speech backend; emitted plain text", file=sys.stderr)
    return 0


def parser():
    p = argparse.ArgumentParser(prog="quassel-type", description=__doc__)
    sub = p.add_subparsers(dest="command", required=True)
    status = sub.add_parser("status", help="show daemon, server, model, and profile")
    status.add_argument("--json", action="store_true")
    sub.add_parser("toggle", help="toggle the existing daemon service")
    sub.add_parser("panic", help="stop an active dictation")
    sub.add_parser("raw-last", help="put the raw text of the last dictation on the clipboard "
                                    "(kept only in the daemon's memory)")
    transcribe = sub.add_parser("transcribe", help="transcribe an existing media file")
    transcribe.add_argument("file")
    formats = transcribe.add_mutually_exclusive_group()
    formats.add_argument("--srt", action="store_const", const="srt", dest="output")
    formats.add_argument("--vtt", action="store_const", const="vtt", dest="output")
    formats.add_argument("--json", action="store_const", const="json", dest="output")
    models = sub.add_parser("model", help="list or select installed speech models")
    model_sub = models.add_subparsers(dest="model_command", required=True)
    model_sub.add_parser("list")
    use = model_sub.add_parser("use")
    use.add_argument("name")
    use.add_argument("--download", action="store_true", help="download a missing model")
    dictionary = sub.add_parser("dict", help="manage the local dictionary")
    dict_sub = dictionary.add_subparsers(dest="dict_command", required=True)
    dict_sub.add_parser("list")
    add = dict_sub.add_parser("add")
    add.add_argument("word")
    remove = dict_sub.add_parser("remove")
    remove.add_argument("word")
    history = sub.add_parser("history", help="read enabled local history")
    hist_sub = history.add_subparsers(dest="history_command", required=True)
    history_list = hist_sub.add_parser("list")
    history_list.add_argument("--since", type=_history_since, default=0)
    return p


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv[:1] == ["profiles"]:
        from . import profiles
        return profiles.cli(argv[1:])
    args = parser().parse_args(argv)
    if args.command == "status":
        _emit(status_data(), args.json)
        return 0
    if args.command in ("toggle", "panic"):
        return _control(args.command)
    if args.command == "raw-last":
        return _raw_last()
    if args.command == "transcribe":
        return _transcribe(args.file, args.output or "text")
    if args.command == "model":
        if args.model_command == "list":
            current = _model_name(config.read_serverenv().get("MODEL_PATH"))
            for name in config.MODELS:
                target = os.path.join(config.MODELDIR, f"ggml-{name}.bin")
                print(f"{name}\t{'selected' if name == current else 'installed' if os.path.isfile(target) else 'missing'}")
            return 0
        return _model_use(args.name, args.download)
    if args.command == "dict":
        if args.dict_command == "list":
            print("\n".join(config.dictionary_words()))
            return 0
        return _dict_add(args.word) if args.dict_command == "add" else _dict_remove(args.word)
    return _history_list(args.since)


if __name__ == "__main__":
    raise SystemExit(main())
