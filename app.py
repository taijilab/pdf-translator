from flask import Flask, render_template, request, send_file, after_this_request, jsonify, Response
from werkzeug.utils import secure_filename
import os
import subprocess
import shutil
import fitz
from translator import PDFTranslator
import tempfile
import queue
import threading
import json
import re
import time
import uuid
from collections import Counter

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
app = Flask(
    __name__,
    template_folder=os.path.join(BASE_DIR, 'templates'),
    static_folder=os.path.join(BASE_DIR, 'static')
)
app.config['MAX_CONTENT_LENGTH'] = 200 * 1024 * 1024  # 200MB max file size
app.config['UPLOAD_FOLDER'] = tempfile.gettempdir()
app.config['ALLOWED_EXTENSIONS'] = {'pdf'}


def _read_git_value(args, fallback):
    cmd = list(args)
    if cmd and cmd[0] == 'git':
        git_bin = '/usr/bin/git' if os.path.exists('/usr/bin/git') else (shutil.which('git') or 'git')
        cmd[0] = git_bin
    try:
        return subprocess.check_output(cmd, cwd=BASE_DIR, text=True).strip()
    except Exception:
        return fallback


GLOSSARY_PATH = os.path.join(BASE_DIR, 'glossary.json')


def get_build_metadata():
    app_version = os.environ.get('PDFAPP_VERSION', 'v1.0.0')
    build_number = os.environ.get('PDFAPP_BUILD') or _read_git_value(['git', 'rev-list', '--count', 'HEAD'], '0')
    build_commit = os.environ.get('PDFAPP_COMMIT') or _read_git_value(['git', 'rev-parse', '--short', 'HEAD'], 'dev')
    build_label = f'build {build_number} ({build_commit})'
    return {
        'app_version': app_version,
        'build_number': build_number,
        'build_commit': build_commit,
        'build_label': build_label,
    }


@app.after_request
def add_no_cache_headers(response):
    response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
    response.headers['Pragma'] = 'no-cache'
    response.headers['Expires'] = '0'
    build = get_build_metadata()
    response.headers['X-App-Build'] = f"{build['build_number']} ({build['build_commit']})"
    return response


@app.errorhandler(413)
def request_entity_too_large(e):
    return jsonify({'error': '文件过大（超过200MB），请压缩PDF后再试'}), 413

# 存储进度信息
progress_queues = {}

# 存储取消标志
cancel_flags = {}

# 存储任务工作区和输出文件信息
task_registry = {}

task_registry_lock = threading.Lock()

# 允许的文件扩展名
def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in app.config['ALLOWED_EXTENSIONS']

def normalize_task_id(raw_task_id):
    """规范化任务ID，防止注入和路径穿透。"""
    if not raw_task_id:
        return f"task_{uuid.uuid4().hex}"
    if re.fullmatch(r'[A-Za-z0-9_-]{1,64}', raw_task_id):
        return raw_task_id
    return f"task_{uuid.uuid4().hex}"

def create_task_workspace(task_id):
    """为任务创建独立临时目录。"""
    return tempfile.mkdtemp(prefix=f"pdf_task_{task_id}_", dir=app.config['UPLOAD_FOLDER'])


def load_glossary():
    if not os.path.exists(GLOSSARY_PATH):
        return []
    try:
        with open(GLOSSARY_PATH, 'r', encoding='utf-8') as f:
            data = json.load(f)
        if isinstance(data, dict):
            data = data.get('terms', [])
        if not isinstance(data, list):
            return []
        terms = []
        seen = set()
        for term in data:
            clean = " ".join(str(term).strip().split())
            if len(clean) < 2:
                continue
            key = clean.lower()
            if key in seen:
                continue
            seen.add(key)
            terms.append(clean)
        return terms
    except Exception:
        return []


def save_glossary(terms):
    clean_terms = []
    seen = set()
    for term in terms:
        clean = " ".join(str(term).strip().split())
        if len(clean) < 2:
            continue
        key = clean.lower()
        if key in seen:
            continue
        seen.add(key)
        clean_terms.append(clean)
    with open(GLOSSARY_PATH, 'w', encoding='utf-8') as f:
        json.dump({'terms': clean_terms}, f, ensure_ascii=False, indent=2)
    return clean_terms


def extract_glossary_candidates(text, existing_terms=None, limit=15):
    existing_terms = {term.lower() for term in (existing_terms or [])}
    pattern = re.compile(
        r'\b(?:[A-Z][A-Za-z0-9&®\-]+|[A-Z]{2,})(?:\s+(?:[A-Z][A-Za-z0-9&®\-]+|[A-Z]{2,})){0,3}\b'
    )
    candidates = []
    for match in pattern.finditer(text):
        term = " ".join(match.group(0).split())
        if len(term) < 2 or len(term) > 60:
            continue
        if term.lower() in existing_terms:
            continue
        if re.fullmatch(r'Page \d+ of \d+', term):
            continue
        candidates.append(term)

    counts = Counter(candidates)
    ranked = [
        term for term, count in counts.most_common()
        if count >= 2 and not re.fullmatch(r'(Chapter|Page|Appendix)\s+\w+', term)
    ]
    return ranked[:limit]


def parse_glossary_input(raw_glossary):
    if not raw_glossary:
        return []
    if isinstance(raw_glossary, list):
        return raw_glossary
    try:
        parsed = json.loads(raw_glossary)
        if isinstance(parsed, list):
            return parsed
    except Exception:
        pass
    return [line.strip() for line in str(raw_glossary).splitlines() if line.strip()]


def extract_pdf_text(filepath):
    try:
        doc = fitz.open(filepath)
        parts = [page.get_text("text") for page in doc]
        doc.close()
        return "\n".join(parts)
    except Exception:
        return ""

@app.route('/')
def index():
    build = get_build_metadata()
    return render_template(
        'index.html',
        app_version=build['app_version'],
        build_number=build['build_number'],
        build_commit=build['build_commit'],
        build_label=build['build_label'],
        glossary_terms=load_glossary()
    )


@app.route('/version')
def version():
    return jsonify(get_build_metadata())

@app.route('/analyze', methods=['POST'])
def analyze():
    """分析上传的PDF文件，返回页数、字数、语言等信息"""
    # 检查是否有文件
    if 'file' not in request.files:
        return jsonify({'error': '没有上传文件'}), 400

    file = request.files['file']

    # 检查文件名
    if file.filename == '':
        return jsonify({'error': '没有选择文件'}), 400

    if not allowed_file(file.filename):
        return jsonify({'error': '只支持PDF文件'}), 400

    temp_file = tempfile.NamedTemporaryFile(
        delete=False,
        suffix=".pdf",
        prefix="analyze_",
        dir=app.config['UPLOAD_FOLDER']
    )
    filepath = temp_file.name
    temp_file.close()
    file.save(filepath)

    try:
        # 分析PDF
        translator = PDFTranslator()
        analysis = translator.analyze_pdf(filepath)
        doc = extract_pdf_text(filepath)
        glossary_terms = load_glossary()
        analysis['glossary_terms'] = glossary_terms
        analysis['suggested_terms'] = extract_glossary_candidates(doc, glossary_terms)

        # 删除临时文件
        os.remove(filepath)

        # 根据页数估算翻译时间（批量模式）
        total_pages = analysis['total_pages']
        # 批量模式：约30-60页/分钟，取保守值30页/分钟
        estimated_time_minutes = max(1, total_pages / 30)

        # 转换为分钟和秒
        if estimated_time_minutes < 1:
            estimated_time_str = f"{int(estimated_time_minutes * 60)}秒"
        elif estimated_time_minutes < 60:
            estimated_time_str = f"{int(estimated_time_minutes)}分钟"
        else:
            hours = int(estimated_time_minutes // 60)
            mins = int(estimated_time_minutes % 60)
            estimated_time_str = f"{hours}小时{mins}分钟"

        analysis['estimated_time'] = estimated_time_str
        analysis['estimated_time_minutes'] = round(estimated_time_minutes, 1)

        return jsonify(analysis)

    except Exception as e:
        # 清理临时文件
        if os.path.exists(filepath):
            os.remove(filepath)
        return jsonify({'error': str(e)}), 500

@app.route('/translate', methods=['POST'])
def translate():
    # 检查是否有文件
    if 'file' not in request.files:
        return jsonify({'error': '没有上传文件'}), 400

    file = request.files['file']

    # 检查文件名
    if file.filename == '':
        return jsonify({'error': '没有选择文件'}), 400

    if not allowed_file(file.filename):
        return jsonify({'error': '只支持PDF文件'}), 400

    # 获取翻译参数
    api_type = request.form.get('api_type', 'google')
    api_key = request.form.get('api_key', '')
    source_lang = request.form.get('source_lang', 'auto')
    target_lang = request.form.get('target_lang', 'en')
    task_id = normalize_task_id(request.form.get('task_id', ''))
    concurrency = int(request.form.get('concurrency', 4))  # 获取并发数，默认4
    glossary_terms = parse_glossary_input(request.form.get('glossary_terms', '')) or load_glossary()

    # 调试：打印并发参数
    print(f"[DEBUG] Received concurrency parameter: {concurrency}")

    # 保存上传的文件到任务专属目录
    filename = secure_filename(file.filename)
    base_name, _ = os.path.splitext(filename)
    if not base_name:
        base_name = "document"

    task_dir = create_task_workspace(task_id)
    filepath = os.path.join(task_dir, f"input_{filename}")
    output_filename = f"translated_{base_name}.pdf"
    output_filepath = os.path.join(task_dir, output_filename)

    file.save(filepath)

    # 创建进度队列
    progress_queue = queue.Queue()
    with task_registry_lock:
        progress_queues[task_id] = progress_queue
        cancel_flags[task_id] = False
        task_registry[task_id] = {
            'task_dir': task_dir,
            'output_file': output_filename,
            'created_at': time.time()
        }

    # 进度回调函数
    def progress_callback(progress_data):
        # 使用json.dumps确保JSON格式正确
        progress_queue.put(progress_data)

    # 在新线程中执行翻译
    def translate_in_background():
        try:
            # 日志回调函数
            def log_callback(message, log_type='info'):
                log_entry = {
                    'type': 'log',
                    'message': message,
                    'log_type': log_type
                }
                progress_queue.put(log_entry)

            # 检查是否取消的函数
            def check_cancelled():
                if cancel_flags.get(task_id, False):
                    progress_queue.put({'status': 'cancelled'})
                    raise Exception('Translation cancelled by user')

            translator = PDFTranslator(
                api_type=api_type,
                api_key=api_key if api_key else None,
                progress_callback=progress_callback,
                log_callback=log_callback,
                cancel_callback=check_cancelled,
                glossary_terms=glossary_terms
            )

            # 发送初始化日志
            log_callback('正在初始化翻译器...', 'info')

            translator.translate_pdf(
                filepath,
                output_filepath,
                source_lang=source_lang,
                target_lang=target_lang,
                concurrency=concurrency
            )

            progress_queue.put({
                'status': 'completed',
                'task_id': task_id,
                'output_file': output_filename
            })

        except Exception as e:
            error_msg = str(e)
            import traceback
            tb_str = traceback.format_exc()
            print(f'Translation error: {error_msg}')
            print(tb_str)

            if 'Translation cancelled by user' in error_msg:
                progress_queue.put({'status': 'cancelled', 'message': '翻译已取消'})
            else:
                # 把完整堆栈发给前端，方便定位错误行
                log_callback(f'翻译失败: {error_msg}', 'error')
                log_callback(f'详细错误信息:\n{tb_str}', 'error')
                progress_queue.put({'status': 'error', 'error': error_msg})
        finally:
            # 清理文件
            try:
                if os.path.exists(filepath):
                    os.remove(filepath)
            except:
                pass
            # 清理取消标志
            with task_registry_lock:
                if task_id in cancel_flags:
                    del cancel_flags[task_id]

    thread = threading.Thread(target=translate_in_background)
    thread.start()

    return jsonify({'status': 'processing', 'task_id': task_id})

@app.route('/translate_text', methods=['POST'])
def translate_text():
    """提取PDF文本，翻译成指定语言，生成TXT文件"""
    # 检查是否有文件
    if 'file' not in request.files:
        return jsonify({'error': '没有上传文件'}), 400

    file = request.files['file']

    # 检查文件名
    if file.filename == '':
        return jsonify({'error': '没有选择文件'}), 400

    if not allowed_file(file.filename):
        return jsonify({'error': '只支持PDF文件'}), 400

    # 获取翻译参数
    api_type = request.form.get('api_type', 'google')
    api_key = request.form.get('api_key', '')
    source_lang = request.form.get('source_lang', 'auto')
    target_lang = request.form.get('target_lang', 'zh')
    task_id = normalize_task_id(request.form.get('task_id', ''))
    concurrency = int(request.form.get('concurrency', 4))
    glossary_terms = parse_glossary_input(request.form.get('glossary_terms', '')) or load_glossary()

    # 保存上传的文件到任务专属目录
    filename = secure_filename(file.filename)
    base_name, _ = os.path.splitext(filename)
    if not base_name:
        base_name = "document"

    task_dir = create_task_workspace(task_id)
    filepath = os.path.join(task_dir, f"input_{filename}")
    output_filename = f"translated_{base_name}.txt"
    output_filepath = os.path.join(task_dir, output_filename)

    file.save(filepath)

    # 创建进度队列
    progress_queue = queue.Queue()
    with task_registry_lock:
        progress_queues[task_id] = progress_queue
        cancel_flags[task_id] = False
        task_registry[task_id] = {
            'task_dir': task_dir,
            'output_file': output_filename,
            'created_at': time.time()
        }

    # 进度回调函数
    def progress_callback(progress_data):
        progress_queue.put(progress_data)

    # 在新线程中执行翻译
    def translate_text_in_background():
        try:
            # 日志回调函数
            def log_callback(message, log_type='info'):
                log_entry = {
                    'type': 'log',
                    'message': message,
                    'log_type': log_type
                }
                progress_queue.put(log_entry)

            # 检查是否取消的函数
            def check_cancelled():
                if cancel_flags.get(task_id, False):
                    progress_queue.put({'status': 'cancelled'})
                    raise Exception('Translation cancelled by user')

            translator = PDFTranslator(
                api_type=api_type,
                api_key=api_key if api_key else None,
                progress_callback=progress_callback,
                log_callback=log_callback,
                cancel_callback=check_cancelled,
                glossary_terms=glossary_terms
            )

            # 发送初始化日志
            log_callback('正在提取PDF文本...', 'info')

            # 执行文本翻译
            translator.translate_pdf_to_text(
                filepath,
                output_filepath,
                source_lang=source_lang,
                target_lang=target_lang,
                concurrency=concurrency
            )

            progress_queue.put({
                'status': 'completed',
                'task_id': task_id,
                'output_file': output_filename,
                'input_tokens': translator.input_tokens,
                'output_tokens': translator.output_tokens
            })

        except Exception as e:
            error_msg = str(e)
            import traceback
            tb_str = traceback.format_exc()
            print(f'Text translation error: {error_msg}')
            print(tb_str)

            if 'Translation cancelled by user' in error_msg:
                progress_queue.put({'status': 'cancelled', 'message': '翻译已取消'})
            else:
                log_callback(f'翻译失败: {error_msg}', 'error')
                log_callback(f'详细错误信息:\n{tb_str}', 'error')
                progress_queue.put({'status': 'error', 'error': error_msg})
        finally:
            # 清理文件
            try:
                if os.path.exists(filepath):
                    os.remove(filepath)
            except:
                pass
            # 清理取消标志
            with task_registry_lock:
                if task_id in cancel_flags:
                    del cancel_flags[task_id]

    thread = threading.Thread(target=translate_text_in_background)
    thread.start()

    return jsonify({'status': 'processing', 'task_id': task_id})

@app.route('/cancel/<task_id>', methods=['POST'])
def cancel_translation(task_id):
    """取消翻译任务"""
    with task_registry_lock:
        if task_id in cancel_flags:
            cancel_flags[task_id] = True
            return jsonify({'status': 'cancelling', 'message': '正在取消翻译...'})
    return jsonify({'error': 'Task not found'}), 404

@app.route('/progress/<task_id>')
def progress(task_id):
    """Server-Sent Events端点，用于实时推送进度"""
    def generate():
        with task_registry_lock:
            queue_obj = progress_queues.get(task_id)
        if not queue_obj:
            error_data = {'error': 'Invalid task ID'}
            yield f"data: {json.dumps(error_data)}\n\n"
            return

        try:
            while True:
                try:
                    # 增加超时时间到120秒
                    progress = queue_obj.get(timeout=120)

                    # 发送心跳，保持连接
                    if progress.get('type') == 'heartbeat':
                        yield ": heartbeat\n\n"
                        continue

                    # 使用json.dumps确保正确的JSON格式
                    yield f"data: {json.dumps(progress)}\n\n"

                    if progress.get('status') == 'completed':
                        break
                    elif progress.get('status') == 'error':
                        break
                    elif progress.get('status') == 'cancelled':
                        break

                except queue.Empty:
                    # 发送心跳保持连接
                    yield ": heartbeat\n\n"
                    continue

        except GeneratorExit:
            # 客户端断开连接
            print(f"Client disconnected from task {task_id}")
        except Exception as e:
            print(f"Error in progress stream: {e}")
            error_data = {'error': str(e)}
            yield f"data: {json.dumps(error_data)}\n\n"
        finally:
            # 清理队列
            with task_registry_lock:
                if task_id in progress_queues:
                    del progress_queues[task_id]

    return Response(generate(), mimetype='text/event-stream')

@app.route('/download/<task_id>/<filename>')
def download(task_id, filename):
    """下载翻译后的文件"""
    with task_registry_lock:
        task_meta = task_registry.get(task_id)

    if not task_meta:
        return jsonify({'error': 'Task not found'}), 404

    if filename != task_meta.get('output_file'):
        return jsonify({'error': 'File not found for task'}), 404

    filepath = os.path.join(task_meta['task_dir'], filename)
    if not os.path.exists(filepath):
        return jsonify({'error': 'File not ready'}), 404

    # 不删除文件，允许多次下载
    # 文件会在系统临时目录中自动清理，或用户可以手动清理

    # 根据文件扩展名设置MIME类型
    if filename.endswith('.txt'):
        mimetype = 'text/plain; charset=utf-8'
    else:
        mimetype = 'application/pdf'

    return send_file(
        filepath,
        as_attachment=True,
        download_name=filename,
        mimetype=mimetype
    )


@app.route('/glossary', methods=['GET', 'POST'])
def glossary():
    if request.method == 'GET':
        return jsonify({'terms': load_glossary()})

    data = request.get_json(silent=True) or {}
    terms = data.get('terms', [])
    saved = save_glossary(terms)
    return jsonify({'status': 'ok', 'terms': saved})

if __name__ == '__main__':
    # 本地运行：python app.py
    # 生产部署：gunicorn --workers 1 --threads 8 --timeout 0 app:app
    # 注意：必须单进程，progress_queues/cancel_flags 存于内存，多进程会丢失 SSE 进度流
    app.run(debug=False, use_reloader=False, host='0.0.0.0', port=5001, threaded=True)
