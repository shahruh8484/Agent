import pg from 'pg';
import { config } from './config.js';

// BIGINT (int8) и NUMERIC возвращаем числами — суммы в сумах помещаются в Number.
pg.types.setTypeParser(20, (v) => Number(v));
pg.types.setTypeParser(1700, (v) => Number(v));
// DATE оставляем строкой YYYY-MM-DD, чтобы не зависеть от часового пояса сервера.
pg.types.setTypeParser(1082, (v) => v);

export const pool = new pg.Pool({ connectionString: config.databaseUrl, max: 20, options: '-c timezone=UTC' });

export type Db = pg.Pool | pg.PoolClient;

export async function query<T = any>(text: string, params: unknown[] = [], db: Db = pool): Promise<T[]> {
  const res = await db.query(text, params as any[]);
  return res.rows as T[];
}

export async function one<T = any>(text: string, params: unknown[] = [], db: Db = pool): Promise<T | undefined> {
  const rows = await query<T>(text, params, db);
  return rows[0];
}

export async function tx<T>(fn: (c: pg.PoolClient) => Promise<T>): Promise<T> {
  const client = await pool.connect();
  try {
    await client.query('BEGIN');
    const result = await fn(client);
    await client.query('COMMIT');
    return result;
  } catch (e) {
    await client.query('ROLLBACK');
    throw e;
  } finally {
    client.release();
  }
}
