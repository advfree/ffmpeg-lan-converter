# FFmpeg 转换台

一个适合在 Ubuntu Docker 上运行的内网 FFmpeg 批量转换 Web 工具。前端资源全部包含在镜像中，不使用 CDN，程序也不会主动访问外网。

## 功能

- 浏览挂载目录、选择单个文件或扫描文件夹（可递归），支持全选与取消选择。
- 视频、音频、图片常用输出格式；无损预设均明确标注。
- M4A 支持 AAC 有损编码、AAC/ALAC 原音频封装（不重编码）和 ALAC 无损编码。
- 默认使用 M4A 原音频封装、空文件名后缀和“同名跳过”。
- 默认输出到源文件夹，也可选择挂载范围内的其他已有文件夹。
- 同名文件支持跳过、自动加序号或覆盖目标文件。
- 先写入临时文件，FFmpeg 成功且 FFprobe 校验通过后再原子改名；只有随后才会按选项删除源文件。
- 串行任务队列、实时进度、取消、逐文件结果与错误信息。
- 浅色、深色、随系统主题。
- 本地账号密码、PBKDF2 密码哈希、HttpOnly 会话 Cookie、CSRF 防护和登录限速。
- 非 root 容器、丢弃 Linux capabilities、健康检查和磁盘空间预检。

## 快速启动

```bash
git clone https://github.com/advfree/ffmpeg-lan-converter.git
cd ffmpeg-lan-converter
cp .env.example .env
mkdir -p data files
sudo chown -R 10001:10001 data files
docker compose up -d --build
docker compose logs ffmpeg-converter
```

访问 `http://服务器IP:10888`。默认账号为 `root`。如果 `.env` 没有设置 `APP_PASSWORD`，首次启动日志会显示一次随机密码，配置会持久化到 `./data/config.json`。

也可以直接使用发布的镜像，将 `compose.yaml` 中的 `build: .` 改为：

```yaml
image: ghcr.io/advfree/ffmpeg-lan-converter:latest
```

## 挂载其他目录

编辑 `.env`：

```dotenv
HOST_MEDIA_ROOT=/path/on/host
CONTAINER_MEDIA_ROOT=/media
```

`HOST_MEDIA_ROOT` 是主机上的媒体目录；`CONTAINER_MEDIA_ROOT` 是网页中显示的容器路径。应用只能访问该挂载范围。若主机目录权限较严格，请确保容器用户 `10001:10001` 对媒体文件有相应的读写权限。

如果希望网页显示和接受主机的绝对路径，可以把两项设成同一个绝对路径。对于 `/root` 下的路径，仍需确保父目录可遍历且媒体目录有权限。

## 密码

可在首次启动前通过 `.env` 固定密码：

```dotenv
APP_USERNAME=root
APP_PASSWORD=replace-with-a-long-password
```

该设置只在 `./data/config.json` 尚未创建时生效。之后可以在网页右上角修改密码。

如果忘记密码，可先停止服务、确认目标后删除 `./data/config.json`，再启动以生成新密码。此操作不会删除媒体目录中的文件：

```bash
docker compose down
rm ./data/config.json
docker compose up -d
docker compose logs ffmpeg-converter
```

## 安全提示

建议仅向可信内网开放端口。若需要跨不可信网络访问，请使用 HTTPS 反向代理并增加访问控制。

启用“成功后删除源文件”前请仔细核对扫描范围。应用只会在输出完成且 FFprobe 校验通过后删除选中的源媒体文件，但删除仍不可撤销。

“无损”表示本次编码或封装不会额外丢失信息；将已经有损的音频转成 FLAC、WAV 或 ALAC，不能恢复此前丢失的音质。原流封装要求源编码能被目标容器承载。

## 开发测试

```bash
python -m unittest discover -s tests -v
node --check static/app.js
```

项目采用 [MIT License](LICENSE)。
