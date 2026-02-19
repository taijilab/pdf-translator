const dropArea = document.getElementById('dropArea');
const fileInput = document.getElementById('fileInput');
const fileInfo = document.getElementById('fileInfo');
const translateForm = document.getElementById('translateForm');
const translateBtn = document.getElementById('translateBtn');
const btnText = document.getElementById('btnText');
const btnLoader = document.getElementById('btnLoader');
const progressSection = document.getElementById('progressSection');
const progressFill = document.getElementById('progressFill');
const progressPercentage = document.getElementById('progressPercentage');
const progressText = document.getElementById('progressText');
const inputTokensEl = document.getElementById('inputTokens');
const outputTokensEl = document.getElementById('outputTokens');
const estimatedCostEl = document.getElementById('estimatedCost');
const elapsedTimeEl = document.getElementById('elapsedTime');
const remainingTimeEl = document.getElementById('remainingTime');
const translationLog = document.getElementById('logContent');
const messageDiv = document.getElementById('message');
const apiTypeSelect = document.getElementById('apiType');
const apiKeySection = document.getElementById('apiKeySection');
const cancelBtn = document.getElementById('cancelBtn');
const fileAnalysisDiv = document.getElementById('fileAnalysis');
const apiKeyInput = document.getElementById('apiKey');
const concurrencyInput = document.getElementById('concurrency');
const comparisonContent = document.getElementById('processContent');
let currentTaskId = null;
let eventSource = null;
let translationStartTime = null;
let elapsedTimeTimer = null;

// 翻译对照数据
let translationData = [];  // [{page_num, block_idx, original, translated}]

// 从localStorage恢复上次的设置
function restoreSettings() {
    const savedApiType = localStorage.getItem('lastApiType');
    const savedApiKey = localStorage.getItem('lastApiKey');
    const savedConcurrency = localStorage.getItem('lastConcurrency');

    if (savedApiType) {
        apiTypeSelect.value = savedApiType;
        // 触发change事件以更新UI
        apiTypeSelect.dispatchEvent(new Event('change'));
    }

    if (savedApiKey) {
        apiKeyInput.value = savedApiKey;
    }

    if (savedConcurrency) {
        concurrencyInput.value = savedConcurrency;
    }
}

// 保存设置到localStorage
function saveSettings() {
    localStorage.setItem('lastApiType', apiTypeSelect.value);
    localStorage.setItem('lastApiKey', apiKeyInput.value);
    localStorage.setItem('lastConcurrency', concurrencyInput.value);
}

// API选择变化时显示/隐藏API密钥输入并保存
apiTypeSelect.addEventListener('change', (e) => {
    if (e.target.value === 'deepseek' || e.target.value === 'zhipu' || e.target.value === 'openrouter' || e.target.value === 'kimi' || e.target.value === 'gpt') {
        apiKeySection.style.display = 'block';
    } else {
        apiKeySection.style.display = 'none';
    }
    saveSettings();
});

// API密钥变化时保存
apiKeyInput.addEventListener('input', () => {
    saveSettings();
});

// 并发数变化时保存
concurrencyInput.addEventListener('input', () => {
    saveSettings();
});

// 页面加载时恢复设置
document.addEventListener('DOMContentLoaded', () => {
    restoreSettings();

    // 绑定拷贝日志按钮
    const copyLogBtn = document.getElementById('copyLogBtn');
    if (copyLogBtn) {
        copyLogBtn.addEventListener('click', copyLogs);
    }
});

// 更新翻译过程视图（显示原文和译文，覆盖式）
function updateProcessViewWithOriginal(originalText, current, total) {
    // 移除空状态
    const emptyState = comparisonContent.querySelector('.empty-state');
    if (emptyState) {
        emptyState.remove();
    }

    // 清空之前的内容，只显示当前翻译的内容
    comparisonContent.innerHTML = '';

    // 创建新行
    const row = document.createElement('div');
    row.className = 'translation-row-simple';
    row.id = `current-translation-row`;

    // 原文单元格
    const originalCell = document.createElement('div');
    originalCell.className = 'translation-cell-simple original-simple';
    originalCell.innerHTML = `<span class="progress-badge original-badge">原文 [${current}/${total}]</span> ${originalText}`;

    // 译文单元格（等待中）
    const translatedCell = document.createElement('div');
    translatedCell.className = 'translation-cell-simple translated-simple pending';
    translatedCell.innerHTML = `<span class="progress-badge translated-badge">译文 [${current}/${total}]</span> 正在翻译...`;
    translatedCell.id = 'pending-translation-cell';

    row.appendChild(originalCell);
    row.appendChild(translatedCell);
    comparisonContent.appendChild(row);

    // 自动滚动到底部
    comparisonContent.scrollTop = comparisonContent.scrollHeight;
}

// 更新译文（填充到待翻译的单元格）
function updatePendingTranslationSimple(translatedText, current, total, apiTime) {
    const pendingCell = document.getElementById('pending-translation-cell');
    if (pendingCell) {
        pendingCell.className = 'translation-cell-simple translated-simple';
        pendingCell.innerHTML = `<span class="progress-badge translated-badge">译文 [${current}/${total}] (${apiTime.toFixed(1)}s)</span> ${translatedText}`;
    }
}

// 更新翻译过程视图（简洁单列显示，覆盖式）- 已废弃
function updateProcessViewSimple(translatedText, current, total) {
    // 移除空状态
    const emptyState = comparisonContent.querySelector('.empty-state');
    if (emptyState) {
        emptyState.remove();
    }

    // 清空之前的内容，只显示当前翻译的内容
    comparisonContent.innerHTML = '';

    // 创建新行
    const row = document.createElement('div');
    row.className = 'translation-row-simple';

    // 翻译单元格（单列）
    const cell = document.createElement('div');
    cell.className = 'translation-cell-simple';
    cell.innerHTML = `<span class="progress-badge">[${current}/${total}]</span> ${translatedText}`;

    row.appendChild(cell);
    comparisonContent.appendChild(row);

    // 自动滚动到底部
    comparisonContent.scrollTop = comparisonContent.scrollHeight;
}

// 更新翻译过程视图（左右对照显示原文和译文）
function updateProcessView(originalText, translatedText = null) {
    // 移除空状态
    const emptyState = comparisonContent.querySelector('.empty-state');
    if (emptyState) {
        emptyState.remove();
    }

    // 创建新行
    const row = document.createElement('div');
    row.className = 'translation-row';

    // 原文单元格
    const originalCell = document.createElement('div');
    originalCell.className = 'translation-cell original';
    originalCell.textContent = originalText;

    // 译文单元格
    const translatedCell = document.createElement('div');
    translatedCell.className = 'translation-cell translated';
    if (translatedText) {
        translatedCell.textContent = translatedText;
    } else {
        translatedCell.classList.add('pending');
        translatedCell.textContent = '正在翻译...';
        translatedCell.id = 'pending-translation-' + Date.now();
    }

    row.appendChild(originalCell);
    row.appendChild(translatedCell);

    // 添加到内容区域
    comparisonContent.appendChild(row);

    // 自动滚动到底部
    comparisonContent.scrollTop = comparisonContent.scrollHeight;

    // 保持最多显示20行
    while (comparisonContent.children.length > 20) {
        comparisonContent.removeChild(comparisonContent.firstChild);
    }
}

// 更新待翻译的译文
function updatePendingTranslation(translatedText) {
    const pendingCell = comparisonContent.querySelector('.translation-cell.pending');
    if (pendingCell) {
        pendingCell.textContent = translatedText;
        pendingCell.classList.remove('pending');
    }
}

// 复制日志
// 复制日志
function copyLogs() {
    const copyBtn = document.getElementById('copyLogBtn');
    const logContent = document.getElementById('logContent');

    if (!logContent) {
        showMessage('没有日志可复制', 'info');
        return;
    }

    const textToCopy = logContent.innerText;

    if (!textToCopy || textToCopy.trim() === '') {
        showMessage('日志为空', 'info');
        return;
    }

    // 复制到剪贴板
    navigator.clipboard.writeText(textToCopy).then(() => {
        // 显示成功状态
        copyBtn.innerHTML = `
            <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke="currentColor" style="width: 16px; height: 16px; vertical-align: middle;">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 13l4 4L19 7" />
            </svg>
            已复制！
        `;
        copyBtn.classList.add('copied');

        showMessage('日志已复制到剪贴板', 'success');

        // 2秒后恢复按钮
        setTimeout(() => {
            copyBtn.innerHTML = `
                <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke="currentColor" style="width: 16px; height: 16px; vertical-align: middle;">
                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M8 16H6a2 2 0 01-2-2V6a2 2 0 012-2h8a2 2 0 012 2v2m-6 12h8a2 2 0 002-2v-8a2 2 0 00-2-2h-8a2 2 0 00-2 2v8a2 2 0 002 2z" />
                </svg>
                复制日志
            `;
            copyBtn.classList.remove('copied');
        }, 2000);
    }).catch(err => {
        console.error('复制失败:', err);
        showMessage('复制失败，请手动选择日志复制', 'error');
    });
}

// 点击上传区域
dropArea.addEventListener('click', () => {
    fileInput.click();
});

// 文件选择
fileInput.addEventListener('change', (e) => {
    handleFile(e.target.files[0]);
});

// 拖拽上传
dropArea.addEventListener('dragover', (e) => {
    e.preventDefault();
    dropArea.classList.add('dragover');
});

dropArea.addEventListener('dragleave', () => {
    dropArea.classList.remove('dragover');
});

dropArea.addEventListener('drop', (e) => {
    e.preventDefault();
    dropArea.classList.remove('dragover');

    const file = e.dataTransfer.files[0];
    if (file && file.type === 'application/pdf') {
        handleFile(file);
    } else {
        showMessage('请上传PDF文件', 'error');
    }
});

// 处理文件
async function handleFile(file) {
    if (file) {
        fileInfo.textContent = `已选择: ${file.name} (${formatFileSize(file.size)})`;

        // 分析文件
        try {
            const formData = new FormData();
            formData.append('file', file);

            showMessage('正在分析文件...', 'info');

            const response = await fetch('/analyze', {
                method: 'POST',
                body: formData
            });

            const data = await response.json();

            if (data.error) {
                showMessage(data.error, 'error');
                return;
            }

            // 显示文件信息
            document.getElementById('totalPages').textContent = data.total_pages;
            document.getElementById('charCount').textContent = data.char_count.toLocaleString();
            document.getElementById('detectedLang').textContent = data.lang_name;
            document.getElementById('estimatedTime').textContent = data.estimated_time;

            fileAnalysisDiv.style.display = 'block';

            // 根据检测到的语言自动设置源语言
            if (data.lang_code && data.lang_code !== 'unknown') {
                const sourceLangSelect = document.getElementById('sourceLang');
                // 尝试匹配语言代码
                let matchedOption = null;
                for (let i = 0; i < sourceLangSelect.options.length; i++) {
                    if (sourceLangSelect.options[i].value === data.lang_code) {
                        matchedOption = sourceLangSelect.options[i];
                        break;
                    }
                }

                // 如果找到匹配的语言，设置源语言
                if (matchedOption) {
                    sourceLangSelect.value = data.lang_code;
                    console.log(`自动设置源语言为: ${matchedOption.text}`);
                }
            }

        } catch (error) {
            console.error('Analysis error:', error);
            showMessage('文件分析失败', 'error');
        }
    }
}

// 格式化文件大小
function formatFileSize(bytes) {
    if (bytes === 0) return '0 Bytes';
    const k = 1024;
    const sizes = ['Bytes', 'KB', 'MB', 'GB'];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return Math.round(bytes / Math.pow(k, i) * 100) / 100 + ' ' + sizes[i];
}

// 显示消息
function showMessage(text, type = 'success') {
    messageDiv.textContent = text;
    messageDiv.className = `message ${type}`;
    messageDiv.hidden = false;

    setTimeout(() => {
        messageDiv.hidden = true;
    }, 5000);
}

// 添加日志（简化版）
function addLog(message, type = 'info') {
    const logEntry = document.createElement('div');
    logEntry.className = `log-entry ${type}`;

    // 只显示文本，不添加时间戳
    logEntry.textContent = message;

    translationLog.appendChild(logEntry);
    translationLog.scrollTop = translationLog.scrollHeight;

    // 限制日志条目数量
    const maxLogEntries = 200;
    while (translationLog.children.length > maxLogEntries) {
        translationLog.removeChild(translationLog.firstChild);
    }
}

// 更新进度
function updateProgress(data) {
    const percentage = data.percentage || 0;
    progressFill.style.width = percentage + '%';
    progressPercentage.textContent = percentage + '%';
    progressText.textContent = data.message || '处理中...';

    // 更新时间统计
    if (data.elapsed_time !== undefined) {
        elapsedTimeEl.textContent = formatTime(data.elapsed_time);
    }
    if (data.estimated_remaining !== undefined) {
        if (data.estimated_remaining > 0) {
            remainingTimeEl.textContent = formatTime(data.estimated_remaining);
        } else {
            remainingTimeEl.textContent = '计算中...';
        }
    }

    // 更新token统计
    if (data.input_tokens !== undefined) {
        inputTokensEl.textContent = data.input_tokens.toLocaleString();
    }
    if (data.output_tokens !== undefined) {
        outputTokensEl.textContent = data.output_tokens.toLocaleString();
    }
    if (data.estimated_cost !== undefined) {
        estimatedCostEl.textContent = '$' + data.estimated_cost.toFixed(4);
    }
}

// 格式化时间
function formatTime(seconds) {
    if (seconds < 60) {
        return Math.round(seconds) + '秒';
    } else if (seconds < 3600) {
        const mins = Math.floor(seconds / 60);
        const secs = Math.round(seconds % 60);
        return `${mins}分${secs}秒`;
    } else {
        const hours = Math.floor(seconds / 3600);
        const mins = Math.floor((seconds % 3600) / 60);
        return `${hours}小时${mins}分钟`;
    }
}

// 表单提交
translateForm.addEventListener('submit', async (e) => {
    e.preventDefault();

    const file = fileInput.files[0];
    if (!file) {
        showMessage('请选择一个PDF文件', 'error');
        return;
    }

    // 验证API密钥
    const apiType = apiTypeSelect.value;
    const apiKey = document.getElementById('apiKey').value;
    if ((apiType === 'deepseek' || apiType === 'zhipu' || apiType === 'openrouter' || apiType === 'kimi' || apiType === 'gpt') && !apiKey) {
        showMessage('⚠️ 付费API需要API密钥。建议使用"Google翻译 (免费)"选项，无需API Key！', 'error');
        return;
    }

    // 禁用按钮并显示取消按钮
    translateBtn.disabled = true;
    btnText.textContent = '翻译中...';
    btnLoader.hidden = false;
    cancelBtn.hidden = false;
    progressSection.hidden = false;
    messageDiv.hidden = true;
    translationLog.innerHTML = ''; // 清空日志
    clearComparisonView(); // 清空对照视图
    document.getElementById('completionActions').hidden = true; // 隐藏完成按钮

    // 生成任务ID
    const taskId = 'task_' + Date.now()
    currentTaskId = taskId;;

    // 添加初始日志
    addLog('开始翻译任务...', 'info');
    addLog(`文件: ${file.name}`, 'info');
    addLog(`翻译服务: ${apiTypeSelect.options[apiTypeSelect.selectedIndex].text}`, 'info');
    addLog(`源语言: ${document.getElementById('sourceLang').value}`, 'info');
    addLog(`目标语言: ${document.getElementById('targetLang').value}`, 'info');
    addLog(`输出格式: ${document.getElementById('outputFormat').value}`, 'info');

    // 获取并发数
    const concurrency = parseInt(document.getElementById('concurrency').value) || 4;
    console.log(`[DEBUG] Concurrency set to: ${concurrency}`);  // 调试日志
    addLog(`并发线程数: ${concurrency}`, 'info');

    // 获取输出格式
    const outputFormat = document.getElementById('outputFormat').value;

    const formData = new FormData();
    formData.append('file', file);
    formData.append('api_type', apiType);
    formData.append('api_key', apiKey);
    formData.append('source_lang', document.getElementById('sourceLang').value);
    formData.append('target_lang', document.getElementById('targetLang').value);
    formData.append('task_id', taskId);
    formData.append('concurrency', concurrency);

    // 启动实时计时器
    startElapsedTimeTimer();

    try {
        // 根据输出格式选择不同的API端点
        const endpoint = outputFormat === 'text' ? '/translate_text' : '/translate';

        // 启动翻译任务
        const response = await fetch(endpoint, {
            method: 'POST',
            body: formData
        });

        if (!response.ok) {
            const error = await response.json();
            throw new Error(error.error || '翻译失败');
        }

        const result = await response.json();

        if (result.status === 'processing') {
            // 连接到SSE流获取进度
            connectToProgressStream(taskId, file.name);
        } else {
            throw new Error('Unknown status');
        }

    } catch (error) {
        addLog(`错误: ${error.message}`, 'error');
        showMessage(error.message, 'error');
        resetButton();
    }
});

// 取消翻译按钮
cancelBtn.addEventListener('click', async () => {
    if (!currentTaskId) return;

    try {
        addLog('正在取消翻译...', 'info');

        const response = await fetch(`/cancel/${currentTaskId}`, {
            method: 'POST'
        });

        if (!response.ok) {
            throw new Error('取消请求失败');
        }

        addLog('翻译已取消', 'success');
        showMessage('翻译已取消', 'success');

        // 关闭SSE连接
        if (eventSource) {
            eventSource.close();
        }

        resetButton();

    } catch (error) {
        addLog(`取消失败: ${error.message}`, 'error');
        showMessage(error.message, 'error');
    }
});

// 连接到进度流
function connectToProgressStream(taskId, filename) {
    // 关闭旧的连接
    if (eventSource) {
        eventSource.close();
    }

    eventSource = new EventSource(`/progress/${taskId}`);

    eventSource.onopen = () => {
        console.log('SSE连接已建立');
    };

    eventSource.onmessage = (event) => {
        try {
            // 跳过心跳消息
            if (event.data.startsWith(':')) {
                return;
            }

            const data = JSON.parse(event.data);

            if (data.error) {
                addLog(`错误: ${data.error}`, 'error');
                showMessage(data.error, 'error');
                eventSource.close();
                resetButton();
                return;
            }

            if (data.status === 'completed') {
                addLog('翻译完成！', 'success');
                updateProgress({
                    percentage: 100,
                    message: '翻译完成',
                    input_tokens: data.input_tokens || 0,
                    output_tokens: data.output_tokens || 0,
                    estimated_cost: data.estimated_cost || 0
                });

                // 显示预览和下载按钮
                showCompletionActions(data.output_file);

                showMessage('翻译完成！请预览或下载文件', 'success');
                eventSource.close();
                currentTaskId = null;
                resetButton();
                return;
            }

            if (data.status === 'cancelled') {
                addLog('翻译已被用户取消', 'info');
                showMessage('翻译已取消', 'info');
                eventSource.close();
                currentTaskId = null;
                resetButton();
                return;
            }

            if (data.status === 'error') {
                addLog(`错误: ${data.error}`, 'error');
                showMessage(data.error, 'error');
                eventSource.close();
                currentTaskId = null;
                resetButton();
                return;
            }

            // 处理日志消息
            if (data.type === 'log') {
                addLog(data.message, data.log_type || 'info');

                // 检测新格式的原文日志: [原文 序号/总数] 内容
                const originalMatch = data.message.match(/\[原文 (\d+)\/(\d+)\] (.+)/);
                if (originalMatch) {
                    const current = parseInt(originalMatch[1]);
                    const total = parseInt(originalMatch[2]);
                    const originalText = originalMatch[3].trim();
                    // 显示原文，等待译文
                    updateProcessViewWithOriginal(originalText, current, total);
                    return;
                }

                // 检测新格式的译文日志: [译文 序号/总数] 内容 (耗时: Xs)
                const translatedMatch = data.message.match(/\[译文 (\d+)\/(\d+)\] (.+?) \(耗时: ([\d.]+)s\)/);
                if (translatedMatch) {
                    const current = parseInt(translatedMatch[1]);
                    const total = parseInt(translatedMatch[2]);
                    const translatedText = translatedMatch[3].trim();
                    const apiTime = parseFloat(translatedMatch[4]);
                    // 更新译文
                    updatePendingTranslationSimple(translatedText, current, total, apiTime);
                    return;
                }

                return;
            }

            // 更新进度
            if (data.current !== undefined && data.total !== undefined) {
                updateProgress(data);

                // 添加详细日志（如果有的话）
                if (data.message && !data.message.includes('正在翻译第')) {
                    // 只记录非标准的进度消息
                    addLog(data.message, 'info');
                }
            }

        } catch (e) {
            console.error('Error parsing SSE data:', event.data, e);
        }
    };

    eventSource.onerror = (error) => {
        console.error('SSE connection error:', error);

        // 检查连接状态
        if (eventSource.readyState === EventSource.CLOSED) {
            addLog('连接已关闭', 'error');
            resetButton();
        } else if (eventSource.readyState === EventSource.CONNECTING) {
            addLog('正在重新连接...', 'info');
        } else {
            addLog('连接错误，正在重试...', 'error');
            // 3秒后尝试重连
            setTimeout(() => {
                if (currentTaskId === taskId) {
                    connectToProgressStream(taskId, filename);
                }
            }, 3000);
        }
    };
}

// 下载文件
async function downloadFile(filename) {
    try {
        const response = await fetch(`/download/${filename}`);
        if (!response.ok) throw new Error('下载失败');

        const blob = await response.blob();
        const url = window.URL.createObjectURL(blob);
        const a = document.createElement('a');

        // 确保在当前窗口下载，不打开新标签页
        a.style.display = 'none';
        a.href = url;
        a.download = filename;
        // 关键：不设置target，保持在当前窗口
        a.setAttribute('target', '_self');

        document.body.appendChild(a);
        a.click();

        // 清理
        setTimeout(() => {
            document.body.removeChild(a);
            window.URL.revokeObjectURL(url);
        }, 100);

        addLog(`✅ 文件已下载: ${filename}`, 'success');
        showMessage(`✅ 文件已下载: ${filename}`, 'success');
    } catch (error) {
        addLog(`❌ 下载失败: ${error.message}`, 'error');
        showMessage(`❌ 下载失败: ${error.message}`, 'error');
    }
}

// 开始实时计时器
function startElapsedTimeTimer() {
    // 停止旧的计时器（如果有）
    stopElapsedTimeTimer();

    translationStartTime = Date.now();

    // 每秒更新已用时间
    elapsedTimeTimer = setInterval(() => {
        if (translationStartTime) {
            const elapsed = (Date.now() - translationStartTime) / 1000;
            elapsedTimeEl.textContent = formatTime(elapsed);
        }
    }, 1000);
}

// 停止实时计时器
function stopElapsedTimeTimer() {
    if (elapsedTimeTimer) {
        clearInterval(elapsedTimeTimer);
        elapsedTimeTimer = null;
    }
    translationStartTime = null;
}

// 重置按钮
function resetButton() {
    translateBtn.disabled = false;
    btnText.textContent = '开始翻译';
    btnLoader.hidden = true;
    cancelBtn.hidden = true;
    currentTaskId = null;
    stopElapsedTimeTimer(); // 停止计时器
}

// 清空翻译过程视图
function clearComparisonView() {
    comparisonContent.innerHTML = `
        <div class="empty-state">
            <p>等待翻译...</p>
        </div>
    `;
}

// 显示完成后的操作按钮
function showCompletionActions(outputFile) {
    const completionActions = document.getElementById('completionActions');
    const downloadBtn = document.getElementById('downloadBtn');
    const previewBtn = document.getElementById('previewBtn');

    // 保存文件名
    downloadBtn.dataset.filename = outputFile;
    previewBtn.dataset.filename = outputFile;

    // 显示按钮区域
    completionActions.hidden = false;

    // 绑定下载按钮事件
    downloadBtn.onclick = () => {
        downloadFile(outputFile);
    };

    // 绑定预览按钮事件
    previewBtn.onclick = () => {
        previewFile(outputFile);
    };
}

// 预览文件
async function previewFile(filename) {
    // 检查是否是TXT文件
    if (filename.endsWith('.txt')) {
        // 使用模态框预览文本
        showTextPreview(filename);
    } else {
        // PDF文件在新窗口打开
        const previewUrl = `/download/${filename}`;
        window.open(previewUrl, '_blank');
    }
}

// 显示文本预览模态框
async function showTextPreview(filename) {
    const modal = document.getElementById('previewModal');
    const previewText = document.getElementById('previewText');
    const previewInfo = document.getElementById('previewInfo');
    const modalClose = document.getElementById('modalClose');
    const copyPreviewBtn = document.getElementById('copyPreviewBtn');

    // 显示模态框
    modal.hidden = false;
    previewText.textContent = '加载中...';

    try {
        const response = await fetch(`/download/${filename}`);
        if (!response.ok) throw new Error('加载失败');

        const text = await response.text();
        previewText.textContent = text;
        previewInfo.textContent = `字符数: ${text.length.toLocaleString()}`;

        // 绑定复制按钮
        copyPreviewBtn.onclick = () => {
            navigator.clipboard.writeText(text).then(() => {
                const originalText = copyPreviewBtn.innerHTML;
                copyPreviewBtn.innerHTML = '✓ 已复制';
                setTimeout(() => {
                    copyPreviewBtn.innerHTML = originalText;
                }, 2000);
            });
        };

    } catch (error) {
        previewText.textContent = `加载失败: ${error.message}`;
    }

    // 绑定关闭按钮
    modalClose.onclick = () => {
        modal.hidden = true;
    };

    // 点击背景关闭
    modal.onclick = (e) => {
        if (e.target === modal) {
            modal.hidden = true;
        }
    };
}
