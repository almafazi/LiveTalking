###############################################################################
#  Easy-Wav2Lip inspired realtime adapter.
#
#  This module intentionally reuses LiveTalking's 256px Wav2Lip checkpoint and
#  realtime audio pipeline.  Easy-Wav2Lip itself is an offline video workflow;
#  the part that transfers safely to realtime is its soft mouth-region blend.
###############################################################################

import cv2
import numpy as np

from avatars.wav2lip_avatar import (
    LipReal,
    load_avatar,
    load_model,
    suppress_teeth_highlights,
    warm_up,
)
from registry import register


def build_feathered_mouth_mask(height, width, feather, width_ratio, height_ratio):
    """Return a soft alpha mask that changes only the lower/mouth face area."""
    mask = np.zeros((height, width), dtype=np.uint8)
    center = (width // 2, int(height * 0.69))
    axes = (
        max(1, int(width * width_ratio / 2)),
        max(1, int(height * height_ratio / 2)),
    )
    cv2.ellipse(mask, center, axes, 0, 0, 360, 255, -1)

    kernel = max(3, int(feather))
    if kernel % 2 == 0:
        kernel += 1
    return cv2.GaussianBlur(mask, (kernel, kernel), 0).astype(np.float32) / 255.0


@register("avatar", "easywav2lip")
class EasyWav2LipReal(LipReal):
    """Wav2Lip 256 with an Easy-Wav2Lip-style feathered mouth composite."""

    def _mouth_mask(self, height, width):
        # 蒙版只取决于裁剪尺寸和三个固定参数，按尺寸缓存避免每帧重建
        cache = getattr(self, "_mouth_mask_cache", None)
        if cache is None:
            cache = self._mouth_mask_cache = {}
        mask = cache.get((height, width))
        if mask is None:
            mask = build_feathered_mouth_mask(
                height,
                width,
                self.opt.easywav2lip_feather,
                self.opt.easywav2lip_mouth_width,
                self.opt.easywav2lip_mouth_height,
            )[..., None]
            cache[(height, width)] = mask
        return mask

    def paste_back_frame(self, pred_frame, idx: int):
        y1, y2, x1, x2 = self.coord_list_cycle[idx]
        combine_frame = self.frame_list_cycle[idx].copy()

        processed = suppress_teeth_highlights(pred_frame, self.teeth_suppression)
        generated = cv2.resize(processed, (x2 - x1, y2 - y1)).astype(np.float32)
        original = combine_frame[y1:y2, x1:x2].astype(np.float32)

        alpha = self._mouth_mask(y2 - y1, x2 - x1)
        blended = generated * alpha + original * (1.0 - alpha)
        combine_frame[y1:y2, x1:x2] = np.clip(blended, 0, 255).astype(np.uint8)
        return combine_frame


__all__ = [
    "EasyWav2LipReal",
    "build_feathered_mouth_mask",
    "load_avatar",
    "load_model",
    "warm_up",
]
