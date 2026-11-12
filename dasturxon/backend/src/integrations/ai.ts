import Anthropic from '@anthropic-ai/sdk';
import { zodOutputFormat } from '@anthropic-ai/sdk/helpers/zod';
import { z } from 'zod';
import { config } from '../config.js';

// AI-проверка фото кухни (ТЗ 4.1, шаг 3). Решение AI — предварительное:
// финально заявку одобряет администратор.
const Verdict = z.object({
  approved: z.boolean(),
  reason: z.string(),
});
export type KitchenVerdict = z.infer<typeof Verdict> & { source: 'ai' | 'skipped'; };

const PROMPT = `Это фото кухни домашнего повара, который хочет продавать еду через маркетплейс.
Оцени:
1) видна ли рабочая поверхность;
2) есть ли явные признаки антисанитарии (мусор, животные на столе, плесень);
3) выглядит ли это как жилое помещение, где можно готовить еду.
Если фото не позволяет оценить (темно, размыто, не кухня) — не одобряй.
В поле reason кратко на русском объясни причину отказа (или напиши "ok", если всё в порядке).`;

let client: Anthropic | null = null;

export async function verifyKitchenPhotos(photoUrls: string[]): Promise<KitchenVerdict> {
  if (!config.ai.enabled || photoUrls.length === 0) {
    return { approved: true, reason: 'AI-проверка отключена — требуется ручная проверка администратором', source: 'skipped' };
  }
  client ??= new Anthropic();
  const response = await client.messages.parse({
    model: config.ai.model,
    max_tokens: 2000,
    output_config: { effort: 'low', format: zodOutputFormat(Verdict) },
    messages: [{
      role: 'user',
      content: [
        ...photoUrls.map((url) => ({ type: 'image' as const, source: { type: 'url' as const, url } })),
        { type: 'text' as const, text: PROMPT },
      ],
    }],
  });
  if (response.stop_reason === 'refusal' || !response.parsed_output) {
    // Не блокируем повара из-за сбоя модели — отправляем на ручную проверку.
    return { approved: true, reason: 'AI не дал ответа — требуется ручная проверка', source: 'skipped' };
  }
  return { ...response.parsed_output, source: 'ai' };
}
