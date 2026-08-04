# 前端页面quick_start
本项目的前端代码统一放置在 frontend/ 目录下。如果你需要运行或修改前端页面，请按照以下步骤操作：

## 1. 环境准备 (Prerequisites)
在运行前端代码之前，请确保你的电脑上已经安装了 Node.js。

推荐安装版本：Node.js 22.x (LTS版本)

检查是否安装成功，请在终端运行：

`node -v`
`npm -v`

## 2. 安装前端依赖
   
由于禁止提交 node_modules，拉取代码后你需要先安装依赖：

### 第一步：进入前端文件夹
`cd frontend`

### 第二步：安装所有依赖包
`npm install`

## 3. 本地启动前端服务

依赖安装完成后，运行以下命令启动本地开发服务器：

`npm run dev`

执行成功后，终端会输出一个本地链接（通常是 http://localhost:5173 或 http://localhost:3000），在浏览器中打开该链接即可看到前端页面。

## 4. 前端环境变量配置 (重要)
如果前端需要调用后端 API 或第三方服务，请在 frontend/ 目录下创建一个 .env 或 .env.local 文件，并配置相关环境变量。

⚠️ 警告：绝对不要把包含真实 API Key 的 .env 文件提交到 GitHub！（已经在 .gitignore 中配置拦截）。
