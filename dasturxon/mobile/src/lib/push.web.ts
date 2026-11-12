// В вебе push не используется (expo-notifications там не поддерживает FCM-токены)
export async function registerPush(): Promise<void> {}
export function resetPush() {}
