import { CeoAgent } from './src/layers/agents/ceo.js';
import * as path from 'path';
import { fileURLToPath } from 'url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));

async function testCeoScan() {
  console.log('🧪 测试CEO本地文件扫描能力...\n');

  const ceo = new CeoAgent('test-project');

  // 测试scanLocalProject（访问私有方法，用any绕过）
  console.log('1️⃣  测试scanLocalProject...');
  const projectScan = (ceo as any).scanLocalProject();
  console.log('扫描结果:', JSON.stringify(projectScan, null, 2));

  // 测试loadMemorySummary
  console.log('\n2️⃣  测试loadMemorySummary...');
  try {
    const memorySummary = await (ceo as any).loadMemorySummary();
    console.log('记忆摘要:', JSON.stringify(memorySummary, null, 2));
  } catch (e) {
    console.log('记忆加载失败（可能数据库未初始化）:', e);
  }

  // 测试getSystemPrompt
  console.log('\n3️⃣  CEO系统prompt预览（前500字符）:');
  const systemPrompt = (ceo as any).getSystemPrompt();
  console.log(systemPrompt.slice(0, 500) + '...');

  console.log('\n✅ 测试完成！');

  // 总结
  console.log('\n📊 总结:');
  if (projectScan.hasProjects) {
    console.log(`- 发现 ${projectScan.projects.length} 个历史项目`);
    projectScan.projects.forEach((p: any) => {
      console.log(`  * ${p.projectId}: ${p.files.length} 个文件`);
    });
  } else {
    console.log('- 暂无历史项目');
  }
  console.log('- CEO系统prompt已升级为项目管理专家');
}

testCeoScan().catch(console.error);
