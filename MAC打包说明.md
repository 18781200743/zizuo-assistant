# Mac 版打包说明（GitHub Actions，免费）

> PyInstaller 不能跨系统编译，所以必须让 macOS 机器来打。用 GitHub Actions 的 macOS 机器，不用自己有 Mac。

## 一、准备仓库

1. 注册/登录 GitHub，新建一个 **Private** 仓库（不要勾 README）。
2. 上传项目（任选一种）：
   - **GitHub Desktop**（推荐，图形界面）：Add local repository → 选 `F:\F\game\zizuo` → Publish；
   - 或者网页上「Add file → Upload files」，把**除以下目录外**的文件拖进去：
     `.venv`、`dist`、`build`、`saves`、`_re`、`_extract_blyx`、`人物图片`、`templates`。
   - `.gitignore` 已经写好，用 GitHub Desktop 会自动忽略这些。

> `saves/webview` 里是你的登录 Cookie，**绝对不能上传**。`.gitignore` 已经排除了 `saves/`。

## 二、跑打包

1. 进仓库 → 顶部 **Actions**；
2. 左边选 **build-mac** → 右边 **Run workflow** → 绿色按钮；
3. 等 5~10 分钟（第一次装依赖慢一点）；
4. 点开这次运行 → 最下面 **Artifacts** → 下载 `Zizuo-mac-x86_64`。

里面就是 `Zizuo助手-mac.zip`。

## 三、发给朋友

把 zip 发给他，让他按 `Mac版使用说明.txt`：

1. 解压 → 把 `Zizuo助手.app` 拖到「应用程序」；
2. **右键 → 打开 → 打开**（只需一次，因为没买苹果签名）；
3. 打开后点「打开游戏窗口」（需要 Chrome）；
4. 登录游戏。

## 四、如果构建失败

看 Actions 里红色的那一步，把日志发我。常见原因：

- `opencv-python` 在 x86_64 上装不上 → 把 `requirements_mac.txt` 里的版本换成本机可用的；
- Tkinter 缺失 → 换 `python-version: "3.12"`；
- `--target-arch x86_64` 报错 → 删掉这行（runner 本身是 Intel，默认就是 x86_64）。

## 五、自己用 Mac 打包（可选）

有 Mac 的话更简单：

```bash
cd zizuo
bash build_mac.sh
# 产物：dist/Zizuo助手-mac.zip
```
