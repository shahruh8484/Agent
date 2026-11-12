import { config } from '../config.js';

// SMS через Eskiz.uz (ТЗ 11.1). В mock-режиме сообщения пишутся в лог и в sentSms (для тестов).
export const sentSms: { phone: string; text: string }[] = [];

let eskizToken: string | null = null;

async function eskizAuth(): Promise<string> {
  const form = new FormData();
  form.set('email', config.sms.eskizEmail);
  form.set('password', config.sms.eskizPassword);
  const r = await fetch('https://notify.eskiz.uz/api/auth/login', { method: 'POST', body: form });
  if (!r.ok) throw new Error(`Eskiz auth failed: ${r.status}`);
  const j: any = await r.json();
  return j.data.token;
}

export async function sendSms(phone: string, text: string): Promise<void> {
  if (config.sms.provider !== 'eskiz') {
    sentSms.push({ phone, text });
    if (process.env.NODE_ENV !== 'test') console.log(`[sms:mock] ${phone}: ${text}`);
    return;
  }
  for (let attempt = 0; attempt < 2; attempt++) {
    eskizToken ??= await eskizAuth();
    const form = new FormData();
    form.set('mobile_phone', phone.replace('+', ''));
    form.set('message', text);
    form.set('from', config.sms.eskizFrom);
    const r = await fetch('https://notify.eskiz.uz/api/message/sms/send', {
      method: 'POST',
      headers: { Authorization: `Bearer ${eskizToken}` },
      body: form,
    });
    if (r.status === 401) { eskizToken = null; continue; }
    if (!r.ok) throw new Error(`Eskiz send failed: ${r.status}`);
    return;
  }
}
