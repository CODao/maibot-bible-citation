"""Append exact Chinese Union Version references to outgoing QQ group text locally."""
from __future__ import annotations

from bisect import bisect_right
from copy import deepcopy
from pathlib import Path
from typing import ClassVar
from time import monotonic
import asyncio
import hashlib
import json
import re
import string
import unicodedata

from maibot_sdk import HookHandler, MaiBotPlugin, PluginConfigBase
from maibot_sdk.config_widgets import ConfigWidget, ConfigWidgetState
from maibot_sdk.types import HookMode, HookOrder, ErrorPolicy
from pydantic import Field

CITATION_LINE = re.compile(r'(?m)^\s*--出自[（(][^\n]+[）)]\s*$')
SENTENCE_BREAK = re.compile(r'[。！？!?；;\n\r]+')
FENCED_CODE = re.compile(r'```.*?(?:```|\Z)', re.S)
MAX_LOOKUP_CHARS = 512
PARTIAL_SCAN_BUDGET_SECONDS = 0.150


def normalize(text: str) -> str:
    """Normalize a lookup copy only; retain comma/period sentence boundaries."""
    result = []
    for c in unicodedata.normalize('NFKC', text):
        if c.isspace():
            continue
        if c in ',，':
            result.append('，')
        elif c in '.。':
            result.append('。')
        elif not (unicodedata.category(c).startswith('P') or c in string.punctuation):
            result.append(c.casefold())
    return ''.join(result)


def meaningful_length(text: str) -> int:
    return sum(not c.isspace() and c != '\x00' and not unicodedata.category(c).startswith('P')
               for c in text)


class VerseIndex:
    def __init__(self, path: Path):
        meta = json.loads(path.with_name('source.json').read_text(encoding='utf8'))
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != meta['data_sha256']:
            raise ValueError('Bundled Bible text checksum mismatch')
        rows = json.loads(raw)
        if len(rows) != meta['verse_count'] or len({r['book'] for r in rows}) != 66:
            raise ValueError('Incomplete bundled Bible data')
        self.rows = rows
        self.starts = []
        pieces = []
        offset = 0
        for row in rows:
            self.starts.append(offset)
            piece = normalize(row['text']) + '\n'
            pieces.append(piece)
            offset += len(piece)
        # Newlines prevent a match from crossing a verse boundary.
        self.corpus = ''.join(pieces)
        # A small Bloom prefilter rules out absent four-character seeds. Every
        # positive is still checked against the original corpus, so hash
        # collisions can never produce a false citation.
        self.seed_bits = bytearray(1 << 21)
        self.seed_mask = (1 << 24) - 1
        for piece in pieces:
            for n in range(len(piece) - 4):
                value = hash(piece[n:n + 4])
                for bit in (value & self.seed_mask, (value >> 24) & self.seed_mask):
                    self.seed_bits[bit >> 3] |= 1 << (bit & 7)

    def _partial_find(self, text: str, min_chars: int, max_references: int) -> list[str]:
        # Native substring searches keep this small and dependency-free. Extend
        # every exact seed to the longest shared span without crossing a verse.
        deadline = monotonic() + PARTIAL_SCAN_BUDGET_SECONDS
        token = normalize(re.sub(r'(?:https?://|www\.)\S+', '\x00', text, flags=re.I))
        best = {}
        covered = {}
        seed_positions = {}
        for qpos in range(len(token) - min_chars + 1):
            if monotonic() >= deadline:
                return []
            seed = token[qpos:qpos + min_chars]
            if '\x00' in seed or not any('\u3400' <= c <= '\u9fff' for c in seed):
                continue
            if seed not in seed_positions:
                value = hash(seed[:4])
                bits = (value & self.seed_mask, (value >> 24) & self.seed_mask)
                if len(seed) >= 4 and not all(self.seed_bits[b >> 3] & (1 << (b & 7)) for b in bits):
                    seed_positions[seed] = []
                else:
                    positions, start = [], 0
                    while True:
                        if monotonic() >= deadline:
                            return []
                        pos = self.corpus.find(seed, start)
                        if pos < 0:
                            break
                        positions.append(pos)
                        start = pos + 1
                    seed_positions[seed] = positions
            for pos in seed_positions[seed]:
                if monotonic() >= deadline:
                    return []
                diagonal = pos - qpos
                if any(a <= qpos and qpos + min_chars <= b for a, b in covered.get(diagonal, ())):
                    continue
                row_id = bisect_right(self.starts, pos) - 1
                verse_start = self.starts[row_id]
                verse_end = self.starts[row_id + 1] - 1 if row_id + 1 < len(self.starts) else len(self.corpus) - 1
                left, right = qpos, qpos + min_chars
                while left > 0 and diagonal + left > verse_start and token[left - 1] == self.corpus[diagonal + left - 1]:
                    left -= 1
                while right < len(token) and diagonal + right < verse_end and token[right] == self.corpus[diagonal + right]:
                    right += 1
                covered.setdefault(diagonal, []).append((left, right))
                length = meaningful_length(token[left:right])
                if length < min_chars:
                    continue
                boundary = int(diagonal + left == verse_start) + int(diagonal + right == verse_end)
                score = (length, boundary)
                if score > best.get(row_id, (0, 0)):
                    best[row_id] = score
        ranked = sorted(best, key=lambda i: (-best[i][0], -best[i][1], i))
        if monotonic() >= deadline:
            return []
        return [f"{self.rows[i]['book']} {self.rows[i]['chapter']}:{self.rows[i]['verse']}" for i in ranked[:max_references]]

    def find(self, text: str, *, min_chars: int = 4, max_references: int = 1,
             match_sentences: bool = True, partial_match: bool = True) -> list[str]:
        if not isinstance(text, str) or not text or len(text) > MAX_LOOKUP_CHARS or CITATION_LINE.search(text):
            return []
        visible = FENCED_CODE.sub('', text)
        if partial_match:
            return self._partial_find(visible, min_chars, max_references)
        candidates = [visible]
        if match_sentences:
            candidates.extend(SENTENCE_BREAK.split(visible))
        normalized = []
        for candidate in candidates:
            if re.search(r'https?://|www\.', candidate, flags=re.I):
                continue
            token = normalize(candidate)
            if meaningful_length(token) >= min_chars and any('\u3400' <= c <= '\u9fff' for c in token) and token not in normalized:
                normalized.append(token)
        # Prefer the longest exact sentence, then canonical book/chapter/verse order.
        normalized.sort(key=len, reverse=True)
        references = []
        for token in normalized[:64]:
            start = 0
            while len(references) < max_references:
                pos = self.corpus.find(token, start)
                if pos < 0:
                    break
                row = self.rows[bisect_right(self.starts, pos) - 1]
                reference = f"{row['book']} {row['chapter']}:{row['verse']}"
                if reference not in references:
                    references.append(reference)
                start = pos + len(token)
            if len(references) >= max_references:
                break
        return references


def annotate_message(message: dict, index: VerseIndex, settings) -> tuple[dict | None, list[str]]:
    if not settings.enabled or not isinstance(message, dict) or message.get('platform') != 'qq':
        return None, []
    info = message.get('message_info') or {}
    if settings.group_only and not info.get('group_info'):
        return None, []
    if message.get('is_notify') or message.get('is_command'):
        return None, []
    segments = message.get('raw_message')
    if not isinstance(segments, list):
        return None, []
    # Only actual outgoing text, never quoted messages, OCR, image captions or history.
    # Adjacent text segments may form one sentence; nontext segments form boundaries.
    chunks = []
    text_length = 0
    for seg in segments:
        if not isinstance(seg, dict):
            return None, []
        if seg.get('type') == 'text' and isinstance(seg.get('data'), str):
            text_length += len(seg['data'])
            if text_length > MAX_LOOKUP_CHARS:
                return None, []
            chunks.append(seg['data'])
        else:
            chunks.append('\x00')
    text = ''.join(chunks)
    reply_length = meaningful_length(normalize(FENCED_CODE.sub('', text)))
    if not reply_length:
        return None, []
    effective_min_chars = min(settings.min_chars, reply_length)
    refs = index.find(text, min_chars=effective_min_chars,
                      max_references=settings.max_references,
                      match_sentences=settings.match_sentences,
                      partial_match=settings.partial_match)
    if not refs:
        return None, []
    suffix = '\n' + '\n'.join(f'--出自（{ref}）' for ref in refs)
    changed = deepcopy(message)
    changed['raw_message'].append({'type': 'text', 'data': suffix})
    processed = changed.get('processed_plain_text')
    changed['processed_plain_text'] = (processed if isinstance(processed, str) else text) + suffix
    return changed, refs


class PluginMetadata(PluginConfigBase):
    config_version: str = '1.1.0'


class Settings(PluginConfigBase):
    plugin: PluginMetadata = Field(default_factory=PluginMetadata)
    enabled: bool = Field(True, description='开启圣经原句自动出处标注')
    group_only: bool = Field(True, description='仅处理 QQ 群聊，关闭后同时处理 QQ 私聊')
    min_chars: int = Field(5, ge=2, le=30, description='匹配片段的最少字数，不含标点空格；整条回复不足此字数时，须完整命中该短回复')
    partial_match: bool = Field(True, description='部分命中即可：回复中任意连续片段与同一节经文逐字一致便追加出处')
    match_sentences: bool = Field(True, description='关闭部分匹配时，是否也检查由句号问号感叹号分号或换行分隔的完整句子')
    max_references: int = Field(1, ge=1, le=3, description='最多追加几条出处；优先最长片段，同长度优先贴近经节首尾，再按经卷顺序')


class BibleCitationPlugin(MaiBotPlugin):
    config_model: ClassVar = Settings
    config_widgets = [
        ConfigWidget(id='status', kind='status', label='本地经文匹配状态'),
        ConfigWidget(id='self_test', kind='action', label='本机自检（不向群发消息）'),
    ]

    def __init__(self):
        super().__init__()
        self.index = None
        self.checked = 0
        self.matched = 0
        self.last_references = []
        self.test_status = '尚未执行自检'

    async def on_load(self):
        self.index = await asyncio.to_thread(VerseIndex, Path(__file__).parent / 'assets' / 'verses.json')

    async def on_unload(self):
        self.index = None

    async def on_config_update(self, scope: str, config_data: dict, version: str):
        # The SDK applies self.config before invoking this required callback.
        pass

    @HookHandler('send_service.before_send', name='bible_exact_citation',
                 description='发送前本地逐字匹配圣经原句，追加经卷章节',
                 mode=HookMode.BLOCKING, order=HookOrder.LATE,
                 timeout_ms=3000, error_policy=ErrorPolicy.SKIP)
    async def before_send(self, message=None, **kwargs):
        if self.index is None or not isinstance(message, dict):
            return {'action': 'continue'}
        self.checked += 1
        changed, refs = await asyncio.to_thread(annotate_message, message, self.index, self.config)
        if changed is None:
            return {'action': 'continue'}
        if refs:
            self.matched += 1
            self.last_references = refs
        return {'action': 'continue', 'modified_kwargs': {**kwargs, 'message': changed}}

    async def get_config_widget_state(self):
        count = len(self.index.rows) if self.index is not None else 0
        state = f'本地简体和合本：66 卷 / {count:,} 条经文；仅本地匹配，不调用模型。\n本次运行检查 {self.checked} 条，标注 {self.matched} 条（含本机自检）。\n{self.test_status}'
        if self.last_references:
            state += '\n最近命中出处：' + '、'.join(self.last_references)
        return {'status': ConfigWidgetState(text=state, tone='error' if self.test_status.startswith('自检失败') else ('success' if count else 'warning')),
                'self_test': ConfigWidgetState(disabled=self.index is None)}

    async def on_config_widget_action(self, action_id: str):
        if action_id != 'self_test' or self.index is None:
            raise ValueError('Unsupported action or index unavailable')
        self.test_status = '自检进行中，尚未通过'
        sample = {'platform':'qq', 'message_info':{'group_info':{'group_id':'local-test','group_name':'local-test'}},
                  'raw_message':[{'type':'text','data':'日光之下并无新事。'}],
                  'processed_plain_text':'日光之下并无新事。'}
        changed, refs = annotate_message(sample, self.index, Settings())
        if not (refs == ['传道书 1:9'] and changed['raw_message'][-1]['data'] == '\n--出自（传道书 1:9）'):
            self.test_status = '自检失败：固定经文与出处不一致'
            raise RuntimeError(self.test_status)
        if not (not self.index.find('今晚吃火锅还是烧烤呀')):
            self.test_status = '自检失败：无关文字被误匹配'
            raise RuntimeError(self.test_status)
        if not (not self.index.find('好的')):
            self.test_status = '自检失败：过短查询被误匹配'
            raise RuntimeError(self.test_status)
        partial = await self.before_send(message={**sample, 'raw_message': [{'type': 'text', 'data': '那“请你给我水喝”也算命中啊'}], 'processed_plain_text': '那“请你给我水喝”也算命中啊'}, typing=False, set_reply=False, storage_message=True, show_log=False)
        if not ('创世记 24:45' in partial['modified_kwargs']['message']['raw_message'][-1]['data']):
            self.test_status = '自检失败：部分经文出处不一致'
            raise RuntimeError(self.test_status)
        if not (partial['modified_kwargs']['typing'] is False):
            self.test_status = '自检失败：发送参数未保留'
            raise RuntimeError(self.test_status)
        short, _ = annotate_message({**sample, 'raw_message': [{'type': 'text', 'data': '给我水喝'}], 'processed_plain_text': '给我水喝'}, self.index, Settings())
        if not (short is not None):
            self.test_status = '自检失败：短回复例外失效'
            raise RuntimeError(self.test_status)
        long_short, _ = annotate_message({**sample, 'raw_message': [{'type': 'text', 'data': '那给我水喝也算命中啊'}], 'processed_plain_text': '那给我水喝也算命中啊'}, self.index, Settings())
        if not (long_short is None):
            self.test_status = '自检失败：长回复门槛失效'
            raise RuntimeError(self.test_status)
        preserved = partial['modified_kwargs']['message']['raw_message'][0]['data']
        if not (preserved == '那“请你给我水喝”也算命中啊'):
            self.test_status = '自检失败：实际回复正文被改写'
            raise RuntimeError(self.test_status)
        punctuation_only = await self.before_send(message={**sample, 'raw_message': [{'type': 'text', 'data': '？！'}], 'processed_plain_text': '？！'})
        if not (punctuation_only == {'action': 'continue'}):
            self.test_status = '自检失败：纯标点回复被拦截'
            raise RuntimeError(self.test_status)
        self.test_status = '自检通过：仅查询副本清理标点，逗号句号保留为边界；实际回复正文完整保留；5 字门槛及短回复例外正常。'



def create_plugin():
    return BibleCitationPlugin()
