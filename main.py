import sys
import os
import json
import threading
import time
from pathlib import Path

from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QSlider, QLabel, QComboBox, QCheckBox,
    QGroupBox, QMessageBox, QSystemTrayIcon, QMenu, QAction, QFrame,
    QScrollArea
)
from PyQt5.QtCore import Qt, QTimer, pyqtSignal, QObject
from PyQt5.QtGui import QIcon, QFont

import sounddevice as sd
from pygame import mixer
from pynput import keyboard

# 尝试导入vosk，如果失败则禁用语音识别
try:
    import vosk
    VOSK_AVAILABLE = True
except ImportError:
    VOSK_AVAILABLE = False
    print("Vosk未安装，语音识别功能不可用")

# 配置文件路径
CONFIG_FILE = Path.home() / ".guanyu_song_config.json"
# 使用列表保持顺序
DEFAULT_HOTKEY = ['alt', 'g', 'y']
# 默认关键词列表（最多10个）
DEFAULT_KEYWORDS = ['释怀', '天意']
# 默认模型路径
DEFAULT_MODEL_PATH = 'vosk-model-small-cn-0.22'
# 音乐预设
PRESET_CLASSIC = {
    'name': '经典模式',
    'files': ['guanyu_song.mp3']
}
PRESET_GACHA = {
    'name': '抽卡模式',
    'files': ['guanyu_song_1.mp3', 'guanyu_song_2.mp3', 'guanyu_song_3.mp3', 'guanyu_song_4.mp3']
}

# 按键优先级排序（用于显示）
KEY_PRIORITY = {
    'ctrl': 0, 'alt': 1, 'shift': 2, 'win': 3, 'cmd': 3
}


class SignalEmitter(QObject):
    """用于线程间通信的信号发射器"""
    trigger_play = pyqtSignal()
    keyword_detected = pyqtSignal(str)  # 传递检测到的关键词
    hotkey_captured = pyqtSignal(list)  # 新增：快捷键捕获完成信号


class HotkeyListener:
    """热键监听器"""
    def __init__(self, hotkey_list, callback):
        # 存储标准化后的热键集合
        self.hotkey_set = set(self._normalize_hotkey_list(hotkey_list))
        self.current_keys = set()
        self.callback = callback
        self.listener = None
        self.lock = threading.Lock()
        self.triggered = False  # 防止重复触发
        
    def _normalize_hotkey_list(self, hotkey_list):
        """标准化热键列表"""
        return [self._normalize_single_key(k) for k in hotkey_list]
    
    def _normalize_single_key(self, key_name):
        """标准化单个按键名称"""
        key_name = str(key_name).lower().strip()
        # 统一Alt键
        if key_name in ('alt_l', 'alt_r', 'alt_gr', 'altgr'):
            return 'alt'
        # 统一Ctrl键
        if key_name in ('ctrl_l', 'ctrl_r', 'control', 'control_l', 'control_r'):
            return 'ctrl'
        # 统一Shift键
        if key_name in ('shift_l', 'shift_r'):
            return 'shift'
        # 统一Windows/Command键
        if key_name in ('cmd', 'cmd_l', 'cmd_r', 'win', 'super', 'super_l', 'super_r'):
            return 'win'
        return key_name
        
    def normalize_key(self, key):
        """标准化pynput按键对象"""
        try:
            if hasattr(key, 'char') and key.char:
                return key.char.lower()
            elif hasattr(key, 'name') and key.name:
                return self._normalize_single_key(key.name)
            else:
                key_str = str(key).lower().replace('key.', '')
                return self._normalize_single_key(key_str)
        except Exception:
            return str(key).lower()
    
    def on_press(self, key):
        with self.lock:
            normalized = self.normalize_key(key)
            self.current_keys.add(normalized)
            
            # 检查是否匹配热键组合
            if not self.triggered and self.hotkey_set.issubset(self.current_keys):
                self.triggered = True
                # 使用线程调用回调，避免阻塞
                threading.Thread(target=self._safe_callback, daemon=True).start()
    
    def _safe_callback(self):
        """安全地执行回调"""
        try:
            self.callback()
        except Exception as e:
            print(f"热键回调错误: {e}")
            
    def on_release(self, key):
        with self.lock:
            normalized = self.normalize_key(key)
            self.current_keys.discard(normalized)
            # 当所有热键都释放后，重置触发状态
            if not self.hotkey_set.issubset(self.current_keys):
                self.triggered = False
        
    def start(self):
        if self.listener is None or not self.listener.running:
            self.listener = keyboard.Listener(
                on_press=self.on_press,
                on_release=self.on_release
            )
            self.listener.start()
        
    def stop(self):
        if self.listener and self.listener.running:
            self.listener.stop()
            self.listener = None
            
    def update_hotkey(self, new_hotkey_list):
        with self.lock:
            self.hotkey_set = set(self._normalize_hotkey_list(new_hotkey_list))
            self.current_keys.clear()
            self.triggered = False


class HotkeyCapture:
    """快捷键捕获器 - 独立类处理快捷键捕获"""
    def __init__(self, callback):
        self.callback = callback  # 捕获完成后的回调
        self.captured_keys = set()
        self.listener = None
        self.lock = threading.Lock()
        self.capture_timer = None
        self.is_capturing = False
        
    def _normalize_key(self, key):
        """标准化按键"""
        try:
            if hasattr(key, 'char') and key.char:
                return key.char.lower()
            elif hasattr(key, 'name') and key.name:
                name = key.name.lower()
                if name in ('alt_l', 'alt_r', 'alt_gr'):
                    return 'alt'
                if name in ('ctrl_l', 'ctrl_r', 'control_l', 'control_r'):
                    return 'ctrl'
                if name in ('shift_l', 'shift_r'):
                    return 'shift'
                if name in ('cmd_l', 'cmd_r', 'super_l', 'super_r'):
                    return 'win'
                return name
            else:
                return str(key).lower().replace('key.', '')
        except Exception:
            return None
    
    def on_press(self, key):
        if not self.is_capturing:
            return
        with self.lock:
            normalized = self._normalize_key(key)
            if normalized:
                self.captured_keys.add(normalized)
    
    def on_release(self, key):
        if not self.is_capturing:
            return
        # 当有按键释放且已捕获足够按键时，完成捕获
        with self.lock:
            if len(self.captured_keys) >= 2:
                self.finish_capture()
    
    def start(self):
        """开始捕获"""
        self.is_capturing = True
        self.captured_keys = set()
        self.listener = keyboard.Listener(
            on_press=self.on_press,
            on_release=self.on_release
        )
        self.listener.start()
        
        # 设置超时（5秒后自动取消）
        self.capture_timer = threading.Timer(5.0, self.cancel_capture)
        self.capture_timer.start()
    
    def finish_capture(self):
        """完成捕获"""
        if not self.is_capturing:
            return
        self.is_capturing = False
        
        if self.capture_timer:
            self.capture_timer.cancel()
        
        if self.listener:
            self.listener.stop()
            self.listener = None
        
        # 排序并返回结果
        result = self._sort_hotkey(list(self.captured_keys))
        self.callback(result)
    
    def cancel_capture(self):
        """取消捕获"""
        if not self.is_capturing:
            return
        self.is_capturing = False
        
        if self.listener:
            self.listener.stop()
            self.listener = None
        
        self.callback(None)  # 返回None表示取消
    
    def _sort_hotkey(self, keys):
        """按优先级排序热键"""
        def key_priority(k):
            return (KEY_PRIORITY.get(k, 10), k)
        return sorted(keys, key=key_priority)


class VoiceRecognizer:
    """语音识别器"""
    def __init__(self, keywords, callback, device_index=None, model_path=None):
        self.keywords = keywords if isinstance(keywords, list) else [keywords]
        self.callback = callback
        self.device_index = device_index
        self.user_model_path = model_path  # 用户指定的模型路径
        self.running = False
        self.thread = None
        self.model = None
        self.enabled = VOSK_AVAILABLE
        
    def load_model(self):
        """加载Vosk模型"""
        if not VOSK_AVAILABLE:
            self.enabled = False
            return False
        try:
            model_path = self.find_model_path()
            if model_path and os.path.exists(model_path):
                self.model = vosk.Model(model_path)
                self.enabled = True
                print(f"模型加载成功，语音识别已启用")
                return True
        except Exception as e:
            print(f"模型加载失败: {e}")
            self.enabled = False
        return False
    
    def find_model_path(self):
        """查找Vosk模型路径"""
        # 优先使用用户指定的路径
        if self.user_model_path:
            if os.path.exists(self.user_model_path):
                print(f"使用用户指定的语音模型: {self.user_model_path}")
                return self.user_model_path
            else:
                print(f"警告: 用户指定的模型路径不存在: {self.user_model_path}")
        
        # 自动查找
        if getattr(sys, 'frozen', False):
            base_dir = sys._MEIPASS
        else:
            base_dir = os.path.dirname(os.path.abspath(__file__))
    
        possible_paths = [
            os.path.join(base_dir, "vosk-model-small-cn-0.22"),
            os.path.join(base_dir, "model"),
            "vosk-model-small-cn-0.22",
            "model",
            os.path.join(str(Path.home()), "vosk-model-small-cn-0.22"),
        ]
    
        for path in possible_paths:
            if os.path.exists(path):
                print(f"找到语音模型: {path}")
                return path
    
        print("未找到语音模型，语音识别功能不可用")
        return None
        
    def recognize_loop(self):
        """语音识别循环"""
        if not self.model:
            if not self.load_model():
                print("无法加载语音模型，语音识别功能不可用")
                self.enabled = False
                return
            else:
                self.enabled = True
                
        try:
            recognizer = vosk.KaldiRecognizer(self.model, 16000)
            
            with sd.RawInputStream(
                samplerate=16000, 
                blocksize=8000, 
                device=self.device_index,
                dtype='int16', 
                channels=1
            ) as stream:
                while self.running:
                    data, overflowed = stream.read(4000)
                    if recognizer.AcceptWaveform(bytes(data)):
                        result = json.loads(recognizer.Result())
                        text = result.get('text', '')
                        # 检查任何一个关键词
                        for keyword in self.keywords:
                            if keyword in text:
                                print(f"检测到关键词: {keyword}")
                                self.callback(keyword)
                                break
        except Exception as e:
            print(f"语音识别错误: {e}")
            self.enabled = False
            
    def start(self):
        if not VOSK_AVAILABLE:
            return
        self.running = True
        self.thread = threading.Thread(target=self.recognize_loop, daemon=True)
        self.thread.start()
        
    def stop(self):
        self.running = False
        if self.thread:
            self.thread.join(timeout=1)
            
    def set_device(self, device_index):
        self.device_index = device_index
        # 重启识别器以应用新设备
        if self.running:
            self.stop()
            self.start()
    
    def set_model_path(self, model_path):
        """设置模型路径"""
        self.user_model_path = model_path
        self.model = None  # 清除当前模型
        self.enabled = False  # 重置状态，等待重新加载
        # 重启识别器以应用新模型
        if self.running:
            self.stop()
            self.start()


class AudioPlayer:
    """音频播放器"""
    def __init__(self, audio_files, base_path):
        # 支持单个文件或文件列表
        if isinstance(audio_files, str):
            self.audio_files = [audio_files]
        else:
            self.audio_files = audio_files
        self.base_path = base_path
        self.current_file = None
        self.is_playing = False
        self.initialized = False
        self.lock = threading.Lock()  # 添加线程锁
        self._init_mixer()
        
    def _init_mixer(self):
        try:
            mixer.init()
            self.initialized = True
        except Exception as e:
            print(f"音频初始化失败: {e}")
            self.initialized = False

    def set_output_device(self, device_index):
        """尝试切换输出设备（best-effort）：重新初始化 pygame.mixer"""
        self.output_device_index = device_index
        acquired = self.lock.acquire(timeout=2.0)
        if not acquired:
            print("[警告] 无法获取音频锁，切换输出设备被跳过")
            return

        try:
            try:
                mixer.music.stop()
            except Exception:
                pass
            try:
                mixer.quit()
            except Exception:
                pass

            # best-effort: some pygame versions accept devicename
            if device_index is None:
                try:
                    mixer.init()
                    self.initialized = True
                except Exception as e:
                    print(f"恢复默认输出设备失败: {e}")
                    self.initialized = False
            else:
                try:
                    dev_info = sd.query_devices(device_index)
                    dev_name = dev_info.get('name')
                except Exception:
                    dev_name = None

                if dev_name:
                    try:
                        mixer.init(devicename=dev_name)
                        self.initialized = True
                    except TypeError:
                        try:
                            mixer.init()
                            self.initialized = True
                        except Exception as e:
                            print(f"初始化输出设备失败: {e}")
                            self.initialized = False
                    except Exception as e:
                        print(f"使用设备名初始化失败: {e}")
                        try:
                            mixer.init()
                            self.initialized = True
                        except Exception as e2:
                            print(f"退回默认初始化仍失败: {e2}")
                            self.initialized = False
                else:
                    try:
                        mixer.init()
                        self.initialized = True
                    except Exception as e:
                        print(f"初始化输出设备失败: {e}")
                        self.initialized = False
        finally:
            self.lock.release()
        
    def play(self, specific_file=None):
        """播放音频，如果不指定文件则随机选择一个"""
        if not self.initialized:
            return
        
        # 使用线程锁保护，避免与语音识别冲突
        acquired = self.lock.acquire(timeout=2.0)
        if not acquired:
            print("[警告] 无法获取音频锁，播放操作被跳过")
            return
        
        try:
            if specific_file:
                # 播放指定文件
                file_path = specific_file if os.path.isabs(specific_file) else os.path.join(self.base_path, specific_file)
            else:
                # 随机选择一个文件
                import random
                if not self.audio_files:
                    print("没有可用的音频文件")
                    return
                selected_file = random.choice(self.audio_files)
                # 判断是绝对路径还是相对路径
                file_path = selected_file if os.path.isabs(selected_file) else os.path.join(self.base_path, selected_file)
            
            if not os.path.exists(file_path):
                print(f"音频文件不存在: {file_path}")
                return
            
            self.current_file = os.path.basename(file_path)
            mixer.music.load(file_path)
            mixer.music.play()
            self.is_playing = True
            print(f"正在播放: {self.current_file}")
        except Exception as e:
            print(f"播放错误: {e}")
        finally:
            self.lock.release()
            
    def stop(self):
        if not self.initialized:
            return
        
        acquired = self.lock.acquire(timeout=2.0)
        if not acquired:
            print("[警告] 无法获取音频锁，停止操作被跳过")
            return
        
        try:
            mixer.music.stop()
            self.is_playing = False
        except Exception as e:
            print(f"停止播放错误: {e}")
        finally:
            self.lock.release()
        
    def toggle(self):
        # stop和play方法内部已经有锁保护
        if self.is_playing:
            self.stop()
        else:
            self.play()
        return self.is_playing
            
    def set_volume(self, volume):
        """设置音量 (0.0 - 1.0)"""
        if self.initialized:
            try:
                mixer.music.set_volume(volume)
            except Exception:
                pass
    
    def update_files(self, audio_files):
        """更新音频文件列表"""
        if isinstance(audio_files, str):
            self.audio_files = [audio_files]
        else:
            self.audio_files = audio_files
    
    def get_current_file(self):
        """获取当前播放的文件名"""
        return self.current_file
    
    def preview(self, filename):
        """试听指定文件（异步执行）"""
        def _preview():
            self.stop()
            time.sleep(0.1)  # 短暂延迟确保停止完成
            self.play(specific_file=filename)
        
        # 在新线程中执行，避免阻塞UI
        threading.Thread(target=_preview, daemon=True).start()


class ConfigManager:
    """配置管理器"""
    def __init__(self):
        self.config = self.load_config()
        
    def load_config(self):
        default_config = {
            'hotkey': DEFAULT_HOTKEY.copy(),
            'volume': 0.7,
            'auto_start': False,
            'audio_device': None,
            'audio_output_device': None,  # 输出设备索引或None
            'keywords': DEFAULT_KEYWORDS.copy(),
            'music_files': PRESET_CLASSIC['files'].copy(),
            'current_preset': 'classic',  # 'classic', 'gacha', 'custom'
            'model_path': None  # 用户指定的模型路径
        }
        try:
            if CONFIG_FILE.exists():
                with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                    saved_config = json.load(f)
                    default_config.update(saved_config)
        except Exception as e:
            print(f"配置加载失败: {e}")
        return default_config
        
    def save_config(self):
        try:
            # 确保所有字符串都是正确的UTF-8
            with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
                json.dump(self.config, f, ensure_ascii=False, indent=2)
            print(f"[配置] 已保存到: {CONFIG_FILE}")
            print(f"[配置] hotkey: {self.config.get('hotkey')}")
        except Exception as e:
            print(f"配置保存失败: {e}")
            
    def get(self, key, default=None):
        return self.config.get(key, default)
        
    def set(self, key, value):
        self.config[key] = value
        self.save_config()


class MainWindow(QMainWindow):
    """主窗口"""
    def __init__(self):
        super().__init__()
        
        # 获取资源路径
        self.base_path = self.get_resource_path()
        
        # 初始化组件
        self.config = ConfigManager()
        self.signals = SignalEmitter()
        
        # 音频播放器
        music_files = self.config.get('music_files', PRESET_CLASSIC['files'].copy())
        self.player = AudioPlayer(music_files, self.base_path)
        self.player.set_volume(self.config.get('volume', 0.7))
        
        # 热键监听器
        self.hotkey_listener = HotkeyListener(
            self.config.get('hotkey', DEFAULT_HOTKEY.copy()),
            self.on_hotkey_triggered
        )
        
        # 语音识别器
        self.voice_recognizer = VoiceRecognizer(
            self.config.get('keywords', DEFAULT_KEYWORDS.copy()),
            self.on_keyword_detected,
            self.config.get('audio_device'),
            self.config.get('model_path')
        )
        
        # 快捷键捕获器
        self.hotkey_capture = None
        
        # 信号连接
        self.signals.trigger_play.connect(self.toggle_play)
        self.signals.keyword_detected.connect(self.on_keyword_detected_ui)
        self.signals.hotkey_captured.connect(self.on_hotkey_capture_finished)
        
        # 初始化UI
        self.init_ui()
        self.init_tray()
        
        # 启动监听
        self.hotkey_listener.start()
        self.voice_recognizer.start()
        
    def get_resource_path(self):
        """获取资源文件路径"""
        if getattr(sys, 'frozen', False):
            return sys._MEIPASS
        return os.path.dirname(os.path.abspath(__file__))
        
    def init_ui(self):
        """初始化用户界面"""
        self.setWindowTitle("关羽之歌便携版 - by 依然匹萨吧")
        
        # 设置图标
        icon_path = os.path.join(self.base_path, "guanyu_icon.ico")
        if os.path.exists(icon_path):
            self.setWindowIcon(QIcon(icon_path))
        
        # DPI适配
        self.setup_dpi_scaling()
        
        # 创建滚动区域
        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setFrameShape(QFrame.NoFrame)
        self.setCentralWidget(scroll_area)
        
        # 主窗口部件（放在滚动区域内）
        central_widget = QWidget()
        scroll_area.setWidget(central_widget)
        layout = QVBoxLayout(central_widget)
        layout.setSpacing(15)
        layout.setContentsMargins(20, 20, 20, 20)
        
        # === 触发说明 ===
        trigger_group = QGroupBox("触发方式")
        trigger_layout = QVBoxLayout(trigger_group)
        
        trigger_label = QLabel()
        keywords = self.config.get('keywords', DEFAULT_KEYWORDS.copy())
        keywords_display = '【' + '】【'.join(keywords) + '】'
        trigger_label.setText(
            f'<p style="line-height: 1.6;">'
            f'按下 <b style="color: #2196F3;">快捷键</b> 或语音中检测到关键词 '
            f'<b style="color: #E91E63;">{keywords_display}</b> 时触发播放<br>'
            f'<span style="color: #666; font-size: 9pt;">(再次触发可停止播放)</span>'
            f'<span style="color: #999; font-size: 9pt;">本软件免费，禁止商用贩卖，音乐版权归音乐作者所有</span>'
            f'</p>'
        )
        trigger_label.setTextFormat(Qt.RichText)
        trigger_label.setWordWrap(True)
        trigger_layout.addWidget(trigger_label)
        self.trigger_label = trigger_label  # 保存引用以便更新
        
        layout.addWidget(trigger_group)
        
        # === 状态显示区域 ===
        status_group = QGroupBox("状态信息")
        status_layout = QVBoxLayout(status_group)
        
        # 正在播放
        playing_layout = QHBoxLayout()
        playing_layout.addWidget(QLabel("正在播放:"))
        self.playing_label = QLabel("无")
        self.playing_label.setStyleSheet("""
            color: #4CAF50; 
            font-weight: bold;
            font-size: 10pt;
        """)
        playing_layout.addWidget(self.playing_label)
        playing_layout.addStretch()
        status_layout.addLayout(playing_layout)
        
        # 最近检测
        detect_layout = QHBoxLayout()
        detect_layout.addWidget(QLabel("检测到关键词:"))
        self.detect_label = QLabel("无")
        self.detect_label.setStyleSheet("""
            color: #E91E63; 
            font-weight: bold;
            font-size: 10pt;
        """)
        detect_layout.addWidget(self.detect_label)
        detect_layout.addStretch()
        status_layout.addLayout(detect_layout)
        
        layout.addWidget(status_group)
        
        # === 播放控制组 ===
        play_group = QGroupBox("播放控制")
        play_layout = QVBoxLayout(play_group)
        
        # 播放按钮
        self.play_btn = QPushButton("▶ 播放")
        self.play_btn.setMinimumHeight(50)
        self.play_btn.setStyleSheet("""
            QPushButton {
                font-size: 14pt;
                font-weight: bold;
                border-radius: 8px;
                background-color: #4CAF50;
                color: white;
            }
            QPushButton:hover {
                background-color: #45a049;
            }
            QPushButton:pressed {
                background-color: #3d8b40;
            }
        """)
        self.play_btn.clicked.connect(self.toggle_play)
        play_layout.addWidget(self.play_btn)
        
        # 音量控制
        volume_layout = QHBoxLayout()
        volume_label = QLabel("音量:")
        self.volume_slider = QSlider(Qt.Horizontal)
        self.volume_slider.setRange(0, 100)
        self.volume_slider.setValue(int(self.config.get('volume', 0.7) * 100))
        self.volume_slider.valueChanged.connect(self.on_volume_changed)
        self.volume_value_label = QLabel(f"{self.volume_slider.value()}%")
        self.volume_value_label.setMinimumWidth(40)
        volume_layout.addWidget(volume_label)
        volume_layout.addWidget(self.volume_slider)
        volume_layout.addWidget(self.volume_value_label)
        play_layout.addLayout(volume_layout)
        
        layout.addWidget(play_group)
        
        # === 关键词和音乐设置（横向排布） ===
        settings_row1 = QHBoxLayout()
        settings_row1.setSpacing(10)
        
        # === 关键词设置组 ===
        keyword_group = QGroupBox("关键词设置 (最多10个)")
        keyword_layout = QVBoxLayout(keyword_group)
        
        keywords = self.config.get('keywords', DEFAULT_KEYWORDS.copy())
        
        # 显示当前关键词
        self.keywords_display_label = QLabel('、'.join(keywords))
        self.keywords_display_label.setStyleSheet("""
            font-weight: bold; 
            color: #E91E63; 
            font-size: 10pt;
            padding: 5px 8px;
            background-color: #FCE4EC;
            border-radius: 4px;
        """)
        self.keywords_display_label.setWordWrap(True)
        keyword_layout.addWidget(self.keywords_display_label)
        
        # 按钮
        keyword_btn_layout = QHBoxLayout()
        self.edit_keywords_btn = QPushButton("编辑")
        self.reset_keywords_btn = QPushButton("默认")
        keyword_btn_layout.addWidget(self.edit_keywords_btn)
        keyword_btn_layout.addWidget(self.reset_keywords_btn)
        keyword_layout.addLayout(keyword_btn_layout)
        
        self.edit_keywords_btn.clicked.connect(self.open_keyword_editor)
        self.reset_keywords_btn.clicked.connect(self.reset_keywords)
        
        settings_row1.addWidget(keyword_group)
        
        # === 音乐设置组 ===
        music_group = QGroupBox("音乐设置 (最多10首)")
        music_layout = QVBoxLayout(music_group)
        
        # 显示当前模式
        current_preset = self.config.get('current_preset', 'classic')
        music_files = self.config.get('music_files', PRESET_CLASSIC['files'].copy())
        
        preset_name = {
            'classic': '经典模式',
            'gacha': '抽卡模式',
            'custom': '自定义模式'
        }.get(current_preset, '自定义模式')
        
        mode_count_layout = QHBoxLayout()
        self.preset_label = QLabel(preset_name)
        self.preset_label.setStyleSheet("""
            font-weight: bold; 
            color: #FF9800; 
            font-size: 10pt;
            padding: 5px 8px;
            background-color: #FFF3E0;
            border-radius: 4px;
        """)
        mode_count_layout.addWidget(self.preset_label)
        self.music_count_label = QLabel(f"{len(music_files)}首")
        self.music_count_label.setStyleSheet("color: #666; font-size: 10pt;")
        mode_count_layout.addWidget(self.music_count_label)
        mode_count_layout.addStretch()
        music_layout.addLayout(mode_count_layout)
        
        # 按钮组
        music_btn_layout = QHBoxLayout()
        self.manage_music_btn = QPushButton("管理")
        self.preset_classic_btn = QPushButton("经典模式")
        self.preset_gacha_btn = QPushButton("抽卡模式")
        music_btn_layout.addWidget(self.manage_music_btn)
        music_btn_layout.addWidget(self.preset_classic_btn)
        music_btn_layout.addWidget(self.preset_gacha_btn)
        music_layout.addLayout(music_btn_layout)
        
        self.manage_music_btn.clicked.connect(self.open_music_manager)
        self.preset_classic_btn.clicked.connect(lambda: self.apply_preset('classic'))
        self.preset_gacha_btn.clicked.connect(lambda: self.apply_preset('gacha'))
        
        settings_row1.addWidget(music_group)
        
        layout.addLayout(settings_row1)
        
        device_group = QGroupBox("音频输入/输出设备 (用于语音识别与播放)")
        device_layout = QVBoxLayout(device_group)

        # 输入与输出设备并列显示
        h = QHBoxLayout()
        input_label = QLabel("输入设备:")
        self.device_combo = QComboBox()
        self.device_combo.setMinimumWidth(220)
        self.device_combo.currentIndexChanged.connect(self.on_device_changed)
        h.addWidget(input_label)
        h.addWidget(self.device_combo)

        output_label = QLabel("输出设备:")
        self.output_device_combo = QComboBox()
        self.output_device_combo.setMinimumWidth(220)
        self.output_device_combo.currentIndexChanged.connect(self.on_output_device_changed)
        h.addWidget(output_label)
        h.addWidget(self.output_device_combo)

        device_layout.addLayout(h)

        # 枚举并填充设备（函数会同时填充输入与输出下拉）
        self.populate_audio_devices()
        
        # 模型路径设置
        model_path_layout = QHBoxLayout()
        model_path_label = QLabel("语音模型:\n(可换不同语言的模型)")
        self.model_path_display = QLabel()
        self.update_model_path_display()
        self.model_path_display.setStyleSheet("""
            color: #666; 
            font-size: 9pt;
            padding: 2px;
        """)
        self.model_path_display.setWordWrap(True)
        browse_model_btn = QPushButton("选择模型文件夹")
        browse_model_btn.clicked.connect(self.browse_model_folder)
        clear_model_btn = QPushButton("恢复默认")
        clear_model_btn.clicked.connect(self.clear_model_path)
        model_path_layout.addWidget(model_path_label)
        model_path_layout.addWidget(self.model_path_display, 1)
        model_path_layout.addWidget(browse_model_btn)
        model_path_layout.addWidget(clear_model_btn)
        device_layout.addLayout(model_path_layout)
        
        # 语音识别状态
        self.voice_status_label = QLabel()
        self.update_voice_status()
        device_layout.addWidget(self.voice_status_label)
        
        layout.addWidget(device_group)
        
        # === 快捷键和其他设置（横向排布） ===
        settings_row2 = QHBoxLayout()
        settings_row2.setSpacing(10)
        
        # === 快捷键设置组 ===
        hotkey_group = QGroupBox("快捷键设置")
        hotkey_layout = QVBoxLayout(hotkey_group)
        
        current_hotkey = self.config.get('hotkey', DEFAULT_HOTKEY.copy())
        hotkey_str = self.format_hotkey(current_hotkey)
        
        self.hotkey_label = QLabel(hotkey_str)
        self.hotkey_label.setStyleSheet("""
            font-weight: bold; 
            color: #2196F3; 
            font-size: 11pt;
            padding: 5px 10px;
            background-color: #E3F2FD;
            border-radius: 4px;
        """)
        self.hotkey_label.setAlignment(Qt.AlignCenter)
        hotkey_layout.addWidget(self.hotkey_label)
        
        hotkey_btn_layout = QHBoxLayout()
        self.set_hotkey_btn = QPushButton("修改")
        self.reset_hotkey_btn = QPushButton("默认")
        hotkey_btn_layout.addWidget(self.set_hotkey_btn)
        hotkey_btn_layout.addWidget(self.reset_hotkey_btn)
        hotkey_layout.addLayout(hotkey_btn_layout)
        
        self.set_hotkey_btn.clicked.connect(self.start_hotkey_capture)
        self.reset_hotkey_btn.clicked.connect(self.reset_hotkey)
        
        settings_row2.addWidget(hotkey_group)
        
        # === 其他设置组 ===
        settings_group = QGroupBox("其他设置")
        settings_layout = QVBoxLayout(settings_group)
        
        self.autostart_checkbox = QCheckBox("开机自动启动")
        self.autostart_checkbox.setChecked(self.config.get('auto_start', False))
        self.autostart_checkbox.stateChanged.connect(self.on_autostart_changed)
        settings_layout.addWidget(self.autostart_checkbox)
        
        # 打开配置文件位置按钮
        open_config_btn = QPushButton("打开配置文件位置")
        open_config_btn.clicked.connect(self.open_config_location)
        settings_layout.addWidget(open_config_btn)
        
        settings_row2.addWidget(settings_group)
        
        layout.addLayout(settings_row2)
        
        # === 作者信息（紧凑版） ===
        author_layout = QHBoxLayout()
        author_layout.addStretch()
        author_label = QLabel(
            '<span style="color: #999; font-size: 9pt;">作者: </span>'
            '<a href="https://space.bilibili.com/6297797" '
            'style="color: #2196F3; text-decoration: none;">依然匹萨吧</a>'
        )
        author_label.setOpenExternalLinks(True)
        author_label.setTextFormat(Qt.RichText)
        author_layout.addWidget(author_label)
        author_layout.addStretch()
        layout.addLayout(author_layout)
        
        # 设置窗口大小（调整为合理大小，内容可滚动）
        self.setMinimumSize(520, 600)
        self.resize(520, 710)
        
    def format_hotkey(self, hotkey_list):
        """格式化热键显示"""
        # 按优先级排序
        def key_priority(k):
            return (KEY_PRIORITY.get(k.lower(), 10), k)
        sorted_keys = sorted(hotkey_list, key=key_priority)
        # 确保单字符键正确显示
        display_keys = []
        for k in sorted_keys:
            # 确保k是字符串类型
            key_str = str(k) if k else ''
            print(f"[调试] 处理按键: {repr(k)}, 类型: {type(k)}, 长度: {len(key_str)}")
            # 单字符直接大写显示
            if len(key_str) == 1:
                display_keys.append(key_str.upper())
            else:
                # 修饰键首字母大写
                display_keys.append(key_str.capitalize())
        result = ' + '.join(display_keys)
        print(f"[调试] 格式化结果: {repr(result)}")
        return result
        
    def update_voice_status(self):
        """更新语音识别状态显示"""
        if not VOSK_AVAILABLE:
            self.voice_status_label.setText(
                '<span style="color: #999;">⚠ Vosk未安装，语音识别不可用</span>'
            )
        elif self.voice_recognizer.enabled:
            self.voice_status_label.setText(
                '<span style="color: #4CAF50;">✓ 语音识别已启用</span>'
            )
        else:
            self.voice_status_label.setText(
                '<span style="color: #FF9800;">⚠ 语音模型未找到，请尝试手动指定模型路径</span>'
            )
        self.voice_status_label.setTextFormat(Qt.RichText)
        
    def setup_dpi_scaling(self):
        """设置DPI缩放"""
        screen = QApplication.primaryScreen()
        dpi = screen.logicalDotsPerInch()
        scale_factor = dpi / 150.0
        
        base_font_size = max(9, int(10 * scale_factor))
        font = QFont()
        font.setPointSize(base_font_size)
        self.setFont(font)
        
    def init_tray(self):
        """初始化系统托盘"""
        icon_path = os.path.join(self.base_path, "guanyu_icon.ico")
        
        self.tray_icon = QSystemTrayIcon(self)
        if os.path.exists(icon_path):
            self.tray_icon.setIcon(QIcon(icon_path))
        else:
            # 使用默认图标
            self.tray_icon.setIcon(self.style().standardIcon(
                self.style().SP_MediaPlay))
        
        self.tray_icon.setToolTip("关羽之歌便携版")
        
        # 托盘菜单
        tray_menu = QMenu()
        
        show_action = QAction("显示主窗口", self)
        show_action.triggered.connect(self.show_and_activate)
        tray_menu.addAction(show_action)
        
        play_action = QAction("播放/停止", self)
        play_action.triggered.connect(self.toggle_play)
        tray_menu.addAction(play_action)
        
        tray_menu.addSeparator()
        
        quit_action = QAction("退出", self)
        quit_action.triggered.connect(self.quit_app)
        tray_menu.addAction(quit_action)
        
        self.tray_icon.setContextMenu(tray_menu)
        self.tray_icon.activated.connect(self.on_tray_activated)
        self.tray_icon.show()
        
    def show_and_activate(self):
        """显示并激活窗口"""
        self.show()
        self.showNormal()
        self.activateWindow()
        self.raise_()
        
    def populate_audio_devices(self):
        """填充音频设备列表"""
        # 清空并添加默认项
        try:
            self.device_combo.clear()
            self.device_combo.addItem("默认设备", None)
        except Exception:
            pass

        try:
            self.output_device_combo.clear()
            self.output_device_combo.addItem("默认设备", None)
        except Exception:
            pass

        try:
            devices = sd.query_devices()
            for i, device in enumerate(devices):
                name = device.get('name', f"device-{i}")
                # 输入设备
                if device.get('max_input_channels', 0) > 0:
                    self.device_combo.addItem(f"{name}", i)
                # 输出设备
                if device.get('max_output_channels', 0) > 0:
                    self.output_device_combo.addItem(f"{name}", i)
        except Exception as e:
            print(f"获取音频设备失败: {e}")

        # 恢复保存的输入设备选择
        saved_device = self.config.get('audio_device')
        if saved_device is not None:
            index = self.device_combo.findData(saved_device)
            if index >= 0:
                self.device_combo.setCurrentIndex(index)

        # 恢复保存的输出设备选择
        saved_out = self.config.get('audio_output_device')
        if saved_out is not None:
            index_out = self.output_device_combo.findData(saved_out)
            if index_out >= 0:
                self.output_device_combo.setCurrentIndex(index_out)
                
    def on_keyword_detected_ui(self, keyword):
        """关键词检测UI更新（主线程）"""
        from datetime import datetime
        time_str = datetime.now().strftime("%H:%M:%S")
        status_text = f"{keyword} ({time_str})"
        self.detect_label.setText(status_text)
        print(f"[UI已更新] 检测到关键词: {status_text}")
        # 触发播放
        self.toggle_play()
    
    def on_hotkey_triggered(self):
        """热键触发回调（从子线程调用）"""
        # 先更新UI再触发播放
        try:
            from datetime import datetime
            time_str = datetime.now().strftime("%H:%M:%S")
            status_text = f"快捷键触发 ({time_str})"
            # 确保在主线程中更新UI - 使用默认参数捕获变量
            QTimer.singleShot(0, lambda text=status_text: self.detect_label.setText(text))
            print(f"[UI更新] {status_text}")
        except Exception as e:
            print(f"更新检测标签失败: {e}")
        # 然后触发播放
        self.signals.trigger_play.emit()
        
    def on_keyword_detected(self, keyword):
        """关键词检测回调（从子线程调用）"""
        # 先更新UI再触发播放
        try:
            from datetime import datetime
            time_str = datetime.now().strftime("%H:%M:%S")
            status_text = f"{keyword} ({time_str})"
            print(f"[关键词检测] {keyword} at {time_str}")
            # 通过信号在主线程中更新UI
            self.signals.keyword_detected.emit(keyword)
        except Exception as e:
            print(f"关键词检测处理失败: {e}")
            # 即使出错也尝试触发播放
            self.signals.keyword_detected.emit(keyword if keyword else "未知")
        
    def toggle_play(self):
        """切换播放状态"""
        is_playing = self.player.toggle()
        if is_playing:
            self.play_btn.setText("⏹ 停止")
            self.play_btn.setStyleSheet("""
                QPushButton {
                    font-size: 14pt;
                    font-weight: bold;
                    border-radius: 8px;
                    background-color: #f44336;
                    color: white;
                }
                QPushButton:hover {
                    background-color: #da190b;
                }
                QPushButton:pressed {
                    background-color: #b71c1c;
                }
            """)
            # 更新正在播放的显示
            current_file = self.player.get_current_file()
            if current_file:
                self.playing_label.setText(current_file)
        else:
            self.play_btn.setText("▶ 播放")
            self.play_btn.setStyleSheet("""
                QPushButton {
                    font-size: 14pt;
                    font-weight: bold;
                    border-radius: 8px;
                    background-color: #4CAF50;
                    color: white;
                }
                QPushButton:hover {
                    background-color: #45a049;
                }
                QPushButton:pressed {
                    background-color: #3d8b40;
                }
            """)
            # 停止播放时清空显示
            self.playing_label.setText("无")
            
    def on_volume_changed(self, value):
        """音量变化处理"""
        volume = value / 100.0
        self.player.set_volume(volume)
        self.volume_value_label.setText(f"{value}%")
        self.config.set('volume', volume)
        
    def on_device_changed(self, index):
        """音频设备变化处理"""
        device_index = self.device_combo.currentData()
        self.config.set('audio_device', device_index)
        self.voice_recognizer.set_device(device_index)
        # 延迟更新状态
        QTimer.singleShot(1000, self.update_voice_status)

    def on_output_device_changed(self, index):
        """输出设备变化处理"""
        device_index = self.output_device_combo.currentData()
        self.config.set('audio_output_device', device_index)
        # 尝试通知播放器切换输出（best-effort）
        try:
            self.player.set_output_device(device_index)
        except Exception as e:
            print(f"切换输出设备时发生错误: {e}")
        
    def start_hotkey_capture(self):
        """开始捕获新快捷键"""
        # 暂停热键监听
        self.hotkey_listener.stop()
        
        # 更新UI
        self.set_hotkey_btn.setText("请按下新快捷键组合...")
        self.set_hotkey_btn.setEnabled(False)
        self.reset_hotkey_btn.setEnabled(False)
        
        # 创建并启动捕获器
        self.hotkey_capture = HotkeyCapture(self.on_capture_callback)
        self.hotkey_capture.start()
        
    def on_capture_callback(self, result):
        """捕获回调（从子线程调用）"""
        # 使用信号发送到主线程
        self.signals.hotkey_captured.emit(result if result else [])
        
    def on_hotkey_capture_finished(self, result):
        """快捷键捕获完成（主线程）"""
        self.set_hotkey_btn.setEnabled(True)
        self.reset_hotkey_btn.setEnabled(True)
        self.set_hotkey_btn.setText("修改")
        
        if result and len(result) >= 2:
            # 保存新热键
            self.config.set('hotkey', result)
            self.hotkey_listener.update_hotkey(result)
            
            # 更新显示
            hotkey_str = self.format_hotkey(result)
            self.hotkey_label.setText(hotkey_str)
            
            QMessageBox.information(
                self, "成功", 
                f"快捷键已修改为: {hotkey_str}"
            )
        elif result is not None and len(result) < 2:
            QMessageBox.warning(
                self, "提示", 
                "请至少按下2个键的组合"
            )
        else:
            QMessageBox.information(
                self, "提示", 
                "快捷键设置已取消"
            )
        
        # 重新启动热键监听
        self.hotkey_listener.start()
            
    def reset_hotkey(self):
        """重置快捷键为默认值"""
        default = DEFAULT_HOTKEY.copy()
        self.config.set('hotkey', default)
        self.hotkey_listener.update_hotkey(default)
        hotkey_str = self.format_hotkey(default)
        self.hotkey_label.setText(hotkey_str)
        QMessageBox.information(
            self, "提示", 
            f"快捷键已恢复为默认值: {hotkey_str}"
        )
        
    def open_keyword_editor(self):
        """打开关键词编辑对话框"""
        from PyQt5.QtWidgets import QDialog, QTextEdit
        
        dialog = QDialog(self)
        dialog.setWindowTitle("编辑关键词")
        dialog.setMinimumWidth(350)
        
        layout = QVBoxLayout(dialog)
        
        # 说明文本
        label = QLabel("每行输入一个关键词，最多10个。保持为空行则不添加：")
        label.setStyleSheet("color: #666; font-size: 10pt; margin-bottom: 10px;")
        layout.addWidget(label)
        
        # 文本编辑框
        text_edit = QTextEdit()
        current_keywords = self.config.get('keywords', DEFAULT_KEYWORDS.copy())
        text_edit.setPlainText('\n'.join(current_keywords))
        text_edit.setMinimumHeight(200)
        layout.addWidget(text_edit)
        
        # 按钮
        button_layout = QHBoxLayout()
        button_layout.addStretch()
        
        ok_btn = QPushButton("保存")
        cancel_btn = QPushButton("取消")
        
        def on_ok():
            keywords_text = text_edit.toPlainText()
            # 处理关键词
            keywords = [k.strip() for k in keywords_text.split('\n') if k.strip()]
            
            # 验证
            if len(keywords) == 0:
                QMessageBox.warning(dialog, "提示", "至少需要设置一个关键词")
                return
            if len(keywords) > 10:
                QMessageBox.warning(dialog, "提示", "最多只能设置10个关键词")
                return
            
            # 检查是否有重复
            if len(keywords) != len(set(keywords)):
                QMessageBox.warning(dialog, "提示", "关键词不能重复")
                return
            
            # 保存配置
            self.config.set('keywords', keywords)
            self.voice_recognizer.keywords = keywords
            
            # 更新UI显示
            self.keywords_display_label.setText('、'.join(keywords))
            self.update_trigger_label()
            
            QMessageBox.information(dialog, "成功", f"关键词已更新: {', '.join(keywords)}")
            dialog.accept()
        
        ok_btn.clicked.connect(on_ok)
        cancel_btn.clicked.connect(dialog.reject)
        
        button_layout.addWidget(ok_btn)
        button_layout.addWidget(cancel_btn)
        layout.addLayout(button_layout)
        
        dialog.exec_()
    
    def reset_keywords(self):
        """重置关键词为默认值"""
        default_keywords = DEFAULT_KEYWORDS.copy()
        self.config.set('keywords', default_keywords)
        self.voice_recognizer.keywords = default_keywords
        
        # 更新UI显示
        self.keywords_display_label.setText('、'.join(default_keywords))
        self.update_trigger_label()
        
        QMessageBox.information(
            self, "提示", 
            f"关键词已恢复为默认值: {', '.join(default_keywords)}"
        )
    
    def update_trigger_label(self):
        """更新触发说明标签"""
        keywords = self.config.get('keywords', DEFAULT_KEYWORDS.copy())
        keywords_display = '【' + '】【'.join(keywords) + '】'
        self.trigger_label.setText(
            f'<p style="line-height: 1.6;">'
            f'按下 <b style="color: #2196F3;">快捷键</b> 或语音中检测到关键词 '
            f'<b style="color: #E91E63;">{keywords_display}</b> 时触发播放<br>'
            f'<span style="color: #666; font-size: 9pt;">(再次触发可停止播放)</span>'
            f'</p>'
        )
    
    def open_music_manager(self):
        """打开音乐管理对话框"""
        from PyQt5.QtWidgets import QDialog, QListWidget, QFileDialog
        
        dialog = QDialog(self)
        dialog.setWindowTitle("音乐管理")
        dialog.setMinimumSize(500, 400)
        
        layout = QVBoxLayout(dialog)
        
        # 说明文本
        label = QLabel("最多添加10个音乐文件，支持mp3/wav/ogg格式。触发时将随机播放一首：")
        label.setStyleSheet("color: #666; font-size: 10pt; margin-bottom: 10px;")
        layout.addWidget(label)
        
        # 音乐列表
        from PyQt5.QtWidgets import QListWidgetItem
        music_list = QListWidget()
        current_files = self.config.get('music_files', PRESET_CLASSIC['files'].copy())
        for file in current_files:
            # 判断是绝对路径还是相对路径
            if os.path.isabs(file):
                filename = os.path.basename(file)
                item = QListWidgetItem(filename)
                item.setData(Qt.UserRole, file)  # 保存完整路径
                item.setToolTip(file)  # 鼠标悬停显示完整路径
                music_list.addItem(item)
            else:
                # 相对路径（预设模式）
                item = QListWidgetItem(file)
                item.setData(Qt.UserRole, file)
                music_list.addItem(item)
        music_list.setSelectionMode(QListWidget.SingleSelection)
        layout.addWidget(music_list)
        
        # 按钮组
        button_layout = QHBoxLayout()
        
        add_btn = QPushButton("添加文件")
        remove_btn = QPushButton("删除选中")
        preview_btn = QPushButton("试听")
        stop_preview_btn = QPushButton("停止试听")
        
        def on_add():
            if music_list.count() >= 10:
                QMessageBox.warning(dialog, "提示", "最多只能添加10个音乐文件")
                return
            
            files, _ = QFileDialog.getOpenFileNames(
                dialog,
                "选择音乐文件",
                "",
                "音频文件 (*.mp3 *.wav *.ogg);;所有文件 (*.*)"
            )
            
            if files:
                for file_path in files:
                    if music_list.count() >= 10:
                        QMessageBox.warning(dialog, "提示", "已达到10个文件上限")
                        break
                    
                    # 检查文件是否存在
                    if not os.path.exists(file_path):
                        QMessageBox.warning(dialog, "错误", f"文件不存在: {file_path}")
                        continue
                    
                    # 检查是否已在列表中
                    exists = False
                    for i in range(music_list.count()):
                        # 使用data存储完整路径，text显示文件名
                        item = music_list.item(i)
                        if item.data(Qt.UserRole) == file_path:
                            exists = True
                            break
                    
                    if exists:
                        QMessageBox.information(dialog, "提示", f"文件已在列表中")
                        continue
                    
                    # 直接添加文件路径，不复制
                    try:
                        from PyQt5.QtWidgets import QListWidgetItem
                        filename = os.path.basename(file_path)
                        item = QListWidgetItem(filename)
                        item.setData(Qt.UserRole, file_path)  # 保存完整路径
                        item.setToolTip(file_path)  # 鼠标悬停显示完整路径
                        music_list.addItem(item)
                    except Exception as e:
                        QMessageBox.warning(dialog, "错误", f"添加文件失败: {e}")
        
        def on_remove():
            current_item = music_list.currentItem()
            if not current_item:
                QMessageBox.warning(dialog, "提示", "请先选择要删除的文件")
                return
            
            filename = current_item.text()
            reply = QMessageBox.question(
                dialog, "确认", 
                f"确定要从列表中删除 {filename} 吗？\n(文件不会从磁盘删除)",
                QMessageBox.Yes | QMessageBox.No
            )
            
            if reply == QMessageBox.Yes:
                music_list.takeItem(music_list.row(current_item))
        
        def on_preview():
            current_item = music_list.currentItem()
            if not current_item:
                QMessageBox.warning(dialog, "提示", "请先选择要试听的文件")
                return
            
            # 获取保存的完整路径
            file_path = current_item.data(Qt.UserRole)
            if not file_path:
                # 兼容旧格式（相对路径）
                file_path = os.path.join(self.base_path, current_item.text())
            
            if not os.path.exists(file_path):
                QMessageBox.warning(dialog, "错误", f"文件不存在: {file_path}")
                return
            
            # 异步预览，避免阻塞UI
            try:
                self.player.preview(file_path)
                print(f"[试听] 开始播放: {os.path.basename(file_path)}")
            except Exception as e:
                print(f"[试听错误] {e}")
                QMessageBox.warning(dialog, "错误", f"试听失败: {e}")
        
        def on_stop_preview():
            # 异步停止，避免阻塞UI
            threading.Thread(target=self.player.stop, daemon=True).start()
        
        add_btn.clicked.connect(on_add)
        remove_btn.clicked.connect(on_remove)
        preview_btn.clicked.connect(on_preview)
        stop_preview_btn.clicked.connect(on_stop_preview)
        
        button_layout.addWidget(add_btn)
        button_layout.addWidget(remove_btn)
        button_layout.addWidget(preview_btn)
        button_layout.addWidget(stop_preview_btn)
        layout.addLayout(button_layout)
        
        # 确定/取消按钮
        dialog_btn_layout = QHBoxLayout()
        dialog_btn_layout.addStretch()
        
        ok_btn = QPushButton("保存")
        cancel_btn = QPushButton("取消")
        
        def on_ok():
            # 收集音乐文件列表（保存完整路径）
            files = []
            for i in range(music_list.count()):
                item = music_list.item(i)
                # 获取保存的完整路径
                file_path = item.data(Qt.UserRole)
                if file_path:
                    files.append(file_path)
                else:
                    # 兼容旧格式
                    files.append(item.text())
            
            if len(files) == 0:
                QMessageBox.warning(dialog, "提示", "至少需要一个音乐文件")
                return
            
            # 保存配置
            self.config.set('music_files', files)
            self.config.set('current_preset', 'custom')
            
            # 更新播放器
            self.player.update_files(files)
            
            # 更新UI显示
            self.preset_label.setText('自定义模式')
            self.music_count_label.setText(f"{len(files)}首")
            
            QMessageBox.information(dialog, "成功", f"音乐列表已更新，共 {len(files)} 首")
            dialog.accept()
        
        ok_btn.clicked.connect(on_ok)
        cancel_btn.clicked.connect(dialog.reject)
        
        dialog_btn_layout.addWidget(ok_btn)
        dialog_btn_layout.addWidget(cancel_btn)
        layout.addLayout(dialog_btn_layout)
        
        dialog.exec_()
    
    def apply_preset(self, preset_type):
        """应用预设模式"""
        if preset_type == 'classic':
            preset = PRESET_CLASSIC
        elif preset_type == 'gacha':
            preset = PRESET_GACHA
        else:
            return
        
        # 检查文件是否存在
        missing_files = []
        for filename in preset['files']:
            file_path = os.path.join(self.base_path, filename)
            if not os.path.exists(file_path):
                missing_files.append(filename)
        
        if missing_files:
            msg = f"以下文件不存在，无法应用{preset['name']}：\n" + "\n".join(missing_files)
            msg += "\n\n请确保这些文件在程序目录下。"
            QMessageBox.warning(self, "文件缺失", msg)
            return
        
        # 应用预设
        self.config.set('music_files', preset['files'].copy())
        self.config.set('current_preset', preset_type)
        
        # 更新播放器
        self.player.update_files(preset['files'])
        
        # 更新UI
        self.preset_label.setText(preset['name'])
        self.music_count_label.setText(f"{len(preset['files'])}首")
        
        QMessageBox.information(
            self, "成功", 
            f"已应用{preset['name']}，共 {len(preset['files'])} 首音乐"
        )
        
    def on_autostart_changed(self, state):
        """开机启动设置变化"""
        enabled = state == Qt.Checked
        self.config.set('auto_start', enabled)
        success = self.set_autostart(enabled)
        if not success:
            self.autostart_checkbox.blockSignals(True)
            self.autostart_checkbox.setChecked(not enabled)
            self.autostart_checkbox.blockSignals(False)
        
    def update_model_path_display(self):
        """更新模型路径显示"""
        model_path = self.config.get('model_path')
        if model_path:
            # 截断显示过长的路径
            if len(model_path) > 50:
                display_path = "..." + model_path[-47:]
            else:
                display_path = model_path
            self.model_path_display.setText(display_path)
            self.model_path_display.setToolTip(model_path)
        else:
            self.model_path_display.setText(f"默认: {DEFAULT_MODEL_PATH}")
            self.model_path_display.setToolTip(f"使用默认模型路径: {DEFAULT_MODEL_PATH}")
    
    def browse_model_folder(self):
        """浏览并选择模型文件夹"""
        from PyQt5.QtWidgets import QFileDialog
        
        current_path = self.config.get('model_path')
        if not current_path or not os.path.exists(current_path):
            current_path = str(Path.home())
        
        folder = QFileDialog.getExistingDirectory(
            self,
            "选择Vosk模型文件夹",
            current_path,
            QFileDialog.ShowDirsOnly | QFileDialog.DontResolveSymlinks
        )
        
        if folder:
            # 验证是否是有效的模型文件夹
            required_files = ['am', 'conf', 'graph']
            is_valid = all(os.path.exists(os.path.join(folder, f)) for f in required_files)
            
            if not is_valid:
                reply = QMessageBox.question(
                    self,
                    "确认",
                    f"所选文件夹可能不是有效的Vosk模型文件夹。\n\n"
                    f"有效的模型文件夹应包含: am, conf, graph 等子文件夹。\n\n"
                    f"是否仍要使用此路径？",
                    QMessageBox.Yes | QMessageBox.No
                )
                if reply != QMessageBox.Yes:
                    return
            
            # 保存路径
            self.config.set('model_path', folder)
            self.voice_recognizer.set_model_path(folder)
            self.update_model_path_display()
            
            # 如果语音识别正在运行，延迟更新状态以确保模型加载完成
            QTimer.singleShot(2000, self.update_voice_status)
            
            # 根据验证结果给出提示
            if is_valid:
                QMessageBox.information(
                    self, "成功",
                    f"已设置模型路径为:\n{folder}\n\n模型已加载，语音识别功能可用。"
                )
            else:
                QMessageBox.information(
                    self, "成功",
                    f"已设置模型路径为:\n{folder}\n\n请确保这是有效的Vosk模型文件夹。"
                )
    
    def clear_model_path(self):
        """清除模型路径，恢复默认路径"""
        self.config.set('model_path', None)
        self.voice_recognizer.set_model_path(None)
        self.update_model_path_display()
        
        # 延迟更新状态，确保模型加载完成
        QTimer.singleShot(2000, self.update_voice_status)
        
        QMessageBox.information(
            self, "提示",
            f"已恢复默认模型路径。\n程序将使用: {DEFAULT_MODEL_PATH}"
        )
    
    def open_config_location(self):
        """打开配置文件所在位置"""
        try:
            import subprocess
            config_path = str(CONFIG_FILE.absolute())
            
            if sys.platform == 'win32':
                # Windows: 使用 explorer 并选中文件
                subprocess.run(['explorer', '/select,', config_path])
            elif sys.platform == 'darwin':
                # macOS: 使用 Finder 并选中文件
                subprocess.run(['open', '-R', config_path])
            else:
                # Linux: 打开文件所在目录
                config_dir = str(CONFIG_FILE.parent)
                subprocess.run(['xdg-open', config_dir])
                
            print(f"已打开配置文件位置: {config_path}")
        except Exception as e:
            print(f"打开配置文件位置失败: {e}")
            QMessageBox.warning(self, "错误", f"无法打开配置文件位置: {e}")
    
    def set_autostart(self, enabled):
        """设置开机启动"""
        if sys.platform == 'win32':
            try:
                import winreg
                key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
                app_name = "GuanyuSongPlayer"
                
                key = winreg.OpenKey(
                    winreg.HKEY_CURRENT_USER, key_path, 0, 
                    winreg.KEY_SET_VALUE | winreg.KEY_QUERY_VALUE
                )
                
                if enabled:
                    if getattr(sys, 'frozen', False):
                        exe_path = sys.executable
                    else:
                        exe_path = f'"{sys.executable}" "{os.path.abspath(__file__)}"'
                    winreg.SetValueEx(key, app_name, 0, winreg.REG_SZ, exe_path)
                else:
                    try:
                        winreg.DeleteValue(key, app_name)
                    except FileNotFoundError:
                        pass
                        
                winreg.CloseKey(key)
                return True
            except Exception as e:
                print(f"设置开机启动失败: {e}")
                QMessageBox.warning(self, "警告", f"设置开机启动失败: {e}")
                return False
        else:
            QMessageBox.information(
                self, "提示", 
                "开机启动功能目前仅支持Windows系统"
            )
            return False
                
    def on_tray_activated(self, reason):
        """托盘图标激活处理"""
        if reason == QSystemTrayIcon.DoubleClick:
            self.show_and_activate()
            
    def closeEvent(self, event):
        """窗口关闭事件"""
        event.ignore()
        self.hide()
        self.tray_icon.showMessage(
            "关羽之歌便携版",
            "程序已最小化到系统托盘，双击图标可重新打开",
            QSystemTrayIcon.Information,
            2000
        )
        
    def quit_app(self):
        """退出应用"""
        # 停止所有监听器
        self.hotkey_listener.stop()
        self.voice_recognizer.stop()
        self.player.stop()
        
        # 隐藏托盘图标
        self.tray_icon.hide()
        
        # 退出应用
        QApplication.quit()


def main():
    # 启用高DPI支持
    if hasattr(Qt, 'AA_EnableHighDpiScaling'):
        QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    if hasattr(Qt, 'AA_UseHighDpiPixmaps'):
        QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)
    
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    
    # 设置应用样式
    app.setStyle('Fusion')
    
    window = MainWindow()
    window.show()
    
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()