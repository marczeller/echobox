"""Voice fingerprint enrollment + identification for Echobox.

Uses pyannote.audio's wespeaker-voxceleb-resnet34-LM model to compute speaker
embeddings from audio. Enrolled voices live in ~/echobox/voices/ as pairs of
files: <slug>.npy (the embedding) + <slug>.json (name + metadata).

At identification time, per-speaker audio segments are aggregated, a single
embedding is computed, and cosine similarity against each enrolled voice is
used to map SPEAKER_XX → real name when above a confidence threshold.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


VOICES_DIR = Path(__file__).resolve().parent.parent / "voices"
EMBED_MODEL = "pyannote/wespeaker-voxceleb-resnet34-LM"
MATCH_THRESHOLD = 0.55
MIN_SEGMENT_DURATION = 1.0


class SpeakerIdError(RuntimeError):
    pass


def _require_numpy():
    try:
        import numpy as np
    except ImportError as exc:
        raise SpeakerIdError("numpy is required for voice identification") from exc
    return np


def _load_embedding_inference():
    try:
        import os
        from pyannote.audio import Inference, Model
    except ImportError as exc:
        raise SpeakerIdError("pyannote.audio is required") from exc
    token = os.environ.get("HF_TOKEN", "").strip()
    if not token:
        token_file = Path.home() / ".cache" / "huggingface" / "token"
        if token_file.exists():
            token = token_file.read_text().strip()
    kwargs = {"token": token} if token else {}
    try:
        model = Model.from_pretrained(EMBED_MODEL, **kwargs)
    except Exception as exc:
        raise SpeakerIdError(f"failed to load {EMBED_MODEL}: {exc}") from exc
    inference = Inference(model, window="whole")
    try:
        import torch
        if torch.backends.mps.is_available():
            inference.to(torch.device("mps"))
    except Exception:
        pass
    return inference


def _slug(value: str) -> str:
    out = []
    for ch in value.lower():
        if ch.isalnum():
            out.append(ch)
        elif out and out[-1] != "-":
            out.append("-")
    return "".join(out).strip("-") or "voice"


def enroll(wav_path: str | Path, slug: str, display_name: str) -> Path:
    """Compute an embedding for a reference WAV and store it under voices/.

    The reference WAV should be 20-60 seconds of the target speaker talking
    alone, cleanly. Returns the path to the saved .npy file.
    """
    np = _require_numpy()
    wav = Path(wav_path).expanduser().resolve()
    if not wav.exists():
        raise SpeakerIdError(f"reference WAV not found: {wav}")
    slug_clean = _slug(slug)
    VOICES_DIR.mkdir(parents=True, exist_ok=True)
    inference = _load_embedding_inference()
    embedding = inference(str(wav))
    vec = np.asarray(embedding, dtype="float32").reshape(-1)
    norm = float(np.linalg.norm(vec))
    if norm > 0:
        vec = vec / norm
    npy_path = VOICES_DIR / f"{slug_clean}.npy"
    json_path = VOICES_DIR / f"{slug_clean}.json"
    np.save(npy_path, vec)
    json_path.write_text(
        json.dumps(
            {
                "slug": slug_clean,
                "display_name": display_name,
                "source_wav": str(wav),
                "model": EMBED_MODEL,
                "dim": int(vec.shape[0]),
                "enrolled_at": datetime.now(timezone.utc).isoformat(),
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return npy_path


def load_enrolled_voices() -> list[dict[str, Any]]:
    """Return enrolled voices as a list of {slug, name, embedding} dicts."""
    if not VOICES_DIR.exists():
        return []
    np = _require_numpy()
    out: list[dict[str, Any]] = []
    for json_path in sorted(VOICES_DIR.glob("*.json")):
        try:
            meta = json.loads(json_path.read_text())
        except Exception:
            continue
        slug = meta.get("slug") or json_path.stem
        npy_path = VOICES_DIR / f"{slug}.npy"
        if not npy_path.exists():
            continue
        try:
            vec = np.load(npy_path).astype("float32").reshape(-1)
        except Exception:
            continue
        norm = float(np.linalg.norm(vec))
        if norm > 0:
            vec = vec / norm
        out.append(
            {
                "slug": slug,
                "name": meta.get("display_name") or slug,
                "embedding": vec,
            }
        )
    return out


def _embed_segment(inference: Any, wav_path: Path, start: float, end: float):
    np = _require_numpy()
    from pyannote.core import Segment
    try:
        embedding = inference.crop(str(wav_path), Segment(start, end))
    except Exception:
        return None
    vec = np.asarray(embedding, dtype="float32").reshape(-1)
    norm = float(np.linalg.norm(vec))
    if norm > 0:
        vec = vec / norm
    return vec


def identify_speakers(
    wav_path: str | Path,
    diarization_segments: list[dict[str, Any]],
    logger: Any = None,
) -> dict[str, str]:
    """Given a WAV and diarization segments, return a SPEAKER_XX -> name map.

    Only speakers matched with cosine similarity >= MATCH_THRESHOLD against an
    enrolled voice are included in the returned map. Unmatched speakers keep
    their SPEAKER_XX label upstream.
    """
    log = logger or (lambda _msg: None)
    voices = load_enrolled_voices()
    if not voices:
        return {}
    try:
        np = _require_numpy()
        inference = _load_embedding_inference()
        inference.window = "whole"
    except SpeakerIdError as exc:
        log(f"Speaker ID skipped: {exc}")
        return {}

    wav = Path(wav_path).expanduser()
    if not wav.exists():
        return {}

    segments_by_speaker: dict[str, list[tuple[float, float]]] = {}
    for seg in diarization_segments:
        speaker = str(seg.get("speaker") or "").strip()
        if not speaker or not speaker.startswith("SPEAKER_"):
            continue
        start = float(seg.get("start", 0) or 0)
        end = float(seg.get("end", 0) or 0)
        if end - start < MIN_SEGMENT_DURATION:
            continue
        segments_by_speaker.setdefault(speaker, []).append((start, end))

    if not segments_by_speaker:
        return {}

    mapping: dict[str, str] = {}
    for speaker, spans in segments_by_speaker.items():
        spans.sort(key=lambda s: s[1] - s[0], reverse=True)
        embeddings = []
        for start, end in spans[:5]:
            vec = _embed_segment(inference, wav, start, end)
            if vec is not None:
                embeddings.append(vec)
            if len(embeddings) >= 3:
                break
        if not embeddings:
            continue
        avg = np.mean(np.stack(embeddings, axis=0), axis=0)
        norm = float(np.linalg.norm(avg))
        if norm > 0:
            avg = avg / norm
        best_score = -1.0
        best_name = ""
        for voice in voices:
            score = float(np.dot(avg, voice["embedding"]))
            if score > best_score:
                best_score = score
                best_name = voice["name"]
        if best_score >= MATCH_THRESHOLD:
            mapping[speaker] = best_name
            log(f"Speaker {speaker} -> {best_name} (cosine={best_score:.3f})")
        else:
            log(f"Speaker {speaker} unmatched (best cosine={best_score:.3f} < {MATCH_THRESHOLD})")
    return mapping


def relabel_transcript(transcript_text: str, mapping: dict[str, str]) -> str:
    """Replace SPEAKER_XX tokens in a transcript with mapped real names."""
    if not mapping:
        return transcript_text
    out = transcript_text
    for label in sorted(mapping, key=len, reverse=True):
        out = out.replace(f"{label}:", f"{mapping[label]}:")
    return out


def main() -> int:
    import argparse
    parser = argparse.ArgumentParser(description="Echobox voice enrollment")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_enroll = sub.add_parser("enroll", help="Enroll a reference voice")
    p_enroll.add_argument("slug", help="short slug, e.g. marc / ernesto / andrey")
    p_enroll.add_argument("wav", help="reference WAV file (20-60s of clean speech)")
    p_enroll.add_argument("name", help="display name, e.g. 'Marc Zeller'")

    p_list = sub.add_parser("list", help="List enrolled voices")

    p_delete = sub.add_parser("delete", help="Delete an enrolled voice by slug")
    p_delete.add_argument("slug", help="slug of the voice to remove (e.g. marc)")

    p_test = sub.add_parser("test", help="Identify speakers in a diarized WAV (JSON output)")
    p_test.add_argument("wav")
    p_test.add_argument("segments_json", help="JSON with list of {start,end,speaker}")

    args = parser.parse_args()

    if args.cmd == "enroll":
        path = enroll(args.wav, args.slug, args.name)
        print(f"Enrolled {args.name} -> {path}")
        return 0
    if args.cmd == "list":
        voices = load_enrolled_voices()
        if not voices:
            print("No voices enrolled.")
            return 0
        for v in voices:
            print(f"  {v['slug']:16s} {v['name']}")
        return 0
    if args.cmd == "delete":
        slug = args.slug.strip().lower()
        npy_path = VOICES_DIR / f"{slug}.npy"
        json_path = VOICES_DIR / f"{slug}.json"
        if not npy_path.exists() and not json_path.exists():
            print(f"No voice enrolled as {slug!r}.")
            return 1
        npy_path.unlink(missing_ok=True)
        json_path.unlink(missing_ok=True)
        print(f"Deleted voice {slug}")
        return 0
    if args.cmd == "test":
        segs = json.loads(Path(args.segments_json).read_text())
        mapping = identify_speakers(args.wav, segs, logger=print)
        print(json.dumps(mapping, indent=2))
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
