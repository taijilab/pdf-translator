from deep_translator import GoogleTranslator
import fitz  # PyMuPDF
import requests
import io
import re
import os
import time

class PDFTranslator:
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

    def __init__(self, api_type='google', api_key=None, progress_callback=None, log_callback=None, cancel_callback=None):
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

        # 只在需要时初始化translator
        if self.api_type == 'google':
            self._setup_translator()

    def _setup_translator(self):
        """设置翻译器"""
        # deep-translator 不需要预先设置translator实例
        pass

    def _is_translatable(self, text):
        """跳过纯数字、符号和极短块，减少无意义调用。"""
        stripped = text.strip()
        if len(stripped) <= 1:
            return False
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

    def _protect_formatting(self, text):
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

        return text, placeholders

    def _restore_formatting(self, text, placeholders):
        """恢复被保护的特殊格式字符"""
        if not placeholders:
            return text

        for placeholder, original in placeholders.items():
            text = text.replace(placeholder, original)

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
            prompt = f"请将以下文本翻译成{target_lang_name}，只返回翻译结果：\n\n{text}"

            # 记录输入token
            input_tokens = self._estimate_tokens(text)
            self.input_tokens += input_tokens

            # 只在第一次显示详细token信息
            if self.input_tokens == input_tokens:
                self._add_log(f'开始翻译，文本长度: {len(text)} 字符, 输入tokens: {input_tokens}', 'info')

            data = {
                "model": "deepseek/deepseek-chat",
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
            print(f'OpenRouter translation error: {error_msg}')

            # 检查是否是认证错误
            if '401' in error_msg or 'auth' in error_msg.lower() or 'cookie' in error_msg.lower():
                self._add_log('❌ OpenRouter API Key无效或未正确设置！', 'error')
                self._add_log('💡 建议：请使用"Google翻译（免费）"选项，无需API Key', 'info')
                self._add_log('💡 或者在OpenRouter获取有效的API Key: https://openrouter.ai/keys', 'info')
            else:
                self._add_log(f'OpenRouter翻译错误: {error_msg}', 'error')

            return text

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

            # 存储每页的翻译结果: {page_num: [(rect, translated_text, font_info), ...]}
            page_translations_map = {}

            # 收集所有需要翻译的文本块，并保存字体信息
            # 使用 span 级别提取以保留项目符号等特殊格式
            all_blocks = []
            for page_num in range(total_pages):
                try:
                    page = doc[page_num]
                    # 使用 dict 模式获取详细的字体信息
                    text_dict = page.get_text("dict")

                    block_idx = 0
                    for block in text_dict['blocks']:
                        if block['type'] != 0:
                            continue

                        # 按 span 分组提取文本和格式信息
                        spans_data = []
                        block_text = ""
                        for line in block['lines']:
                            for span in line['spans']:
                                text = span['text']
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

                        # 获取该块的主要字体信息（取第一个 span 的字体作为代表）
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
                            'text': block_text,
                            'rect': rect,
                            'font_info': font_info
                        })
                        block_idx += 1
                except Exception as page_err:
                    self._add_log(f'第{page_num+1}页文本提取失败（已跳过）: {page_err}', 'error')
                    continue

            total_blocks = len(all_blocks)
            self._add_log(f'总共提取到 {total_blocks} 个文本块', 'info')

            # 并发翻译所有文本块
            self._add_log('=' * 60, 'info')
            self._add_log('开始翻译...', 'info')

            # 将短文本块合并成批次，减少 API 调用次数
            groups = self._group_short_blocks(all_blocks, max_group_chars=4000, short_threshold=500)
            batch_count = sum(1 for t, _ in groups if t == 'batch' and len(_) > 1)
            single_count = len(groups) - batch_count
            self._add_log(f'文本块分组完成：{single_count} 个单独翻译，{batch_count} 个批次合并翻译', 'info')

            results = {}
            completed_count = [0]
            lock = threading.Lock()
            BATCH_SEPARATOR = "\n---SPLIT---\n"

            def _calc_remaining(elapsed, done, total):
                if done <= 0:
                    return 0
                return (elapsed / done) * (total - done)

            def translate_unit(unit):
                """翻译一个工作单元（单块或批次短文本块）"""
                group_type, blocks = unit
                api_start_time = time.time()

                # ---- 批次翻译（多个短文本块合并为一次 API 调用） ----
                if group_type == 'batch' and len(blocks) > 1:
                    texts = [self._clean_text(b['text']) for b in blocks]
                    combined = BATCH_SEPARATOR.join(texts)

                    # 先记录所有原文日志
                    with lock:
                        base_num = completed_count[0]
                        for i, text in enumerate(texts):
                            display = text[:200] + '...' if len(text) > 200 else text
                            self._add_log(f'[原文 {base_num + i + 1}/{total_blocks}] {display}', 'info')

                    try:
                        self._check_cancelled()
                        input_tokens = self._estimate_tokens(combined)

                        if self.api_type == 'google':
                            combined_translated = GoogleTranslator(
                                source=normalized_source, target=normalized_target
                            ).translate(combined)
                        else:
                            combined_translated = self._translate_text(combined, source_lang, target_lang)

                        api_time = time.time() - api_start_time
                        output_tokens = self._estimate_tokens(combined_translated)

                        # 拆分结果；如数量不匹配则逐一翻译（不回退原文）
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

                        with lock:
                            base_num = completed_count[0]
                            for i, (block, translated) in enumerate(zip(blocks, parts)):
                                display = translated[:200] + '...' if len(translated) > 200 else translated
                                self._add_log(
                                    f'[译文 {base_num + i + 1}/{total_blocks}] {display} (耗时: {api_time:.1f}s)',
                                    'success'
                                )
                                cache_key = (texts[i], source_lang, target_lang)
                                self._translation_cache[cache_key] = translated

                            self.input_tokens += input_tokens
                            self.output_tokens += output_tokens
                            completed_count[0] += len(blocks)

                            elapsed_time = time.time() - translation_start_time
                            current = completed_count[0]
                            est_remaining = _calc_remaining(elapsed_time, current, total_blocks)
                            self._update_progress(
                                current, total_blocks,
                                f'已翻译 {current}/{total_blocks} 个文本块...',
                                elapsed_time=elapsed_time, estimated_remaining=est_remaining
                            )

                        return [(b, t) for b, t in zip(blocks, parts)]

                    except Exception as e:
                        print(f'批次翻译失败: {e}')
                        with lock:
                            completed_count[0] += len(blocks)
                            elapsed_time = time.time() - translation_start_time
                            current = completed_count[0]
                            est_remaining = _calc_remaining(elapsed_time, current, total_blocks)
                            self._update_progress(
                                current, total_blocks,
                                f'已翻译 {current}/{total_blocks} 个文本块...',
                                elapsed_time=elapsed_time, estimated_remaining=est_remaining
                            )
                        return [(b, b['text']) for b in blocks]

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
                                current_num = completed_count[0] + 1
                                display_orig = text[:200] + '...' if len(text) > 200 else text
                                display_trans = translated[:200] + '...' if len(translated) > 200 else translated
                                self._add_log(f'[原文 {current_num}/{total_blocks}] {display_orig}', 'info')
                                self._add_log(f'[译文 {current_num}/{total_blocks}] {display_trans} (缓存命中)', 'success')
                                completed_count[0] += 1
                                elapsed_time = time.time() - translation_start_time
                                current = completed_count[0]
                                est_remaining = _calc_remaining(elapsed_time, current, total_blocks)
                                self._update_progress(
                                    current, total_blocks,
                                    f'已翻译 {current}/{total_blocks} 个文本块...',
                                    elapsed_time=elapsed_time, estimated_remaining=est_remaining
                                )
                            return [(block_info, translated)]

                        with lock:
                            current_num = completed_count[0] + 1
                            display_original = text[:200] + '...' if len(text) > 200 else text
                            self._add_log(f'[原文 {current_num}/{total_blocks}] {display_original}', 'info')

                        input_tokens = self._estimate_tokens(text)

                        if self.api_type == 'google':
                            translated = GoogleTranslator(
                                source=normalized_source, target=normalized_target
                            ).translate(text)
                        else:
                            translated = self._translate_text(text, source_lang, target_lang)

                        output_tokens = self._estimate_tokens(translated)
                        api_time = time.time() - api_start_time

                        with lock:
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
                            est_remaining = _calc_remaining(elapsed_time, current, total_blocks)
                            self._update_progress(
                                current, total_blocks,
                                f'已翻译 {current}/{total_blocks} 个文本块...',
                                elapsed_time=elapsed_time, estimated_remaining=est_remaining
                            )

                        return [(block_info, translated)]

                    except Exception as e:
                        print(f'Translation error for block {block_info["block_idx"]} (attempt {attempt + 1}): {e}')
                        if attempt == max_retries - 1:
                            with lock:
                                completed_count[0] += 1
                                elapsed_time = time.time() - translation_start_time
                                current = completed_count[0]
                                est_remaining = _calc_remaining(elapsed_time, current, total_blocks)
                                self._update_progress(
                                    current, total_blocks,
                                    f'已翻译 {current}/{total_blocks} 个文本块...',
                                    elapsed_time=elapsed_time, estimated_remaining=est_remaining
                                )
                            return [(block_info, block_info['text'])]
                        else:
                            time.sleep(0.5 * (2 ** attempt))  # 指数退避

            # 使用用户设置的并发数翻译（以 group 为并发单元）
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
                    self._add_log(f'--- 第 {page_num + 1} 页（已跳过，{len(page_translations)} 个文本块） ---', 'info')

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
                except Exception as page_setup_err:
                    self._add_log(f'第{page_num+1}页初始化失败（已跳过）: {page_setup_err}', 'error')
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

                for idx, (text_rect, translated_text, font_info) in enumerate(page_translations):
                    try:
                        written = False

                        # 获取原始字体信息
                        original_font = font_info.get('font', 'helv')
                        original_size = font_info.get('size', 11)
                        original_flags = font_info.get('flags', 0)

                        # 判断是否为粗体
                        is_bold = (original_flags & 16) != 0
                        is_italic = (original_flags & 1) != 0

                        # 检测翻译后文本的主要语言
                        chinese_chars = len(re.findall(r'[\u4e00-\u9fff]', translated_text))
                        total_chars = len(translated_text)
                        is_chinese = chinese_chars > total_chars * 0.3 if total_chars > 0 else False

                        # 根据翻译后文本语言选择字体
                        if is_chinese:
                            # 中文翻译：使用中文字体
                            if is_bold:
                                font_names = ['china-s', 'china-ss', 'china-t']
                            else:
                                font_names = ['china-s', 'china-t', 'china-ss']
                        else:
                            # 英文或其他语言：保留原始字体或使用通用英文字体
                            # PyMuPDF 支持的内置字体
                            if original_font and 'Times' in original_font:
                                font_names = ['times-roman', 'times-italic', 'times-bold', 'helv']
                            elif original_font and 'Helv' in original_font:
                                font_names = ['helv', 'helv-bold', 'times-roman']
                            elif original_font and 'Courier' in original_font:
                                font_names = ['courier', 'courier-bold', 'helv']
                            else:
                                # 通用英文字体，根据粗体/斜体选择
                                if is_bold and is_italic:
                                    font_names = ['helv-bolditalic', 'helv-bold', 'helv']
                                elif is_bold:
                                    font_names = ['helv-bold', 'helv', 'times-bold']
                                elif is_italic:
                                    font_names = ['helv-italic', 'helv', 'times-italic']
                                else:
                                    font_names = ['helv', 'times-roman', 'courier']

                        # 使用原始字体大小（中文适当缩小）
                        if is_chinese:
                            base_fontsize = max(6, original_size * 0.85)
                        else:
                            base_fontsize = max(6, original_size * 0.95)
                        font_sizes = [base_fontsize, base_fontsize * 0.85, base_fontsize * 0.7, 6]

                        for font_name in font_names:
                            # 逐步缩小字体尝试放入原矩形
                            for fontsize in font_sizes:
                                try:
                                    result = new_page.insert_textbox(
                                        text_rect,
                                        translated_text,
                                        fontsize=fontsize,
                                        fontname=font_name,
                                        color=(0, 0, 0),
                                        align=0
                                    )
                                    if result >= 0:
                                        written = True
                                        break
                                except Exception:
                                    break  # 该字体不可用，换下一个
                            if written:
                                break

                            # 原矩形装不下：扩展到页面底部再试
                            if not written:
                                try:
                                    extended_rect = fitz.Rect(
                                        text_rect.x0, text_rect.y0,
                                        text_rect.x1, max(text_rect.y1, page_bottom)
                                    )
                                    result = new_page.insert_textbox(
                                        extended_rect,
                                        translated_text,
                                        fontsize=6,
                                        fontname=font_name,
                                        color=(0, 0, 0),
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
                                    color=(0, 0, 0)
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

                # 显示当前翻译内容（包含原文和译文）
                display_text = text[:100] + '...' if len(text) > 100 else text
                display_translated = translated[:100] + '...' if len(translated) > 100 else translated
                # 发送原文日志
                self._add_log(f'[原文 {chunk_idx + 1}/{len(text_chunks)}] {display_text}', 'info')
                # 发送译文日志
                self._add_log(f'[译文 {chunk_idx + 1}/{len(text_chunks)}] {display_translated} (耗时: {api_time:.1f}s)', 'success')

                with lock:
                    self.input_tokens += input_tokens
                    self.output_tokens += output_tokens
                    completed_count[0] += 1

                    # 更新进度
                    elapsed_time = time.time() - translation_start_time
                    current = completed_count[0]
                    total_chunks = len(text_chunks)
                    est_remaining = (elapsed_time / current) * (total_chunks - current) if current > 0 else 0
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
