import dotenv from 'dotenv';
dotenv.config();

console.log('=== 测试火山引擎 LLM ===\n');

const { LLMClient } = await import('./src/layers/tools/llm-client.js');

const client = new LLMClient();
console.log('Client created');
console.log('isConfigured():', client.isConfigured());

// 偷看一下 client 的内部配置
console.log('\n检查 Client 配置:');
console.log('  (没有直接访问私有属性的方法，让我们尝试调用)');

console.log('\n正在调用 LLM...');
try {
  const result = await client.chatWithSystem(
    '你是一个AI助手',
    '你好，请用一句话介绍你自己'
  );
  console.log('\n✅ 调用成功!');
  console.log('回复:', result);
} catch (e) {
  console.error('\n❌ 调用失败:');
  console.error(e);
}
