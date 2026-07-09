# 飞书Bot 配置指南

## 1. 创建飞书应用

1. 访问 [飞书开放平台](https://open.feishu.cn/)
2. 登录后进入 **开发者后台**
3. 点击 **创建企业自建应用**
4. 填写信息：
   - 应用名称：OPC Agent
   - 应用描述：多Agent协作开发系统
   - 点击 **确定创建**

## 2. 获取凭证

在应用详情页，找到 **凭证与基础信息**：

```
App ID: cli_a1b2c3d4e5f6g7h8
App Secret: abcdef1234567890abcdef1234567890
```

将这些填入 `.env.feishu`

## 3. 配置事件订阅

1. 左侧菜单选择 **事件订阅**
2. 点击 **添加事件订阅**
3. 配置：
   - **请求网址（Request URL）**: `https://your-domain.com/webhook/event`
     - （开发时可以用 ngrok 内网穿透）
   - **Encrypt Key**: 点击刷新生成一个，填入配置
   - **Verification Token**: 系统自动生成，填入配置

4. 点击 **保存**，然后点击 **发送验证请求** 测试连接

## 4. 添加事件

在 **事件订阅** 页面，点击 **添加事件**：

添加以下事件：
- `im.message.receive_v1` - 接收消息

点击 **保存** 后需要重新发布应用

## 5. 配置权限

左侧菜单选择 **权限管理**，添加以下权限：

- `im:message` - 发送消息
- `im:message.group_at_msg` - 获取群@消息
- `im:message.group_msg` - 获取群消息（如果需要）
- `im:message.p2p_msg` - 获取私聊消息

## 6. 发布版本

1. 左侧菜单选择 **版本管理与发布**
2. 点击 **创建版本**
3. 填写版本信息，点击 **保存**
4. 点击 **申请发布**
5. 选择 **发布范围**（企业内全部员工或指定部门）
6. 点击 **确认申请发布**

（如果只是测试用，可以在 **测试企业与人员** 中添加测试人员，不用正式发布）

## 7. 获取群聊ID

1. 在飞书中创建一个群聊
2. 将机器人添加到群聊
3. 在群聊中 **@机器人** 说一句话
4. 查看服务端日志，会打印出 `chat_id`

或者使用飞书API获取群列表

## 8. 启动服务

```bash
# 复制配置模板
cp .env.feishu.example .env.feishu

# 编辑配置，填入真实凭证
vim .env.feishu

# 设置环境变量并启动
export $(cat .env.feishu | xargs)
npm run dev
```

## 9. 开发时使用 ngrok

如果没有公网域名，可以用 ngrok 做内网穿透：

```bash
# 安装 ngrok
# 下载: https://ngrok.com/download

# 启动隧道
ngrok http 3000

# 会得到一个公网地址，如: https://abc123.ngrok.io
# 将这个地址填入飞书事件订阅的 Request URL:
# https://abc123.ngrok.io/webhook/event
```

## 10. 测试

1. **私聊机器人**，发送 `/help` 查看帮助
2. 私聊发送 `/start 做一个待办应用` 启动项目
3. 在群聊中可以看到各Agent的实时对话

## 完整的 .env.feishu 示例

```env
# 飞书应用凭证
FEISHU_APP_ID=cli_a1b2c3d4e5f6g7h8
FEISHU_APP_SECRET=abcdef1234567890abcdef1234567890

# 事件验证配置
FEISHU_VERIFICATION_TOKEN=abcdef123456
FEISHU_ENCRYPT_KEY=abcdefghijklmnopqrstuvwxyz123456

# 群聊ID
FEISHU_GROUP_CHAT_ID=oc_a1b2c3d4e5f6g7h8i9j0k1l2

# 服务端口
FEISHU_PORT=3000

# 运行模式：cli 或 feishu
OPC_MODE=feishu
```

## 故障排查

| 问题 | 可能原因 | 解决方案 |
|-----|---------|---------|
| 飞书验证请求失败 | URL不对或服务没启动 | 检查ngrok/公网地址和服务状态 |
| 收不到群消息 | 权限没开或机器人不在群 | 检查权限配置和群成员 |
| 消息发送失败 | App Secret错误 | 检查凭证配置 |
