# Easy-Wav2Lip and ER-NeRF models

LiveTalking exposes both integrations through the normal `--model` option.
They keep the existing TTS, interruption, session, and WebRTC/WHIP/RTMP output
pipeline, but have different asset and runtime requirements.

## Easy-Wav2Lip

`easywav2lip` is a realtime-safe adaptation of Easy-Wav2Lip's feathered mouth
compositing. It deliberately reuses LiveTalking's Wav2Lip 256 model instead of
embedding Easy-Wav2Lip's offline file/FFmpeg workflow.

It uses the same checkpoint and generated avatar as `wav2lip`:

```bash
python app.py \
  --transport rtcpush \
  --model easywav2lip \
  --avatar_id wav2lip256_avatar1 \
  --batch_size 4 \
  --max_session 1 \
  --push_url 'http://127.0.0.1:10100/rtc/v1/whip/?app=live&stream=livestream&eip=127.0.0.1'
```

Optional blend tuning:

```text
--easywav2lip_feather 21
--easywav2lip_mouth_width 0.72
--easywav2lip_mouth_height 0.36
```

Larger width/height values replace more of the original face. A larger feather
value produces a softer transition. Avatar generation for this model uses the
existing `avatars.wav2lip.genavatar` command and artifact format.

This adapter does not run GFPGAN/ESRGAN per frame. Those restorers add latency,
can flicker, and may alter generated lip or teeth geometry.

## ER-NeRF

ER-NeRF is subject-specific: preprocess and train one workspace for each
person. The realtime adapter loads the official checkout dynamically and sends
its rendered BGR frames through LiveTalking's current output stack.

### Important environment boundary

Run ER-NeRF on a dedicated LiveTalking GPU instance, venv, or container. The
adapter imports ER-NeRF in-process, so that Python environment must contain
both LiveTalking's server dependencies and ER-NeRF's PyTorch/CUDA dependencies.
Do not replace the working Wav2Lip production environment in place.

The upstream implementation was tested with an older PyTorch/CUDA stack and
compiles custom encoder/raymarching extensions. Use `max_session=1`.

### Prepare the external checkout

```bash
git clone https://github.com/Fictionarry/ER-NeRF.git /workspace/ER-NeRF
cd /workspace/ER-NeRF
pip install -r requirements.txt
```

Follow ER-NeRF's preprocessing and training instructions for a 25 FPS subject.
For realtime use, train/extract features using the same HuBERT or Wav2Vec2
family selected by `--ernerf_asr_model`. DeepSpeech is not loaded directly by
the realtime adapter.

The required runtime inputs are:

```text
/workspace/ER-NeRF/
  encoding.py
  nerf_triplane/
  raymarching/
  ...
/workspace/ER-NeRF/data/<subject>/
  transforms_train.json
  au.csv
  ...preprocessed subject files...
/workspace/ER-NeRF/<workspace>/checkpoints/
  ...trained checkpoint...
```

### Run ER-NeRF through LiveTalking

Use a small batch because each batch frame is rendered sequentially:

```bash
python app.py \
  --transport rtcpush \
  --model ernerf \
  --avatar_id obama \
  --batch_size 1 \
  --max_session 1 \
  --ernerf_root /workspace/ER-NeRF \
  --ernerf_path /workspace/ER-NeRF/data/obama \
  --ernerf_workspace /workspace/ER-NeRF/trial_obama_torso \
  --ernerf_asr_model facebook/hubert-large-ls960-ft \
  --ernerf_att 2 \
  --ernerf_torso \
  --push_url 'http://127.0.0.1:10100/rtc/v1/whip/?app=live&stream=livestream&eip=127.0.0.1'
```

Override `--ernerf_pose` or `--ernerf_au` when those files are not named
`transforms_train.json` and `au.csv` inside the subject directory.

ER-NeRF does not use `data/avatars/<avatar_id>`. To switch subjects, restart
the runtime with another `--ernerf_path` and `--ernerf_workspace`.

## Licensing

Easy-Wav2Lip-inspired blending does not change the license of the Wav2Lip
checkpoint. The public upstream Wav2Lip weights are restricted to
personal/research/non-commercial use. Audit or replace the weights before a
commercial deployment. ER-NeRF code is MIT-licensed; separately verify the
training video, checkpoints, and all model dependencies.
