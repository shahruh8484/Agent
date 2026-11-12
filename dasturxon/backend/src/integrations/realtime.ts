// Тонкая обёртка над Socket.io, чтобы сервисы не зависели от сервера (в тестах — no-op).
type Emitter = (room: string, event: string, payload: unknown) => void;

let emitter: Emitter = () => {};
export const emitted: { room: string; event: string; payload: any }[] = [];

export function setEmitter(fn: Emitter) { emitter = fn; }

export function emit(room: string, event: string, payload: unknown) {
  if (process.env.NODE_ENV === 'test') emitted.push({ room, event, payload });
  emitter(room, event, payload);
}

export const rooms = {
  user: (id: number) => `user:${id}`,
  order: (id: number) => `order:${id}`,
  admin: 'admin',
};
