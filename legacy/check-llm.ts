import dotenv from 'dotenv';
dotenv.config();

console.log('环境变量检查:');
console.log('USE_LLM:', process.env.USE_LLM);
console.log('OPENAI_API_KEY:', process.env.OPENAI_API_KEY ? '已设置' : '未设置');
console.log('API_BASE_URL:', process.env.API_BASE_URL);
console.log('MODEL_NAME:', process.env.MODEL_NAME);

// 测试导入 LLMClient
console.log('\n正在导入 LLMClient...');
const { LLMClient, llmClient } = await import('./src/layers/tools/llm-client.js');

console.log('llmClient 已创建');
console.log('isConfigured():', llmClient.isConfigured());

// 检查内部配置
console.log('\nLLMClient 内部配置检查:');
const testClient = new LLMClient();
console.log('测试 client 创建完成');
