@echo off
echo 正在安装依赖...
python -m venv .venv
call .venv\Scripts\activate
pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
pip install pyinstaller -i https://pypi.tuna.tsinghua.edu.cn/simple

echo 正在打包...
pyinstaller --clean build.spec

echo 打包完成！
pause