import sys
import os
import json
import threading
import time
from pathlib import Path

from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QSlider, QLabel, QComboBox, QCheckBox,
    QGroupBox, QMessageBox, QSystemTrayIcon, QMenu, QAction, QFrame
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

# 按键优先级排序（用于显示）
KEY_PRIORITY = {
    'ctrl': 0, 'alt': 1, 'shift': 2, 'win': 3, 'cmd': 3
}


class SignalEmitter(QObject):
    """用于线程间通信的信号发射器"""
    trigger_play = pyqtSignal()
    keyword_detected = pyqtSignal()
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
    def __init__(self, keyword, callback, device_index=None):
        self.keyword = keyword
        self.callback = callback
        self.device_index = device_index
        self.running = False
        self.thread = None
        self.model = None
        self.enabled = VOSK_AVAILABLE
        
    def load_model(self):
        """加载Vosk模型"""
        if not VOSK_AVAILABLE:
            return False
        try:
            model_path = self.find_model_path()
            if model_path and os.path.exists(model_path):
                self.model = vosk.Model(model_path)
                return True
        except Exception as e:
            print(f"模型加载失败: {e}")
        return False
    
    def find_model_path(self):
        """查找Vosk模型路径"""
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
                        if self.keyword in text:
                            print(f"检测到关键词: {self.keyword}")
                            self.callback()
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


class AudioPlayer:
    """音频播放器"""
    def __init__(self, audio_file):
        self.audio_file = audio_file
        self.is_playing = False
        self.initialized = False
        self._init_mixer()
        
    def _init_mixer(self):
        try:
            mixer.init()
            self.initialized = True
        except Exception as e:
            print(f"音频初始化失败: {e}")
            self.initialized = False
        
    def play(self):
        if not self.initialized:
            return
        try:
            if not os.path.exists(self.audio_file):
                print(f"音频文件不存在: {self.audio_file}")
                return
            mixer.music.load(self.audio_file)
            mixer.music.play()
            self.is_playing = True
        except Exception as e:
            print(f"播放错误: {e}")
            
    def stop(self):
        if not self.initialized:
            return
        try:
            mixer.music.stop()
        except Exception:
            pass
        self.is_playing = False
        
    def toggle(self):
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
            'keyword': '释怀'
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
            with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
                json.dump(self.config, f, ensure_ascii=False, indent=2)
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
        audio_file = os.path.join(self.base_path, "guanyu_song.mp3")
        self.player = AudioPlayer(audio_file)
        self.player.set_volume(self.config.get('volume', 0.7))
        
        # 热键监听器
        self.hotkey_listener = HotkeyListener(
            self.config.get('hotkey', DEFAULT_HOTKEY.copy()),
            self.on_hotkey_triggered
        )
        
        # 语音识别器
        self.voice_recognizer = VoiceRecognizer(
            self.config.get('keyword', '释怀'),
            self.on_keyword_detected,
            self.config.get('audio_device')
        )
        
        # 快捷键捕获器
        self.hotkey_capture = None
        
        # 信号连接
        self.signals.trigger_play.connect(self.toggle_play)
        self.signals.keyword_detected.connect(self.toggle_play)
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
        self.setWindowTitle("关羽之歌便携版")
        
        # 设置图标
        icon_path = os.path.join(self.base_path, "guanyu_icon.ico")
        if os.path.exists(icon_path):
            self.setWindowIcon(QIcon(icon_path))
        
        # DPI适配
        self.setup_dpi_scaling()
        
        # 主窗口部件
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        layout = QVBoxLayout(central_widget)
        layout.setSpacing(15)
        layout.setContentsMargins(20, 20, 20, 20)
        
        # === 触发说明 ===
        trigger_group = QGroupBox("触发方式")
        trigger_layout = QVBoxLayout(trigger_group)
        
        trigger_label = QLabel()
        keyword = self.config.get('keyword', '释怀')
        trigger_label.setText(
            f'<p style="line-height: 1.6;">'
            f'按下 <b style="color: #2196F3;">快捷键</b> 或语音中检测到关键词 '
            f'<b style="color: #E91E63;">【{keyword}】</b> 时触发播放<br>'
            f'<span style="color: #666; font-size: 9pt;">(再次触发可停止播放)</span>'
            f'</p>'
        )
        trigger_label.setTextFormat(Qt.RichText)
        trigger_label.setWordWrap(True)
        trigger_layout.addWidget(trigger_label)
        self.trigger_label = trigger_label  # 保存引用以便更新
        
        layout.addWidget(trigger_group)
        
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
        
        # === 设备选择组 ===
        device_group = QGroupBox("音频输入设备 (用于语音识别)")
        device_layout = QVBoxLayout(device_group)
        
        self.device_combo = QComboBox()
        self.populate_audio_devices()
        self.device_combo.currentIndexChanged.connect(self.on_device_changed)
        device_layout.addWidget(self.device_combo)
        
        # 语音识别状态
        self.voice_status_label = QLabel()
        self.update_voice_status()
        device_layout.addWidget(self.voice_status_label)
        
        layout.addWidget(device_group)
        
        # === 快捷键设置组 ===
        hotkey_group = QGroupBox("快捷键设置")
        hotkey_layout = QVBoxLayout(hotkey_group)
        
        current_hotkey = self.config.get('hotkey', DEFAULT_HOTKEY.copy())
        hotkey_str = self.format_hotkey(current_hotkey)
        
        hotkey_display_layout = QHBoxLayout()
        hotkey_display_layout.addWidget(QLabel("当前快捷键:"))
        self.hotkey_label = QLabel(hotkey_str)
        self.hotkey_label.setStyleSheet("""
            font-weight: bold; 
            color: #2196F3; 
            font-size: 12pt;
            padding: 5px 10px;
            background-color: #E3F2FD;
            border-radius: 4px;
        """)
        hotkey_display_layout.addWidget(self.hotkey_label)
        hotkey_display_layout.addStretch()
        hotkey_layout.addLayout(hotkey_display_layout)
        
        hotkey_btn_layout = QHBoxLayout()
        self.set_hotkey_btn = QPushButton("修改快捷键")
        self.set_hotkey_btn.clicked.connect(self.start_hotkey_capture)
        self.reset_hotkey_btn = QPushButton("恢复默认")
        self.reset_hotkey_btn.clicked.connect(self.reset_hotkey)
        hotkey_btn_layout.addWidget(self.set_hotkey_btn)
        hotkey_btn_layout.addWidget(self.reset_hotkey_btn)
        hotkey_layout.addLayout(hotkey_btn_layout)
        
        layout.addWidget(hotkey_group)
        
        # === 其他设置组 ===
        settings_group = QGroupBox("其他设置")
        settings_layout = QVBoxLayout(settings_group)
        
        self.autostart_checkbox = QCheckBox("开机自动启动")
        self.autostart_checkbox.setChecked(self.config.get('auto_start', False))
        self.autostart_checkbox.stateChanged.connect(self.on_autostart_changed)
        settings_layout.addWidget(self.autostart_checkbox)
        
        layout.addWidget(settings_group)
        
        # === 作者信息 ===
        author_frame = QFrame()
        author_frame.setStyleSheet("""
            QFrame {
                background-color: #f5f5f5;
                border-radius: 8px;
                padding: 10px;
            }
        """)
        author_layout = QHBoxLayout(author_frame)
        author_layout.setContentsMargins(10, 8, 10, 8)
        author_layout.addStretch()
        author_label = QLabel(
            '作者@<a href="https://space.bilibili.com/6297797" '
            'style="color: #2196F3; text-decoration: none; font-weight: bold;">'
            '依然匹萨吧</a>'
        )
        author_label.setOpenExternalLinks(True)
        author_label.setTextFormat(Qt.RichText)
        author_layout.addWidget(author_label)
        author_layout.addStretch()
        layout.addWidget(author_frame)
        
        # 设置窗口大小
        self.setMinimumSize(420, 520)
        self.resize(450, 560)
        
    def format_hotkey(self, hotkey_list):
        """格式化热键显示"""
        # 按优先级排序
        def key_priority(k):
            return (KEY_PRIORITY.get(k.lower(), 10), k)
        sorted_keys = sorted(hotkey_list, key=key_priority)
        return ' + '.join([k.upper() for k in sorted_keys])
        
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
                '<span style="color: #FF9800;">⚠ 语音模型未找到，请下载模型</span>'
            )
        self.voice_status_label.setTextFormat(Qt.RichText)
        
    def setup_dpi_scaling(self):
        """设置DPI缩放"""
        screen = QApplication.primaryScreen()
        dpi = screen.logicalDotsPerInch()
        scale_factor = dpi / 96.0
        
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
        self.device_combo.clear()
        self.device_combo.addItem("默认设备", None)
        
        try:
            devices = sd.query_devices()
            for i, device in enumerate(devices):
                if device['max_input_channels'] > 0:
                    self.device_combo.addItem(f"{device['name']}", i)
        except Exception as e:
            print(f"获取音频设备失败: {e}")
            
        # 设置当前选中的设备
        saved_device = self.config.get('audio_device')
        if saved_device is not None:
            index = self.device_combo.findData(saved_device)
            if index >= 0:
                self.device_combo.setCurrentIndex(index)
                
    def on_hotkey_triggered(self):
        """热键触发回调（从子线程调用）"""
        self.signals.trigger_play.emit()
        
    def on_keyword_detected(self):
        """关键词检测回调（从子线程调用）"""
        self.signals.keyword_detected.emit()
        
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
        self.set_hotkey_btn.setText("修改快捷键")
        
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
        
    def on_autostart_changed(self, state):
        """开机启动设置变化"""
        enabled = state == Qt.Checked
        self.config.set('auto_start', enabled)
        success = self.set_autostart(enabled)
        if not success:
            self.autostart_checkbox.blockSignals(True)
            self.autostart_checkbox.setChecked(not enabled)
            self.autostart_checkbox.blockSignals(False)
        
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