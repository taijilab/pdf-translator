from flask import Flask, render_template, request, send_file, after_this_request, jsonify, Response
from werkzeug.utils import secure_filename
import os
from translator import PDFTranslator
import tempfile
import queue
import threading
import json

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 50 * 1024 * 1024  # 50MB max file size
app.config['UPLOAD_FOLDER'] = tempfile.gettempdir()
app.config['ALLOWED_EXTENSIONS'] = {'pdf'}

# 存储进度信息
progress_queues = {}

# 存储取消标志
cancel_flags = {}

# 允许的文件扩展名
def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in app.config['ALLOWED_EXTENSIONS']

@app.route('/')
def index():
    return render_template('index.html')

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

    # 保存临时文件
    filename = secure_filename(file.filename)
    filepath = os.path.join(app.config['UPLOAD_FOLDER'], f'temp_{filename}')
    file.save(filepath)

    try:
        # 分析PDF
        translator = PDFTranslator()
        analysis = translator.analyze_pdf(filepath)

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
    task_id = request.form.get('task_id', '')
    concurrency = int(request.form.get('concurrency', 4))  # 获取并发数，默认4

    # 调试：打印并发参数
    print(f"[DEBUG] Received concurrency parameter: {concurrency}")

    # 保存上传的文件
    filename = secure_filename(file.filename)
    filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
    file.save(filepath)

    # 创建进度队列
    progress_queue = queue.Queue()
    progress_queues[task_id] = progress_queue

    # 初始化取消标志
    cancel_flags[task_id] = False

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
                cancel_callback=check_cancelled
            )

            # 发送初始化日志
            log_callback('正在初始化翻译器...', 'info')

            output_filename = f'translated_{filename}'
            output_filepath = os.path.join(app.config['UPLOAD_FOLDER'], output_filename)

            translator.translate_pdf(
                filepath,
                output_filepath,
                source_lang=source_lang,
                target_lang=target_lang,
                concurrency=concurrency
            )

            progress_queue.put({'status': 'completed', 'output_file': output_filename})

        except Exception as e:
            error_msg = str(e)
            print(f'Translation error: {error_msg}')
            import traceback
            traceback.print_exc()

            if 'Translation cancelled by user' in error_msg:
                progress_queue.put({'status': 'cancelled', 'message': '翻译已取消'})
            else:
                log_callback(f'翻译失败: {error_msg}', 'error')
                progress_queue.put({'status': 'error', 'error': error_msg})
        finally:
            # 清理文件
            try:
                if os.path.exists(filepath):
                    os.remove(filepath)
            except:
                pass
            # 清理取消标志
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
    task_id = request.form.get('task_id', '')
    concurrency = int(request.form.get('concurrency', 4))

    # 保存上传的文件
    filename = secure_filename(file.filename)
    filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
    file.save(filepath)

    # 创建进度队列
    progress_queue = queue.Queue()
    progress_queues[task_id] = progress_queue

    # 初始化取消标志
    cancel_flags[task_id] = False

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
                cancel_callback=check_cancelled
            )

            # 发送初始化日志
            log_callback('正在提取PDF文本...', 'info')

            # 生成输出文件名
            base_name = filename.rsplit('.', 1)[0]
            output_filename = f'translated_{base_name}.txt'
            output_filepath = os.path.join(app.config['UPLOAD_FOLDER'], output_filename)

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
                'output_file': output_filename,
                'input_tokens': translator.input_tokens,
                'output_tokens': translator.output_tokens
            })

        except Exception as e:
            error_msg = str(e)
            print(f'Text translation error: {error_msg}')
            import traceback
            traceback.print_exc()

            if 'Translation cancelled by user' in error_msg:
                progress_queue.put({'status': 'cancelled', 'message': '翻译已取消'})
            else:
                log_callback(f'翻译失败: {error_msg}', 'error')
                progress_queue.put({'status': 'error', 'error': error_msg})
        finally:
            # 清理文件
            try:
                if os.path.exists(filepath):
                    os.remove(filepath)
            except:
                pass
            # 清理取消标志
            if task_id in cancel_flags:
                del cancel_flags[task_id]

    thread = threading.Thread(target=translate_text_in_background)
    thread.start()

    return jsonify({'status': 'processing', 'task_id': task_id})

@app.route('/cancel/<task_id>', methods=['POST'])
def cancel_translation(task_id):
    """取消翻译任务"""
    if task_id in cancel_flags:
        cancel_flags[task_id] = True
        return jsonify({'status': 'cancelling', 'message': '正在取消翻译...'})
    else:
        return jsonify({'error': 'Task not found'}), 404

@app.route('/progress/<task_id>')
def progress(task_id):
    """Server-Sent Events端点，用于实时推送进度"""
    def generate():
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
            if task_id in progress_queues:
                del progress_queues[task_id]

    return Response(generate(), mimetype='text/event-stream')

@app.route('/download/<filename>')
def download(filename):
    """下载翻译后的文件"""
    filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)

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

if __name__ == '__main__':
    # 本地运行：python app.py
    # 生产部署：gunicorn --workers 1 --threads 8 --timeout 0 app:app
    # 注意：必须单进程，progress_queues/cancel_flags 存于内存，多进程会丢失 SSE 进度流
    app.run(debug=False, use_reloader=False, host='0.0.0.0', port=5001, threaded=True)
