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

        # 只在需要时初始化translator
        if self.api_type == 'google':
            self._setup_translator()

    def _setup_translator(self):
        """设置翻译器"""
        # deep-translator 不需要预先设置translator实例
        pass

    def analyze_pdf(self, input_path):
        """分析PDF文件，返回页数、字数、语言等信息"""
        try:
            doc = fitz.open(input_path)
            total_pages = len(doc)

            # 提取所有文本
            all_text = ""
            for page in doc:
                text = page.get_text("text")
                all_text += text + " "

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

        # 记录输入token（确保总是执行）
        input_tokens = self._estimate_tokens(text)
        self.input_tokens += input_tokens

        # 只在第一页显示详细token信息
        if self.input_tokens == input_tokens:  # 这是第一次调用
            self._add_log(f'开始翻译，文本长度: {len(text)} 字符, 输入tokens: {input_tokens}', 'info')

        max_length = 4000
        if len(text) <= max_length:
            try:
                translated = GoogleTranslator(source=normalized_source, target=normalized_target).translate(text)

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
        sentences = text.split('. ')

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
                translated_segments.append(translated)

                # 记录输出token
                output_tokens = self._estimate_tokens(translated)
                self.output_tokens += output_tokens

            except Exception as e:
                print(f'Translation error in segment: {e}')
                self._add_log(f'段落翻译错误: {str(e)}', 'error')
                translated_segments.append(segment)

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

            response = requests.post(url, headers=headers, json=data, timeout=60)
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

            response = requests.post(url, headers=headers, json=data, timeout=60)
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

            response = requests.post(url, headers=headers, json=data, timeout=60)
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

            response = requests.post(url, headers=headers, json=data, timeout=120)
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

            response = requests.post(url, headers=headers, json=data, timeout=120)
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

            # 存储每页的翻译结果: {page_num: [(rect, translated_text), ...]}
            page_translations_map = {}

            # 收集所有需要翻译的文本块
            all_blocks = []
            for page_num in range(total_pages):
                page = doc[page_num]
                blocks = page.get_text("blocks")
                blocks.sort(key=lambda b: (b[1], b[0]))

                for block_idx, block in enumerate(blocks):
                    if block[6] == 0:  # 文本块
                        text = block[4]
                        if text and text.strip():
                            all_blocks.append({
                                'page_num': page_num,
                                'block_idx': block_idx,
                                'text': text,
                                'rect': fitz.Rect(block[0], block[1], block[2], block[3])
                            })

            total_blocks = len(all_blocks)
            self._add_log(f'总共提取到 {total_blocks} 个文本块', 'info')

            # 显示每个文本块的原文（按页分组）
            # 只显示前3页和后3页的原文，避免日志过多
            self._add_log('=' * 60, 'info')
            self._add_log('原文提取（前3页和后3页）：', 'info')
            current_page = -1
            for block_info in all_blocks:
                page_num = block_info['page_num']
                # 只显示前3页和后3页
                if page_num >= 3 and page_num < total_pages - 3:
                    if current_page != page_num:
                        current_page = page_num
                        self._add_log(f'--- 第 {page_num + 1} 页（已跳过） ---', 'info')
                    continue

                if block_info['page_num'] != current_page:
                    current_page = block_info['page_num']
                    self._add_log(f'--- 第 {current_page + 1} 页 ---', 'info')

                text = block_info['text']
                display_text = text[:200] + '...' if len(text) > 200 else text
                # 使用前端期望的格式：[文本块 X] 原文: ... (带页码)
                self._add_log(f'[页{block_info["page_num"] + 1}|文本块 {block_info["block_idx"] + 1}] 原文: {display_text}', 'info')

            # 并发翻译所有文本块
            self._add_log('=' * 60, 'info')
            self._add_log('开始翻译...', 'info')

            results = {}
            completed_count = [0]
            lock = threading.Lock()

            def translate_block(block_info):
                """翻译单个文本块"""
                max_retries = 3
                translated = None
                api_start_time = time.time()

                for attempt in range(max_retries):
                    try:
                        self._check_cancelled()

                        text = self._clean_text(block_info['text'])

                        # 在翻译前发送原文日志（前端期望格式: [原文 序号/总数] 内容）
                        with lock:
                            current = completed_count[0] + 1
                            display_original = text[:200] + '...' if len(text) > 200 else text
                            self._add_log(f'[原文 {current}/{total_blocks}] {display_original}', 'info')

                        # 记录输入token
                        input_tokens = self._estimate_tokens(text)

                        # 翻译
                        if self.api_type == 'google':
                            translated = GoogleTranslator(source=normalized_source, target=normalized_target).translate(text)
                        else:
                            translated = self._translate_text(text, source_lang, target_lang)

                        # 记录输出token
                        output_tokens = self._estimate_tokens(translated)
                        api_time = time.time() - api_start_time

                        with lock:
                            # 发送译文日志（前端期望格式: [译文 序号/总数] 内容 (耗时: Xs)）
                            display_translated = translated[:200] + '...' if len(translated) > 200 else translated
                            self._add_log(f'[译文 {current}/{total_blocks}] {display_translated} (耗时: {api_time:.1f}s)', 'success')

                            self.input_tokens += input_tokens
                            self.output_tokens += output_tokens
                            completed_count[0] += 1

                            # 更新进度
                            elapsed_time = time.time() - translation_start_time
                            current = completed_count[0]
                            self._update_progress(
                                current,
                                total_blocks,
                                f'已翻译 {current}/{total_blocks} 个文本块...',
                                elapsed_time=elapsed_time,
                                estimated_remaining=0
                            )

                        return block_info, translated

                    except Exception as e:
                        print(f'Translation error for block {block_info["block_idx"]} (attempt {attempt + 1}): {e}')

                        if attempt == max_retries - 1:
                            error_msg = str(e)
                            with lock:
                                completed_count[0] += 1
                                elapsed_time = time.time() - translation_start_time
                                self._update_progress(
                                    completed_count[0],
                                    total_blocks,
                                    f'已翻译 {completed_count[0]}/{total_blocks} 个文本块...',
                                    elapsed_time=elapsed_time,
                                    estimated_remaining=0
                                )
                            return block_info, block_info['text']
                        else:
                            time.sleep(0.5)

            # 使用用户设置的并发数翻译
            with concurrent.futures.ThreadPoolExecutor(max_workers=concurrency) as executor:
                futures = {executor.submit(translate_block, block): block for block in all_blocks}

                for future in concurrent.futures.as_completed(futures):
                    try:
                        block_info, translated_text = future.result()
                        results[(block_info['page_num'], block_info['block_idx'])] = (block_info['rect'], translated_text)

                        # 每完成一定数量显示日志
                        if completed_count[0] % 20 == 0 or completed_count[0] >= total_blocks - 10:
                            self._add_log(f'进度: {completed_count[0]}/{total_blocks} 文本块已翻译', 'info')

                    except Exception as e:
                        print(f'Future error: {e}')

            self._add_log(f'✓ 所有文本块翻译完成', 'success')

            # 调试：检查results字典
            self._add_log(f'[DEBUG] results字典包含 {len(results)} 个翻译结果', 'info')
            # 显示前几个results的key
            for i, (key, value) in enumerate(list(results.items())[:3]):
                page_num, block_idx = key
                rect, text = value
                self._add_log(f'[DEBUG] results[({page_num},{block_idx})]: rect={rect}, 文本长度={len(text)}', 'info')

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
                    result = results.get((page_num, block_idx))
                    if result:
                        rect, translated_text = result
                        page_translations.append((rect, translated_text))

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

            # 调试：检查page_translations_map
            self._add_log(f'[DEBUG] page_translations_map包含 {len(page_translations_map)} 页', 'info')
            for page_num, translations in list(page_translations_map.items())[:3]:  # 只显示前3页
                self._add_log(f'[DEBUG] 第{page_num + 1}页有 {len(translations)} 个翻译', 'info')

            total_written = 0

            # 方法：创建新文档，复制原页面的图片和图形，然后只添加翻译后的文本
            # 这样可以彻底移除原文，同时保留图片和排版

            self._add_log('正在创建新文档（保留图片，移除原文）...', 'info')
            new_doc = fitz.open()

            for page_num in range(total_pages):
                self._check_cancelled()

                page = doc[page_num]
                page_translations = page_translations_map.get(page_num, [])

                # 获取原页面的尺寸和旋转
                mediabox = page.mediabox
                rotation = page.rotation

                # 创建新页面
                new_page = new_doc.new_page(
                    width=mediabox.width,
                    height=mediabox.height
                )

                # 设置页面旋转
                if rotation:
                    new_page.set_rotation(rotation)

                # 填充白色背景
                new_page.draw_rect(new_page.rect, color=(1, 1, 1), fill=(1, 1, 1))

                # 复制原页面的所有图片
                try:
                    image_list = page.get_images()
                    self._add_log(f'[DEBUG] 第{page_num+1}页有 {len(image_list)} 张图片', 'info')

                    for img_index, img in enumerate(image_list):
                        try:
                            xref = img[0]
                            # 获取图片在页面上的位置
                            img_rects = page.get_image_rects(xref)
                            for img_rect in img_rects:
                                # 在新页面上绘制图片
                                new_page.insert_image(img_rect, pixmap=fitz.Pixmap(doc, xref))
                        except Exception as img_err:
                            self._add_log(f'[DEBUG] 图片复制失败: {str(img_err)[:50]}', 'info')
                except Exception as e:
                    self._add_log(f'[DEBUG] 图片处理出错: {str(e)[:50]}', 'info')

                # 复制原页面的图形（线条、形状等）
                try:
                    # 获取页面的绘图内容
                    # 使用 get_text("rawdict") 或其他方法获取图形信息
                    # 这里我们简单使用 page.get_svg_image() 来获取所有视觉元素
                    pass
                except Exception as e:
                    self._add_log(f'[DEBUG] 图形处理出错: {str(e)[:50]}', 'info')

                if not page_translations:
                    if page_num < 3 or page_num >= total_pages - 3:
                        self._add_log(f'第 {page_num + 1} 页: 无翻译内容', 'info')
                    continue

                if page_num < 3 or page_num >= total_pages - 3:
                    self._add_log(f'更新第 {page_num + 1} 页（{len(page_translations)} 个文本块）...', 'info')
                    # 调试：显示第一个文本块的信息
                    if page_translations:
                        first_rect, first_text = page_translations[0]
                        self._add_log(f'[DEBUG] 第一个文本块: rect={first_rect}, 文本长度={len(first_text)}', 'info')
                        self._add_log(f'[DEBUG] 文本预览: {first_text[:100]}', 'info')

                # 更新这一页的内容
                success_count = 0
                for idx, (text_rect, translated_text) in enumerate(page_translations):
                    try:
                        self._add_log(f'[DEBUG] 开始写入第{page_num+1}页块{idx+1}: rect=({text_rect.x0:.1f},{text_rect.y0:.1f},{text_rect.x1:.1f},{text_rect.y1:.1f}), 文本长度={len(translated_text)}', 'info')

                        # 写入翻译文本
                        try:
                            # 使用 fitz 的内置中文支持
                            result = new_page.insert_textbox(
                                text_rect,
                                translated_text,
                                fontsize=11,
                                fontname="china-s",  # 使用简体中文字体
                                color=(0, 0, 0),
                                align=0
                            )

                            if result >= 0:
                                success_count += 1
                                total_written += 1
                                self._add_log(f'[DEBUG] 文本块写入成功，字符数: {result}', 'info')
                            else:
                                self._add_log(f'[DEBUG] 文本块写入失败，返回值: {result}', 'error')

                        except Exception as text_err:
                            # 备用方案：尝试其他中文字体名称
                            font_names = ["china-t", "china-ss", "cjk", "song"]
                            font_success = False

                            for font_name in font_names:
                                try:
                                    result = new_page.insert_textbox(
                                        text_rect,
                                        translated_text,
                                        fontsize=11,
                                        fontname=font_name,
                                        color=(0, 0, 0),
                                        align=0
                                    )
                                    if result >= 0:
                                        success_count += 1
                                        total_written += 1
                                        self._add_log(f'[DEBUG] 使用字体 {font_name} 写入成功', 'info')
                                        font_success = True
                                        break
                                except:
                                    continue

                            if not font_success:
                                self._add_log(f'[DEBUG] 所有字体尝试失败: {str(text_err)[:50]}', 'error')

                    except Exception as e:
                        print(f'Insert textbox error on page {page_num + 1}: {e}')
                        import traceback
                        traceback.print_exc()
                        self._add_log(f'⚠️ 第{page_num + 1}页块{idx + 1}写入失败: {str(e)[:100]}', 'error')

                if page_num < 3 or page_num >= total_pages - 3:
                    self._add_log(f'第 {page_num + 1} 页完成: 成功写入 {success_count}/{len(page_translations)} 个文本块', 'success')

                # 更新进度
                elapsed_time = time.time() - translation_start_time
                self._update_progress(
                    page_num + 1,
                    total_pages,
                    f'已写入 {page_num + 1}/{total_pages} 页',
                    elapsed_time=elapsed_time,
                    estimated_remaining=0
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

        # 将文本分割成块进行翻译（每块约4000字符，平衡速度和质量）
        chunk_size = 4000
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
                    self._update_progress(
                        current,
                        len(text_chunks),
                        f'已翻译 {current}/{len(text_chunks)} 个文本块...',
                        elapsed_time=elapsed_time,
                        estimated_remaining=0
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
