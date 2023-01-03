import os

import whisper
from whisper.audio import SAMPLE_RATE

import numpy as np
import torch

from stt import logger
from .word_alignment import compute_alignment
from .text_normalize import remove_punctuation, normalize_text, remove_emoji
from .load_model import load_alignment_model, get_alignment_model

# TODO: understand and remove this limitations
torch.set_num_threads(1)


def get_language():
    return os.environ.get("LANGUAGE", None)


def decode(audio: torch.Tensor,
           model: whisper.model.Whisper,
           alignment_model: "Any",
           with_word_timestamps: bool,
           language: str = None,
           beam_size: int = None,
           no_speech_threshold: float = 0.6,
           logprob_threshold: float = -1.0,
           compression_ratio_threshold: float = 2.4,
           normalize_text_as_words=False,
           remove_punctuation_from_words=False,
           ) -> dict:
    """Transcribe the audio data using Whisper with the defined model."""
    result = {"text": "", "confidence-score": 0.0, "words": []}

    fp16 = model.device != torch.device("cpu")

    if language is None:
        language = get_language()

    logger.info(f"Transcribing audio with language {language}...")

    whisper_res = model.transcribe(audio,
                                   language=language,
                                   fp16=fp16,
                                   temperature=0.0,  # For deterministic results
                                   beam_size=beam_size,
                                   no_speech_threshold=no_speech_threshold,
                                   logprob_threshold=logprob_threshold,
                                   compression_ratio_threshold=compression_ratio_threshold
                                   )

    text = whisper_res["text"]
    text = remove_emoji(text).strip()
    if normalize_text_as_words:
        text = normalize_text(text, language)
        if remove_punctuation_from_words:
            text = remove_punctuation(text)
    segments = whisper_res["segments"]
    if language is None:
        language = whisper_res["language"]
        logger.info(f"Detected language: {language}")
    if isinstance(alignment_model, dict):
        # Load alignment model on the fly
        if language not in alignment_model:
            alignment_model_name = get_alignment_model(language)
            logger.info(f"Loading alignment model {alignment_model_name} ({'local' if os.path.exists(alignment_model_name) else 'remote'})...")
            alignment_model[language] = load_alignment_model(alignment_model_name, device=model.device, download_root="/opt")
        spec_alignment_model = alignment_model[language]
    else:
        spec_alignment_model = alignment_model

    result["text"] = text
    result["confidence-score"] = np.exp(np.array([r["avg_logprob"]
                                        for r in segments])).mean() if len(segments) else 0.0
    if not with_word_timestamps:
        if not normalize_text_as_words:
            text = normalize_text(text, language)
            if remove_punctuation_from_words:
                text = remove_punctuation(text)
        result["words"] = text.split()
    else:
        # Compute word timestamps
        result["words"] = []
        max_t = audio.shape[0]

        # Ensure that the segments start / end time are increasing
        # (because there is no guarantee with Whisper)
        previous_start = 0.0
        for segment in segments:
            if segment["start"] < previous_start:
                segment["start"] = previous_start
            if segment["end"] <= segment["start"]:
                segment["end"] = segment["start"] + 1.0
            previous_start = segment["end"]

        for segment in segments:
            offset = segment["start"]
            start = min(max_t, round(segment["start"] * SAMPLE_RATE))
            end = min(max_t, round(segment["end"] * SAMPLE_RATE))
            sub_audio = audio[start:end]
            sub_text = segment["text"]
            logger.debug(f"Aligning text: {sub_text}")
            sub_text = remove_emoji(sub_text).strip()
            sub_text = normalize_text(sub_text, language)
            if remove_punctuation_from_words:
                sub_text = remove_punctuation(sub_text)
            if not sub_text:
                logger.warn(
                    f"Lost text in segment {segment['start']}-{segment['end']}")
                continue
            labels, emission, trellis, segments, word_segments = compute_alignment(
                sub_audio, sub_text, spec_alignment_model)
            ratio = len(sub_audio) / (trellis.size(0) * SAMPLE_RATE)
            sub_words = sub_text.split()
            if len(sub_words) == len(word_segments):
                for word, segment in zip(sub_words, word_segments):
                    result["words"].append({
                        "word": word,
                        "start": segment.start * ratio + offset,
                        "end": segment.end * ratio + offset,
                        "conf": segment.score,
                    })
            else:
                logger.warn(
                    f"Alignment failed. Results might differ on some words.\nNumber of words: {len(sub_words)} != {len(word_segments)}\n>>>\n{sub_words}\n<<<\n{[segment.label for segment in word_segments]}")
                for segment in word_segments:
                    result["words"].append({
                        "word": segment.label,
                        "start": segment.start * ratio + offset,
                        "end": segment.end * ratio + offset,
                        "conf": segment.score,
                    })

    return result
