export interface LLMConfig {
  apiKey: string;
  baseUrl?: string;
  model?: string;
  apiFormat?: 'anthropic' | 'openai';  // 新增：支持两种格式
  apiVersion?: string;  // 新增：可配置anthropic-version
}

export interface Message {
  role: 'system' | 'user' | 'assistant';
  content: string;
}

export interface LLMResponse {
  content: string;
  usage?: {
    promptTokens: number;
    completionTokens: number;
    totalTokens: number;
  };
}

const DEFAULT_MODEL = 'agnes-2.0-flash';
const DEFAULT_BASE_URL = 'https://apihub.agnes-ai.com/v1';
const DEFAULT_API_FORMAT = 'anthropic';
const DEFAULT_ANTHROPIC_VERSION = '2023-06-01';

export class LLMClient {
  private config: LLMConfig;

  constructor(config?: Partial<LLMConfig>) {
    const apiKey = config?.apiKey || process.env.OPENAI_API_KEY || process.env.ANTHROPIC_API_KEY || '';
    const baseUrl = config?.baseUrl || process.env.API_BASE_URL || DEFAULT_BASE_URL;
    const model = config?.model || process.env.MODEL_NAME || DEFAULT_MODEL;
    const apiFormat = (config?.apiFormat || process.env.API_FORMAT || DEFAULT_API_FORMAT) as 'anthropic' | 'openai';
    const apiVersion = config?.apiVersion || process.env.ANTHROPIC_VERSION || DEFAULT_ANTHROPIC_VERSION;

    if (!apiKey && process.env.USE_LLM !== 'false') {
      console.warn('[LLMClient] No API key found, will use hardcoded mode');
    }

    this.config = { apiKey, baseUrl, model, apiFormat, apiVersion };
  }

  isConfigured(): boolean {
    return !!this.config.apiKey && process.env.USE_LLM !== 'false';
  }

  async chat(messages: Message[], options?: { temperature?: number; maxTokens?: number }): Promise<LLMResponse> {
    if (!this.isConfigured()) {
      throw new Error('LLM not configured - set OPENAI_API_KEY or USE_LLM=false to use hardcoded mode');
    }

    if (this.config.apiFormat === 'openai') {
      return this.chatOpenAI(messages, options);
    } else {
      return this.chatAnthropic(messages, options);
    }
  }

  // ========== 流式响应支持 ==========
  async *chatStream(
    messages: Message[],
    options?: { temperature?: number; maxTokens?: number }
  ): AsyncGenerator<string, LLMResponse, unknown> {
    if (!this.isConfigured()) {
      throw new Error('LLM not configured - set OPENAI_API_KEY or USE_LLM=false to use hardcoded mode');
    }

    if (this.config.apiFormat === 'openai') {
      return yield* this.chatStreamOpenAI(messages, options);
    } else {
      return yield* this.chatStreamAnthropic(messages, options);
    }
  }

  private async *chatStreamAnthropic(
    messages: Message[],
    options?: { temperature?: number; maxTokens?: number }
  ): AsyncGenerator<string, LLMResponse, unknown> {
    let systemPrompt = '';
    const filteredMessages = messages.filter(m => {
      if (m.role === 'system') {
        systemPrompt = m.content;
        return false;
      }
      return true;
    });

    const body: any = {
      model: this.config.model,
      messages: filteredMessages,
      temperature: options?.temperature ?? 0.7,
      max_tokens: options?.maxTokens ?? 4096,
      stream: true
    };

    if (systemPrompt) {
      body.system = systemPrompt;
    }

    let url = this.config.baseUrl || DEFAULT_BASE_URL;
    if (!url.includes('/messages') && !url.includes('/v1')) {
      url = `${url}/v1/messages`;
    } else if (url.includes('/v1') && !url.includes('/messages')) {
      url = `${url}/messages`;
    }

    const headers: any = {
      'Content-Type': 'application/json',
      'x-api-key': this.config.apiKey,
      'anthropic-version': this.config.apiVersion || DEFAULT_ANTHROPIC_VERSION,
      'accept': 'text/event-stream'
    };

    const response = await fetch(url, {
      method: 'POST',
      headers,
      body: JSON.stringify(body),
    });

    if (!response.ok) {
      const errorText = await response.text();
      throw new Error(`LLM request failed: ${response.status} ${response.statusText} - ${errorText}`);
    }

    const reader = response.body?.getReader();
    const decoder = new TextDecoder();
    let fullContent = '';
    let inputTokens = 0;
    let outputTokens = 0;

    if (reader) {
      try {
        while (true) {
          const { done, value } = await reader.read();
          if (done) break;

          const chunk = decoder.decode(value);
          const lines = chunk.split('\n');

          for (const line of lines) {
            if (line.startsWith('data: ')) {
              const dataStr = line.slice(6);
              if (dataStr === '[DONE]') continue;

              try {
                const data = JSON.parse(dataStr);

                if (data.type === 'message_start') {
                  inputTokens = data.message?.usage?.input_tokens || 0;
                } else if (data.type === 'content_block_delta') {
                  const text = data.delta?.text || '';
                  if (text) {
                    fullContent += text;
                    yield text;
                  }
                } else if (data.type === 'message_delta') {
                  outputTokens = data.usage?.output_tokens || 0;
                }
              } catch {
                // 忽略解析错误
              }
            }
          }
        }
      } finally {
        reader.releaseLock();
      }
    }

    return {
      content: fullContent,
      usage: {
        promptTokens: inputTokens,
        completionTokens: outputTokens,
        totalTokens: inputTokens + outputTokens
      }
    };
  }

  private async *chatStreamOpenAI(
    messages: Message[],
    options?: { temperature?: number; maxTokens?: number }
  ): AsyncGenerator<string, LLMResponse, unknown> {
    const body: any = {
      model: this.config.model,
      messages: messages,
      temperature: options?.temperature ?? 0.7,
      max_tokens: options?.maxTokens ?? 4096,
      stream: true
    };

    let url = this.config.baseUrl || DEFAULT_BASE_URL;
    if (!url.includes('/chat/completions') && !url.includes('/v1')) {
      url = `${url}/v1/chat/completions`;
    } else if (url.includes('/v1') && !url.includes('/chat/completions')) {
      url = `${url}/chat/completions`;
    }

    const headers: any = {
      'Content-Type': 'application/json',
      'Authorization': `Bearer ${this.config.apiKey}`,
      'accept': 'text/event-stream'
    };

    const response = await fetch(url, {
      method: 'POST',
      headers,
      body: JSON.stringify(body),
    });

    if (!response.ok) {
      const errorText = await response.text();
      throw new Error(`LLM request failed: ${response.status} ${response.statusText} - ${errorText}`);
    }

    const reader = response.body?.getReader();
    const decoder = new TextDecoder();
    let fullContent = '';
    let promptTokens = 0;
    let completionTokens = 0;

    if (reader) {
      try {
        while (true) {
          const { done, value } = await reader.read();
          if (done) break;

          const chunk = decoder.decode(value);
          const lines = chunk.split('\n');

          for (const line of lines) {
            if (line.startsWith('data: ')) {
              const dataStr = line.slice(6);
              if (dataStr === '[DONE]') continue;

              try {
                const data = JSON.parse(dataStr);
                const text = data.choices?.[0]?.delta?.content || '';
                if (text) {
                  fullContent += text;
                  yield text;
                }

                if (data.usage) {
                  promptTokens = data.usage.prompt_tokens || 0;
                  completionTokens = data.usage.completion_tokens || 0;
                }
              } catch {
                // 忽略解析错误
              }
            }
          }
        }
      } finally {
        reader.releaseLock();
      }
    }

    return {
      content: fullContent,
      usage: {
        promptTokens,
        completionTokens,
        totalTokens: promptTokens + completionTokens
      }
    };
  }

  private async chatAnthropic(messages: Message[], options?: { temperature?: number; maxTokens?: number }): Promise<LLMResponse> {
    // 提取 system prompt（Anthropic 格式是单独字段）
    let systemPrompt = '';
    const filteredMessages = messages.filter(m => {
      if (m.role === 'system') {
        systemPrompt = m.content;
        return false;
      }
      return true;
    });

    // 构建 Anthropic 格式请求
    const body: any = {
      model: this.config.model,
      messages: filteredMessages,
      temperature: options?.temperature ?? 0.7,
      max_tokens: options?.maxTokens ?? 4096,
    };

    if (systemPrompt) {
      body.system = systemPrompt;
    }

    // 自动处理URL路径
    let url = this.config.baseUrl || DEFAULT_BASE_URL;
    if (!url.includes('/messages') && !url.includes('/v1')) {
      url = `${url}/v1/messages`;
    } else if (url.includes('/v1') && !url.includes('/messages')) {
      url = `${url}/messages`;
    }

    const headers: any = {
      'Content-Type': 'application/json',
      'x-api-key': this.config.apiKey,  // Anthropic标准header
    };

    // 只有配置了apiVersion才添加anthropic-version header
    if (this.config.apiVersion) {
      headers['anthropic-version'] = this.config.apiVersion;
    }

    const response = await fetch(url, {
      method: 'POST',
      headers,
      body: JSON.stringify(body),
    });

    if (!response.ok) {
      const errorText = await response.text();
      throw new Error(`LLM request failed: ${response.status} ${response.statusText} - ${errorText}`);
    }

    const data = await response.json();

    // 解析 Anthropic 响应
    let content = '';
    if (data.content && Array.isArray(data.content)) {
      content = data.content
        .filter((block: any) => block.type === 'text')
        .map((block: any) => block.text)
        .join('');
    } else if (typeof data.content === 'string') {
      content = data.content;
    }

    return {
      content,
      usage: {
        promptTokens: data.usage?.input_tokens || 0,
        completionTokens: data.usage?.output_tokens || 0,
        totalTokens: (data.usage?.input_tokens || 0) + (data.usage?.output_tokens || 0),
      },
    };
  }

  private async chatOpenAI(messages: Message[], options?: { temperature?: number; maxTokens?: number }): Promise<LLMResponse> {
    // 构建 OpenAI 格式请求
    const body: any = {
      model: this.config.model,
      messages: messages,
      temperature: options?.temperature ?? 0.7,
      max_tokens: options?.maxTokens ?? 4096,
    };

    // 自动处理URL路径
    let url = this.config.baseUrl || DEFAULT_BASE_URL;
    if (!url.includes('/chat/completions') && !url.includes('/v1')) {
      url = `${url}/v1/chat/completions`;
    } else if (url.includes('/v1') && !url.includes('/chat/completions')) {
      url = `${url}/chat/completions`;
    }

    const headers: any = {
      'Content-Type': 'application/json',
      'Authorization': `Bearer ${this.config.apiKey}`,
    };

    const response = await fetch(url, {
      method: 'POST',
      headers,
      body: JSON.stringify(body),
    });

    if (!response.ok) {
      const errorText = await response.text();
      throw new Error(`LLM request failed: ${response.status} ${response.statusText} - ${errorText}`);
    }

    const data = await response.json();

    // 解析 OpenAI 响应
    let content = '';
    if (data.choices && data.choices.length > 0) {
      content = data.choices[0]?.message?.content || '';
    }

    return {
      content,
      usage: {
        promptTokens: data.usage?.prompt_tokens || 0,
        completionTokens: data.usage?.completion_tokens || 0,
        totalTokens: data.usage?.total_tokens || 0,
      },
    };
  }

  async chatWithSystem(systemPrompt: string, userPrompt: string, options?: { temperature?: number; maxTokens?: number }): Promise<string> {
    const messages: Message[] = [
      { role: 'system', content: systemPrompt },
      { role: 'user', content: userPrompt },
    ];
    const response = await this.chat(messages, options);
    return response.content;
  }
}

export const llmClient = new LLMClient();
