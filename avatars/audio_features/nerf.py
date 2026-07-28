###############################################################################
#  Streaming Wav2Vec2/HuBERT features for the optional ER-NeRF adapter.
###############################################################################

import numpy as np
import torch

from avatars.audio_features.base_asr import BaseASR
from utils.logger import logger


class NerfASR(BaseASR):
    """Build ER-NeRF's [attention, channels, 16] audio windows in batches."""

    def __init__(self, opt, parent, processor, model):
        super().__init__(opt, parent)
        self.device = next(model.parameters()).device
        self.processor = processor
        self.model = model
        self.context_size = opt.m

        model_name = opt.asr_model.lower()
        if "hubert" in model_name:
            self.audio_dim = 1024
        elif "esperanto" in model_name:
            self.audio_dim = 44
        elif "deepspeech" in model_name:
            self.audio_dim = 29
        else:
            self.audio_dim = 32

        self.feature_capacity = max(32, self.context_size * 8)
        self.feature_ring = torch.zeros(
            self.feature_capacity,
            self.audio_dim,
            dtype=torch.float32,
            device=self.device,
        )
        self.write_index = 0
        self.window_front = self.feature_capacity - 8
        self.window_tail = 8
        self.attention_windows = [
            torch.zeros(self.audio_dim, 16, dtype=torch.float32, device=self.device)
            for _ in range(4)
        ]

        # Left padding is immediately available. Right context is accumulated
        # from the real stream before the first feature batch is emitted.
        self.frames.extend(
            [np.zeros(self.chunk, dtype=np.float32)] * self.stride_left_size
        )

    def _extract_logits(self, samples):
        inputs = self.processor(
            samples,
            sampling_rate=self.sample_rate,
            return_tensors="pt",
            padding=True,
        )
        with torch.no_grad():
            result = self.model(inputs.input_values.to(self.device))
            if "hubert" in self.opt.asr_model.lower():
                logits = result.last_hidden_state
            else:
                logits = result.logits

        left = max(0, self.stride_left_size)
        right = max(left, logits.shape[1] - self.stride_right_size + 1)
        return logits[0, left:right]

    def _append_features(self, features):
        for feature in features:
            self.feature_ring[self.write_index] = feature
            self.write_index = (self.write_index + 1) % self.feature_capacity

    def _read_base_window(self):
        if self.window_front < self.window_tail:
            window = self.feature_ring[self.window_front:self.window_tail]
        else:
            window = torch.cat(
                (
                    self.feature_ring[self.window_front:],
                    self.feature_ring[:self.window_tail],
                ),
                dim=0,
            )
        self.window_front = (self.window_front + 2) % self.feature_capacity
        self.window_tail = (self.window_tail + 2) % self.feature_capacity
        return window.permute(1, 0)

    def _next_window(self):
        base = self._read_base_window()
        if self.opt.att == 0:
            return base.unsqueeze(0)

        self.attention_windows.append(base)
        while len(self.attention_windows) < 8:
            self.attention_windows.append(self._read_base_window())
        result = torch.stack(self.attention_windows, dim=0)
        self.attention_windows = self.attention_windows[1:]
        return result

    def run_step(self):
        for _ in range(self.batch_size * 2):
            audio_frame = self.get_audio_frame()
            self.frames.append(audio_frame.data)
            self.output_queue.put(audio_frame)

        minimum = self.stride_left_size + self.context_size + self.stride_right_size
        if len(self.frames) < minimum:
            return

        samples = np.concatenate(self.frames)
        logits = self._extract_logits(samples)
        self._append_features(logits)

        feature_batch = [self._next_window() for _ in range(self.batch_size)]
        self.feat_queue.put(feature_batch)
        self.frames = self.frames[-(self.stride_left_size + self.stride_right_size):]

    def warm_up(self):
        logger.info(
            "ER-NeRF ASR ready; right-context latency %.3fs",
            self.stride_right_size * 0.02,
        )
