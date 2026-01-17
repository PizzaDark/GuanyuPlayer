@echo off
echo 正在安装依赖...
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
pip install pyinstaller

echo 正在打包...
pyinstaller --clean build.spec

echo 打包完成！
pause