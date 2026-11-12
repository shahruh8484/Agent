// Матрица доступа админ-панели (ТЗ 7.1).
export type StaffRole = 'owner' | 'accountant' | 'support';

export type Permission =
  | 'dashboard'            // главный дашборд (бухгалтер/поддержка — частично)
  | 'finance'              // финансовая аналитика
  | 'tax_reports'          // налоговые отчёты / выгрузка
  | 'payouts_run'          // запуск выплат
  | 'chefs_view'           // список поваров
  | 'chefs_verify'         // верификация
  | 'chefs_block_permanent'
  | 'chefs_block_temporary' // поддержка — до 24 часов
  | 'buyers_view'
  | 'complaints'
  | 'orders_view'
  | 'refund_small'         // до 50 000 сум
  | 'refund_large'         // свыше 50 000 сум
  | 'settings'             // комиссия, настройки платформы
  | 'staff_manage';

const matrix: Record<StaffRole, Permission[]> = {
  owner: ['dashboard', 'finance', 'tax_reports', 'payouts_run', 'chefs_view', 'chefs_verify', 'chefs_block_permanent',
    'chefs_block_temporary', 'buyers_view', 'complaints', 'orders_view', 'refund_small', 'refund_large', 'settings', 'staff_manage'],
  accountant: ['dashboard', 'finance', 'tax_reports', 'payouts_run', 'chefs_view', 'orders_view'],
  support: ['dashboard', 'chefs_view', 'chefs_verify', 'chefs_block_temporary', 'buyers_view', 'complaints', 'orders_view', 'refund_small'],
};

export function can(role: StaffRole, perm: Permission): boolean {
  return matrix[role]?.includes(perm) ?? false;
}

export function permissionsOf(role: StaffRole): Permission[] {
  return matrix[role] ?? [];
}
