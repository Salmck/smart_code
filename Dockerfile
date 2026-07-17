# Ordinex 专属增长码系统 —— 生产镜像
# 构建：docker build -t ordinex .
# 运行：docker run -d -p 80:8000 -e JWT_SECRET=xxx -e DYNCODE_SECRET=yyy -v ordinex-data:/app/data ordinex
FROM python:3.12-slim

WORKDIR /app

# 依赖单独一层，利用缓存
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# 数据卷：SQLite 库与上传文件持久化到这里
ENV UPLOAD_DIR=/app/data/uploads \
    DATABASE_URL=sqlite:////app/data/ordinex.db \
    DEBUG=false
VOLUME ["/app/data"]

EXPOSE 8000

# 生产建议通过 -e 注入 JWT_SECRET / DYNCODE_SECRET / SMS_PROVIDER 等
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
