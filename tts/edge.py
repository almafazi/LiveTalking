import re
import time
import asyncio
import numpy as np
import resampy
import soundfile as sf
import edge_tts
from io import BytesIO

from utils.logger import logger
from .base_tts import BaseTTS, State
from registry import register


def _split_text(text: str, max_chars: int = 120) -> list:
    """将长文本按句子切分成较短片段，降低首音频延迟。

    EdgeTTS 每次调用会等整段合成完才返回，所以先按句末标点切分、
    再贪心地拼回不超过 max_chars 的片段；超长片段硬切。
    """
    text = (text or "").strip()
    if not text:
        return []
    if len(text) <= max_chars:
        return [text]
    sentences = [p.strip() for p in re.split(r'(?<=[.!?。！？\n])\s*', text) if p and p.strip()]
    if not sentences:
        return [text]
    parts, buf = [], ""
    for s in sentences:
        if not buf:
            buf = s
        elif len(buf) + 1 + len(s) <= max_chars:
            buf = f"{buf} {s}"
        else:
            parts.append(buf)
            buf = s
        while len(buf) > max_chars:  # 单句仍超长时硬切
            parts.append(buf[:max_chars])
            buf = buf[max_chars:].lstrip()
    if buf:
        parts.append(buf)
    return parts or [text]


@register("tts", "edgetts")
class EdgeTTS(BaseTTS):
    def txt_to_audio(self,msg:tuple[str, dict]):
        text,textevent = msg
        voice = self.opt.REF_FILE or "zh-CN-YunxiaNeural"
        voicename = textevent.get('tts', {}).get('ref_file',voice) #self.opt.REF_FILE #"zh-CN-YunxiaNeural"
        t = time.time()
        # 切分长文本，使第一段音频更快开始播放
        parts = _split_text(text, max_chars=120)
        for i, part in enumerate(parts):
            if self.state != State.RUNNING:
                break
            self.input_stream.seek(0)
            self.input_stream.truncate()
            asyncio.new_event_loop().run_until_complete(self.__main(voicename, part))
            logger.info(f'-------edge tts time part {i+1}/{len(parts)}:{time.time()-t:.4f}s len={len(part)}')
            if self.input_stream.getbuffer().nbytes <= 0:  # edgetts err
                logger.error('edgetts err!!!!!')
                continue

            self.input_stream.seek(0)
            stream = self.__create_bytes_stream(self.input_stream)
            streamlen = stream.shape[0]
            idx = 0
            while streamlen >= self.chunk and self.state == State.RUNNING:
                eventpoint = {}
                streamlen -= self.chunk
                if i == 0 and idx == 0:
                    eventpoint = {'status': 'start', 'text': text}
                elif i == len(parts) - 1 and streamlen < self.chunk:
                    eventpoint = {'status': 'end', 'text': text}
                eventpoint.update(**textevent)
                self.parent.put_audio_frame(stream[idx:idx+self.chunk], eventpoint)
                idx += self.chunk
        self.input_stream.seek(0)
        self.input_stream.truncate()
        logger.info(f'-------edge tts total time:{time.time()-t:.4f}s parts={len(parts)}')

    def __create_bytes_stream(self,byte_stream):
        #byte_stream=BytesIO(buffer)
        stream, sample_rate = sf.read(byte_stream) # [T*sample_rate,] float64
        logger.info(f'[INFO]tts audio stream {sample_rate}: {stream.shape}')
        stream = stream.astype(np.float32)

        if stream.ndim > 1:
            logger.info(f'[WARN] audio has {stream.shape[1]} channels, only use the first.')
            stream = stream[:, 0]
    
        if sample_rate != self.sample_rate and stream.shape[0]>0:
            logger.info(f'[WARN] audio sample rate is {sample_rate}, resampling into {self.sample_rate}.')
            stream = resampy.resample(x=stream, sr_orig=sample_rate, sr_new=self.sample_rate)

        return stream
    
    async def __main(self,voicename: str, text: str):
        try:
            communicate = edge_tts.Communicate(text, voicename)

            #with open(OUTPUT_FILE, "wb") as file:
            first = True
            async for chunk in communicate.stream():
                if first:
                    first = False
                if chunk["type"] == "audio" and self.state==State.RUNNING:
                    #self.push_audio(chunk["data"])
                    self.input_stream.write(chunk["data"])
                    #file.write(chunk["data"])
                elif chunk["type"] == "WordBoundary":
                    pass
        except Exception as e:
            logger.exception('edgetts')
