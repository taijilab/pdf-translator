from deep_translator import GoogleTranslator
import fitz  # PyMuPDF
import requests
import io
import re
import os
import time
import html
import json
from difflib import SequenceMatcher

class PDFTranslator:
    SYSTEM_FONT_CANDIDATES = [
        ('ui_cjk', '/System/Library/Fonts/Supplemental/Songti.ttc'),
        ('ui_heiti', '/System/Library/Fonts/STHeiti Light.ttc'),
        ('ui_unicode', '/Library/Fonts/Arial Unicode.ttf'),
    ]

    # API价格（每1M tokens的价格，单位：美元）
    PRICING = {
        'google': {
            'input': 0,
            'output': 0
        },
        'deepseek': {
            'input': 0.14,  # $0.14 per 1M tokens
            'output': 0.28  # $0.28 per 1M tokens
        },
        'zhipu': {
            'input': 0.5,   # GLM-4-Flash: ~$0.5 per 1M tokens
            'output': 0.5
        },
        'openrouter': {
            'input': 0.14,  # DeepSeek via OpenRouter: $0.14 per 1M tokens
            'output': 0.28  # DeepSeek via OpenRouter: $0.28 per 1M tokens
        },
        'kimi': {
            'input': 1.2,   # Moonshot v1-auto via OpenRouter: ~$1.2 per 1M tokens
            'output': 1.2   # Moonshot v1-auto via OpenRouter: ~$1.2 per 1M tokens
        },
        'gpt': {
            'input': 2.0,   # GPT-4.1 via OpenRouter: ~$2.0 per 1M tokens
            'output': 8.0   # GPT-4.1 via OpenRouter: ~$8.0 per 1M tokens
        }
    }

    def __init__(self, api_type='google', api_key=None, progress_callback=None, log_callback=None, cancel_callback=None, glossary_terms=None):
        self.api_type = api_type
        self.api_key = api_key
        self.progress_callback = progress_callback
        self.log_callback = log_callback
        self.cancel_callback = cancel_callback
        self.input_tokens = 0
        self.output_tokens = 0
        self.translator = None  # 初始化为None
        self._translation_cache = {}  # 翻译缓存：(text, src, tgt) -> translated
        self._session = requests.Session()  # 复用 HTTP 连接，减少握手开销
        self.glossary_terms = self._normalize_glossary_terms(glossary_terms or [])

        # 只在需要时初始化translator
        if self.api_type == 'google':
            self._setup_translator()

    def _setup_translator(self):
        """设置翻译器"""
        # deep-translator 不需要预先设置translator实例
        pass

    def _normalize_glossary_terms(self, terms):
        seen = set()
        normalized = []
        for term in terms:
            if not term:
                continue
            clean = " ".join(str(term).strip().split())
            if len(clean) < 2:
                continue
            key = clean.lower()
            if key in seen:
                continue
            seen.add(key)
            normalized.append(clean)
        normalized.sort(key=len, reverse=True)
        return normalized

    def _is_url_only_text(self, text):
        if not text:
            return False
        normalized = " ".join(text.split())
        return bool(re.fullmatch(r'(https?://[^\s]+|www\.[^\s]+)', normalized))

    def _is_translatable(self, text):
        """判断文本块是否需要翻译；跳过纯数字/符号/极短文本，节省 API 调用"""
        stripped = text.strip()
        if len(stripped) <= 1:
            return False
        # 纯数字、标点、空白、数学符号、特殊字符组成的块跳过
        if re.match(r'^[\d\s\.,\-\+\(\)\[\]\{\}\/\\\|：；。、！？，""''「」【】・…—–×÷=<>%°#@&*^~`\'\"]+$', stripped):
            return False
        return True

    def _group_short_blocks(self, all_blocks, max_group_chars=800, short_threshold=150):
        """将连续短文本块合并成批次，减少 API 调用次数。
        返回 list of ('single'|'batch', [block_info, ...])。"""
        SEPARATOR = "\n---SPLIT---\n"
        sep_len = len(SEPARATOR)
        groups = []
        current_group = []
        current_len = 0

        for block in all_blocks:
            text_len = len(block['text'].strip())
            layout_hint = block.get('font_info', {}).get('layout_hint', 'body')
            if layout_hint != 'body':
                if current_group:
                    groups.append(('batch', current_group))
                    current_group, current_len = [], 0
                groups.append(('single', [block]))
                continue
            if text_len >= short_threshold:
                # 长文本块：先把当前积累的短块入队，再单独处理
                if current_group:
                    groups.append(('batch', current_group))
                    current_group, current_len = [], 0
                groups.append(('single', [block]))
            else:
                # 短文本块：尝试合并到当前批次
                added_len = text_len + (sep_len if current_group else 0)
                if current_group and current_len + added_len > max_group_chars:
                    groups.append(('batch', current_group))
                    current_group, current_len = [], 0
                    added_len = text_len
                current_group.append(block)
                current_len += added_len

        if current_group:
            groups.append(('batch', current_group))

        return groups

    def _should_use_fast_block_extraction(self, total_pages, file_size_mb):
        """大文件优先使用 blocks 提取，减少 dict/span 解析开销。"""
        return total_pages >= 30 or file_size_mb >= 8

    def _should_emit_detail_log(self, current, total):
        """大任务只抽样输出块级原文/译文日志，避免 SSE 和前端渲染过载。"""
        if total <= 200:
            return True
        if current <= 8 or current > total - 8:
            return True
        step = 25 if total <= 1000 else 50
        return current % step == 0

    def _should_emit_progress_update(self, current, total):
        """大任务降低进度推送频率，减少队列和前端更新压力。"""
        if total <= 200:
            return True
        if current <= 5 or current == total:
            return True
        step = 10 if total <= 1000 else 20
        return current % step == 0

    def _build_text_page_runs(self, text_pages, max_chars=12000):
        """把连续纯文字页合并成翻译 run，减少 API 调用。"""
        runs = []
        current_run = []
        current_chars = 0

        for page_info in text_pages:
            text_len = len(page_info['text'])
            is_consecutive = not current_run or page_info['page_num'] == current_run[-1]['page_num'] + 1

            if current_run and (not is_consecutive or current_chars + text_len > max_chars):
                runs.append(current_run)
                current_run = []
                current_chars = 0

            current_run.append(page_info)
            current_chars += text_len

        if current_run:
            runs.append(current_run)

        return runs

    def _normalize_extracted_block_text(self, text):
        """规范化提取出的块文本，减少字体私有区符号导致的乱码。"""
        if not text:
            return text
        return text.replace('\uf0a7', '•').replace('', '•')

    def _is_page_footer_text(self, text):
        stripped = " ".join(text.strip().split())
        return bool(re.match(r'^Page\s+\d+\s+of\s+\d+$', stripped, re.IGNORECASE))

    def _translate_special_text(self, text, target_lang):
        """对页码等固定结构做本地稳定转换，避免交给模型后被改乱。"""
        stripped = " ".join(text.strip().split())
        match = re.match(r'^Page\s+(\d+)\s+of\s+(\d+)$', stripped, re.IGNORECASE)
        if match:
            page_no, total_pages = match.groups()
            if target_lang == 'zh':
                return f'第 {page_no} 页，共 {total_pages} 页'
            return stripped
        return None

    def _contains_long_english_run(self, text):
        normalized = " ".join(text.split())
        return bool(re.search(r'(?:[A-Za-z][A-Za-z\'’\-]{1,}(?:[\s\-/&,;:().]+|$)){6,}', normalized))

    def _contains_meta_translation_note(self, text):
        normalized = "\n".join(line.strip() for line in text.splitlines() if line.strip())
        patterns = [
            r'严格遵循',
            r'严格遵照',
            r'处理原则',
            r'处理方式',
            r'符合以下',
            r'未添加额外说明',
            r'完全符合要求',
            r'交办要求',
            r'解释性说明',
            r'全数转换为',
            r'不保留任何英文',
            r'章节编号采用',
            r'括号使用',
            r'保留原排版',
            r'英文句子彻底转化',
            r'根据硬性要求',
            r'注释[:：]',
            r'(?m)^\d+\.\s+',
        ]
        return any(re.search(pattern, normalized, flags=re.IGNORECASE) for pattern in patterns)

    def _has_repeated_lines(self, text):
        lines = [" ".join(line.split()) for line in text.splitlines() if line.strip()]
        if len(lines) < 4:
            return False
        seen = {}
        for line in lines:
            key = line.lower()
            seen[key] = seen.get(key, 0) + 1
            if seen[key] >= 2 and len(line) >= 12:
                return True
        return False

    def _translate_text_openrouter_force_chinese(self, text, target_lang='zh'):
        """OpenRouter 二次强制重翻，尽量消除长英文残留。"""
        text = self._clean_text(text)
        url = "https://openrouter.ai/api/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json; charset=utf-8",
            "HTTP-Referer": "https://pdf-translator.local",
        }

        lang_names = {
            'en': '英语',
            'zh': '中文',
            'ja': '日语',
            'ko': '韩语',
            'fr': '法语',
            'de': '德语',
            'es': '西班牙语',
            'ru': '俄语',
            'ar': '阿拉伯语'
        }
        target_lang_name = lang_names.get(target_lang, target_lang)
        prompt = (
            f"将下面内容完整翻译成{target_lang_name}。\n"
            "硬性要求：\n"
            "1. 除 URL、页码、明确品牌名外，不允许保留英文句子。\n"
            "2. 如果输出中还有完整英文短语或英文句子，视为失败。\n"
            "3. 保留段落、项目符号和换行结构。\n"
            "4. 只返回译文本身，不要解释。\n\n"
            f"{text}"
        )
        data = {
            "model": "deepseek/deepseek-chat",
            "messages": [
                {"role": "system", "content": "你是严格的中文翻译引擎。必须输出完整中文译文，不能保留整句英文。"},
                {"role": "user", "content": prompt}
            ],
            "temperature": 0
        }
        response = self._session.post(url, headers=headers, json=data, timeout=75)
        result = response.json()
        if 'choices' in result and result['choices']:
            return self._normalize_translated_text(result['choices'][0]['message']['content'].strip())
        raise Exception(f"API Error: {result}")

    def _split_text_for_strict_retry(self, text, max_chars=260):
        """把长文本拆成更稳的小段，降低模型漏译后半段的概率。"""
        chunks = []
        current = ""
        paragraphs = [part for part in re.split(r'(\n+)', text) if part]

        def flush():
            nonlocal current
            if current.strip():
                chunks.append(current.strip())
            current = ""

        for part in paragraphs:
            if part.startswith('\n'):
                if len(current) + len(part) <= max_chars:
                    current += part
                else:
                    flush()
                continue

            sentences = re.split(r'(?<=[\.\?!。！？:：;；])\s+', part)
            for sentence in sentences:
                sentence = sentence.strip()
                if not sentence:
                    continue
                addition = sentence if not current else f"{current} {sentence}"
                if len(addition) <= max_chars:
                    current = addition
                else:
                    flush()
                    if len(sentence) <= max_chars:
                        current = sentence
                    else:
                        for i in range(0, len(sentence), max_chars):
                            chunks.append(sentence[i:i + max_chars])
        flush()
        return chunks or [text]

    def _strict_translate_text(self, text, source_lang, target_lang):
        """严格翻译：失败时继续缩块重试，而不是直接回退原文。"""
        special_text = self._translate_special_text(text, target_lang)
        if special_text is not None:
            return special_text

        if self.api_type == 'openrouter':
            direct = self._translate_text_openrouter_force_chinese(text, target_lang)
            if not self._should_retry_translation(text, direct, target_lang):
                return direct

            translated_chunks = []
            for chunk in self._split_text_for_strict_retry(text):
                translated_chunks.append(self._translate_text_openrouter_force_chinese(chunk, target_lang))
            return self._normalize_translated_text("\n".join(translated_chunks))

        if self.api_type == 'google':
            return self._translate_text_google(text, source_lang, target_lang)

        return self._translate_text(text, source_lang, target_lang)

    def _is_toc_like_line(self, text):
        normalized = " ".join(text.strip().split())
        return bool(
            re.search(r'\b\d+$', normalized) or
            re.match(r'^(Chapter|Appendix|Table of Contents|Disclaimers|Welcome|How to Use)', normalized, re.IGNORECASE)
        )

    def _is_toc_like_page(self, page_blocks):
        if not page_blocks:
            return False
        joined = "\n".join(block['text'] for block in page_blocks)
        if 'TABLE OF CONTENTS' in joined.upper():
            return True
        toc_lines = sum(1 for block in page_blocks if self._is_toc_like_line(block['text']))
        return toc_lines >= max(5, int(len(page_blocks) * 0.45))

    def _should_retry_translation(self, source_text, translated_text, target_lang):
        if target_lang != 'zh':
            return False
        source = " ".join(source_text.strip().split())
        translated = " ".join(translated_text.strip().split())
        if not source or not translated:
            return False

        chinese_chars = len(re.findall(r'[\u4e00-\u9fff]', translated))
        ascii_chars = len(re.findall(r'[A-Za-z]', translated))
        source_ascii_chars = len(re.findall(r'[A-Za-z]', source))
        similarity = SequenceMatcher(None, source.lower(), translated.lower()).ratio()

        if similarity >= 0.72 and chinese_chars < max(8, len(translated) * 0.08):
            return True
        if self._contains_long_english_run(translated):
            return True
        if ascii_chars > chinese_chars * 4 and similarity >= 0.55:
            return True
        if source_ascii_chars >= 12 and chinese_chars == 0 and ascii_chars >= max(10, source_ascii_chars * 0.6):
            return True
        if source_ascii_chars >= 24 and chinese_chars < 6 and similarity >= 0.35:
            return True
        if source_ascii_chars >= 40 and ascii_chars >= 24:
            return True
        if self._has_repeated_lines(translated):
            return True
        if self._contains_meta_translation_note(translated):
            return True
        return False

    def _ensure_target_translation(self, source_text, translated_text, source_lang, target_lang):
        translated_text = self._normalize_translated_text(translated_text)
        if not self._should_retry_translation(source_text, translated_text, target_lang):
            return translated_text

        retried = translated_text
        for _ in range(2):
            if self.api_type == 'google':
                retried = self._translate_text_google(source_text, source_lang, target_lang)
            elif self.api_type == 'openrouter':
                retried = self._translate_text_openrouter_force_chinese(source_text, target_lang)
            else:
                retried = self._translate_text(source_text, source_lang, target_lang)

            retried = self._normalize_translated_text(retried if retried else translated_text)
            if not self._should_retry_translation(source_text, retried, target_lang):
                return retried

        return retried

    def _normalize_translated_text(self, text):
        if not text:
            return text

        text = text.replace('\x00', '')
        text = text.replace('?', '？') if re.search(r'[A-Za-z]\?[A-Za-z]', text) else text
        text = text.replace('�', '')
        def collapse_spaced_letters(match):
            fragment = match.group(0)
            pieces = fragment.split()
            if len(pieces) < 3 or any(not piece.isalpha() or len(piece) > 2 for piece in pieces):
                return fragment
            collapsed = ''.join(pieces)
            collapsed = re.sub(r'(?<=[a-z])(?=[A-Z])', ' ', collapsed)
            return collapsed

        def collapse_spaced_digits(match):
            pieces = match.group(0).split()
            return ''.join(pieces)

        text = text.replace('•  ', '• ')
        text = re.sub(r'(?<!\S)(?:[A-Za-z]{1,2}\s+){2,}[A-Za-z]{1,2}(?!\S)', collapse_spaced_letters, text)
        text = re.sub(r'(?<!\S)(?:\d\s+){1,}\d(?!\S)', collapse_spaced_digits, text)
        text = re.sub(r'([A-Za-z0-9])\s+\.\s+(?=[A-Za-z0-9])', r'\1.', text)
        text = re.sub(r'([:/._-])\s+(?=[A-Za-z0-9])', r'\1', text)
        text = re.sub(r'(?<=[A-Za-z0-9])\s+([:/._-])', r'\1', text)
        text = re.sub(r'\bW\s+W\s+W\b', 'WWW', text, flags=re.IGNORECASE)
        text = re.sub(r'(?i)w\s+w\s+w\s*\.\s*', 'www.', text)
        text = re.sub(r'\s+\)', ')', text)
        text = re.sub(r'\(\s+', '(', text)
        text = re.sub(r'\n?注释[:：]\n?(?:\d+\.\s*.*(?:\n|$)){1,6}', '\n', text, flags=re.IGNORECASE)
        text = re.sub(r'\n?说明[:：]\n?(?:[-•\d].*(?:\n|$)){1,8}', '\n', text, flags=re.IGNORECASE)
        text = re.sub(r'(?s)[（(]说明[:：].*$', '', text)
        text = re.sub(r'(?s)说明[:：].*$', '', text)
        text = re.sub(r'[\(（]保留原排版.*?[\)）]', '', text)
        text = re.sub(r'(?m)^(?:注[:：]|输出[:：]?|翻译[:：]?|完全符合要求.*|严格遵循要求.*|章节编号.*|括号使用.*|空行结构.*)$', '', text)
        text = re.sub(r'(?m)^\d+\.\s*(?:严格遵循要求.*|确保未出现.*|数字.*|空行结构.*|完全符合.*)$', '', text)
        text = re.sub(r'（注：.*?）', '', text, flags=re.DOTALL)
        text = re.sub(r'\(注：.*?\)', '', text, flags=re.DOTALL)
        text = re.sub(r'\n{3,}', '\n\n', text)
        return text.strip()

    def _register_page_fonts(self, page):
        registered = []
        for font_name, font_path in self.SYSTEM_FONT_CANDIDATES:
            if not os.path.exists(font_path):
                continue
            try:
                page.insert_font(fontname=font_name, fontfile=font_path)
                registered.append(font_name)
            except Exception:
                continue
        return registered

    def _pdf_color_to_rgb(self, color_value):
        if isinstance(color_value, tuple) and len(color_value) == 3:
            return color_value
        if not isinstance(color_value, int):
            return (0, 0, 0)
        r = ((color_value >> 16) & 255) / 255.0
        g = ((color_value >> 8) & 255) / 255.0
        b = (color_value & 255) / 255.0
        return (r, g, b)

    def _looks_light_color(self, color_value):
        r, g, b = self._pdf_color_to_rgb(color_value)
        return (r + g + b) / 3 >= 0.72

    def _extract_span_style_hints(self, page):
        span_hints = []
        try:
            text_dict = page.get_text("dict")
        except Exception:
            return span_hints

        for block in text_dict.get('blocks', []):
            if block.get('type') != 0:
                continue
            for line in block.get('lines', []):
                for span in line.get('spans', []):
                    text = self._normalize_extracted_block_text(span.get('text', ''))
                    if not text:
                        continue
                    span_hints.append({
                        'rect': fitz.Rect(span['bbox']),
                        'text': text,
                        'font': span.get('font', 'helv'),
                        'size': span.get('size', 11),
                        'flags': span.get('flags', 0),
                        'color': span.get('color', 0),
                    })
        return span_hints

    def _apply_block_style_hints(self, page_blocks, span_hints):
        if not page_blocks or not span_hints:
            return page_blocks

        for block in page_blocks:
            block_rect = block['rect']
            overlaps = []
            for span in span_hints:
                inter = block_rect & span['rect']
                if inter.is_empty:
                    continue
                overlaps.append((inter.get_area(), span))
            if not overlaps:
                continue

            overlaps.sort(key=lambda item: (-item[0], item[1]['rect'].y0, item[1]['rect'].x0))
            best_span = overlaps[0][1]
            block.setdefault('font_info', {}).update({
                'font': best_span.get('font', 'helv'),
                'size': best_span.get('size', 11),
                'flags': best_span.get('flags', 0),
                'color': best_span.get('color', 0),
            })
        return page_blocks

    def _copy_vector_drawings(self, source_page, target_page):
        try:
            drawings = source_page.get_drawings()
        except Exception:
            return

        for drawing in drawings:
            rect = drawing.get('rect')
            if not rect:
                continue

            fill = drawing.get('fill')
            color = drawing.get('color')
            width = drawing.get('width') or 0
            fill_opacity = drawing.get('fill_opacity', 1.0)
            stroke_opacity = drawing.get('stroke_opacity', 1.0)
            drawing_type = drawing.get('type')

            try:
                if drawing_type in ('f', 'fs', 'sf'):
                    target_page.draw_rect(
                        rect,
                        color=color,
                        fill=fill,
                        width=width,
                        fill_opacity=fill_opacity,
                        stroke_opacity=stroke_opacity,
                        overlay=True,
                    )
                elif drawing_type == 's':
                    target_page.draw_rect(
                        rect,
                        color=color,
                        width=width,
                        stroke_opacity=stroke_opacity,
                        overlay=True,
                    )
            except Exception:
                continue

    def _build_font_candidates(self, registered_fonts, translated_text, original_font, is_bold, is_italic, is_chinese):
        has_latin = bool(re.search(r'[A-Za-z0-9]', translated_text))

        if is_chinese:
            base = []
            if has_latin:
                base.extend([name for name in ('ui_unicode', 'ui_heiti', 'ui_cjk') if name in registered_fonts])
            else:
                base.extend([name for name in ('ui_unicode', 'ui_heiti', 'ui_cjk') if name in registered_fonts])
            base.extend(['china-s', 'china-t', 'china-ss'])
            return base

        if original_font and 'Times' in original_font:
            return ['times-roman', 'times-italic', 'times-bold', 'helv']
        if original_font and 'Helv' in original_font:
            return ['helv', 'helv-bold', 'times-roman']
        if original_font and 'Courier' in original_font:
            return ['courier', 'courier-bold', 'helv']
        if is_bold and is_italic:
            return ['helv-bolditalic', 'helv-bold', 'helv']
        if is_bold:
            return ['helv-bold', 'helv', 'times-bold']
        if is_italic:
            return ['helv-italic', 'helv', 'times-italic']
        return ['helv', 'times-roman', 'courier']

    def _classify_block_for_merge(self, block):
        text = block['text'].strip()
        normalized = " ".join(text.split())
        rect = block['rect']
        width = max(1, rect.width)
        x0 = rect.x0

        is_footer = self._is_page_footer_text(text)
        starts_bullet = bool(re.match(r'^[•●◆◾▪◦■□▪\-\*\d]+\s+', text))
        bullet_heading = starts_bullet and normalized.endswith(':') and len(normalized) <= 80
        is_continuation = (not starts_bullet) and x0 >= 100 and len(normalized) > 20
        is_caption = bool(re.match(r'^(Chapter\s+\d+|Variations|Wrist Mobility Stretches|Wrist Relief Position|What is|Introduction to)', normalized, re.IGNORECASE))
        is_heading = (
            (normalized.endswith(':') and len(normalized) <= 80) or
            (len(normalized) <= 40 and normalized.isupper())
        )
        is_short = len(normalized) <= 32

        if is_footer:
            kind = 'footer'
        elif is_caption:
            kind = 'caption'
        elif bullet_heading:
            kind = 'heading'
        elif starts_bullet:
            kind = 'list_item'
        elif is_continuation:
            kind = 'list_cont'
        elif is_heading:
            kind = 'heading'
        elif is_short:
            kind = 'short'
        else:
            kind = 'body'

        return {
            'kind': kind,
            'indent': x0,
            'width': width,
            'starts_bullet': starts_bullet,
        }

    def _merge_page_blocks_for_translation(self, page_num, page_blocks, max_chars=5000, max_vertical_gap=24):
        """把同页相邻文本块合并成更大的翻译区域，减少含图页的翻译单元数量。"""
        if not page_blocks:
            return []

        merged = []
        current = None

        for block in page_blocks:
            if current is None:
                current = dict(block)
                current['merge_meta'] = self._classify_block_for_merge(current)
                continue

            current_rect = current['rect']
            block_rect = block['rect']
            gap = block_rect.y0 - current_rect.y1
            current_meta = current.get('merge_meta', self._classify_block_for_merge(current))
            block_meta = self._classify_block_for_merge(block)
            same_left = abs(block_rect.x0 - current_rect.x0) <= 14
            overlap_width = min(block_rect.x1, current_rect.x1) - max(block_rect.x0, current_rect.x0)
            min_width = max(1, min(block_rect.width, current_rect.width))
            horizontal_overlap_ratio = overlap_width / min_width if overlap_width > 0 else 0
            same_column = same_left and (abs(block_rect.x1 - current_rect.x1) <= 48 or horizontal_overlap_ratio >= 0.75)
            list_same_column = abs(block_rect.x0 - current_rect.x0) <= 24 or block_rect.x0 >= current_rect.x0 + 8
            merged_len = len(current['text']) + len(block['text'])
            compatible_kind = current_meta['kind'] == 'body' and block_meta['kind'] == 'body'
            compatible_list = (
                current_meta['kind'] in ('list_item', 'list_cont') and
                block_meta['kind'] == 'list_cont' and
                block_rect.x0 >= current_rect.x0 + 8
            )
            should_merge = (
                compatible_kind and same_column
            ) or (
                compatible_list and list_same_column
            )

            if should_merge and gap <= max_vertical_gap and merged_len <= max_chars:
                joiner = "\n\n" if gap > 10 else "\n"
                current['text'] = f"{current['text'].rstrip()}{joiner}{block['text'].lstrip()}"
                current['rect'] = fitz.Rect(
                    min(current_rect.x0, block_rect.x0),
                    min(current_rect.y0, block_rect.y0),
                    max(current_rect.x1, block_rect.x1),
                    max(current_rect.y1, block_rect.y1)
                )
                current['merge_meta'] = current_meta if compatible_kind else {
                    **current_meta,
                    'kind': 'list_item'
                }
            else:
                current.pop('merge_meta', None)
                merged.append(current)
                current = dict(block)
                current['merge_meta'] = block_meta

        if current is not None:
            current.pop('merge_meta', None)
            merged.append(current)

        for idx, block in enumerate(merged):
            block['page_num'] = page_num
            block['block_idx'] = idx

        return merged

    def analyze_pdf(self, input_path):
        """分析PDF文件，返回页数、字数、语言等信息"""
        try:
            doc = fitz.open(input_path)
            total_pages = len(doc)

            # 提取所有文本
            parts = []
            for page in doc:
                text = page.get_text("text")
                parts.append(text)
            all_text = " ".join(parts)

            doc.close()

            # 统计字数
            char_count = len(all_text.strip())
            word_count = len(all_text.split())

            # 检测主要语言
            chinese_chars = len(re.findall(r'[\u4e00-\u9fff]', all_text))
            english_chars = len(re.findall(r'[a-zA-Z]', all_text))
            japanese_chars = len(re.findall(r'[\u3040-\u309f\u30a0-\u30ff]', all_text))
            korean_chars = len(re.findall(r'[\uac00-\ud7af]', all_text))

            total_chars = len(all_text)

            # 判断主要语言
            if chinese_chars > total_chars * 0.3:
                detected_lang = 'zh'
                lang_name = '中文'
            elif english_chars > total_chars * 0.3:
                detected_lang = 'en'
                lang_name = '英语'
            elif japanese_chars > total_chars * 0.2:
                detected_lang = 'ja'
                lang_name = '日语'
            elif korean_chars > total_chars * 0.2:
                detected_lang = 'ko'
                lang_name = '韩语'
            else:
                detected_lang = 'auto'
                lang_name = '混合/其他'

            # 估算总tokens
            total_tokens = self._estimate_tokens(all_text)

            return {
                'total_pages': total_pages,
                'char_count': char_count,
                'word_count': word_count,
                'detected_lang': detected_lang,
                'lang_name': lang_name,
                'total_tokens': total_tokens
            }

        except Exception as e:
            print(f'Error analyzing PDF: {e}')
            return {
                'total_pages': 0,
                'char_count': 0,
                'word_count': 0,
                'detected_lang': 'auto',
                'lang_name': '未知',
                'total_tokens': 0
            }

    def _clean_text(self, text):
        """清理文本中的特殊Unicode字符，避免编码错误"""
        if not text:
            return text

        # 替换可能有问题的Unicode空格字符为普通空格
        text = text.replace('\u00a0', ' ')  # 不换行空格
        text = text.replace('\u202f', ' ')  # 窄不换行空格
        text = text.replace('\u2009', ' ')  # 窄空格
        text = text.replace('\u200a', ' ')  # 极窄空格
        text = text.replace('\u200b', '')   # 零宽空格
        text = text.replace('\u200c', '')   # 零宽非连接符
        text = text.replace('\u200d', '')   # 零宽连接符
        text = text.replace('\ufeff', '')   # 零宽非断空格

        # 替换其他控制字符（保留换行符和制表符）
        import unicodedata
        text = ''.join(char for char in text
                       if unicodedata.category(char)[0] != 'C'
                       or char in '\n\r\t')

        return text

    def _protect_formatting(self, text, use_glossary=True):
        """保护特殊格式字符，用占位符替换，翻译后再恢复"""
        if not text:
            return text, {}

        import re
        placeholders = {}
        idx = 0

        # 定义需要保护的特殊格式
        # 项目符号
        bullet_patterns = [
            r'[●◆◾▪◦■□▪•]',  # 各种项目符号
            r'\uf0b7',  # Wingdings 项目符号
        ]

        # 网址链接
        url_pattern = r'(https?://[^\s]+|www\.[^\s]+)'

        # 注册商标等符号
        trademark_patterns = [
            r'®',
            r'©',
            r'™',
        ]

        # 处理项目符号
        def replace_bullets(match):
            nonlocal idx, placeholders
            placeholder = f'__BULLET_{idx}__'
            placeholders[placeholder] = match.group(0)
            idx += 1
            return placeholder

        for pattern in bullet_patterns:
            text = re.sub(pattern, replace_bullets, text)

        # 处理网址
        def replace_urls(match):
            nonlocal idx, placeholders
            placeholder = f'__URL_{idx}__'
            placeholders[placeholder] = match.group(0)
            idx += 1
            return placeholder

        text = re.sub(url_pattern, replace_urls, text)

        # 处理商标等符号
        def replace_trademarks(match):
            nonlocal idx, placeholders
            placeholder = f'__SYM_{idx}__'
            placeholders[placeholder] = match.group(0)
            idx += 1
            return placeholder

        for pattern in trademark_patterns:
            text = re.sub(pattern, replace_trademarks, text)

        # 处理术语库，避免高频专业术语被翻译
        if use_glossary:
            for term in self.glossary_terms:
                escaped_term = re.escape(term)
                placeholder = f'__TERM_{idx}__'
                replaced_text, count = re.subn(escaped_term, placeholder, text, count=1, flags=re.IGNORECASE)
                while count:
                    placeholders[placeholder] = term
                    idx += 1
                    text = replaced_text
                    placeholder = f'__TERM_{idx}__'
                    replaced_text, count = re.subn(escaped_term, placeholder, text, count=1, flags=re.IGNORECASE)

        return text, placeholders

    def _restore_formatting(self, text, placeholders):
        """恢复被保护的特殊格式字符"""
        if not placeholders:
            return text

        for placeholder, original in placeholders.items():
            restored = original
            if placeholder.startswith('__BULLET_'):
                restored = '-'
            elif placeholder.startswith('__TERM_') and ' ' in original:
                restored = original.replace(' ', '\u00a0')
            text = text.replace(placeholder, restored)

        return text

    def _estimate_tokens(self, text):
        """估算文本的token数量（粗略估计：中文约1字符=1token，英文约4字符=1token）"""
        if not text:
            return 0

        # 检测是否主要是中文
        chinese_chars = len(re.findall(r'[\u4e00-\u9fff]', text))
        total_chars = len(text)

        if chinese_chars > total_chars * 0.3:
            # 主要是中文
            return len(text)
        else:
            # 主要是英文或其他语言，约4字符=1token
            return max(1, len(text) // 4)

    def _calculate_cost(self):
        """计算预估费用"""
        pricing = self.PRICING.get(self.api_type, {'input': 0, 'output': 0})
        input_cost = (self.input_tokens / 1_000_000) * pricing['input']
        output_cost = (self.output_tokens / 1_000_000) * pricing['output']
        return input_cost + output_cost

    def _check_cancelled(self):
        """检查是否需要取消翻译"""
        if self.cancel_callback:
            self.cancel_callback()

    def _update_progress(self, current, total, message, elapsed_time=0, estimated_remaining=0):
        """更新进度"""
        # 检查是否取消
        self._check_cancelled()

        if self.progress_callback:
            # 添加token和费用信息
            cost = self._calculate_cost()
            progress_data = {
                'current': current,
                'total': total,
                'percentage': int((current / total) * 100) if total > 0 else 0,
                'message': message,
                'input_tokens': self.input_tokens,
                'output_tokens': self.output_tokens,
                'estimated_cost': round(cost, 4),
                'elapsed_time': round(elapsed_time, 1),
                'estimated_remaining': round(estimated_remaining, 1)
            }
            self.progress_callback(progress_data)

    def _add_log(self, message, log_type='info'):
        """添加日志"""
        if self.log_callback:
            self.log_callback(message, log_type)

    def _translate_text_google(self, text, source_lang='auto', target_lang='en'):
        """使用Google Translate翻译（通过deep-translator库）"""
        if not text or not text.strip():
            return text

        special_text = self._translate_special_text(text, target_lang)
        if special_text is not None:
            return special_text

        # 语言代码映射 (deep-translator使用的代码)
        lang_mapping = {
            'zh': 'zh-CN',
            'en': 'en',
            'ja': 'ja',
            'ko': 'ko',
            'fr': 'fr',
            'de': 'de',
            'es': 'es-ES',
            'ru': 'ru',
            'ar': 'ar'
        }

        normalized_target = lang_mapping.get(target_lang, target_lang)
        # deep-translator 使用 'auto' 作为源语言
        normalized_source = 'auto'

        # 保护特殊格式字符（项目符号、链接等）
        protected_text, placeholders = self._protect_formatting(text)

        # 记录输入token（确保总是执行）
        input_tokens = self._estimate_tokens(text)
        self.input_tokens += input_tokens

        # 只在第一页显示详细token信息
        if self.input_tokens == input_tokens:  # 这是第一次调用
            self._add_log(f'开始翻译，文本长度: {len(text)} 字符, 输入tokens: {input_tokens}', 'info')

        max_length = 4000
        if len(protected_text) <= max_length:
            try:
                translated = GoogleTranslator(source=normalized_source, target=normalized_target).translate(protected_text)

                # 恢复被保护的格式字符
                translated = self._restore_formatting(translated, placeholders)

                # 记录输出token
                output_tokens = self._estimate_tokens(translated)
                self.output_tokens += output_tokens

                return translated
            except Exception as e:
                print(f'Translation error: {e}')
                self._add_log(f'Google翻译错误: {str(e)}', 'error')
                return text

        # 分段翻译
        segments = []
        current_segment = ''
        sentences = protected_text.split('. ')

        for sentence in sentences:
            if len(current_segment) + len(sentence) < max_length:
                current_segment += sentence + '. '
            else:
                if current_segment:
                    segments.append(current_segment.strip())
                current_segment = sentence + '. '

        if current_segment:
            segments.append(current_segment.strip())

        translated_segments = []
        for i, segment in enumerate(segments):
            try:
                self._add_log(f'翻译段落 {i+1}/{len(segments)}', 'info')
                translated = GoogleTranslator(source=normalized_source, target=normalized_target).translate(segment)
                # 恢复被保护的格式字符
                translated = self._restore_formatting(translated, placeholders)
                translated_segments.append(translated)

                # 记录输出token
                output_tokens = self._estimate_tokens(translated)
                self.output_tokens += output_tokens

            except Exception as e:
                print(f'Translation error in segment: {e}')
                self._add_log(f'段落翻译错误: {str(e)}', 'error')
                # 恢复后添加原文
                restored_segment = self._restore_formatting(segment, placeholders)
                translated_segments.append(restored_segment)

        return '. '.join(translated_segments)

    def _translate_text_deepseek(self, text, source_lang='auto', target_lang='en'):
        """使用DeepSeek API翻译"""
        if not text or not text.strip():
            return text

        try:
            # 清理文本中的特殊Unicode字符
            text = self._clean_text(text)

            url = "https://api.deepseek.com/v1/chat/completions"
            headers = {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json; charset=utf-8"
            }

            lang_names = {
                'en': '英语',
                'zh': '中文',
                'ja': '日语',
                'ko': '韩语',
                'fr': '法语',
                'de': '德语',
                'es': '西班牙语',
                'ru': '俄语',
                'ar': '阿拉伯语'
            }

            target_lang_name = lang_names.get(target_lang, target_lang)
            prompt = f"请将以下文本翻译成{target_lang_name}，只返回翻译结果：\n\n{text}"

            # 记录输入token
            input_tokens = self._estimate_tokens(text)
            self.input_tokens += input_tokens

            # 只在第一次显示详细token信息
            if self.input_tokens == input_tokens:
                self._add_log(f'开始翻译，文本长度: {len(text)} 字符, 输入tokens: {input_tokens}', 'info')

            data = {
                "model": "deepseek-chat",
                "messages": [
                    {"role": "system", "content": "你是一个专业的翻译助手。"},
                    {"role": "user", "content": prompt}
                ],
                "temperature": 0.3
            }

            response = self._session.post(url, headers=headers, json=data, timeout=60)
            result = response.json()

            if 'choices' in result and len(result['choices']) > 0:
                translated = result['choices'][0]['message']['content'].strip()

                # 记录输出token
                output_tokens = self._estimate_tokens(translated)
                self.output_tokens += output_tokens

                return translated
            else:
                raise Exception(f"API Error: {result}")

        except Exception as e:
            error_msg = str(e)
            print(f'DeepSeek translation error: {error_msg}')

            # 检查是否是认证错误
            if '401' in error_msg or 'auth' in error_msg.lower() or 'Invalid' in error_msg:
                self._add_log('❌ DeepSeek API Key无效！请检查API Key设置', 'error')
                self._add_log('💡 建议：请使用"Google翻译（免费）"选项，无需API Key', 'info')
                self._add_log('💡 或者获取DeepSeek API Key: https://platform.deepseek.com/', 'info')
            else:
                self._add_log(f'DeepSeek翻译错误: {error_msg}', 'error')

            return text

    def _translate_text_zhipu(self, text, source_lang='auto', target_lang='en'):
        """使用智谱AI API翻译"""
        if not text or not text.strip():
            return text

        try:
            # 清理文本中的特殊Unicode字符
            text = self._clean_text(text)

            url = "https://open.bigmodel.cn/api/paas/v4/chat/completions"
            headers = {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json; charset=utf-8"
            }

            lang_names = {
                'en': '英语',
                'zh': '中文',
                'ja': '日语',
                'ko': '韩语',
                'fr': '法语',
                'de': '德语',
                'es': '西班牙语',
                'ru': '俄语',
                'ar': '阿拉伯语'
            }

            target_lang_name = lang_names.get(target_lang, target_lang)
            prompt = f"请将以下文本翻译成{target_lang_name}，只返回翻译结果：\n\n{text}"

            # 记录输入token
            input_tokens = self._estimate_tokens(text)
            self.input_tokens += input_tokens

            data = {
                "model": "GLM-4-Flash",
                "messages": [
                    {"role": "system", "content": "你是一个专业的翻译助手。"},
                    {"role": "user", "content": prompt}
                ],
                "temperature": 0.3
            }

            response = self._session.post(url, headers=headers, json=data, timeout=60)
            result = response.json()

            if 'choices' in result and len(result['choices']) > 0:
                translated = result['choices'][0]['message']['content'].strip()

                # 记录输出token
                output_tokens = self._estimate_tokens(translated)
                self.output_tokens += output_tokens

                return translated
            else:
                raise Exception(f"API Error: {result}")

        except Exception as e:
            error_msg = str(e)
            print(f'Zhipu AI translation error: {error_msg}')

            # 检查是否是认证错误
            if '401' in error_msg or 'auth' in error_msg.lower() or 'Invalid' in error_msg:
                self._add_log('❌ 智谱AI API Key无效！请检查API Key设置', 'error')
                self._add_log('💡 建议：请使用"Google翻译（免费）"选项，无需API Key', 'info')
                self._add_log('💡 或者获取智谱AI API Key: https://open.bigmodel.cn/', 'info')
            else:
                self._add_log(f'智谱AI翻译错误: {error_msg}', 'error')

            return text

    def _translate_text_openrouter(self, text, source_lang='auto', target_lang='en'):
        """使用OpenRouter的DeepSeek API翻译"""
        if not text or not text.strip():
            return text

        try:
            # 清理文本中的特殊Unicode字符
            text = self._clean_text(text)

            url = "https://openrouter.ai/api/v1/chat/completions"
            headers = {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json; charset=utf-8",
                "HTTP-Referer": "https://pdf-translator.local",  # OpenRouter要求
            }

            lang_names = {
                'en': '英语',
                'zh': '中文',
                'ja': '日语',
                'ko': '韩语',
                'fr': '法语',
                'de': '德语',
                'es': '西班牙语',
                'ru': '俄语',
                'ar': '阿拉伯语'
            }

            target_lang_name = lang_names.get(target_lang, target_lang)
            prompt = (
                f"把下面文本翻译成{target_lang_name}。\n"
                "严格要求：\n"
                "1. 只返回译文，不要解释、注释、括号说明或前后缀。\n"
                "2. 保留项目符号、URL、数字、章节号和专有名词格式。\n"
                "3. 如果原文已经是目标语言或不需要翻译，原样返回。\n\n"
                f"{text}"
            )

            # 记录输入token
            input_tokens = self._estimate_tokens(text)
            self.input_tokens += input_tokens

            # 只在第一次显示详细token信息
            if self.input_tokens == input_tokens:
                self._add_log(f'开始翻译，文本长度: {len(text)} 字符, 输入tokens: {input_tokens}', 'info')

            data = {
                "model": "deepseek/deepseek-chat",
                "messages": [
                    {"role": "system", "content": "你是严格的翻译引擎，只输出译文本身。禁止添加解释、备注、示例、总结或引号。"},
                    {"role": "user", "content": prompt}
                ],
                "temperature": 0
            }

            response = self._session.post(url, headers=headers, json=data, timeout=60)
            result = response.json()

            if 'choices' in result and len(result['choices']) > 0:
                translated = result['choices'][0]['message']['content'].strip()
                translated = self._normalize_translated_text(translated)

                if self._should_retry_translation(text, translated, target_lang):
                    retry_prompt = (
                        f"把下面英文完整翻译成{target_lang_name}。\n"
                        "严格要求：\n"
                        "1. 除 URL、页码、专有名词外，不得保留整句英文。\n"
                        "2. 只返回译文，不要解释。\n"
                        "3. 保留项目符号和原有换行。\n\n"
                        f"{text}"
                    )
                    retry_data = {
                        "model": "deepseek/deepseek-chat",
                        "messages": [
                            {"role": "system", "content": "你是严格的翻译引擎，必须输出完整中文译文。"},
                            {"role": "user", "content": retry_prompt}
                        ],
                        "temperature": 0
                    }
                    retry_response = self._session.post(url, headers=headers, json=retry_data, timeout=60)
                    retry_result = retry_response.json()
                    if 'choices' in retry_result and retry_result['choices']:
                        translated = self._normalize_translated_text(
                            retry_result['choices'][0]['message']['content'].strip()
                        )

                # 记录输出token
                output_tokens = self._estimate_tokens(translated)
                self.output_tokens += output_tokens

                return translated
            else:
                raise Exception(f"API Error: {result}")

        except Exception as e:
            error_msg = str(e)
            print(f'OpenRouter translation error: {error_msg}')

            # 检查是否是认证错误
            if '401' in error_msg or 'auth' in error_msg.lower() or 'cookie' in error_msg.lower():
                self._add_log('❌ OpenRouter API Key无效或未正确设置！', 'error')
                self._add_log('💡 建议：请使用"Google翻译（免费）"选项，无需API Key', 'info')
                self._add_log('💡 或者在OpenRouter获取有效的API Key: https://openrouter.ai/keys', 'info')
            else:
                self._add_log(f'OpenRouter翻译错误: {error_msg}', 'error')

            return text

    def _translate_text_openrouter_batch(self, texts, source_lang='auto', target_lang='en'):
        """使用 OpenRouter 批量翻译短文本块，要求返回稳定的 XML 结构。"""
        if not texts:
            return []

        url = "https://openrouter.ai/api/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json; charset=utf-8",
            "HTTP-Referer": "https://pdf-translator.local",
        }

        lang_names = {
            'en': '英语',
            'zh': '中文',
            'ja': '日语',
            'ko': '韩语',
            'fr': '法语',
            'de': '德语',
            'es': '西班牙语',
            'ru': '俄语',
            'ar': '阿拉伯语'
        }

        target_lang_name = lang_names.get(target_lang, target_lang)
        items = []
        for idx, text in enumerate(texts):
            safe_text = html.escape(text, quote=False)
            items.append(f'<item id="{idx}">{safe_text}</item>')
        payload = "\n".join(items)

        prompt = (
            f"把下面 XML 中每个 item 的内容分别翻译成{target_lang_name}。\n"
            "严格要求：\n"
            "1. 只返回 XML，不要解释。\n"
            "2. 保留 item 标签和原有 id，不要增删、合并、重排。\n"
            "3. 只翻译每个 item 标签内部的文本内容。\n"
            "4. 保留项目符号、URL、数字、章节号和专有名词格式。\n"
            "5. 如果某段本就不需要翻译，原样保留。\n\n"
            f"{payload}"
        )

        data = {
            "model": "deepseek/deepseek-chat",
            "messages": [
                {"role": "system", "content": "你是严格的 XML 翻译引擎。必须返回合法 XML，保持 item id 不变，只输出 XML。"},
                {"role": "user", "content": prompt}
            ],
            "temperature": 0
        }

        response = self._session.post(url, headers=headers, json=data, timeout=90)
        result = response.json()
        if 'choices' not in result or not result['choices']:
            raise Exception(f"API Error: {result}")

        content = result['choices'][0]['message']['content'].strip()
        matches = re.findall(r'<item\s+id="(\d+)">(.*?)</item>', content, flags=re.DOTALL)
        if len(matches) != len(texts):
            raise ValueError(f"批量返回项数不匹配: expected={len(texts)}, actual={len(matches)}")

        translated = [None] * len(texts)
        for raw_idx, raw_text in matches:
            idx = int(raw_idx)
            if idx < 0 or idx >= len(texts):
                raise ValueError(f"批量返回 id 越界: {idx}")
            translated[idx] = self._normalize_translated_text(html.unescape(raw_text.strip()))

        if any(item is None for item in translated):
            raise ValueError("批量返回缺少部分 item")

        for idx, translated_text in enumerate(translated):
            if self._should_retry_translation(texts[idx], translated_text, target_lang):
                raise ValueError(f"批量返回存在未翻译项: {idx}")

        return translated

    def _translate_text_kimi(self, text, source_lang='auto', target_lang='en'):
        """使用OpenRouter的Kimi (moonshot-v1-auto) API翻译"""
        if not text or not text.strip():
            return text

        try:
            # 清理文本中的特殊Unicode字符
            text = self._clean_text(text)

            url = "https://openrouter.ai/api/v1/chat/completions"
            headers = {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json; charset=utf-8",
                "HTTP-Referer": "https://pdf-translator.local",
            }

            lang_names = {
                'en': '英语',
                'zh': '中文',
                'ja': '日语',
                'ko': '韩语',
                'fr': '法语',
                'de': '德语',
                'es': '西班牙语',
                'ru': '俄语',
                'ar': '阿拉伯语'
            }

            target_lang_name = lang_names.get(target_lang, target_lang)
            prompt = f"请将以下文本翻译成{target_lang_name}，只返回翻译结果，不要添加任何解释：\n\n{text}"

            # 记录输入token
            input_tokens = self._estimate_tokens(text)
            self.input_tokens += input_tokens

            # 只在第一次显示详细token信息
            if self.input_tokens == input_tokens:
                self._add_log(f'开始翻译，文本长度: {len(text)} 字符, 输入tokens: {input_tokens}', 'info')

            data = {
                "model": "moonshot/moonshot-v1-auto",
                "messages": [
                    {"role": "system", "content": "你是一个专业的翻译助手，请准确翻译文本，保持原文的格式和语气。"},
                    {"role": "user", "content": prompt}
                ],
                "temperature": 0.1
            }

            response = self._session.post(url, headers=headers, json=data, timeout=120)
            result = response.json()

            if 'choices' in result and len(result['choices']) > 0:
                translated = result['choices'][0]['message']['content'].strip()

                # 记录输出token
                output_tokens = self._estimate_tokens(translated)
                self.output_tokens += output_tokens

                return translated
            else:
                raise Exception(f"API Error: {result}")

        except Exception as e:
            error_msg = str(e)
            print(f'Kimi translation error: {error_msg}')

            # 检查是否是认证错误
            if '401' in error_msg or 'auth' in error_msg.lower() or 'cookie' in error_msg.lower():
                self._add_log('❌ Kimi API Key无效或未正确设置！', 'error')
                self._add_log('💡 建议：请使用"Google翻译（免费）"选项，无需API Key', 'info')
            else:
                self._add_log(f'Kimi翻译错误: {error_msg}', 'error')

            return text

    def _translate_text_gpt(self, text, source_lang='auto', target_lang='en'):
        """使用OpenRouter的GPT-4.1 API翻译"""
        if not text or not text.strip():
            return text

        try:
            # 清理文本中的特殊Unicode字符
            text = self._clean_text(text)

            url = "https://openrouter.ai/api/v1/chat/completions"
            headers = {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json; charset=utf-8",
                "HTTP-Referer": "https://pdf-translator.local",
            }

            lang_names = {
                'en': '英语',
                'zh': '中文',
                'ja': '日语',
                'ko': '韩语',
                'fr': '法语',
                'de': '德语',
                'es': '西班牙语',
                'ru': '俄语',
                'ar': '阿拉伯语'
            }

            target_lang_name = lang_names.get(target_lang, target_lang)
            prompt = f"请将以下文本翻译成{target_lang_name}，只返回翻译结果，不要添加任何解释：\n\n{text}"

            # 记录输入token
            input_tokens = self._estimate_tokens(text)
            self.input_tokens += input_tokens

            # 只在第一次显示详细token信息
            if self.input_tokens == input_tokens:
                self._add_log(f'开始翻译，文本长度: {len(text)} 字符, 输入tokens: {input_tokens}', 'info')

            data = {
                "model": "openai/gpt-4-turbo",
                "messages": [
                    {"role": "system", "content": "你是一个专业的翻译助手，请准确翻译文本，保持原文的格式和语气。"},
                    {"role": "user", "content": prompt}
                ],
                "temperature": 0.1
            }

            response = self._session.post(url, headers=headers, json=data, timeout=120)
            result = response.json()

            if 'choices' in result and len(result['choices']) > 0:
                translated = result['choices'][0]['message']['content'].strip()

                # 记录输出token
                output_tokens = self._estimate_tokens(translated)
                self.output_tokens += output_tokens

                return translated
            else:
                raise Exception(f"API Error: {result}")

        except Exception as e:
            error_msg = str(e)
            print(f'GPT translation error: {error_msg}')

            # 检查是否是认证错误
            if '401' in error_msg or 'auth' in error_msg.lower() or 'cookie' in error_msg.lower():
                self._add_log('❌ GPT API Key无效或未正确设置！', 'error')
                self._add_log('💡 建议：请使用"Google翻译（免费）"选项，无需API Key', 'info')
            else:
                self._add_log(f'GPT翻译错误: {error_msg}', 'error')

            return text

    def _translate_text(self, text, source_lang='auto', target_lang='en'):
        """根据API类型选择翻译方法"""
        special_text = self._translate_special_text(text, target_lang)
        if special_text is not None:
            return special_text

        if self.api_type == 'google':
            return self._translate_text_google(text, source_lang, target_lang)
        elif self.api_type == 'deepseek':
            return self._translate_text_deepseek(text, source_lang, target_lang)
        elif self.api_type == 'zhipu':
            return self._translate_text_zhipu(text, source_lang, target_lang)
        elif self.api_type == 'openrouter':
            return self._translate_text_openrouter(text, source_lang, target_lang)
        elif self.api_type == 'kimi':
            return self._translate_text_kimi(text, source_lang, target_lang)
        elif self.api_type == 'gpt':
            return self._translate_text_gpt(text, source_lang, target_lang)
        else:
            return text

    def _translate_text_batch(self, texts, source_lang='auto', target_lang='en'):
        """批量翻译多个文本，提高速度"""
        if not texts or len(texts) == 0:
            return []

        # 过滤空文本
        valid_texts = [(i, text) for i, text in enumerate(texts) if text and text.strip()]
        if not valid_texts:
            return texts

        # 对于Google Translate，使用批量翻译
        if self.api_type == 'google':
            return self._translate_text_batch_google(valid_texts, source_lang, target_lang)
        else:
            # 对于其他API，逐个翻译（但减少日志）
            results = [None] * len(texts)
            for idx, text in valid_texts:
                results[idx] = self._translate_text(text, source_lang, target_lang)
            return results

    def _translate_text_batch_google(self, valid_texts, source_lang='auto', target_lang='en'):
        """Google Translate批量翻译 - 使用高并发请求（通过deep-translator）"""
        import concurrent.futures
        import threading

        try:
            # 语言代码映射
            lang_mapping = {
                'zh': 'zh-CN',
                'en': 'en',
                'ja': 'ja',
                'ko': 'ko',
                'fr': 'fr',
                'de': 'de',
                'es': 'es-ES',
                'ru': 'ru',
                'ar': 'ar'
            }

            normalized_target = lang_mapping.get(target_lang, target_lang)
            normalized_source = 'auto'  # deep-translator 使用 auto

            results = {}
            lock = threading.Lock()

            # 增加并发数到10，提高速度
            max_workers = 10

            def translate_single(idx, text):
                try:
                    # 清理文本
                    text = self._clean_text(text)

                    # 记录输入token
                    input_tokens = self._estimate_tokens(text)
                    with lock:
                        self.input_tokens += input_tokens

                    # 翻译
                    translated = GoogleTranslator(source=normalized_source, target=normalized_target).translate(text)

                    # 记录输出token
                    output_tokens = self._estimate_tokens(translated)
                    with lock:
                        self.output_tokens += output_tokens

                    return (idx, translated, None)
                except Exception as e:
                    print(f'Translation error for text {idx}: {e}')
                    return (idx, text, str(e))

            # 并发翻译所有文本
            with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
                futures = {executor.submit(translate_single, idx, text): idx for idx, text in valid_texts}

                for future in concurrent.futures.as_completed(futures):
                    try:
                        idx, translated, error = future.result()
                        with lock:
                            results[idx] = translated
                    except Exception as e:
                        idx = futures[future]
                        print(f'Future {idx} failed: {e}')
                        with lock:
                            results[idx] = valid_texts[idx][1]

            # 返回结果数组（保持原始顺序，包括None值）
            return [results.get(i, None) for i in range(len(valid_texts))]

        except Exception as e:
            print(f'Concurrent translation failed: {e}')
            import traceback
            traceback.print_exc()
            # 降级到逐个翻译
            results = {}
            for idx, text in valid_texts:
                try:
                    text = self._clean_text(text)
                    translated = GoogleTranslator(source=normalized_source, target=normalized_target).translate(text)
                    results[idx] = translated
                except Exception as e:
                    print(f'Single translation error: {e}')
                    results[idx] = text
            return [results.get(i, None) for i in range(len(valid_texts))]

    def translate_pdf(self, input_path, output_path, source_lang='auto', target_lang='en', concurrency=4):
        """翻译PDF文件（并发翻译）"""
        import concurrent.futures
        import threading
        import os

        # 调试：打印并发参数
        print(f"[DEBUG] translate_pdf called with concurrency={concurrency}")

        doc = None
        try:
            self._add_log('========== 开始翻译任务 ==========', 'info')
            self._add_log(f'使用文本块级并发翻译，{concurrency} 个线程同时工作 ⚡', 'success')

            # 检查文件是否存在
            self._add_log(f'输入文件: {input_path}', 'info')
            self._add_log(f'输出文件: {output_path}', 'info')

            # 检查文件大小
            self._add_log('正在检查文件大小...', 'info')
            file_size = os.path.getsize(input_path)
            file_size_mb = file_size / (1024 * 1024)
            self._add_log(f'文件大小: {file_size_mb:.2f} MB', 'info')

            # 打开原始PDF
            self._add_log('正在打开PDF文件...', 'info')
            start_time = time.time()
            translation_start_time = start_time

            try:
                doc = fitz.open(input_path)
                open_time = time.time() - start_time
                self._add_log(f'✓ PDF打开成功 (耗时 {open_time:.2f}秒)', 'success')
            except Exception as e:
                self._add_log(f'✗ PDF打开失败: {str(e)}', 'error')
                raise

            total_pages = len(doc)
            self._add_log(f'PDF总页数: {total_pages} 页', 'info')

            # 每N页记录一次日志
            log_interval = max(1, total_pages // 10)

            # 语言代码映射
            lang_mapping = {
                'zh': 'zh-CN',
                'en': 'en',
                'ja': 'ja',
                'ko': 'ko',
                'fr': 'fr',
                'de': 'de',
                'es': 'es',
                'ru': 'ru',
                'ar': 'ar'
            }

            normalized_target = lang_mapping.get(target_lang, target_lang)
            normalized_source = 'auto' if source_lang == 'auto' else lang_mapping.get(source_lang, source_lang)

            # 使用用户设置的并发数
            self._add_log(f'📖 开始提取和翻译（并发数: {concurrency}）...', 'info')
            self._add_log(f'⚡ 使用 {concurrency} 个线程并发翻译', 'success')
            use_fast_extraction = self._should_use_fast_block_extraction(total_pages, file_size_mb)
            extraction_mode = 'blocks' if use_fast_extraction else 'dict/spans'
            self._add_log(f'文本提取模式: {extraction_mode}', 'info')

            # 存储每页的翻译结果: {page_num: [(rect, translated_text, font_info), ...]}
            page_translations_map = {}
            text_page_results = {}
            page_has_images = {}
            text_only_pages = []
            extraction_start_time = time.time()
            raw_image_page_blocks = 0

            # 收集需要保真翻译的含图页文本块，以及纯文字页整页文本
            all_blocks = []
            for page_num in range(total_pages):
                try:
                    page = doc[page_num]
                    image_count = len(page.get_images())
                    page_has_images[page_num] = image_count > 0

                    if not page_has_images[page_num]:
                        page_text = self._clean_text(page.get_text("text"))
                        if page_text and page_text.strip() and self._is_translatable(page_text):
                            text_only_pages.append({
                                'page_num': page_num,
                                'text': page_text
                            })
                            continue

                    if use_fast_extraction:
                        blocks = page.get_text("blocks")
                        blocks.sort(key=lambda b: (b[1], b[0]))
                        page_blocks = []
                        span_hints = self._extract_span_style_hints(page)

                        for block_idx, block in enumerate(blocks):
                            if block[6] != 0:
                                continue

                            text = self._normalize_extracted_block_text(block[4])
                            if not text or not text.strip() or not self._is_translatable(text):
                                continue

                            try:
                                rect = fitz.Rect(block[0], block[1], block[2], block[3])
                            except Exception as rect_err:
                                print(f'[WARN] 第{page_num+1}页块{block_idx}坐标异常: {rect_err}')
                                continue

                            page_blocks.append({
                                'page_num': page_num,
                                'block_idx': block_idx,
                                'text': text,
                                'rect': rect,
                                'font_info': {}
                            })

                        page_blocks = self._apply_block_style_hints(page_blocks, span_hints)
                        raw_image_page_blocks += len(page_blocks)
                        is_toc_page = self._is_toc_like_page(page_blocks)
                        if is_toc_page:
                            merged_page_blocks = page_blocks
                        else:
                            merged_page_blocks = self._merge_page_blocks_for_translation(page_num, page_blocks)
                        for merged_block in merged_page_blocks:
                            if is_toc_page:
                                merged_block['font_info']['layout_hint'] = 'toc'
                            else:
                                merged_block['font_info']['layout_hint'] = self._classify_block_for_merge(merged_block)['kind']
                            merged_block['seq'] = len(all_blocks)
                            all_blocks.append(merged_block)
                    else:
                        # 小文件保留 span 级字体信息，提升写回质量。
                        text_dict = page.get_text("dict")

                        block_idx = 0
                        for block in text_dict['blocks']:
                            if block['type'] != 0:
                                continue

                            spans_data = []
                            block_text = ""
                            for line in block['lines']:
                                for span in line['spans']:
                                    text = self._normalize_extracted_block_text(span['text'])
                                    block_text += text
                                    spans_data.append({
                                        'text': text,
                                        'font': span['font'],
                                        'size': span['size'],
                                        'flags': span['flags'],
                                        'color': span['color']
                                    })

                            if not block_text or not block_text.strip() or not self._is_translatable(block_text):
                                continue

                            try:
                                rect = fitz.Rect(block['bbox'])
                            except Exception as rect_err:
                                print(f'[WARN] 第{page_num+1}页块{block_idx}坐标异常: {rect_err}')
                                continue

                            first_span = spans_data[0] if spans_data else None
                            font_info = {
                                'font': first_span['font'] if first_span else 'helv',
                                'size': first_span['size'] if first_span else 11,
                                'flags': first_span['flags'] if first_span else 0,
                                'color': first_span['color'] if first_span else 0,
                                'spans': spans_data
                            }

                            all_blocks.append({
                                'page_num': page_num,
                                'block_idx': block_idx,
                                'seq': len(all_blocks),
                                'text': block_text,
                                'rect': rect,
                                'font_info': font_info
                            })
                            block_idx += 1
                except Exception as page_err:
                    self._add_log(f'第{page_num+1}页文本提取失败（已跳过）: {page_err}', 'error')
                    continue

            extraction_elapsed = time.time() - extraction_start_time
            total_blocks = len(all_blocks)
            total_text_pages = len(text_only_pages)
            total_translation_units = total_blocks + total_text_pages
            self._add_log(f'纯文字页: {total_text_pages} 页，含图/保真页文本块: {total_blocks} 个', 'info')
            if raw_image_page_blocks:
                self._add_log(f'含图页块合并: {raw_image_page_blocks} -> {total_blocks}', 'info')
            self._add_log(f'文本提取完成 (耗时: {extraction_elapsed:.1f}秒)', 'info')

            # 并发翻译所有文本块
            self._add_log('=' * 60, 'info')
            self._add_log('开始调用翻译API...', 'info')

            completed_count = [0]
            lock = threading.Lock()
            text_page_runs = self._build_text_page_runs(text_only_pages, max_chars=12000)
            self._add_log(f'纯文字页合并为 {len(text_page_runs)} 个跨页翻译批次', 'info')

            def _calc_remaining(elapsed, done, total):
                if done <= 0:
                    return 0
                return (elapsed / done) * (total - done)

            def translate_text_page_run(run):
                page_separator = "\n__PAGE_BREAK__\n"
                page_numbers = [item['page_num'] + 1 for item in run]
                combined = page_separator.join(item['text'] for item in run)
                api_start_time = time.time()

                self._check_cancelled()

                if self.api_type == 'google':
                    translated_combined = self._translate_text_google(combined, source_lang, target_lang)
                else:
                    translated_combined = self._translate_text(combined, source_lang, target_lang)

                parts = translated_combined.split(page_separator)
                if len(parts) != len(run):
                    parts = []
                    for item in run:
                        text = item['text']
                        if self.api_type == 'google':
                            translated = self._translate_text_google(text, source_lang, target_lang)
                        else:
                            translated = self._translate_text(text, source_lang, target_lang)
                        parts.append(translated if translated else text)

                api_time = time.time() - api_start_time

                with lock:
                    for idx, item in enumerate(run):
                        page_num = item['page_num']
                        translated_text = parts[idx] if idx < len(parts) else item['text']
                        text_page_results[page_num] = translated_text
                        current = completed_count[0] + 1
                        if self._should_emit_detail_log(current, total_translation_units):
                            self._add_log(
                                f'[纯文字页 {page_num + 1}] 已完成整页翻译 (批次耗时: {api_time:.1f}s)',
                                'success'
                            )
                        completed_count[0] += 1

                    elapsed_time = time.time() - translation_start_time
                    current = completed_count[0]
                    est_remaining = _calc_remaining(elapsed_time, current, total_translation_units)
                    if self._should_emit_progress_update(current, total_translation_units):
                        self._update_progress(
                            current,
                            total_translation_units,
                            f'已翻译 {current}/{total_translation_units} 个单元...',
                            elapsed_time=elapsed_time,
                            estimated_remaining=est_remaining
                        )

                self._add_log(
                    f'纯文字页批次完成: 第 {page_numbers[0]} 页到第 {page_numbers[-1]} 页',
                    'info'
                )

            if self.api_type == 'google':
                groups = self._group_short_blocks(all_blocks, max_group_chars=4000, short_threshold=500)
                batch_count = sum(1 for t, _ in groups if t == 'batch' and len(_) > 1)
                single_count = len(groups) - batch_count
                self._add_log(f'文本块分组完成：{single_count} 个单独翻译，{batch_count} 个批次合并翻译', 'info')
            elif self.api_type == 'openrouter':
                groups = self._group_short_blocks(all_blocks, max_group_chars=1800, short_threshold=220)
                batch_count = sum(1 for t, blocks in groups if t == 'batch' and len(blocks) > 1)
                single_count = len(groups) - batch_count
                self._add_log(
                    f'文本块分组完成：{single_count} 个单独翻译，{batch_count} 个 OpenRouter 结构化批次',
                    'info'
                )
            else:
                groups = [('single', [block]) for block in all_blocks]
                self._add_log(
                    f'文本块分组完成：{len(groups)} 个独立翻译单元（LLM 模式禁用二次批次，避免分隔符丢失回退）',
                    'info'
                )

            results = {}
            BATCH_SEPARATOR = "\n---SPLIT---\n"

            def translate_unit(unit):
                """翻译一个工作单元（单块或批次短文本块）"""
                group_type, blocks = unit
                api_start_time = time.time()

                if len(blocks) == 1 and blocks[0].get('font_info', {}).get('layout_hint', 'body') != 'body':
                    block_info = blocks[0]
                    text = self._clean_text(block_info['text'])
                    if self._is_url_only_text(text):
                        translated = text
                    else:
                        translated = self._strict_translate_text(text, source_lang, target_lang)
                    translated = self._normalize_translated_text(translated)
                    with lock:
                        current_num = block_info.get('seq', 0) + 1
                        if self._should_emit_detail_log(current_num, total_blocks):
                            display_original = text[:200] + '...' if len(text) > 200 else text
                            display_translated = translated[:200] + '...' if len(translated) > 200 else translated
                            self._add_log(f'[原文 {current_num}/{total_blocks}] {display_original}', 'info')
                            self._add_log(f'[译文 {current_num}/{total_blocks}] {display_translated} (严格翻译)', 'success')
                        completed_count[0] += 1
                        elapsed_time = time.time() - translation_start_time
                        current = completed_count[0]
                        est_remaining = _calc_remaining(elapsed_time, current, total_translation_units)
                        if self._should_emit_progress_update(current, total_translation_units):
                            self._update_progress(
                                current, total_translation_units,
                                f'已翻译 {current}/{total_translation_units} 个单元...',
                                elapsed_time=elapsed_time, estimated_remaining=est_remaining
                            )
                    return [(block_info, translated)]

                # ---- 批次翻译（多个短文本块合并为一次 API 调用） ----
                if group_type == 'batch' and len(blocks) > 1:
                    texts = [self._clean_text(b['text']) for b in blocks]
                    combined = BATCH_SEPARATOR.join(texts)

                    # 先记录所有原文日志
                    with lock:
                        for i, text in enumerate(texts):
                            current_num = blocks[i].get('seq', 0) + 1
                            if self._should_emit_detail_log(current_num, total_blocks):
                                display = text[:200] + '...' if len(text) > 200 else text
                                self._add_log(f'[原文 {current_num}/{total_blocks}] {display}', 'info')

                    try:
                        self._check_cancelled()
                        input_tokens = self._estimate_tokens(combined)

                        if self.api_type == 'google':
                            combined_translated = GoogleTranslator(
                                source=normalized_source, target=normalized_target
                            ).translate(combined)
                        elif self.api_type == 'openrouter':
                            parts = self._translate_text_openrouter_batch(texts, source_lang, target_lang)
                            combined_translated = None
                        else:
                            combined_translated = self._translate_text(combined, source_lang, target_lang)

                        api_time = time.time() - api_start_time
                        output_tokens = self._estimate_tokens(combined_translated) if combined_translated is not None else sum(
                            self._estimate_tokens(part) for part in parts
                        )

                        # 拆分结果；如数量不匹配则逐一翻译（不回退原文）
                        if self.api_type != 'openrouter':
                            parts = combined_translated.split(BATCH_SEPARATOR)
                        if len(parts) != len(blocks):
                            print(f'[WARN] 批次拆分不匹配: 期望 {len(blocks)}, 实际 {len(parts)}，逐一翻译回退')
                            parts = []
                            for t in texts:
                                try:
                                    if self.api_type == 'google':
                                        tr = GoogleTranslator(
                                            source=normalized_source, target=normalized_target
                                        ).translate(t)
                                    else:
                                        tr = self._translate_text(t, source_lang, target_lang)
                                    parts.append(tr if tr else t)
                                except Exception:
                                    parts.append(t)

                        final_parts = []
                        for i, translated in enumerate(parts):
                            final_parts.append(
                                self._ensure_target_translation(
                                    texts[i], translated, source_lang, target_lang
                                )
                            )

                        with lock:
                            for i, (block, translated) in enumerate(zip(blocks, final_parts)):
                                current_num = block.get('seq', 0) + 1
                                if self._should_emit_detail_log(current_num, total_blocks):
                                    display = translated[:200] + '...' if len(translated) > 200 else translated
                                    self._add_log(
                                        f'[译文 {current_num}/{total_blocks}] {display} (耗时: {api_time:.1f}s)',
                                        'success'
                                    )
                                cache_key = (texts[i], source_lang, target_lang)
                                self._translation_cache[cache_key] = translated

                            self.input_tokens += input_tokens
                            self.output_tokens += output_tokens
                            completed_count[0] += len(blocks)

                            elapsed_time = time.time() - translation_start_time
                            current = completed_count[0]
                            est_remaining = _calc_remaining(elapsed_time, current, total_translation_units)
                            if self._should_emit_progress_update(current, total_translation_units):
                                self._update_progress(
                                    current, total_translation_units,
                                    f'已翻译 {current}/{total_translation_units} 个单元...',
                                    elapsed_time=elapsed_time, estimated_remaining=est_remaining
                                )

                        return [(b, t) for b, t in zip(blocks, final_parts)]

                    except Exception as e:
                        print(f'批次翻译失败: {e}')
                        fallback_parts = []
                        for text in texts:
                            try:
                                fallback_parts.append(self._strict_translate_text(text, source_lang, target_lang))
                            except Exception as strict_err:
                                self._add_log(f'批次块严格重翻失败，保留原文: {str(strict_err)[:120]}', 'error')
                                fallback_parts.append(text)
                        with lock:
                            completed_count[0] += len(blocks)
                            elapsed_time = time.time() - translation_start_time
                            current = completed_count[0]
                            est_remaining = _calc_remaining(elapsed_time, current, total_translation_units)
                            self._update_progress(
                                current, total_translation_units,
                                f'已翻译 {current}/{total_translation_units} 个单元...',
                                elapsed_time=elapsed_time, estimated_remaining=est_remaining
                            )
                        return [(b, t) for b, t in zip(blocks, fallback_parts)]

                # ---- 单块翻译 ----
                block_info = blocks[0]
                max_retries = 3

                for attempt in range(max_retries):
                    try:
                        self._check_cancelled()
                        text = self._clean_text(block_info['text'])

                        # 检查缓存
                        cache_key = (text, source_lang, target_lang)
                        if cache_key in self._translation_cache:
                            translated = self._translation_cache[cache_key]
                            with lock:
                                current_num = block_info.get('seq', 0) + 1
                                if self._should_emit_detail_log(current_num, total_blocks):
                                    display_orig = text[:200] + '...' if len(text) > 200 else text
                                    display_trans = translated[:200] + '...' if len(translated) > 200 else translated
                                    self._add_log(f'[原文 {current_num}/{total_blocks}] {display_orig}', 'info')
                                    self._add_log(f'[译文 {current_num}/{total_blocks}] {display_trans} (缓存命中)', 'success')
                                completed_count[0] += 1
                                elapsed_time = time.time() - translation_start_time
                                current = completed_count[0]
                                est_remaining = _calc_remaining(elapsed_time, current, total_translation_units)
                                if self._should_emit_progress_update(current, total_translation_units):
                                    self._update_progress(
                                        current, total_translation_units,
                                        f'已翻译 {current}/{total_translation_units} 个单元...',
                                        elapsed_time=elapsed_time, estimated_remaining=est_remaining
                                    )
                            return [(block_info, translated)]

                        with lock:
                            current_num = block_info.get('seq', 0) + 1
                            if self._should_emit_detail_log(current_num, total_blocks):
                                display_original = text[:200] + '...' if len(text) > 200 else text
                                self._add_log(f'[原文 {current_num}/{total_blocks}] {display_original}', 'info')

                        input_tokens = self._estimate_tokens(text)

                        if self.api_type == 'google':
                            translated = GoogleTranslator(
                                source=normalized_source, target=normalized_target
                            ).translate(text)
                        else:
                            translated = self._translate_text(text, source_lang, target_lang)
                        translated = self._ensure_target_translation(
                            text, translated, source_lang, target_lang
                        )

                        output_tokens = self._estimate_tokens(translated)
                        api_time = time.time() - api_start_time

                        with lock:
                            if self._should_emit_detail_log(current_num, total_blocks):
                                display_translated = translated[:200] + '...' if len(translated) > 200 else translated
                                self._add_log(
                                    f'[译文 {current_num}/{total_blocks}] {display_translated} (耗时: {api_time:.1f}s)',
                                    'success'
                                )
                            self.input_tokens += input_tokens
                            self.output_tokens += output_tokens
                            self._translation_cache[cache_key] = translated
                            completed_count[0] += 1

                            elapsed_time = time.time() - translation_start_time
                            current = completed_count[0]
                            est_remaining = _calc_remaining(elapsed_time, current, total_translation_units)
                            if self._should_emit_progress_update(current, total_translation_units):
                                self._update_progress(
                                    current, total_translation_units,
                                    f'已翻译 {current}/{total_translation_units} 个单元...',
                                    elapsed_time=elapsed_time, estimated_remaining=est_remaining
                                )

                        return [(block_info, translated)]

                    except Exception as e:
                        print(f'Translation error for block {block_info["block_idx"]} (attempt {attempt + 1}): {e}')
                        if attempt == max_retries - 1:
                            try:
                                strict_translated = self._strict_translate_text(text, source_lang, target_lang)
                                return [(block_info, strict_translated)]
                            except Exception as strict_err:
                                self._add_log(
                                    f'第{block_info["page_num"] + 1}页块{block_info["block_idx"] + 1}严格重翻失败，保留原文: {str(strict_err)[:120]}',
                                    'error'
                                )
                            with lock:
                                completed_count[0] += 1
                                elapsed_time = time.time() - translation_start_time
                                current = completed_count[0]
                                est_remaining = _calc_remaining(elapsed_time, current, total_translation_units)
                                if self._should_emit_progress_update(current, total_translation_units):
                                    self._update_progress(
                                        current, total_translation_units,
                                        f'已翻译 {current}/{total_translation_units} 个单元...',
                                        elapsed_time=elapsed_time, estimated_remaining=est_remaining
                                    )
                            return [(block_info, block_info['text'])]
                        else:
                            time.sleep(0.5 * (2 ** attempt))  # 指数退避

            if text_page_runs:
                with concurrent.futures.ThreadPoolExecutor(max_workers=min(concurrency, max(1, len(text_page_runs)))) as executor:
                    futures = [executor.submit(translate_text_page_run, run) for run in text_page_runs]
                    for future in concurrent.futures.as_completed(futures):
                        try:
                            future.result()
                        except Exception as e:
                            print(f'Text-page future error: {e}')

            # 使用用户设置的并发数翻译（以 group 为并发单元）
            if groups:
                with concurrent.futures.ThreadPoolExecutor(max_workers=concurrency) as executor:
                    futures = {executor.submit(translate_unit, group): group for group in groups}

                    for future in concurrent.futures.as_completed(futures):
                        try:
                            block_results = future.result()
                            for block_info, translated_text in block_results:
                                results[(block_info['page_num'], block_info['block_idx'])] = (
                                    block_info['rect'], translated_text
                                )
                        except Exception as e:
                            print(f'Future error: {e}')

            self._add_log(f'✓ 所有文本块翻译完成', 'success')
            print(f'[DEBUG] results字典包含 {len(results)} 个翻译结果')

            # 按页组织翻译结果并显示
            # 只显示前3页和后3页的译文，避免日志过多
            self._add_log('=' * 60, 'info')
            self._add_log('翻译结果（前3页和后3页）：', 'info')

            for page_num in range(total_pages):
                page_translations = []

                if page_num in text_page_results:
                    page_translations_map[page_num] = text_page_results[page_num]
                    if page_num < 3 or page_num >= total_pages - 3:
                        preview = text_page_results[page_num][:200]
                        if len(text_page_results[page_num]) > 200:
                            preview += '...'
                        self._add_log(f'[页{page_num + 1}|整页文本] 译文: {preview}', 'success')
                    continue

                # 获取这一页的所有文本块
                page_blocks = [b for b in all_blocks if b['page_num'] == page_num]

                if not page_blocks:
                    continue

                # 跳过中间页面的详细显示
                if page_num < 3 or page_num >= total_pages - 3:
                    self._add_log(f'--- 第 {page_num + 1} 页 ---', 'info')

                for block_info in page_blocks:
                    block_idx = block_info['block_idx']
                    font_info = block_info.get('font_info', {})
                    result = results.get((page_num, block_idx))
                    if result:
                        rect, translated_text = result
                        # 保存字体信息
                        page_translations.append((rect, translated_text, font_info))

                        # 只显示前3页和后3页的译文
                        if page_num < 3 or page_num >= total_pages - 3:
                            display_translated = translated_text[:200] + '...' if len(translated_text) > 200 else translated_text
                            # 使用前端期望的格式：[页X|文本块 Y] 译文: ...
                            self._add_log(f'[页{page_num + 1}|文本块 {block_idx + 1}] 译文: {display_translated}', 'success')
                    else:
                        self._add_log(f'⚠️ 第{page_num + 1}页块{block_idx + 1}翻译结果丢失', 'error')

                page_translations_map[page_num] = page_translations

                # 记录跳过的页面
                if page_num >= 3 and page_num < total_pages - 3:
                    detail = '整页文本' if isinstance(page_translations_map[page_num], str) else f'{len(page_translations)} 个文本块'
                    self._add_log(f'--- 第 {page_num + 1} 页（已跳过，{detail}） ---', 'info')

            self._add_log(f'✓ 所有页面翻译完成', 'success')

            # 将翻译结果写回PDF
            self._add_log('正在将译文写回PDF...', 'info')
            self._add_log('将彻底移除原文并插入翻译，保留图片和排版格式', 'info')

            print(f'[DEBUG] page_translations_map包含 {len(page_translations_map)} 页')
            total_written = 0

            # 方法：创建新文档，复制原页面的图片和图形，然后只添加翻译后的文本
            # 这样可以彻底移除原文，同时保留图片和排版

            self._add_log('正在创建新文档（保留图片，移除原文）...', 'info')
            new_doc = fitz.open()

            for page_num in range(total_pages):
                self._check_cancelled()

                try:
                    page = doc[page_num]
                    page_translations = page_translations_map.get(page_num, [])

                    # 某些 PDF 在取尺寸或旋转时会抛出异常，直接跳过该页。
                    mediabox = page.mediabox
                    rotation = page.rotation
                    new_page = new_doc.new_page(
                        width=mediabox.width,
                        height=mediabox.height
                    )

                    if rotation and rotation in (90, 180, 270):
                        new_page.set_rotation(rotation)
                    new_page.draw_rect(new_page.rect, color=(1, 1, 1), fill=(1, 1, 1))
                    page_registered_fonts = self._register_page_fonts(new_page)
                    self._copy_vector_drawings(page, new_page)
                except Exception as page_setup_err:
                    self._add_log(f'第{page_num+1}页初始化失败（已跳过）: {page_setup_err}', 'error')
                    continue

                if isinstance(page_translations, str):
                    translated_page_text = self._normalize_translated_text(page_translations)
                    image_top_band = 0
                    try:
                        image_top_band = max(
                            (rect.y1 for img in page.get_images() for rect in page.get_image_rects(img[0]) if rect.y0 <= 5),
                            default=0
                        )
                    except Exception:
                        image_top_band = 0
                    margin = 36
                    top_margin = max(margin, image_top_band + 20)
                    text_rect = fitz.Rect(
                        margin,
                        top_margin,
                        new_page.rect.width - margin,
                        new_page.rect.height - margin
                    )
                    chinese_chars = len(re.findall(r'[\u4e00-\u9fff]', translated_page_text))
                    total_chars = len(translated_page_text)
                    is_chinese = chinese_chars > total_chars * 0.3 if total_chars > 0 else False
                    font_candidates = self._build_font_candidates(
                        page_registered_fonts, translated_page_text, 'helv', False, False, is_chinese
                    )
                    font_sizes = [11, 10, 9, 8, 7, 6]
                    written = False

                    for font_name in font_candidates:
                        for fontsize in font_sizes:
                            try:
                                result = new_page.insert_textbox(
                                    text_rect,
                                    translated_page_text,
                                    fontsize=fontsize,
                                    fontname=font_name,
                                    color=(0, 0, 0),
                                    align=0
                                )
                                if result >= 0:
                                    written = True
                                    break
                            except Exception:
                                break
                        if written:
                            break

                    if written:
                        total_written += 1
                        if page_num < 3 or page_num >= total_pages - 3:
                            self._add_log(f'第 {page_num + 1} 页完成: 整页文本写入成功', 'success')
                    else:
                        self._add_log(f'⚠️ 第{page_num + 1}页整页文本写入失败', 'error')

                    elapsed_time = time.time() - translation_start_time
                    done_pages = page_num + 1
                    est_remaining = _calc_remaining(elapsed_time, done_pages, total_pages)
                    self._update_progress(
                        done_pages,
                        total_pages,
                        f'已写入 {done_pages}/{total_pages} 页',
                        elapsed_time=elapsed_time,
                        estimated_remaining=est_remaining
                    )
                    continue

                # 复制原页面的所有图片
                try:
                    image_list = page.get_images()
                    for img_index, img in enumerate(image_list):
                        try:
                            xref = img[0]
                            img_rects = page.get_image_rects(xref)
                            for img_rect in img_rects:
                                new_page.insert_image(img_rect, pixmap=fitz.Pixmap(doc, xref))
                        except Exception as img_err:
                            print(f'[DEBUG] 图片复制失败(页{page_num+1}): {str(img_err)[:50]}')
                except Exception as e:
                    print(f'[DEBUG] 图片处理出错(页{page_num+1}): {str(e)[:50]}')

                if not page_translations:
                    if page_num < 3 or page_num >= total_pages - 3:
                        self._add_log(f'第 {page_num + 1} 页: 无翻译内容', 'info')
                    continue

                if page_num < 3 or page_num >= total_pages - 3:
                    self._add_log(f'更新第 {page_num + 1} 页（{len(page_translations)} 个文本块）...', 'info')

                # 更新这一页的内容
                success_count = 0
                page_bottom = new_page.rect.height - 5
                image_rects = []
                try:
                    for img in page.get_images():
                        for rect in page.get_image_rects(img[0]):
                            image_rects.append(rect)
                except Exception:
                    image_rects = []
                image_top_band = max((rect.y1 for rect in image_rects if rect.y0 <= 5), default=0)

                for idx, (text_rect, translated_text, font_info) in enumerate(page_translations):
                    try:
                        written = False
                        translated_text = self._normalize_translated_text(translated_text)

                        # 获取原始字体信息
                        original_font = font_info.get('font', 'helv')
                        original_size = font_info.get('size', 11)
                        original_flags = font_info.get('flags', 0)
                        layout_hint = font_info.get('layout_hint', 'body')

                        # 判断是否为粗体
                        is_bold = (original_flags & 16) != 0
                        is_italic = (original_flags & 1) != 0

                        # 检测翻译后文本的主要语言
                        chinese_chars = len(re.findall(r'[\u4e00-\u9fff]', translated_text))
                        total_chars = len(translated_text)
                        is_chinese = chinese_chars > total_chars * 0.3 if total_chars > 0 else False

                        # 根据翻译后文本语言选择字体
                        font_names = self._build_font_candidates(
                            page_registered_fonts, translated_text, original_font, is_bold, is_italic, is_chinese
                        )
                        if layout_hint == 'toc':
                            toc_fonts = [name for name in ('ui_unicode', 'ui_cjk', 'ui_heiti', 'helv') if name in font_names or name in page_registered_fonts]
                            font_names = toc_fonts + [name for name in font_names if name not in toc_fonts]
                        elif layout_hint in ('caption', 'heading', 'short'):
                            heading_fonts = [name for name in ('ui_heiti', 'ui_unicode', 'ui_cjk', 'helv-bold', 'helv') if name in font_names or name in page_registered_fonts]
                            font_names = heading_fonts + [name for name in font_names if name not in heading_fonts]

                        # 标题、图注和短标签优先保留原始字号，正文再适度缩小。
                        if layout_hint in ('caption', 'heading', 'short'):
                            if is_chinese:
                                base_fontsize = max(10, original_size * 1.02)
                            else:
                                base_fontsize = max(10, original_size)
                        elif layout_hint in ('list_item', 'list_cont'):
                            if is_chinese:
                                base_fontsize = max(7, original_size * 0.95)
                            else:
                                base_fontsize = max(7, original_size * 0.98)
                        elif is_chinese:
                            base_fontsize = max(6, original_size * 0.88)
                        else:
                            base_fontsize = max(6, original_size * 0.95)
                        if layout_hint == 'toc':
                            base_fontsize = max(6, base_fontsize * 0.85)
                        font_sizes = [base_fontsize, base_fontsize * 0.95, base_fontsize * 0.9, base_fontsize * 0.84, 6]
                        original_color = font_info.get('color', 0)
                        if self._looks_light_color(original_color):
                            text_color = self._pdf_color_to_rgb(original_color)
                        elif text_rect.y0 < image_top_band:
                            text_color = (1, 1, 1)
                        else:
                            text_color = self._pdf_color_to_rgb(original_color)
                        next_top = None
                        if idx + 1 < len(page_translations):
                            next_top = page_translations[idx + 1][0].y0
                        max_expand_bottom = page_bottom if next_top is None else max(text_rect.y1, next_top - 4)
                        expanded_rect = fitz.Rect(text_rect)
                        if layout_hint in ('caption', 'heading', 'short') and self._looks_light_color(original_color):
                            expanded_rect = fitz.Rect(
                                text_rect.x0,
                                text_rect.y0,
                                new_page.rect.width - 72,
                                min(max_expand_bottom, max(text_rect.y1 + original_size * 1.6, text_rect.y1))
                            )
                        elif layout_hint in ('list_item', 'list_cont'):
                            expanded_rect = fitz.Rect(
                                text_rect.x0,
                                text_rect.y0,
                                text_rect.x1,
                                min(max_expand_bottom, max(text_rect.y1 + original_size * 2.2, text_rect.y1))
                            )

                        for font_name in font_names:
                            single_line_heading = (
                                layout_hint in ('caption', 'heading', 'short', 'toc')
                                and '\n' not in translated_text
                                and len(translated_text) <= 80
                            )
                            if single_line_heading:
                                for fontsize in font_sizes[:-1]:
                                    try:
                                        if layout_hint in ('caption', 'heading', 'short') or (layout_hint == 'toc' and len(translated_text) <= 32):
                                            draw_font = 'ui_unicode' if 'ui_unicode' in page_registered_fonts else font_name
                                        else:
                                            draw_font = font_name
                                        new_page.insert_text(
                                            (text_rect.x0, text_rect.y0 + fontsize),
                                            translated_text,
                                            fontsize=fontsize,
                                            fontname=draw_font,
                                            color=text_color,
                                        )
                                        written = True
                                        break
                                    except Exception:
                                        continue
                                if written:
                                    break

                            rect_variants = [text_rect]
                            if expanded_rect != text_rect:
                                rect_variants.append(expanded_rect)
                            for candidate_rect in rect_variants:
                                for fontsize in font_sizes:
                                    try:
                                        result = new_page.insert_textbox(
                                            candidate_rect,
                                            translated_text,
                                            fontsize=fontsize,
                                            fontname=font_name,
                                            color=text_color,
                                            align=0
                                        )
                                        if result >= 0:
                                            written = True
                                            break
                                    except Exception:
                                        break
                                if written:
                                    break
                            if written:
                                break

                            if not written:
                                try:
                                    extended_rect = fitz.Rect(text_rect.x0, text_rect.y0, expanded_rect.x1, min(max_expand_bottom, page_bottom))
                                    result = new_page.insert_textbox(
                                        extended_rect,
                                        translated_text,
                                        fontsize=6,
                                        fontname=font_name,
                                        color=text_color,
                                        align=0
                                    )
                                    if result >= 0:
                                        written = True
                                        break
                                except Exception:
                                    continue

                        if written:
                            success_count += 1
                            total_written += 1
                        else:
                            # 最后兜底：在矩形起点强制插入单行文本，避免丢失
                            try:
                                new_page.insert_text(
                                    (text_rect.x0, text_rect.y0 + 10),
                                    translated_text,
                                    fontsize=6,
                                    fontname="helv",
                                    color=text_color
                                )
                                success_count += 1
                                total_written += 1
                            except Exception as fallback_err:
                                print(f'[DEBUG] 块{idx+1}最终回退也失败: {str(fallback_err)[:60]}')

                    except Exception as e:
                        print(f'Insert textbox error on page {page_num + 1}: {e}')
                        self._add_log(f'⚠️ 第{page_num + 1}页块{idx + 1}写入失败: {str(e)[:100]}', 'error')

                if page_num < 3 or page_num >= total_pages - 3:
                    self._add_log(f'第 {page_num + 1} 页完成: 成功写入 {success_count}/{len(page_translations)} 个文本块', 'success')

                # 更新进度
                elapsed_time = time.time() - translation_start_time
                done_pages = page_num + 1
                est_remaining = _calc_remaining(elapsed_time, done_pages, total_pages)
                self._update_progress(
                    done_pages,
                    total_pages,
                    f'已写入 {done_pages}/{total_pages} 页',
                    elapsed_time=elapsed_time,
                    estimated_remaining=est_remaining
                )

            # 关闭原文档
            doc.close()

            self._add_log(f'✓ 总共写入 {total_written} 个文本块到PDF', 'success')

            if total_written == 0:
                self._add_log('⚠️ 警告：没有任何文本被写入！请检查上面的日志', 'error')

            # 保存翻译后的PDF
            self._add_log(f'正在保存翻译后的PDF到: {output_path}', 'info')

            # 检查输出路径
            output_dir = os.path.dirname(output_path)
            if output_dir and not os.path.exists(output_dir):
                self._add_log(f'⚠️ 输出目录不存在: {output_dir}', 'error')

            # 保存新文档（包含翻译后的文本和原图）
            new_doc.save(output_path)
            new_doc.close()

            # 验证文件是否保存成功
            if os.path.exists(output_path):
                file_size = os.path.getsize(output_path)
                self._add_log(f'✓ 文件保存成功！大小: {file_size / 1024:.2f} KB', 'success')
            else:
                self._add_log('✗ 文件保存失败！文件不存在', 'error')

            # 最终统计
            total_cost = self._calculate_cost()
            self._add_log(f'\n========== 翻译完成 ==========', 'success')
            self._add_log(f'输入tokens: {self.input_tokens:,}', 'info')
            self._add_log(f'输出tokens: {self.output_tokens:,}', 'info')
            self._add_log(f'预估费用: ${total_cost:.4f} USD', 'info')

            self._update_progress(total_pages, total_pages, '翻译完成！')
            print(f'Translation completed: {output_path}')

        except Exception as e:
            print(f'Fatal error in translate_pdf: {e}')
            import traceback
            traceback.print_exc()
            self._add_log(f'翻译过程发生严重错误: {str(e)}', 'error')
            if doc:
                doc.close()
            raise

    def translate_pdf_to_text(self, input_path, output_path, source_lang='auto', target_lang='zh', concurrency=4):
        """提取PDF文本，翻译成指定语言，生成TXT文件"""
        import concurrent.futures
        import threading

        total_start_time = time.time()

        self._add_log('========== 开始文本翻译任务 ==========', 'info')
        self._add_log(f'输入文件: {input_path}', 'info')
        self._add_log(f'输出文件: {output_path}', 'info')
        self._add_log(f'源语言: {source_lang}', 'info')
        self._add_log(f'目标语言: {target_lang}', 'info')
        self._add_log(f'并发线程数: {concurrency}', 'info')

        # 语言代码映射
        lang_map = {
            'zh': 'zh-cn',
            'en': 'en',
            'ja': 'ja',
            'ko': 'ko',
            'fr': 'fr',
            'de': 'de',
            'es': 'es',
            'ru': 'ru',
            'ar': 'ar'
        }

        normalized_source = lang_map.get(source_lang, source_lang)
        normalized_target = lang_map.get(target_lang, target_lang)

        # 打开PDF并提取文本
        extract_start = time.time()
        self._add_log('正在提取PDF文本...', 'info')
        doc = fitz.open(input_path)
        total_pages = len(doc)

        all_text_blocks = []
        current_position = 0

        for page_num in range(total_pages):
            self._check_cancelled()
            page = doc[page_num]
            text = page.get_text()

            if text and text.strip():
                all_text_blocks.append({
                    'page_num': page_num,
                    'text': text.strip()
                })
                current_position += len(text)

        doc.close()
        extract_time = time.time() - extract_start
        self._add_log(f'✓ 提取到 {len(all_text_blocks)} 页文本，共 {current_position} 个字符 (耗时: {extract_time:.1f}秒)', 'info')

        # 合并所有文本
        full_text = '\n\n'.join([block['text'] for block in all_text_blocks])
        self._add_log(f'合并后总字符数: {len(full_text)}', 'info')

        # 较大的文本块可减少 API 调用次数，文本版输出对版式约束也更低。
        chunk_size = 8000
        text_chunks = []
        for i in range(0, len(full_text), chunk_size):
            chunk = full_text[i:i + chunk_size]
            text_chunks.append(chunk)

        self._add_log(f'分成 {len(text_chunks)} 个文本块进行翻译 (每块约{chunk_size}字符)', 'info')

        # 并发翻译
        translation_start_time = time.time()
        total_chunks = len(text_chunks)
        completed_count = [0]
        lock = threading.Lock()
        results = {}
        api_times = []  # 记录每次API调用耗时

        def translate_chunk(chunk_info):
            """翻译单个文本块"""
            chunk_idx = chunk_info['index']
            text = chunk_info['text']

            try:
                self._check_cancelled()

                # 记录输入token
                input_tokens = self._estimate_tokens(text)

                # 翻译 - 计时
                api_start = time.time()
                if self.api_type == 'google':
                    translated = GoogleTranslator(source=normalized_source, target=normalized_target).translate(text)
                else:
                    translated = self._translate_text(text, source_lang, target_lang)
                api_time = time.time() - api_start
                api_times.append(api_time)

                # 记录输出token
                output_tokens = self._estimate_tokens(translated)

                current_num = chunk_idx + 1
                if self._should_emit_detail_log(current_num, total_chunks):
                    display_text = text[:100] + '...' if len(text) > 100 else text
                    display_translated = translated[:100] + '...' if len(translated) > 100 else translated
                    self._add_log(f'[原文 {current_num}/{total_chunks}] {display_text}', 'info')
                    self._add_log(f'[译文 {current_num}/{total_chunks}] {display_translated} (耗时: {api_time:.1f}s)', 'success')

                with lock:
                    self.input_tokens += input_tokens
                    self.output_tokens += output_tokens
                    completed_count[0] += 1

                    # 更新进度
                    elapsed_time = time.time() - translation_start_time
                    current = completed_count[0]
                    est_remaining = (elapsed_time / current) * (total_chunks - current) if current > 0 else 0
                    if self._should_emit_progress_update(current, total_chunks):
                        self._update_progress(
                            current,
                            total_chunks,
                            f'已翻译 {current}/{total_chunks} 个文本块...',
                            elapsed_time=elapsed_time,
                            estimated_remaining=est_remaining
                        )

                    # 记录翻译结果
                    results[chunk_idx] = translated

                return chunk_idx, translated

            except Exception as e:
                print(f'Translation error for chunk {chunk_idx}: {e}')
                with lock:
                    completed_count[0] += 1
                return chunk_idx, text  # 失败时返回原文

        # 创建任务列表
        chunk_tasks = [{'index': i, 'text': text_chunks[i]} for i in range(len(text_chunks))]

        self._add_log('开始翻译...', 'info')

        with concurrent.futures.ThreadPoolExecutor(max_workers=concurrency) as executor:
            futures = {executor.submit(translate_chunk, chunk): chunk for chunk in chunk_tasks}

            for future in concurrent.futures.as_completed(futures):
                try:
                    future.result()
                except Exception as e:
                    print(f'Future error: {e}')

        translation_time = time.time() - translation_start_time
        self._add_log('✓ 所有文本块翻译完成', 'success')

        # 输出耗时统计
        if api_times:
            avg_api_time = sum(api_times) / len(api_times)
            max_api_time = max(api_times)
            min_api_time = min(api_times)
            total_api_time = sum(api_times)
            self._add_log(f'📊 API耗时统计:', 'info')
            self._add_log(f'  - 总翻译时间: {translation_time:.1f}秒', 'info')
            self._add_log(f'  - API调用总耗时: {total_api_time:.1f}秒', 'info')
            self._add_log(f'  - 单次API平均: {avg_api_time:.1f}秒', 'info')
            self._add_log(f'  - 单次API最快: {min_api_time:.1f}秒', 'info')
            self._add_log(f'  - 单次API最慢: {max_api_time:.1f}秒', 'info')

        # 按顺序组合翻译结果
        translated_text = '\n\n'.join([results[i] for i in range(len(text_chunks))])

        # 保存为TXT文件
        save_start = time.time()
        self._add_log(f'正在保存翻译后的文本到: {output_path}', 'info')

        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(translated_text)

        save_time = time.time() - save_start

        total_time = time.time() - total_start_time
        self._add_log('✓ 文本翻译完成！', 'success')
        self._add_log(f'📊 各阶段耗时:', 'info')
        self._add_log(f'  - PDF文本提取: {extract_time:.1f}秒', 'info')
        self._add_log(f'  - 翻译API调用: {translation_time:.1f}秒', 'info')
        self._add_log(f'  - 文件保存: {save_time:.1f}秒', 'info')
        self._add_log(f'  - 总耗时: {total_time:.1f}秒', 'info')
        self._add_log(f'输入tokens: {self.input_tokens:,}', 'info')
        self._add_log(f'输出tokens: {self.output_tokens:,}', 'info')
        self._add_log(f'预估费用: ${self._calculate_cost():.4f} USD', 'info')
        self._add_log('=' * 40, 'info')
