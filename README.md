# 反馈画布 / Feedback Studio

这是一款用来写视觉反馈的 Windows 桌面工具：结合 PureRef 式无限画布和截图软件的基础标注，图片和标注始终保持可编辑。

## 功能
- 无限画布，沿用 PureRef 的操作和快捷键（中键 / Alt+左键平移、滚轮缩放、Space 聚焦、C+左键裁剪等）
- 可编辑标注：矩形、椭圆、箭头、文字、画笔、荧光笔、直线钢笔、曲线钢笔、编号、区域、放大镜、色块
- 选中文字时滚轮调字号，选中线条时滚轮调粗细
- Ctrl+C / Ctrl+V 跨软件复制粘贴：内部复制保留可编辑对象，粘贴到外部时自动合成一张图
- 多标签无边框窗口、极简模式（Tab，所有标签同步）、全局自定义颜色
- `.fbboard` 项目文件保存原图和全部标注，并自动生成恢复副本
- 局域网共享：一键分享当前画布并复制链接，对方在反馈画布里按 Ctrl+V 即可打开；通过 TLS 加密传输，同一时间只允许一人编辑

## 运行
```bash
cd app
pip install -r requirements.txt
python main.py
```

## 打包 Windows 单文件程序
```bash
cd app
python build_release.py   # 输出到 ../outputs/
```

## 测试
```bash
cd app
python test_v065.py && python test_v064.py && python test_lan_core.py && python test_lan_ui.py && python test_v6.py
```
测试只使用合成数据和本机回环地址，不会改动系统防火墙或注册表。

## 局域网共享说明
- 链接格式为 `http://<IP>:48761/fb#<token>`。`#` 后面包含访问密钥和证书指纹，浏览器不会把这部分发给服务器
- 同一个端口对浏览器返回说明页，对反馈画布提供 TLS 加密服务
- 首次分享时会请求一次管理员权限，放行 Windows 防火墙的 TCP 48761 / UDP 48762
- 不经过任何外部服务器，只在局域网内直连

更新记录见 `CHANGELOG-v0.6.5.txt`、`CHANGELOG-v0.6.4.txt`。
