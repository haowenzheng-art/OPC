import dotenv from 'dotenv';
dotenv.config();

import { startBot, formatAgentName, setCeoAgent as setBotCeo } from './layers/visualization/feishu/bot.js';
import { ProjectOrchestrator } from './layers/orchestration/orchestrator.js';
import { onMessage } from './layers/messaging/bus.js';
import { CeoAgent } from './layers/agents/ceo.js';
import { FeishuMultiBotConfig, setCeoAgent as setServerCeo, replyToUser } from './layers/visualization/feishu/server.js';

// 从环境变量读取配置
const MODE = (process.env.OPC_MODE || 'cli') as 'cli' | 'feishu';
const FEISHU_PORT = parseInt(process.env.FEISHU_BOT_PORT || '3000');

// 全局项目实例
let currentProject: ProjectOrchestrator | null = null;
let currentCeo: CeoAgent | null = null;

async function main() {
  console.log('=== OPC 多Agent系统 ===');
  console.log(`运行模式: ${MODE}`);
  console.log('');

  if (MODE === 'feishu') {
    startFeishuMode();
  } else {
    startCliMode();
  }
}

// ========== CLI模式（自动运行演示） ==========
async function startCliMode() {
  startBot({ mode: 'cli' });

  onMessage((msg) => {
    const from = formatAgentName(msg.fromAgent);
    const to = msg.toAgent ? formatAgentName(msg.toAgent) : '群聊';
    console.log(`[${from}] -> [${to}]: ${msg.content}`);
  });

  await new Promise(r => setTimeout(r, 500));

  const project = new ProjectOrchestrator('简单待办清单');
  await project.init();
  await project.start();

  setTimeout(() => {
    console.log('\n=== 演示结束 ===');
    console.log('查看生成的项目: generated-projects/' + project.projectId);
  }, 25000);
}

// ========== 读取6个Bot的配置 ==========
function getFeishuMultiBotConfig(): FeishuMultiBotConfig {
  return {
    bots: {
      ceo: {
        appId: process.env.FEISHU_CEO_APP_ID || '',
        appSecret: process.env.FEISHU_CEO_APP_SECRET || '',
        verificationToken: process.env.FEISHU_CEO_VERIFICATION_TOKEN || '',
        encryptKey: process.env.FEISHU_CEO_ENCRYPT_KEY || '',
      },
      pm: {
        appId: process.env.FEISHU_PM_APP_ID || '',
        appSecret: process.env.FEISHU_PM_APP_SECRET || '',
      },
      frontend: {
        appId: process.env.FEISHU_FRONTEND_APP_ID || '',
        appSecret: process.env.FEISHU_FRONTEND_APP_SECRET || '',
      },
      backend: {
        appId: process.env.FEISHU_BACKEND_APP_ID || '',
        appSecret: process.env.FEISHU_BACKEND_APP_SECRET || '',
      },
      test: {
        appId: process.env.FEISHU_TEST_APP_ID || '',
        appSecret: process.env.FEISHU_TEST_APP_SECRET || '',
      },
      ops: {
        appId: process.env.FEISHU_OPS_APP_ID || '',
        appSecret: process.env.FEISHU_OPS_APP_SECRET || '',
      },
    },
    groupChatId: process.env.FEISHU_GROUP_CHAT_ID || '',
    port: FEISHU_PORT,
  };
}

// ========== 飞书模式（等待用户指令） ==========
async function startFeishuMode() {
  const feishuConfig = getFeishuMultiBotConfig();

  // 验证CEO Bot配置必需
  if (!feishuConfig.bots.ceo.appId || !feishuConfig.bots.ceo.appSecret) {
    console.error('[错误] CEO Bot配置不完整，请检查 .env');
    process.exit(1);
  }

  if (!feishuConfig.groupChatId) {
    console.error('[错误] 群聊ID未配置，请检查 .env');
    process.exit(1);
  }

  // 启动飞书Bot
  startBot({
    mode: 'feishu',
    feishuConfig
  });

  // 创建CEO Agent等待用户指令
  currentCeo = new CeoAgent('feishu_project');
  currentCeo.onStartProject = handleStartProject;
  currentCeo.onStopProject = handleStopProject;
  currentCeo.onReply = (msg: string) => {
    console.log('[CEO] 发送回复到飞书:', msg);
    replyToUser(msg);
  };

  setBotCeo(currentCeo);
  setServerCeo(currentCeo);

  // 启动CEO Agent的消息处理循环
  currentCeo.run();

  console.log('');
  console.log('[飞书模式] 已启动，等待用户私聊CEO指令...');
  console.log('  • 私聊 "/start [想法]" 开始项目');
  console.log('  • 私聊 "/stop" 停止项目');
  console.log('  • 私聊 "/help" 查看帮助');
}

// ========== 处理启动项目 ==========
async function handleStartProject(userIdea: string) {
  if (currentProject) {
    console.warn('[警告] 已有项目进行中');
    return;
  }

  console.log(`[启动项目] ${userIdea}`);

  currentProject = new ProjectOrchestrator(userIdea, currentCeo || undefined);
  await currentProject.init();
  await currentProject.start();
}

// ========== 处理停止项目 ==========
async function handleStopProject() {
  if (!currentProject) {
    console.warn('[警告] 没有进行中的项目');
    return;
  }

  console.log('[停止项目]');
  currentProject = null;
}

main().catch(console.error);

