###############################################################################
#  Optional ER-NeRF adapter for the current LiveTalking registry/output stack.
#
#  ER-NeRF is loaded from an external checkout. Run this model in a dedicated
#  LiveTalking venv/container because its PyTorch/CUDA requirements differ from
#  Wav2Lip and MuseTalk. See docs/models-easywav2lip-ernerf.md.
###############################################################################

import importlib
import queue
import sys
import time
import types
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np
import torch

from avatars.audio_features.nerf import NerfASR
from avatars.base_avatar import BaseAvatar
from registry import register
from utils.logger import logger


@dataclass
class ERNeRFModelBundle:
    trainer: object
    data_loader: object
    audio_processor: object
    audio_model: object


def _mount_ernerf_package(root):
    """Expose a checkout named `ER-NeRF` as importable package `ernerf`."""
    root = Path(root).expanduser().resolve()
    required = (
        root / "encoding.py",
        root / "nerf_triplane" / "network.py",
        root / "nerf_triplane" / "provider.py",
    )
    missing = [str(path) for path in required if not path.is_file()]
    if missing:
        raise FileNotFoundError(
            "Invalid --ernerf_root; missing: " + ", ".join(missing)
        )

    current = sys.modules.get("ernerf")
    if current is not None and Path(next(iter(current.__path__))).resolve() != root:
        raise RuntimeError("A different ernerf package is already loaded")
    if current is None:
        package = types.ModuleType("ernerf")
        package.__file__ = str(root / "__init__.py")
        package.__path__ = [str(root)]
        package.__package__ = "ernerf"
        sys.modules["ernerf"] = package
    return root


def _apply_ernerf_defaults(opt):
    _mount_ernerf_package(opt.ernerf_root)
    subject = Path(opt.ernerf_path).expanduser().resolve()
    workspace = Path(opt.ernerf_workspace).expanduser().resolve()
    if not subject.is_dir():
        raise FileNotFoundError(f"ER-NeRF subject directory not found: {subject}")
    if not workspace.is_dir():
        raise FileNotFoundError(f"ER-NeRF workspace not found: {workspace}")

    pose = Path(opt.ernerf_pose).expanduser().resolve() if opt.ernerf_pose else subject / "transforms_train.json"
    au = Path(opt.ernerf_au).expanduser().resolve() if opt.ernerf_au else subject / "au.csv"
    if not pose.is_file():
        raise FileNotFoundError(f"ER-NeRF pose JSON not found: {pose}")
    if not au.is_file():
        raise FileNotFoundError(f"ER-NeRF AU CSV not found: {au}")

    values = {
        "path": str(subject),
        "workspace": str(workspace),
        "pose": str(pose),
        "au": str(au),
        "test": True,
        "test_train": False,
        "data_range": [0, -1],
        "seed": 0,
        "ckpt": "latest",
        "num_rays": 4096 * 16,
        "cuda_ray": True,
        "max_steps": 16,
        "num_steps": 16,
        "upsample_steps": 0,
        "update_extra_interval": 16,
        "max_ray_batch": 4096,
        "warmup_step": 10000,
        "amb_aud_loss": 1,
        "amb_eye_loss": 1,
        "unc_loss": 1,
        "lambda_amb": 1e-4,
        "fp16": True,
        "bg_img": "white",
        "fbg": False,
        "exp_eye": True,
        "fix_eye": -1,
        "smooth_eye": True,
        "torso_shrink": 0.8,
        "color_space": "srgb",
        "preload": 0,
        "bound": 1,
        "scale": 4,
        "offset": [0, 0, 0],
        "dt_gamma": 1 / 256,
        "min_near": 0.05,
        "density_thresh": 10,
        "density_thresh_torso": 0.01,
        "patch_size": 1,
        "init_lips": False,
        "finetune_lips": False,
        "smooth_lips": True,
        "torso": bool(opt.ernerf_torso),
        "torso_imgs": str(subject / "torso_imgs") if opt.ernerf_torso else "",
        "head_ckpt": "",
        "gui": False,
        "W": 450,
        "H": 450,
        "radius": 3.35,
        "fovy": 21.24,
        "max_spp": 1,
        "att": opt.ernerf_att,
        "aud": "",
        "emb": False,
        "ind_dim": 4,
        "ind_num": 10000,
        "ind_dim_torso": 8,
        "amb_dim": 2,
        "part": False,
        "part2": False,
        "train_camera": False,
        "smooth_path": True,
        "smooth_path_window": 7,
        "asr": True,
        "asr_wav": "",
        "asr_play": False,
        "asr_model": opt.ernerf_asr_model,
        "asr_save_feats": False,
    }
    for name, value in values.items():
        setattr(opt, name, value)


def load_model(opt):
    if not torch.cuda.is_available():
        raise RuntimeError("ER-NeRF realtime mode requires an NVIDIA CUDA GPU")
    _apply_ernerf_defaults(opt)

    provider = importlib.import_module("ernerf.nerf_triplane.provider")
    utils = importlib.import_module("ernerf.nerf_triplane.utils")
    network = importlib.import_module("ernerf.nerf_triplane.network")

    utils.seed_everything(opt.seed)
    device = torch.device("cuda")
    model = network.NeRFNetwork(opt)
    trainer = utils.Trainer(
        "ngp",
        opt,
        model,
        device=device,
        workspace=opt.workspace,
        criterion=torch.nn.MSELoss(reduction="none"),
        fp16=opt.fp16,
        metrics=[],
        use_checkpoint=opt.ckpt,
    )
    data_loader = provider.NeRFDataset_Test(opt, device=device).dataloader()
    model.aud_features = data_loader._data.auds
    model.eye_areas = data_loader._data.eye_area

    transformers = importlib.import_module("transformers")
    model_name = opt.asr_model.lower()
    if "deepspeech" in model_name:
        raise ValueError(
            "Realtime ER-NeRF does not load DeepSpeech directly; train/use a "
            "HuBERT or Wav2Vec2 feature model instead"
        )
    if "hubert" in model_name:
        audio_processor = transformers.Wav2Vec2Processor.from_pretrained(opt.asr_model)
        audio_model = transformers.HubertModel.from_pretrained(opt.asr_model).to(device).eval()
    else:
        audio_processor = transformers.AutoProcessor.from_pretrained(opt.asr_model)
        audio_model = transformers.AutoModelForCTC.from_pretrained(opt.asr_model).to(device).eval()

    return ERNeRFModelBundle(trainer, data_loader, audio_processor, audio_model)


def load_avatar(opt):
    # ER-NeRF stores the subject, pose and background in its dataset/workspace.
    return None


def warm_up(batch_size, model):
    logger.info(
        "ER-NeRF loaded (%d poses); first render will warm CUDA kernels",
        model.data_loader._data.end_index,
    )


@register("avatar", "ernerf")
class ERNeRFReal(BaseAvatar):
    def __init__(self, opt, model, avatar=None):
        if opt.max_session > 1:
            logger.warning("ER-NeRF shares one CUDA trainer; max_session=1 is recommended")
        super().__init__(opt)
        self.trainer = model.trainer
        self.data_loader = model.data_loader
        self.loader = iter(self.data_loader)
        self.frame_count = self.data_loader._data.end_index
        self.width = opt.W
        self.height = opt.H
        self.asr = NerfASR(
            opt,
            self,
            processor=model.audio_processor,
            model=model.audio_model,
        )
        self.asr.warm_up()

    def get_avatar_length(self):
        return self.frame_count

    def _next_render_data(self):
        try:
            return next(self.loader)
        except StopIteration:
            self.loader = iter(self.data_loader)
            return next(self.loader)

    @torch.no_grad()
    def inference_batch(self, index, audiofeat_batch):
        frames = []
        for audio_features in audiofeat_batch:
            data = self._next_render_data()
            data["auds"] = audio_features
            output = self.trainer.test_gui_with_data(data, self.opt.W, self.opt.H)
            rgb = np.clip(output["image"] * 255.0, 0, 255).astype(np.uint8)
            frames.append(cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR))
        return frames

    def inference(self, quit_event):
        index = 0
        count = 0
        elapsed = 0.0
        logger.info("start ER-NeRF inference")
        while not quit_event.is_set():
            try:
                features = self.asr.feat_queue.get(block=True, timeout=1)
            except queue.Empty:
                continue

            audio_frames = [
                self.asr.output_queue.get() for _ in range(self.batch_size * 2)
            ]
            started = time.perf_counter()
            rendered = self.inference_batch(index, features)
            elapsed += time.perf_counter() - started
            count += len(rendered)
            if count >= 100:
                logger.info("------actual avg ER-NeRF infer fps:%.4f", count / elapsed)
                count = 0
                elapsed = 0.0

            for offset, frame in enumerate(rendered):
                pair = audio_frames[offset * 2:offset * 2 + 2]
                self.res_frame_queue.put((frame, pair, index % self.frame_count))
                index += 1
        logger.info("ER-NeRF inference thread stop")

    def process_frames(self, quit_event):
        self.output.start()
        while not quit_event.is_set():
            try:
                frame, audio_frames, _ = self.res_frame_queue.get(block=True, timeout=1)
            except queue.Empty:
                continue

            self.speaking = not all(item.type != 0 for item in audio_frames)
            self.output.push_video_frame(frame)
            self.record_video_data(frame)
            for audio_frame in audio_frames:
                pcm = (audio_frame.data * 32767).astype(np.int16)
                self.output.push_audio_frame(pcm, audio_frame.userdata)
                self.record_audio_data(pcm)
        self.output.stop()
        logger.info("ER-NeRF process_frames thread stop")

    def paste_back_frame(self, pred_frame, idx):
        return pred_frame


__all__ = ["ERNeRFReal", "load_avatar", "load_model", "warm_up"]
