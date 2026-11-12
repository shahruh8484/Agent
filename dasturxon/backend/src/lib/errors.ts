export class HttpError extends Error {
  constructor(public status: number, public code: string, message: string, public details?: unknown) {
    super(message);
  }
}

export const badRequest = (msg: string, details?: unknown) => new HttpError(400, 'bad_request', msg, details);
export const unauthorized = (msg = 'Требуется авторизация') => new HttpError(401, 'unauthorized', msg);
export const forbidden = (msg = 'Недостаточно прав') => new HttpError(403, 'forbidden', msg);
export const notFound = (msg = 'Не найдено') => new HttpError(404, 'not_found', msg);
export const conflict = (msg: string) => new HttpError(409, 'conflict', msg);
